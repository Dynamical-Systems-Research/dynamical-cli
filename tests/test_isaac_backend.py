"""Isaac is the execution backend: the live run produces the trace."""

import json
import os
import subprocess
from pathlib import Path

import pytest

ISAAC = Path(
    os.environ.get("ISAAC_SIM_ROOT", "/home/jarrodbarnes/.local/share/dynamical/isaac-sim-5.1")
)
requires_isaac = pytest.mark.skipif(not ISAAC.exists(), reason="Isaac Sim not installed")


def _run_isaac_launcher(compiled_world: Path, output: Path) -> subprocess.CompletedProcess[str]:
    """Shell out to the compiled pack's own launcher, exactly as an operator would.

    Kit's EULA gate has no environment variable (piped ``"Yes\\n"``); ``LD_PRELOAD``
    is mandatory or the launcher exits before Kit starts.
    """

    env = dict(os.environ)
    env["LD_PRELOAD"] = (
        "/lib/aarch64-linux-gnu/libgomp.so.1:"
        f"{ISAAC}/lib/python3.11/site-packages/torch.libs/libgomp-58a43326.so.1.0.0"
    )
    return subprocess.run(
        [
            str(ISAAC / "bin" / "python"),
            str(compiled_world / "run_isaac_sim.py"),
            "--world",
            str(compiled_world),
            "--output",
            str(output),
        ],
        input="Yes\n",
        capture_output=True,
        text=True,
        env=env,
    )


