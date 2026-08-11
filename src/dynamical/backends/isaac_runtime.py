"""Compiled Isaac Sim adapter runtime copied into a target pack.

Isaac advances the compiled world. The pack's admitted instrument runtime
produces the scientific observations from the same actions and sample ledger.
Both results are written to one hash-bound trace.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import sys
import zipfile
from pathlib import Path
from typing import Any

# Mirrors the compiled pack's own physics.physics_dt_s (1/120 s); duplicated
# here as wait_steps()'s default so it is directly callable without first
# loading a compiled pack.
PHYSICS_DT_S = 1.0 / 120.0
# The widest duration envelope any declared electrodeposition capability
# carries today is 3600 s (deposition-duration-envelope); this leaves
# headroom well beyond that at the compiled 120 Hz physics rate. The old
# 2400-step (20 s) cap rejected a 120 s OCP hold immediately.
MAX_WAIT_STEPS = 432_000


def wait_steps(
    duration_s: float, *, dt: float = PHYSICS_DT_S, max_steps: int = MAX_WAIT_STEPS
) -> int:
    """Physics steps needed to advance ``duration_s`` seconds at ``dt`` seconds/step."""

    requested = int(round(float(duration_s) / dt))
    if requested > max_steps:
        raise RuntimeError(
            f"a {duration_s!r} s duration needs {requested} steps, which exceeds the "
            f"compiled runtime step limit of {max_steps}"
        )
    return max(requested, 1)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--max-wait-steps", type=int, default=MAX_WAIT_STEPS)
    return parser.parse_args()


def _preflight(args: argparse.Namespace) -> dict[str, Any]:
    from dynamical_runtime_contract import verify_compiled_pack

    pack = verify_compiled_pack(args.world)
    campaign = pack["campaign"]
    if campaign.get("execution_status") != "requires_external_runtime_gate":
        raise RuntimeError(str(campaign.get("blocker")))
    config = pack["backend"]
    if config.get("runtime_status") != "ready_for_external_runtime_gate":
        raise RuntimeError(str(config.get("runtime_blocker")))
    if not (pack["root"] / config["stage"]).is_file():
        raise RuntimeError("compiled root stage is absent")
    runtime = pack["root"] / config["instrument_runtime"]
    if not runtime.is_file():
        raise RuntimeError("compiled instrument runtime is absent")
    if str(runtime) not in sys.path:
        sys.path.insert(0, str(runtime))
    return pack


def _step_world(
    kind: str,
    parameters: dict[str, Any],
    *,
    stage: Any,
    world: Any,
    config: dict[str, Any],
    max_wait_steps: int,
) -> int:
    """Advance the compiled world for one declared action kind.

    Dispatches over the pack's declared action types instead of a fixed
    if-chain over an invented heater vocabulary: ``pick``/``place`` get the
    portable fixed-joint manipulation binding (the only physical interaction
    this adapter can perform without a validated Isaac Lab articulation
    task); every other declared action type -- ``dispense``, ``condition``,
    ``electrodeposit``, ``measure``, ``transfer``, ``wait``, or any future
    one a facility declares -- advances physics for its own requested
    ``duration_s``/``duration`` parameter (one step if it declares neither),
    touching the role prim its capability is bound to, if any. Bulk waiting
    renders only its last step; a 600 s deposition step is tens of thousands
    of physics-only steps, and rendering every one of them is not needed to
    advance a rigid-body world honestly.
    """

    from pxr import Gf, Sdf, UsdPhysics

    roles = config["role_prim_paths"]
    asset_paths = config["asset_prim_paths"]
    dt = config["physics"]["physics_dt_s"]

    if kind == "pick":
        object_id = str(parameters.get("object", ""))
        object_path = asset_paths.get(object_id)
        actor_path = roles.get(kind)
        if object_path is None or actor_path is None:
            raise RuntimeError(f"compiled pick has no bound object or actor for {object_id!r}")
        joint_path = "/Facility/Runtime/PickJoint"
        stage.DefinePrim("/Facility/Runtime", "Scope")
        joint = UsdPhysics.FixedJoint.Define(stage, joint_path)
        joint.CreateBody0Rel().SetTargets([Sdf.Path(actor_path)])
        joint.CreateBody1Rel().SetTargets([Sdf.Path(object_path)])
        world.step(render=True)
        return 1
    if kind == "place":
        object_id = str(parameters.get("object", ""))
        target_id = str(parameters.get("target", ""))
        object_path = asset_paths.get(object_id)
        target_path = asset_paths.get(target_id)
        if object_path is None or target_path is None:
            raise RuntimeError(
                f"compiled place has no bound object/target for {object_id!r}/{target_id!r}"
            )
        stage.RemovePrim("/Facility/Runtime/PickJoint")
        moved = stage.GetPrimAtPath(object_path)
        target = stage.GetPrimAtPath(target_path)
        if not moved.IsValid() or not target.IsValid():
            raise RuntimeError("compiled place prim is absent")
        target_translation = target.GetAttribute("xformOp:translate").Get()
        moved.GetAttribute("xformOp:translate").Set(
            Gf.Vec3d(
                float(target_translation[0]),
                float(target_translation[1]),
                float(target_translation[2]) + 0.25,
            )
        )
        world.step(render=True)
        return 1

    role_path = roles.get(kind)
    if role_path is not None:
        prim = stage.GetPrimAtPath(role_path)
        if not prim.IsValid():
            raise RuntimeError(f"compiled {kind!r} role prim is absent: {role_path}")
    duration = parameters.get("duration_s", parameters.get("duration"))
    steps = (
        wait_steps(float(duration), dt=dt, max_steps=max_wait_steps) if duration is not None else 1
    )
    for step_index in range(steps):
        world.step(render=step_index == steps - 1)
    return steps


def _prim_snapshot(action: dict[str, Any], stage: Any, config: dict[str, Any]) -> dict[str, Any]:
    """Isaac-observable state for one action: role prim pose plus what was commanded.

    Position/validity is real, read straight from the composed stage. The
    value channels this facility declares (dispensed volume, deposited mass,
    overpotential, ...) have no Isaac-computable ground truth at all -- Isaac
    models geometry and contact, not chemistry -- so the one thing this
    snapshot can honestly add beyond prim pose is the campaign's own
    commanded parameters for this action, which ``_channels`` below echoes
    only for the specific channels the compiled pack declared bound to them
    (see ``isaac_sim.py``'s ``_paired_channels``).
    """

    from pxr import Usd, UsdGeom

    values: dict[str, Any] = {}
    for role, path in sorted(config["role_prim_paths"].items()):
        prim = stage.GetPrimAtPath(path)
        if not prim.IsValid():
            values[role] = {"path": path, "valid": False}
            continue
        transform = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        translation = transform.ExtractTranslation()
        values[role] = {
            "path": path,
            "valid": True,
            "world_position_m": [
                float(translation[0]),
                float(translation[1]),
                float(translation[2]),
            ],
        }
    values["commanded_parameters"] = {
        "kind": action["kind"],
        "parameters": dict(action.get("parameters", {})),
    }
    return values


def _instrument_modules(pack: dict[str, Any]) -> tuple[Any, Any]:
    runtime = pack["root"] / pack["backend"]["instrument_runtime"]
    if str(runtime) not in sys.path:
        sys.path.insert(0, str(runtime))
    from dynamical_runtime import instruments, samples

    return instruments, samples


def _model_inputs(binding: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    outputs = state.setdefault("outputs", {})
    for item in binding.get("inputs", []):
        target = str(item["target_port_id"])
        if item["source_kind"] == "campaign_input":
            if "value" not in item:
                raise RuntimeError(f"campaign input has no executable value: {target}")
            values[target] = item["value"]
            continue
        source = outputs.get(str(item["source_id"]), {})
        port = str(item["source_port_id"])
        if port not in source:
            raise RuntimeError(f"operation input is unavailable: {item['source_id']}.{port}")
        values[target] = source[port]
    return values


def _verify_model(pack: dict[str, Any], binding: dict[str, Any], model: Any) -> None:
    runtime = pack["root"] / pack["backend"]["instrument_runtime"]
    source = str(binding["model_implementation_ref"])
    entry = f"dynamical_runtime/instruments/{Path(source).name}"
    module = sys.modules.get(getattr(model, "__module__", ""))
    expected_file = f"{runtime}/{entry}"
    if module is None or getattr(module, "__file__", None) != expected_file:
        raise RuntimeError("resolved instrument model is not from the compiled runtime")
    with zipfile.ZipFile(runtime) as archive:
        actual = hashlib.sha256(archive.read(entry)).hexdigest()
    if actual != binding["model_implementation_sha256"]:
        raise RuntimeError("compiled instrument model differs from its declared implementation")


def _execute_instrument(
    pack: dict[str, Any], action: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any]:
    instruments, samples_module = _instrument_modules(pack)
    binding = pack["campaign"]["provider_bindings"][action["action_id"]]
    operation_id = str(binding["operation_id"])
    model = instruments.resolve(operation_id, str(action["provider_id"]))
    if model is None:
        raise RuntimeError(f"compiled instrument model is absent for {operation_id!r}")
    _verify_model(pack, binding, model)

    samples = state.setdefault("samples", {})
    sample_id = action.get("sample_id")
    current = samples.get(sample_id) if sample_id else None
    if (
        current is None
        and sample_id
        and action.get("station_id")
        and operation_id != "transfer-sample"
    ):
        samples.update(
            samples_module.establish_origin(
                samples,
                sample_id=str(sample_id),
                station_id=str(action["station_id"]),
                step_id=str(action["action_id"]),
            )
        )
        current = samples[sample_id]

    result = model(
        instruments.InstrumentRequest(
            parameters=dict(action.get("parameters", {})),
            inputs=_model_inputs(binding, state),
            sample=current,
        )
    )
    expected_ports = set(binding["output_port_ids"])
    if set(result.outputs) != expected_ports:
        raise RuntimeError(
            f"instrument output contract differs: expected={sorted(expected_ports)}, "
            f"found={sorted(result.outputs)}"
        )
    state["outputs"][action["action_id"]] = dict(result.outputs)

    sample_written = result.sample is not None
    if result.sample is not None:
        if operation_id == "transfer-sample":
            expected = action.get("parameters", {}).get("sample_transition")
            if not isinstance(expected, dict):
                raise RuntimeError("compiled transfer has no sample transition")
            actual = samples_module.build_transition(
                result.sample,
                current_sample=current,
                from_station_hint=str(expected["from_station"]),
                timestamp_s=float(expected["timestamp_s"]),
                step_id=str(action["action_id"]),
            ).model_dump(mode="json")
            compared = {
                key: value
                for key, value in actual.items()
                if key not in {"state_sha256"}
            }
            declared = {
                key: value
                for key, value in expected.items()
                if key not in {"state_sha256"}
            }
            if compared != declared:
                raise RuntimeError("instrument transfer differs from the compiled transition")
        samples[result.sample.id] = result.sample

    output_channels = binding["output_channel_ids"]
    channel_values = {
        channel_id: result.outputs[port_id]
        for port_id, channel_id in output_channels.items()
        if port_id in result.outputs
    }
    channel_uncertainty = {
        channel_id: result.uncertainty.get(port_id)
        for port_id, channel_id in output_channels.items()
        if port_id in result.outputs
    }
    observed = samples.get(sample_id) if sample_id else None
    return {
        "channels": channel_values,
        "uncertainty": channel_uncertainty,
        "reasons": [item.model_dump(mode="json", exclude_none=True) for item in result.reasons],
        "cost_usd": result.cost_usd,
        "duration_s": result.duration_s,
        "sample_id": sample_id,
        "sample_state_sha256": (
            samples_module.state_digest(observed.state) if observed is not None else None
        ),
        "sample_state_written": sample_written,
    }


def _channels(
    snapshot: dict[str, Any],
    config: dict[str, Any],
    *,
    provider_id: str,
    evidence_class: str,
    instrument: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return scientific outputs and declared command echoes for one action."""

    commanded = snapshot.get("commanded_parameters")
    commanded = commanded if isinstance(commanded, dict) else {}
    action_kind = commanded.get("kind")
    parameters = commanded.get("parameters")
    parameters = parameters if isinstance(parameters, dict) else {}
    scientific = instrument.get("channels", {}) if instrument else {}
    uncertainty = instrument.get("uncertainty", {}) if instrument else {}
    channels = []
    for binding in config["observation_bindings"]:
        channel_id = binding["channel_id"]
        if channel_id in scientific:
            value = scientific[channel_id]
            channels.append(
                {
                    "name": channel_id,
                    "value": value,
                    "unit": binding.get("unit", "1"),
                    "quality": "estimated" if value is not None else "unavailable",
                    "origin": "source_model",
                    "provider_id": provider_id,
                    "evidence_class": evidence_class,
                    "uncertainty": {
                        "value": uncertainty.get(channel_id),
                        "kind": "declared",
                        "origin": "compiled admitted instrument model",
                    },
                }
            )
            continue
        if (
            binding.get("status") == "compiled_stage_state_binding"
            and binding.get("action_type") == action_kind
            and binding.get("echoes_parameter") is not None
        ):
            value = parameters.get(binding["echoes_parameter"])
            if value is not None:
                channels.append(
                    {
                        "name": channel_id,
                        "value": value,
                        "unit": binding.get("unit", "1"),
                        "quality": "valid",
                        "origin": "backend_state",
                        "provider_id": provider_id,
                        "evidence_class": evidence_class,
                        "uncertainty": {
                            "value": 0.0,
                            "kind": "declared",
                            "origin": "isaac_sim launcher commanded-parameter echo",
                        },
                    }
                )
    return channels


def _commanded_channels(action: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    """The channels an action's own commanded parameters bind, before it executes.

    ``_channels`` only ever reads ``commanded_parameters`` -- never physics or
    prim state (see its own docstring) -- so this is not a preview or an
    approximation of what ``execute_action`` reports after the action runs, it
    is the identical computation, run before rather than after physics
    stepping. That is what lets pre-action constraints be evaluated, and
    enforced, before an unsafe action is ever submitted to the world.
    """

    kind = action["kind"]
    parameters = dict(action.get("parameters", {}))
    snapshot = {"commanded_parameters": {"kind": kind, "parameters": parameters}}
    return _channels(
        snapshot,
        config,
        provider_id=str(action.get("provider_id", "")),
        evidence_class=str(action.get("evidence_class", "simulator")),
    )


def execute_action(
    pack: dict[str, Any],
    action: dict[str, Any],
    *,
    stage: Any | None = None,
    world: Any | None = None,
    max_wait_steps: int = MAX_WAIT_STEPS,
    scientific_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one action through the compiled world and its admitted instrument model."""

    config = pack["backend"]
    kind = action["kind"]
    parameters = dict(action.get("parameters", {}))
    steps = 0
    if stage is not None and world is not None:
        steps = _step_world(
            kind, parameters, stage=stage, world=world, config=config, max_wait_steps=max_wait_steps
        )
        snapshot = _prim_snapshot(action, stage, config)
    else:
        snapshot = {"commanded_parameters": {"kind": kind, "parameters": parameters}}
    instrument = _execute_instrument(
        pack, action, scientific_state if scientific_state is not None else {}
    )
    channels = _channels(
        snapshot,
        config,
        provider_id=str(action.get("provider_id", "")),
        evidence_class=str(action.get("evidence_class", "simulator")),
        instrument=instrument,
    )
    snapshot["observation_channels"] = channels
    snapshot["scientific_state"] = {
        "sample_id": instrument["sample_id"],
        "sample_state_sha256": instrument["sample_state_sha256"],
        "sample_state_written": instrument["sample_state_written"],
    }
    reasons = [
        {
            "code": "MEASUREMENT_UNAVAILABLE",
            "channel_id": channel["name"],
            "detail": f"{channel['name']!r} has no bound value from this Isaac run",
        }
        for channel in channels
        if channel["quality"] != "valid"
        and channel["value"] is None
    ]
    return {
        "steps": steps,
        "snapshot": snapshot,
        "channels": channels,
        "reasons": [*instrument["reasons"], *reasons],
        "cost_usd": instrument["cost_usd"],
        "duration_s": instrument["duration_s"],
        "sample_id": instrument["sample_id"],
        "sample_state_sha256": instrument["sample_state_sha256"],
        "sample_state_written": instrument["sample_state_written"],
    }


def _handle_constraints(
    constraints: list[dict[str, Any]], *, hard_enforcement: str = "terminate"
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split evaluated constraints into degrade reasons and genuine hard failures.

    A constraint with no measured value (``outcome == "unavailable"``, e.g. a
    channel this run has no binding for) degrades to a recorded
    ``MEASUREMENT_UNAVAILABLE`` reason rather than aborting the run before
    the trace is written. A constraint with a real, measured violation still
    aborts only at ``hard_enforcement`` ("terminate"); ``reject``/``report``
    violations are recorded (via the returned ``constraints`` list itself)
    but must not force campaign failure.

    This applies to post-action constraints only. Pre-action constraints get
    no such leniency in ``main()``: any failure there -- reject, terminate, or
    an unavailable measurement -- prevents the action from executing at all,
    matching campaign.py's ``run_composed_campaign`` exactly (see
    ``test_isaac_rejects_the_action_for_any_failed_pre_action_constraint``). A
    reject-enforcement violation noticed only after the action already ran
    would be too late to mean "reject".
    """

    reasons = []
    hard_failures = []
    for item in constraints:
        if item["passed"]:
            continue
        if item["outcome"] == "unavailable":
            reasons.append(
                {
                    "code": "MEASUREMENT_UNAVAILABLE",
                    "constraint_id": item["constraint_id"],
                    "detail": (
                        f"constraint {item['constraint_id']!r} has no measured value "
                        "from this Isaac run"
                    ),
                }
            )
        elif item["limit"]["enforcement"] == hard_enforcement:
            hard_failures.append(item)
    return reasons, hard_failures


def _write_receipt(output: Path, pack: dict[str, Any], status: str, trace_hash: str | None) -> None:
    from dynamical_runtime_contract import file_sha256

    files = sorted(path for path in output.rglob("*") if path.is_file())
    receipt = {
        "schema_version": "dynamical.runtime-evidence.v1",
        "backend": "isaac_sim",
        "host_id": socket.gethostname(),
        "backend_revision": pack["backend"]["isaac_sim_version"],
        "core_ir_sha256": pack["manifest"]["core_ir_sha256"],
        "compiled_world_sha256": pack["manifest"]["world_sha256"],
        "execution_status": status,
        "trace_sha256": trace_hash,
        "receipt_complete": True,
        "intended_exit_code": 0 if status == "passed" else 1,
        "simulation_app_shutdown_requested": True,
        "runtime_error": (
            None
            if status == "passed"
            else {
                "type": "runtime_failure",
                "message": "Isaac runtime exited before a complete campaign trace passed",
            }
        ),
        "w1_admitted": False,
        "manual_gates": [
            "The portable adapter uses a fixed-joint manipulation binding. A validated "
            "Isaac Lab articulation task remains required.",
            "Inspect the runtime render and trace.",
            "Validate receipt-bound replay.",
            "Confirm no Isaac or Kit process remains.",
        ],
        "artifacts": [
            {"path": str(path.relative_to(output)), "sha256": file_sha256(path)}
            for path in files
            if path.name != "runtime_evidence.json"
        ],
    }
    (output / "runtime_evidence.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> int:
    # The verified pack's file set is exact: never shadow the hashed
    # dynamical_runtime_contract.py with generated bytecode inside the pack.
    sys.dont_write_bytecode = True
    args = _parse_args()
    pack = _preflight(args)
    run_dir = args.output.parent
    run_dir.mkdir(parents=True, exist_ok=True)

    from isaacsim import SimulationApp

    app = SimulationApp({"headless": args.headless})
    status = "failed"
    trace_hash: str | None = None
    try:
        import omni.usd
        from dynamical_runtime_contract import TraceWriter, constraint_evidence, write_snapshot
        from isaacsim.core.api import World

        config = pack["backend"]
        stage_path = pack["root"] / config["stage"]
        if not omni.usd.get_context().open_stage(str(stage_path)):
            raise RuntimeError("Isaac Sim did not open the compiled stage")
        for _ in range(10):
            app.update()
        stage = omni.usd.get_context().get_stage()
        # open_stage returning True is not evidence the stage loaded: a malformed
        # sublayer can make Kit report success with an empty composed stage.
        prim_count = sum(1 for _ in stage.Traverse())
        if prim_count == 0:
            raise RuntimeError("Isaac Sim opened the compiled stage but composed zero prims")
        world = World(
            stage_units_in_meters=1.0,
            physics_dt=config["physics"]["physics_dt_s"],
            rendering_dt=config["physics"]["render_dt_s"],
        )
        world.reset()
        writer = TraceWriter(
            pack,
            run_id=f"isaac-{args.seed:08d}",
            seed=args.seed,
            backend_revision=f"isaac_sim:{config['isaac_sim_version']}",
            provenance={
                "embodied_backend": True,
                "compiled_adapter": True,
                "manipulation_binding": "portable_fixed_joint",
                "w1_admitted": False,
                "prim_count": prim_count,
            },
            output_path=args.output,
        )
        writer.add("campaign_start", 0.0)
        logical_time = 0.0
        scientific_state: dict[str, Any] = {}
        consumed_cost_usd = 0.0
        consumed_duration_s = 0.0
        run_reasons: list[dict[str, Any]] = []
        # dynamical.campaign.validate_events requires the terminal execution_status to
        # be "failed" whenever ANY constraint anywhere in the trace was not passed --
        # stricter than compiled_runtime.py's own (enforcement-level-scoped) copy, since
        # this is the campaign format the wider replay/CLI pipeline consumes. A
        # not-passed post-action constraint never aborts the run (see
        # _handle_constraints), but it must make campaign_end honest about it. A
        # not-passed pre-action constraint is different: it must prevent the action
        # from running at all (see below), so it never reaches campaign_ok as a
        # "ran anyway" outcome.
        campaign_ok = True
        for index, action in enumerate(pack["campaign"]["actions"]):
            # Pre-action constraints are evaluated -- and enforced -- before the
            # action ever executes, from exactly the channels it would echo (see
            # _commanded_channels), matching campaign.py's run_composed_campaign
            # exactly: ANY failed pre-action constraint, not only a
            # terminate-enforcement one, prevents this action's execution. Every
            # facility constraint today is pre_action + reject; submitting the
            # instrument action first and only noticing the violation afterward
            # would make "reject" meaningless.
            preview_channels = _commanded_channels(action, config)
            pre_constraints = constraint_evidence(
                action, pack, phase="pre_action", channels=preview_channels
            )
            campaign_ok = campaign_ok and all(item["passed"] for item in pre_constraints)
            writer.add("action", logical_time, action=action, constraints=pre_constraints)
            if any(not item["passed"] for item in pre_constraints):
                raise RuntimeError(
                    f"action {action['action_id']} failed a pre-action constraint; "
                    "rejected before execution"
                )
            outcome = execute_action(
                pack,
                action,
                stage=stage,
                world=world,
                max_wait_steps=args.max_wait_steps,
                scientific_state=scientific_state,
            )
            consumed_cost_usd += outcome["cost_usd"]
            consumed_duration_s += outcome["duration_s"]
            logical_time += outcome["steps"] * config["physics"]["physics_dt_s"]
            evidence = write_snapshot(
                run_dir / "evidence" / f"observation-{index:03d}.json",
                outcome["snapshot"],
                provider_id=str(action["provider_id"]),
                evidence_class=str(action["evidence_class"]),
            )
            post_constraints = constraint_evidence(
                action, pack, phase="post_action", channels=outcome["channels"]
            )
            post_reasons, post_hard_failures = _handle_constraints(post_constraints)
            campaign_ok = campaign_ok and all(item["passed"] for item in post_constraints)
            if post_hard_failures:
                raise RuntimeError(
                    f"action {action['action_id']} failed a terminate-enforcement "
                    "post-action constraint"
                )
            all_reasons = [*outcome["reasons"], *post_reasons]
            run_reasons.extend(all_reasons)
            observation_provenance = {"reasons": all_reasons} if all_reasons else {}
            if outcome["sample_id"] and outcome["sample_state_sha256"]:
                observation_provenance.update(
                    {
                        "sample_id": outcome["sample_id"],
                        "sample_state_sha256": outcome["sample_state_sha256"],
                        "sample_state_written": outcome["sample_state_written"],
                    }
                )
            writer.add(
                "observation",
                logical_time,
                observation={
                    "frame_id": f"isaac-frame-{index:03d}",
                    "logical_time_s": logical_time,
                    "provider_id": action["provider_id"],
                    "evidence_class": action["evidence_class"],
                    "channels": outcome["channels"],
                    "evidence_ids": [evidence["evidence_id"]],
                },
                evidence=[evidence],
                constraints=post_constraints,
                provenance=observation_provenance or None,
            )
        writer.add(
            "campaign_end",
            logical_time,
            provenance={
                "execution_status": "passed" if campaign_ok else "failed",
                "reasons": run_reasons,
                "cost_consumed_usd": consumed_cost_usd,
                "duration_consumed_s": consumed_duration_s,
            },
        )
        trace_hash = writer.write(args.output)
        # The receipt's own status reports runtime completion (the launcher ran the
        # whole declared campaign and wrote a schema-valid trace), independent of
        # whether the campaign's own constraints all passed -- that is campaign_end's
        # execution_status above, which validate_events checks on its own terms.
        status = "passed"
        return 0
    finally:
        # Write the receipt before closing the app, not after: SimulationApp.close()
        # can tear the process down hard enough that code after it never runs (empirically
        # observed: a passed trace with the receipt silently never written).
        _write_receipt(run_dir, pack, status, trace_hash)
        app.close()


if __name__ == "__main__":
    sys.exit(main())
