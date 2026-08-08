"""Compiled Isaac Sim adapter runtime copied into a target pack."""

from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path
from typing import Any

from dynamical_runtime_contract import (
    TraceWriter,
    constraint_evidence,
    file_sha256,
    verify_compiled_pack,
    write_snapshot,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiled-world", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--max-wait-steps", type=int, default=2400)
    return parser.parse_args()


def _preflight(args: argparse.Namespace) -> dict[str, Any]:
    pack = verify_compiled_pack(args.compiled_world)
    campaign = pack["campaign"]
    if campaign.get("execution_status") != "requires_external_runtime_gate":
        raise RuntimeError(str(campaign.get("blocker")))
    config = pack["backend"]
    if config.get("runtime_status") != "ready_for_external_runtime_gate":
        raise RuntimeError(str(config.get("runtime_blocker")))
    if not (pack["root"] / config["stage"]).is_file():
        raise RuntimeError("compiled root stage is absent")
    return pack


def _attribute(prim: Any, name: str, value_type: Any) -> Any:
    existing = prim.GetAttribute(name)
    return existing if existing else prim.CreateAttribute(name, value_type, custom=True)


def _execute_action(
    action: dict[str, Any],
    *,
    stage: Any,
    world: Any,
    config: dict[str, Any],
    asset_roles: dict[str, str],
    max_wait_steps: int,
) -> int:
    from pxr import Gf, Sdf, UsdPhysics

    roles = config["role_prim_paths"]
    kind = action["kind"]
    parameters = action["parameters"]
    if kind == "wait":
        requested = int(round(float(parameters["duration"]) / config["physics"]["physics_dt_s"]))
        steps = min(max(requested, 1), max_wait_steps)
        if steps != requested:
            raise RuntimeError("wait action exceeds the compiled runtime step limit")
        for _ in range(steps):
            world.step(render=True)
        return steps
    if kind == "set_heater":
        prim = stage.GetPrimAtPath(roles["heater"])
        if not prim.IsValid():
            raise RuntimeError("compiled heater prim is absent")
        _attribute(prim, "dynamical:heaterEnabled", Sdf.ValueTypeNames.Bool).Set(
            bool(parameters["enabled"])
        )
        if parameters.get("target-temperature") is not None:
            _attribute(prim, "dynamical:heaterTargetK", Sdf.ValueTypeNames.Double).Set(
                float(parameters["target-temperature"])
            )
        world.step(render=True)
        return 1
    if kind == "pick":
        if parameters["object"] != asset_roles["beaker"]:
            raise RuntimeError("compiled pick object does not match the beaker role")
        joint_path = "/Facility/Runtime/PickJoint"
        stage.DefinePrim("/Facility/Runtime", "Scope")
        joint = UsdPhysics.FixedJoint.Define(stage, joint_path)
        joint.CreateBody0Rel().SetTargets([Sdf.Path(roles["robot"])])
        joint.CreateBody1Rel().SetTargets([Sdf.Path(roles["beaker"])])
        world.step(render=True)
        return 1
    if kind == "place":
        if parameters["object"] != asset_roles["beaker"]:
            raise RuntimeError("compiled place object does not match the beaker role")
        if parameters["target"] != asset_roles["heater"]:
            raise RuntimeError("compiled place target does not match the heater role")
        stage.RemovePrim("/Facility/Runtime/PickJoint")
        beaker = stage.GetPrimAtPath(roles["beaker"])
        heater = stage.GetPrimAtPath(roles["heater"])
        if not beaker.IsValid() or not heater.IsValid():
            raise RuntimeError("compiled place prim is absent")
        target = heater.GetAttribute("xformOp:translate").Get()
        beaker.GetAttribute("xformOp:translate").Set(
            Gf.Vec3d(float(target[0]), float(target[1]), float(target[2]) + 0.25)
        )
        world.step(render=True)
        return 1
    raise RuntimeError(f"Isaac action mapping is absent for {kind!r}")