@requires_isaac
def test_live_run_composes_the_facility_and_writes_a_trace(
    tmp_path, compiled_electrodeposition_world
):
    trace = tmp_path / "trace.ndjson"
    env = dict(os.environ)
    env["LD_PRELOAD"] = (
        "/lib/aarch64-linux-gnu/libgomp.so.1:"
        f"{ISAAC}/lib/python3.11/site-packages/torch.libs/libgomp-58a43326.so.1.0.0"
    )
    result = subprocess.run(
        [
            str(ISAAC / "bin" / "python"),
            str(compiled_electrodeposition_world / "run_isaac_sim.py"),
            "--world",
            str(compiled_electrodeposition_world),
            "--output",
            str(trace),
        ],
        input="Yes\n",
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    events = [json.loads(line) for line in trace.read_text().splitlines() if line.strip()]
    assert events, "the live run must produce trace events"
    assert events[0]["provenance"]["prim_count"] > 0, (
        "open_stage returning True is not evidence the stage loaded"
    )


@requires_isaac
def test_live_kit_run_of_coverage_campaign_has_zero_lineage_findings(
    tmp_path, compiled_electrodeposition_coverage_world
):
    """The release's central claim, proved on the execution backend it actually
    ships with: a live Isaac Sim Kit run of the full six-step campaign (dispense,
    transfer, condition, transfer, deposit, measure) produces a trace with zero
    sample-lineage findings and a clean run of every action and constraint --
    Isaac's own runtime completion. Isaac Sim has no chemistry model, so it
    cannot honestly report the campaign's required OER overpotential
    measurement (see repair-2 defect 1): runtime completion is not proof
    completion, so ``squidstat.overpotential_v`` stays unavailable and both the
    raw trace's own validation and embodied replay must refuse to call this
    campaign's proof requirement satisfied, even though every action ran and
    every constraint passed.

    Slow: Kit boot plus a real 600 s / 72,000-step deposition (~2.5 minutes wall
    clock, measured). Runs only when Isaac Sim is installed (``requires_isaac``
    above) -- deliberately not part of the default fast suite. The non-Kit half of
    this same claim (the compiled campaign's own lineage data, which is what a live
    run would replay against) is proved unconditionally by
    ``test_coverage_campaign_compiles_for_isaac_with_zero_lineage_findings`` below,
    so CI keeps real coverage of this even where Isaac Sim is never installed.
    """
    from dynamical.campaign import CampaignValidationError, validate_path
    from dynamical.replay import replay_trace

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    trace = run_dir / "campaign_trace.ndjson"
    result = _run_isaac_launcher(compiled_electrodeposition_coverage_world, trace)
    # Isaac's own runtime completion is genuinely clean here: every action ran,
    # every pre-action constraint passed, so the launcher exits 0 -- exactly the
    # "runtime completion" the proof-requirement check must not be fooled by.
    assert result.returncode == 0, result.stderr[-4000:]

    trace_validation = validate_path(trace)
    assert trace_validation["execution_status"] == "failed"
    assert trace_validation["valid"] is False
    proof_reasons = [
        reason
        for reason in trace_validation["reasons"]
        if reason["code"] == "PROOF_OUTPUT_UNAVAILABLE"
    ]
    assert proof_reasons, trace_validation["reasons"]
    assert proof_reasons[0]["channel_id"] == "squidstat.overpotential_v"

    with pytest.raises(CampaignValidationError, match="no passed terminal execution status"):
        replay_trace(
            trace,
            tmp_path / "replay.ndjson",
            compiled_world=compiled_electrodeposition_coverage_world,
            runtime_receipt=run_dir / "runtime_evidence.json",
        )


def test_missing_channel_degrades_rather_than_raising(isaac_pack_with_unbound_channel):
    from dynamical.backends.isaac_runtime import execute_action

    outcome = execute_action(isaac_pack_with_unbound_channel, {"kind": "measure-oer"})
    assert any(r["code"] == "MEASUREMENT_UNAVAILABLE" for r in outcome["reasons"])


def test_unbound_channel_reports_unknown_not_zero_uncertainty(isaac_pack_with_unbound_channel):
    """Repair-2 defect 4, Isaac side: an unbound channel's uncertainty must be
    unknown (``None``), not a fabricated ``0.0`` claiming an exact measurement
    Isaac never made of a value it does not have.
    """
    from dynamical.backends.isaac_runtime import execute_action

    outcome = execute_action(isaac_pack_with_unbound_channel, {"kind": "measure-oer"})
    unavailable = [
        channel for channel in outcome["channels"] if channel["quality"] == "unavailable"
    ]
    assert unavailable
    assert all(channel["uncertainty"]["value"] is None for channel in unavailable)


def test_paired_channels_uses_the_declared_binding_not_list_position():
    """Repair-2 defect 5: an observation channel's echoed parameter is read from
    the capability's own declared ``echoed_parameter_bindings``, never inferred by
    pairing ``observation_channel_ids`` and ``parameters`` by list index. Here the
    declared binding names the *second* channel, not the first -- proving a
    provider that reorders its channels (or that simply doesn't declare a 1:1
    positional correspondence) is bound correctly rather than silently paired
    with the wrong parameter by position.
    """
    from dynamical.backends.isaac_sim import _paired_channels

    capability = {
        "observation_channel_ids": ["device.channel_a", "device.channel_b"],
        "parameters": [{"name": "param_x"}, {"name": "param_y"}],
        "echoed_parameter_bindings": {"device.channel_b": "param_x"},
    }

    pairs = _paired_channels(capability, always_present_parameters={"param_x", "param_y"})

    assert pairs == {"device.channel_b": "param_x"}


def test_wait_beyond_the_old_cap_is_permitted(isaac_pack):
    from dynamical.backends.isaac_runtime import wait_steps

    assert wait_steps(120.0) > 2400, "a 120 s OCP step exceeded the old 20 s cap"


def _action_events_from_isaac_campaign(pack: dict) -> list:
    """Minimal action-only ``TraceEvent``\\ s built straight from a compiled isaac
    pack's own ``runtime_campaign.json``.

    ``dynamical.samples.check_invariants`` reads only ``event.action``, so this is
    enough to prove the compiled campaign's own lineage data -- embedded entirely at
    compile time by ``_runtime_pack.py``'s ``runtime_campaign()``, independent of
    whether Isaac Sim ever actually runs it -- is correct without needing a live Kit
    run at all.
    """

    from dynamical.campaign import ActionRequest, EvidenceClass, RunMode, TraceEvent, stable_hash

    identity = {
        "campaign_id": "isaac-lineage-check",
        "run_id": "isaac-lineage-check",
        "seed": 0,
        "backend_revision": "test-fixture:not-embodied",
        "ir_hash": stable_hash({"fixture": "isaac-lineage-check", "part": "ir"}),
        "world_hash": stable_hash({"fixture": "isaac-lineage-check", "part": "world"}),
        "campaign_hash": stable_hash({"fixture": "isaac-lineage-check", "part": "campaign"}),
    }
    events = []
    for index, action in enumerate(pack["campaign"]["actions"]):
        events.append(
            TraceEvent(
                mode=RunMode.SIMULATE,
                event_type="action",
                event_id=f"isaac-lineage-check:event:{index:06d}",
                sequence=index,
                logical_time_s=float(index),
                provenance={},
                action=ActionRequest(
                    action_id=action["action_id"],
                    kind=action["kind"],
                    actor_id=action["actor_id"],
                    provider_id=action["provider_id"],
                    evidence_class=EvidenceClass(action["evidence_class"]),
                    parameters=action["parameters"],
                    sample_id=action.get("sample_id"),
                    station_id=action.get("station_id"),
                ),
                **identity,
            )
        )
    return events


def _compile_coverage_isaac(
    tmp_path: Path, *, to_squidstat_station: str = "squidstat-echem"
) -> Path:
    from test_electrodeposition_registry import MANIFEST, REGISTRY, _coverage_requirement

    from dynamical.compiler import compile_facility
    from dynamical.composition import compose_virtual_sdl
    from dynamical.schema import load_capability_registry

    registry = load_capability_registry(REGISTRY)
    requirement = _coverage_requirement(to_squidstat_station=to_squidstat_station)
    composition = compose_virtual_sdl(requirement, registry)
    assert composition.status == "COMPILED", composition.reason_codes
    return compile_facility(MANIFEST, "isaac", tmp_path, composition_result=composition).output_dir


def _compile_coverage_isaac_with_narrowed_current_envelope(tmp_path: Path) -> Path:
    """The same compiled six-step isaac world, except ``current-envelope``'s own
    declared bound is narrowed below the campaign's real ``current_a`` (0.002827 A).

    Models a facility whose own safety interlock is tighter than the
    instrument's abstract admitted operating range -- a realistic scenario, not
    a hack: the registry provider's admitted ``validity_envelope`` (what
    composition and ``compile_facility``'s facility-binding cross-check both
    enforce) is untouched, so this reaches ``COMPILED``/compiles exactly like
    the unmodified six-step campaign. Only the facility's own runtime
    constraint -- the one Isaac evaluates pre-action -- is tightened, so the
    otherwise-valid ``deposit`` action now violates it.
    """
    from test_electrodeposition_registry import MANIFEST, REGISTRY, _coverage_requirement

    from dynamical.compiler import compile_facility
    from dynamical.composition import compose_virtual_sdl
    from dynamical.schema import load_capability_registry, load_facility_manifest

    registry = load_capability_registry(REGISTRY)
    requirement = _coverage_requirement()
    composition = compose_virtual_sdl(requirement, registry)
    assert composition.status == "COMPILED", composition.reason_codes

    document = load_facility_manifest(MANIFEST)
    narrowed_constraints = [
        (
            constraint.model_copy(
                update={"bound": constraint.bound.model_copy(update={"maximum": 0.001})}
            )
            if constraint.id == "current-envelope"
            else constraint
        )
        for constraint in document.constraints
    ]
    narrowed_document = document.model_copy(update={"constraints": narrowed_constraints})
    return compile_facility(
        narrowed_document, "isaac", tmp_path, composition_result=composition
    ).output_dir


@requires_isaac
def test_live_kit_run_rejects_the_deposit_action_before_executing_an_unsafe_current(tmp_path):
    """Repair-2 defect 2: a pre-action reject-enforcement violation must prevent its
    action from executing, not merely be noticed afterward.

    Every declared facility constraint is ``pre_action`` + ``reject`` (see
    ``manifests/ac-electrodeposition-cell.yaml``), the live path this exercises:
    ``current-envelope`` (narrowed below the campaign's real 0.002827 A current, see
    ``_compile_coverage_isaac_with_narrowed_current_envelope``) must reject the
    ``deposit`` action before Isaac ever submits it to the instrument, not execute it
    and merely record the violation. Before the fix, this ran to completion and wrote
    a "passed" receipt over an action that actually violated its own declared safety
    envelope -- the reviewer's exact reproduction. After the fix, the receipt honestly
    records "failed", and the partial trace it still wrote (the receipt is written
    before the process exits, per ``main()``'s ``finally``) ends right after
    ``deposit``'s ``action`` event with no matching ``observation`` -- proof the
    instrument action itself was never submitted, not merely that the campaign was
    later marked failed.

    The launcher's raw OS exit code is not asserted here: ``SimulationApp.close()``
    can tear the Kit process down through its own low-level shutdown path before a
    pending Python exception ever gets to set the process exit status (confirmed by
    hand: no traceback reaches stderr even though the receipt and trace both honestly
    record the failure) -- a Kit/Omniverse runtime quirk orthogonal to this repair's
    five defects, all of which are about receipt/trace *content*, not the subprocess's
    raw return code.
    """
    import json as json_module

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    trace = run_dir / "campaign_trace.ndjson"
    compiled_world = _compile_coverage_isaac_with_narrowed_current_envelope(tmp_path / "world")

    _run_isaac_launcher(compiled_world, trace)

    receipt = json_module.loads((run_dir / "runtime_evidence.json").read_text(encoding="utf-8"))
    assert receipt["execution_status"] == "failed"
    assert receipt["intended_exit_code"] == 1

    events = [
        json_module.loads(line)
        for line in trace.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert events[-1]["event_type"] == "action"
    assert events[-1]["action"]["action_id"] == "deposit"
    assert not any(event["event_type"] == "campaign_end" for event in events), (
        "deposit's action event must be the trace's last event: no observation, no "
        "campaign_end -- the deposit instrument action itself must never have run"
    )
    # Every earlier action DID run to completion (each has its matching observation):
    # only the unsafe action itself was rejected before execution, not the whole
    # campaign aborted retroactively.
    earlier_action_ids = [
        event["action"]["action_id"] for event in events[:-1] if event["event_type"] == "action"
    ]
    assert earlier_action_ids == [
        "materialize",
        "dispense",
        "to-arduino",
        "condition",
        "to-squidstat",
    ]
    observation_action_ids = {
        events[index - 1]["action"]["action_id"]
        for index, event in enumerate(events)
        if event["event_type"] == "observation"
    }
    assert observation_action_ids == set(earlier_action_ids)


def test_coverage_campaign_compiles_for_isaac_with_zero_lineage_findings(tmp_path):
    """Non-Kit companion to the live-Kit six-step test above, and the direct isaac-path
    analog of ``test_electrodeposition_registry.py``'s
    ``test_one_sample_moves_through_three_workstations_by_explicit_transfer`` /
    ``test_coverage_campaign_compiles_and_runs_with_zero_lineage_findings``: one
    sample moves through three workstations via explicit ``transfer-sample`` actions,
    each carrying a real embedded ``sample_transition`` (see
    ``_runtime_pack.py::runtime_campaign``, which calls the same registered
    ``transfer.py`` instrument model ``campaign.py``'s composed path calls live), and
    ``check_invariants`` finds nothing wrong with it -- proved without Isaac Sim
    installed, since the lineage data is fixed entirely at compile time.
    """
    from dynamical.backends.compiled_runtime import verify_compiled_pack
    from dynamical.samples import check_invariants

    output = _compile_coverage_isaac(tmp_path)
    pack = verify_compiled_pack(output)
    events = _action_events_from_isaac_campaign(pack)
    assert check_invariants(events) == []


def test_coverage_campaign_retargeted_transfer_still_fails_lineage_on_isaac(tmp_path):
    """Negative control mirroring ``test_electrodeposition_registry.py``'s
    ``test_coverage_campaign_retargeted_transfer_still_fails_lineage``: retargeting
    ``to-squidstat``'s declared destination away from where ``deposit``/``measure``
    actually resolve must still fail lineage on the isaac path too -- an invariant
    that stops firing is worse than one that fires wrongly.
    """
    from dynamical.backends.compiled_runtime import verify_compiled_pack
    from dynamical.samples import check_invariants

    output = _compile_coverage_isaac(tmp_path, to_squidstat_station="arduino-conditioning")
    pack = verify_compiled_pack(output)
    events = _action_events_from_isaac_campaign(pack)
    reasons = check_invariants(events)
    assert any(reason.code == "SAMPLE_TRANSFER_MISSING" for reason in reasons)
