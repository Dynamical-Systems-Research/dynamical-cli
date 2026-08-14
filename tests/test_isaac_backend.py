"""Isaac is the execution backend: the live run produces the trace."""

import json
import os
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

ISAAC = Path(
    os.environ.get("ISAAC_SIM_ROOT", "/home/jarrodbarnes/.local/share/dynamical/isaac-sim-6.0.1")
)
requires_isaac = pytest.mark.skipif(not ISAAC.exists(), reason="Isaac Sim not installed")


def _run_isaac_launcher(compiled_world: Path, output: Path) -> subprocess.CompletedProcess[str]:
    """Shell out to the compiled pack's own launcher, exactly as an operator would."""

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
        env=dict(os.environ),
    )


@requires_isaac
def test_live_run_composes_the_facility_and_writes_a_trace(
    tmp_path, compiled_electrodeposition_world
):
    trace = tmp_path / "trace.ndjson"
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
        env=dict(os.environ),
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
    from dynamical.campaign import validate_path
    from dynamical.replay import replay_trace

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    trace = run_dir / "campaign_trace.ndjson"
    result = _run_isaac_launcher(compiled_electrodeposition_coverage_world, trace)
    assert result.returncode == 0, result.stderr[-4000:]

    trace_validation = validate_path(trace)
    assert trace_validation["execution_status"] == "passed"
    assert trace_validation["valid"] is True
    events = [json.loads(line) for line in trace.read_text().splitlines() if line.strip()]
    overpotential = [
        channel
        for event in events
        for channel in (event.get("observation") or {}).get("channels", [])
        if channel["name"] == "squidstat.overpotential_v"
    ]
    assert len(overpotential) == 1
    assert overpotential[0]["value"] is not None
    assert overpotential[0]["origin"] == "source_model"

    replay = replay_trace(
        trace,
        tmp_path / "replay.ndjson",
        compiled_world=compiled_electrodeposition_coverage_world,
        runtime_receipt=run_dir / "runtime_evidence.json",
    )
    assert replay["valid"] is True


def test_compiled_instrument_runtime_returns_scientific_feedback(
    compiled_electrodeposition_coverage_world,
):
    from dynamical.backends.compiled_runtime import verify_compiled_pack
    from dynamical.campaign import CampaignValidationError
    from dynamical.replay import _expected_snapshot_channels

    pack = verify_compiled_pack(compiled_electrodeposition_coverage_world)
    assert pack["backend"]["isaac_sim_version"] == "6.0.1"
    assert pack["backend"]["isaac_lab_revision"] == "portable-runtime-no-isaac-lab-task"
    assert pack["backend"]["python"] == "3.12"
    launcher = __import__("dynamical.backends.isaac_runtime", fromlist=["execute_action"])
    state = {}
    outcomes = [
        launcher.execute_action(pack, action, scientific_state=state)
        for action in pack["campaign"]["actions"]
    ]
    assert all(outcome["channels"] for outcome in outcomes)
    channels = [channel for outcome in outcomes for channel in outcome["channels"]]
    overpotential = next(
        channel for channel in channels if channel["name"] == "squidstat.overpotential_v"
    )
    assert overpotential["value"] is not None
    assert overpotential["origin"] == "source_model"

    replay_state = {}
    for action, outcome in zip(pack["campaign"]["actions"], outcomes, strict=True):
        snapshot = deepcopy(outcome["snapshot"])
        if action["action_id"] == "measure":
            target = next(
                channel
                for channel in snapshot["observation_channels"]
                if channel["name"] == "squidstat.overpotential_v"
            )
            target["value"] += 1.0
            with pytest.raises(CampaignValidationError, match="admitted instrument runtime"):
                _expected_snapshot_channels(snapshot, action, pack, replay_state)
            break
        _expected_snapshot_channels(snapshot, action, pack, replay_state)


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


def test_wait_beyond_the_old_cap_is_permitted():
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
    """The same compiled coverage isaac world, except ``current-envelope``'s own
    declared bound is narrowed below the campaign's real ``current_a`` (0.002827 A).

    Models a facility whose own safety interlock is tighter than the
    instrument's abstract admitted operating range -- a realistic scenario, not
    a hack: the registry provider's admitted ``validity_envelope`` (what
    composition and ``compile_facility``'s facility-binding cross-check both
    enforce) is untouched, so this reaches ``COMPILED``/compiles exactly like
    the unmodified coverage campaign. Only the facility's own runtime
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
    ``dynamical/bundle/facility.yaml``), the live path this exercises:
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
    """Non-Kit companion to the live-Kit coverage test above, and the direct isaac-path
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