def _prim_snapshot(stage: Any, config: dict[str, Any]) -> dict[str, Any]:
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
        for name in ("dynamical:heaterEnabled", "dynamical:heaterTargetK"):
            attribute = prim.GetAttribute(name)
            if attribute:
                values[role][name] = attribute.Get()
    return values


def _channels(
    snapshot: dict[str, Any],
    config: dict[str, Any],
    *,
    provider_id: str,
    evidence_class: str,
) -> list[dict[str, Any]]:
    heater = snapshot.get("heater", {})
    known = {
        "heater.on": heater.get("dynamical:heaterEnabled"),
        "heater.target_temperature_K": heater.get("dynamical:heaterTargetK"),
    }
    channels = []
    for binding in config["observation_bindings"]:
        value = known.get(binding["channel_id"])
        channels.append(
            {
                "name": binding["channel_id"],
                "value": value,
                "unit": binding.get("unit", "1"),
                "quality": "valid" if value is not None else "unavailable",
                "origin": "backend_state",
                "provider_id": provider_id,
                "evidence_class": evidence_class,
            }
        )
    return channels


def _write_receipt(output: Path, pack: dict[str, Any], status: str, trace_hash: str | None) -> None:
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
    args = _parse_args()
    pack = _preflight(args)
    args.output.mkdir(parents=True, exist_ok=True)

    from isaacsim import SimulationApp

    app = SimulationApp({"headless": args.headless})
    status = "failed"
    trace_hash: str | None = None
    try:
        import omni.usd
        from isaacsim.core.api import World

        config = pack["backend"]
        stage_path = pack["root"] / config["stage"]
        if not omni.usd.get_context().open_stage(str(stage_path)):
            raise RuntimeError("Isaac Sim did not open the compiled stage")
        for _ in range(10):
            app.update()
        stage = omni.usd.get_context().get_stage()
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
            },
        )
        writer.add("campaign_start", 0.0)
        logical_time = 0.0
        for index, action in enumerate(pack["campaign"]["actions"]):
            constraints = constraint_evidence(action, pack, phase="pre_action")
            if any(not item["passed"] for item in constraints):
                raise RuntimeError(f"action {action['action_id']} failed a pre-action constraint")
            writer.add("action", logical_time, action=action, constraints=constraints)
            steps = _execute_action(
                action,
                stage=stage,
                world=world,
                config=config,
                asset_roles=pack["campaign"]["asset_roles"],
                max_wait_steps=args.max_wait_steps,
            )
            snapshot = _prim_snapshot(stage, config)
            observed_channels = _channels(
                snapshot,
                config,
                provider_id=str(action["provider_id"]),
                evidence_class=str(action["evidence_class"]),
            )
            logical_time += steps * config["physics"]["physics_dt_s"]
            evidence = write_snapshot(
                args.output / "evidence" / f"observation-{index:03d}.json",
                snapshot,
                provider_id=str(action["provider_id"]),
                evidence_class=str(action["evidence_class"]),
            )
            post_constraints = constraint_evidence(
                action,
                pack,
                phase="post_action",
                channels=observed_channels,
            )
            if any(not item["passed"] for item in post_constraints):
                raise RuntimeError(f"action {action['action_id']} failed a post-action constraint")
            writer.add(
                "observation",
                logical_time,
                observation={
                    "frame_id": f"isaac-frame-{index:03d}",
                    "logical_time_s": logical_time,
                    "provider_id": action["provider_id"],
                    "evidence_class": action["evidence_class"],
                    "channels": observed_channels,
                    "evidence_ids": [evidence["evidence_id"]],
                },
                evidence=[evidence],
                constraints=post_constraints,
            )
        writer.add("campaign_end", logical_time, provenance={"execution_status": "passed"})
        trace_hash = writer.write(args.output / "campaign_trace.ndjson")
        status = "passed"
        return 0
    finally:
        app.close()
        _write_receipt(args.output, pack, status, trace_hash)


if __name__ == "__main__":
    sys.exit(main())