def _compile_model_backed_isaac_world(destination: Path) -> Path:
    """Compile a campaign whose scientific work is done only by model-backed providers."""

    import test_runtime_pack

    from dynamical.compiler import compile_facility
    from dynamical.composition import compose_virtual_sdl
    from dynamical.schema import load_capability_registry

    composition = compose_virtual_sdl(
        test_runtime_pack._model_backed_requirement(),
        load_capability_registry("dynamical/bundle/registry.yaml"),
    )
    assert composition.status == "COMPILED", composition.reason_codes
    return compile_facility(
        "dynamical/bundle/facility.yaml",
        "isaac",
        destination,
        composition_result=composition,
    ).output_dir


@requires_isaac
def test_live_kit_run_records_the_deposition_provider_outputs(tmp_path):
    """The bath provider's nine scientific outputs must appear in the embodied trace.

    Deposition changes the sample's scientific state, so an Isaac trace that
    omits the provider's outputs cannot support the campaign's evidence chain.
    ``deposit-chemical-bath-capability`` declared no observation channels, and
    the Isaac runtime emits only declared facility channels, so the step ran
    and updated sample state while recording none of its results.
    """

    import json as json_module

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    trace = run_dir / "campaign_trace.ndjson"
    compiled_world = _compile_model_backed_isaac_world(tmp_path / "world")
    result = _run_isaac_launcher(compiled_world, trace)
    assert result.returncode == 0, result.stderr[-4000:]

    events = [json_module.loads(line) for line in trace.read_text().splitlines() if line.strip()]
    bath = [
        event
        for event in events
        if event["event_type"] == "observation"
        and event["observation"]["provider_id"] == "ac-bath-simulator"
    ]
    assert bath, "the bath provider returned no observation"
    values = {channel["name"]: channel["value"] for channel in bath[0]["observation"]["channels"]}
    expected = {
        f"ot2.deposited_fraction_{symbol}"
        for symbol in ("Cr", "Al", "Fe", "Co", "Mn", "Ni", "Cu", "Zn")
    }
    expected.add("ot2.bath_synthesis_time_s")
    assert set(values) == expected, f"deposition outputs not recorded: {expected - set(values)}"
    assert values["ot2.deposited_fraction_Cr"] == 0.25
    assert values["ot2.deposited_fraction_Fe"] == 0.25
    assert values["ot2.deposited_fraction_Co"] == 0.05
    assert values["ot2.deposited_fraction_Ni"] == 0.45
    assert values["ot2.bath_synthesis_time_s"] == 600.0

    # Runtime lineage is the executed digest the runtime records, not the
    # compiled transition's declared placeholder.
    digests = [
        event["provenance"]["sample_state_sha256"]
        for event in events
        if event["event_type"] == "observation"
        and event.get("provenance", {}).get("sample_state_sha256")
    ]
    assert len(set(digests)) > 1, "sample state never advanced across the campaign"
    assert digests[0] != digests[1], "deposition did not advance the recorded sample state"

    twin = [
        event
        for event in events
        if event["event_type"] == "observation"
        and event["observation"]["provider_id"] == "ac-oer-twin"
    ]
    assert twin, "the twin returned no observation"
    twin_values = {
        channel["name"]: channel["value"] for channel in twin[0]["observation"]["channels"]
    }
    assert abs(twin_values["squidstat.overpotential_v"] - 0.263047) < 1e-9, (
        "the twin did not read the deposited state this campaign produced"
    )
    assert twin[0]["provenance"]["sample_state_sha256"] == digests[-1]


@requires_isaac
def test_live_kit_deposition_trace_validates_and_replays(tmp_path):
    """The embodied deposition trace and its replay both validate."""

    from dynamical.campaign import validate_path as campaign_validate_path
    from dynamical.replay import replay_trace

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    trace = run_dir / "campaign_trace.ndjson"
    compiled_world = _compile_model_backed_isaac_world(tmp_path / "world")
    assert _run_isaac_launcher(compiled_world, trace).returncode == 0

    assert campaign_validate_path(trace)["valid"], "the embodied trace did not validate"
    replay = tmp_path / "replay.ndjson"
    replay_trace(trace, replay)
    assert campaign_validate_path(replay)["valid"], "the replayed trace did not validate"
