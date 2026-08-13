from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml
from _fixtures import REFERENCE_REQUIREMENT, write_reference_requirement
from jsonschema import Draft202012Validator

from dynamical.campaign import CampaignValidationError, read_trace
from dynamical.compiler import compile_facility
from dynamical.composition import CompositionResult, compose_files
from dynamical.replay import _expected_snapshot_channels, replay_trace
from dynamical.schema import canonical_sha256

REPOSITORY = Path(__file__).resolve().parents[1]
REGISTRY = REPOSITORY / "registries" / "electrodeposition-capabilities.yaml"
MANIFEST = REPOSITORY / "manifests" / "ac-electrodeposition-cell.yaml"

# A marker channel real for both compiled actions below (any declared facility
# channel works: validate_action/_validate_channel only check that the name is
# in the compiled schema's declared vocabulary and that provider_id/evidence_class
# match the producing action -- they do not tie a channel name to one action kind).
MARKER_CHANNEL = "arduino.conditioning_duration_s"


def _json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _exec_pack_module(name: str, path: Path) -> ModuleType:
    """Load a pack-copied module without writing bytecode into the pack."""

    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def _load_runtime_contract(output: Path) -> ModuleType:
    return _exec_pack_module(
        f"_dynamical_runtime_{output.name}", output / "dynamical_runtime_contract.py"
    )


def _load_isaac_launcher(output: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(output))
    return _exec_pack_module(f"_dynamical_isaac_{output.name}", output / "run_isaac_sim.py")


@pytest.fixture(scope="module")
def backend_packs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Compose and compile the shared electrodeposition requirement for isaac.

    ``isaac`` is the sole embodied target; this still returns a dict keyed by
    target to keep the test bodies below uniform.
    """

    root = tmp_path_factory.mktemp("backend-packs")
    requirement = write_reference_requirement(root / "requirement.yaml")
    composition = compose_files(requirement, REGISTRY, MANIFEST)
    assert composition.status == "COMPILED", composition.reason_codes
    output = root / "isaac"
    compile_facility(MANIFEST, "isaac", output, composition_result=composition)
    return {"isaac": output}


@pytest.fixture(scope="module")
def composed_backend_packs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("composed-backend-packs")
    requirement = write_reference_requirement(root / "requirement.yaml")
    composition = compose_files(requirement, REGISTRY, MANIFEST)
    assert composition.status == "COMPILED"
    output = root / "isaac"
    compile_facility(MANIFEST, "isaac", output, composition_result=composition)
    return {"isaac": output}


def _canonical_writer(
    runtime: ModuleType,
    pack: dict[str, Any],
    evidence_dir: Path,
    *,
    run_id: str,
    output_path: Path | None = None,
) -> Any:
    writer = runtime.TraceWriter(
        pack,
        run_id=run_id,
        seed=7,
        backend_revision="static-test:not-embodied",
        provenance={"embodied_backend": False, "embodied_evidence_bound": False},
        output_path=output_path,
    )
    writer.add("campaign_start", 0.0)
    logical_time = 0.0
    for index, action in enumerate(pack["campaign"]["actions"]):
        # Values inside both of "condition"'s declared pre_action envelopes (0-1800 s,
        # 0-100 %), so the happy-path writer below produces genuinely passed constraints
        # rather than "unavailable" ones -- campaign.py's validate_events (exercised via
        # replay_trace) requires a failed constraint to carry a failed campaign status,
        # which an "unavailable" pre_action measurement paired with a "passed" campaign_end
        # would violate. "transfer" has no applicable pre_action constraints, so these
        # values are simply unused for it.
        action_constraints = runtime.constraint_evidence(
            action,
            pack,
            phase="pre_action",
            channels=[
                {"name": "arduino.conditioning_duration_s", "value": 60.0},
                {"name": "arduino.conditioning_setpoint_percent", "value": 80.0},
            ],
        )
        writer.add(
            "action",
            logical_time,
            action=copy.deepcopy(action),
            constraints=action_constraints,
        )
        logical_time += 1.0
        evidence = runtime.write_snapshot(
            evidence_dir / f"observation-{index:03d}.json",
            {"action_id": action["action_id"], MARKER_CHANNEL: 60.0},
            provider_id=action["provider_id"],
            evidence_class=action["evidence_class"],
        )
        post_constraints = runtime.constraint_evidence(
            action,
            pack,
            phase="post_action",
            channels=[{"name": MARKER_CHANNEL, "value": 60.0}],
        )
        writer.add(
            "observation",
            logical_time,
            observation={
                "frame_id": f"static-frame-{index:03d}",
                "logical_time_s": logical_time,
                "provider_id": action["provider_id"],
                "evidence_class": action["evidence_class"],
                "channels": [
                    {
                        "name": MARKER_CHANNEL,
                        "value": 60.0,
                        "unit": "s",
                        "quality": "valid",
                        "origin": "backend_state",
                        "provider_id": action["provider_id"],
                        "evidence_class": action["evidence_class"],
                        "uncertainty": {
                            "value": 0.0,
                            "kind": "declared",
                            "origin": "test fixture",
                        },
                    }
                ],
                "evidence_ids": [evidence["evidence_id"]],
            },
            evidence=[evidence],
            constraints=post_constraints,
        )
    writer.add(
        "campaign_end",
        logical_time,
        provenance={"execution_status": "passed"},
    )
    return writer


def test_trace_writer_streams_each_event_before_final_validation(
    backend_packs: dict[str, Path],
    tmp_path: Path,
) -> None:
    output = backend_packs["isaac"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    trace = tmp_path / "campaign_trace.ndjson"
    writer = _canonical_writer(
        runtime,
        pack,
        tmp_path / "evidence",
        run_id="streamed-trace",
        output_path=trace,
    )

    assert len(trace.read_text(encoding="utf-8").splitlines()) == len(writer.events)
    assert writer.write(trace) == runtime.file_sha256(trace)


def _resequence(events: list[dict[str, Any]]) -> None:
    for sequence, event in enumerate(events):
        event["sequence"] = sequence


def test_backend_runtime_pack_verifies_all_compiler_artifacts(
    backend_packs: dict[str, Path],
) -> None:
    output = backend_packs["isaac"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)

    assert pack["manifest"]["core_ir_sha256"] == pack["backend"]["core_ir_sha256"]
    assert pack["campaign"]["execution_status"] == "requires_external_runtime_gate"
    assert [action["kind"] for action in pack["campaign"]["actions"]] == ["transfer", "condition"]
    assert pack["action_schema"]["x-dynamical-declared-capability-action-types"] == [
        "aliquot",
        "clean",
        "condition",
        "deposit",
        "dispense",
        "electrodeposit",
        "load",
        "measure",
        "transfer",
    ]
    assert MARKER_CHANNEL in pack["observation_schema"]["x-dynamical-declared-channel-ids"]


def test_standalone_runtime_rejects_artifact_path_escape(tmp_path: Path) -> None:
    output = compile_facility(MANIFEST, "isaac", tmp_path / "isaac").output_dir
    runtime = _load_runtime_contract(output)
    manifest_path = output / "compile_manifest.json"
    manifest = _json(manifest_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    manifest["artifacts"][0] = {
        "path": "../outside.txt",
        "sha256": runtime.file_sha256(outside),
    }
    manifest["world_sha256"] = runtime.stable_hash(
        {item["path"]: item["sha256"] for item in manifest["artifacts"]}
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(runtime.RuntimeContractError, match="unsafe compiled artifact path"):
        runtime.verify_compiled_pack(output)


def test_standalone_runtime_rejects_required_json_symlink_before_read(tmp_path: Path) -> None:
    output = compile_facility(MANIFEST, "isaac", tmp_path / "isaac").output_dir
    runtime = _load_runtime_contract(output)
    outside = tmp_path / "external-invalid.json"
    outside.write_text("not json\n", encoding="utf-8")
    backend = output / "backend_config.json"
    backend.unlink()
    backend.symlink_to(outside)

    with pytest.raises(runtime.RuntimeContractError, match="symbolic link"):
        runtime.verify_compiled_pack(output)


def test_runtime_campaign_uses_exact_facility_parameter_names(
    backend_packs: dict[str, Path],
) -> None:
    campaign = _json(backend_packs["isaac"] / "runtime_campaign.json")
    actions = campaign["actions"]
    transfer, condition = actions

    # transfer-sample is a transport capability: runtime_campaign() calls the
    # same registered instrument model campaign.py's composed path calls live
    # (transfer.py's transfer_sample), so its compiled action carries a real
    # embedded sample_transition alongside its own requested parameters --
    # not just the bare requested-parameter dict a non-transport action has.
    transition = transfer["parameters"].pop("sample_transition")
    assert transfer["parameters"] == {
        "sample_id": "sample-electrodeposition-01",
        "to_station": "arduino-conditioning",
    }
    assert transition["kind"] == "transfer"
    assert transition["sample_id"] == "sample-electrodeposition-01"
    assert transition["to_station"] == "arduino-conditioning"
    assert transition["arrival_confirmed"] is True

    assert condition["parameters"] == {"duration_s": 60.0, "setpoint_percent": 80.0}


def test_standalone_runtime_writes_complete_canonical_dynamical_trace(
    composed_backend_packs: dict[str, Path],
    tmp_path: Path,
) -> None:
    output = composed_backend_packs["isaac"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    writer = _canonical_writer(
        runtime,
        pack,
        tmp_path,
        run_id="static-contract-isaac",
    )

    trace_path = tmp_path / "trace.ndjson"
    trace_hash = writer.write(trace_path)
    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    runtime.validate_trace(events, pack)
    parsed_events = read_trace(trace_path)
    trace_schema = _json(output / "campaign_trace.schema.json")
    validator = Draft202012Validator(trace_schema)
    for event in events:
        validator.validate(event)
    replay_result = replay_trace(
        trace_path,
        tmp_path / "replay-isaac.ndjson",
    )

    assert len(trace_hash) == 64
    assert len(parsed_events) == len(events)
    # T14 closed the gap T16 documented here: runtime_campaign() now threads
    # action.station_id (binding["selected_facility_id"]), mirroring campaign.py's
    # in-process run_composed_campaign, so samples.check_invariants' lineage check on
    # replay has what it needs for a standalone/embodied compiled pack too.
    assert replay_result["valid"] is True, replay_result["validation_reasons"]
    assert replay_result["validation_reasons"] == []
    assert [event["event_type"] for event in events] == [
        "campaign_start",
        *[item for _ in pack["campaign"]["actions"] for item in ("action", "observation")],
        "campaign_end",
    ]
    assert all(event["schema_version"] == "dynamical.campaign.v0.1" for event in events)
    assert events[2]["evidence"][0]["sha256"] == runtime.file_sha256(
        tmp_path / "observation-000.json"
    )


@pytest.mark.parametrize(
    ("defect", "message"),
    [
        ("action_kind", "not declared by the compiled schema"),
        ("parameter", "undeclared parameters"),
        ("actor", "does not match the selected capability provider"),
        ("channel", "not declared by the compiled schema"),
    ],
)
def test_trace_validator_rejects_values_outside_compiled_pack_contract(
    backend_packs: dict[str, Path],
    tmp_path: Path,
    defect: str,
    message: str,
) -> None:
    output = backend_packs["isaac"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    writer = _canonical_writer(
        runtime,
        pack,
        tmp_path,
        run_id=f"rejection-isaac-{defect}",
    )
    events = copy.deepcopy(writer.events)
    if defect == "action_kind":
        events[1]["action"]["kind"] = "drop"
    elif defect == "parameter":
        events[1]["action"]["parameters"]["bogus_extra_field"] = 2.0
    elif defect == "actor":
        events[1]["action"]["actor_id"] = "franka-agent"
    else:
        events[2]["observation"]["channels"][0]["name"] = "runtime/action_success"

    with pytest.raises(runtime.RuntimeContractError, match=message):
        runtime.validate_trace(events, pack)


def test_trace_validator_and_writer_reject_shortened_campaign(
    backend_packs: dict[str, Path],
    tmp_path: Path,
) -> None:
    output = backend_packs["isaac"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    writer = _canonical_writer(
        runtime,
        pack,
        tmp_path,
        run_id="shortened-campaign",
    )
    events = copy.deepcopy(writer.events)
    del events[-3:-1]
    _resequence(events)

    with pytest.raises(runtime.RuntimeContractError, match="complete compiled campaign"):
        runtime.validate_trace(events, pack)

    writer.events = events
    with pytest.raises(runtime.RuntimeContractError, match="complete compiled campaign"):
        writer.write(tmp_path / "shortened.ndjson")
    assert not (tmp_path / "shortened.ndjson").exists()


def test_trace_validator_rejects_longer_campaign(
    backend_packs: dict[str, Path],
    tmp_path: Path,
) -> None:
    output = backend_packs["isaac"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    writer = _canonical_writer(runtime, pack, tmp_path, run_id="longer-campaign")
    events = copy.deepcopy(writer.events)
    events[-1:-1] = copy.deepcopy(events[-3:-1])
    _resequence(events)

    with pytest.raises(runtime.RuntimeContractError, match="complete compiled campaign"):
        runtime.validate_trace(events, pack)


@pytest.mark.parametrize(
    "field",
    ["campaign_id", "ir_hash", "world_hash", "campaign_hash"],
)
def test_trace_validator_rejects_identity_not_bound_to_pack(
    backend_packs: dict[str, Path],
    tmp_path: Path,
    field: str,
) -> None:
    output = backend_packs["isaac"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    writer = _canonical_writer(runtime, pack, tmp_path, run_id=f"changed-{field}")
    events = copy.deepcopy(writer.events)
    replacement = "different-campaign" if field == "campaign_id" else "0" * 64
    for event in events:
        event[field] = replacement

    with pytest.raises(runtime.RuntimeContractError, match=f"trace {field} does not bind"):
        runtime.validate_trace(events, pack)


def test_trace_validator_rejects_unknown_event_type(
    backend_packs: dict[str, Path],
    tmp_path: Path,
) -> None:
    output = backend_packs["isaac"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    writer = _canonical_writer(runtime, pack, tmp_path, run_id="unknown-event")
    events = copy.deepcopy(writer.events)
    events[2]["event_type"] = "telemetry"

    with pytest.raises(runtime.RuntimeContractError, match="unknown event types"):
        runtime.validate_trace(events, pack)


def test_trace_validator_rejects_reordered_actions(
    backend_packs: dict[str, Path],
    tmp_path: Path,
) -> None:
    output = backend_packs["isaac"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    writer = _canonical_writer(runtime, pack, tmp_path, run_id="reordered-actions")
    events = copy.deepcopy(writer.events)
    events[1]["action"], events[3]["action"] = events[3]["action"], events[1]["action"]

    with pytest.raises(runtime.RuntimeContractError, match="differs from the compiled campaign"):
        runtime.validate_trace(events, pack)


def test_composed_runtime_uses_selected_provider_graph(tmp_path: Path) -> None:
    requirement = write_reference_requirement(tmp_path / "requirement.yaml")
    composition = compose_files(requirement, REGISTRY, MANIFEST)
    assert composition.status == "COMPILED"
    output = tmp_path / "composed-isaac"
    compile_facility(MANIFEST, "isaac", output, composition_result=composition)
    campaign = _json(output / "runtime_campaign.json")
    config = _json(output / "backend_config.json")
    assert (
        config["module_status"]["representative_full_facility"] == "ready_for_external_runtime_gate"
    )
    assert config["full_facility_runtime_blocker"] is None
    assert len(campaign["actions"]) == 2
    assert {action["kind"] for action in campaign["actions"]} == {"transfer", "condition"}
    assert {action["provider_id"] for action in campaign["actions"]} == {
        "ac-transfer-simulator",
        "ac-arduino-simulator",
    }
    assert {action["evidence_class"] for action in campaign["actions"]} == {"simulator"}
    assert {binding.operation_id for binding in composition.virtual_sdl.operation_bindings} == {
        "transfer-sample",
        "condition-ultrasonic",
    }
    condition_action = next(
        action for action in campaign["actions"] if action["kind"] == "condition"
    )
    assert condition_action["parameters"] == {"duration_s": 60.0, "setpoint_percent": 80.0}
    assert condition_action["provider_id"] == "ac-arduino-simulator"
    # provider_bindings is keyed by action_id (not action kind): look each action's
    # binding up by its own action_id.
    bindings = campaign["provider_bindings"]
    assert set(bindings) == {action["action_id"] for action in campaign["actions"]}
    for action in campaign["actions"]:
        assert bindings[action["action_id"]]["provider_id"] == action["provider_id"]
    runtime = _load_runtime_contract(output)
    verified = runtime.verify_compiled_pack(output)
    runtime.validate_action(campaign["actions"][0], verified)
    manifest = _json(output / "compile_manifest.json")
    assert "composition_result.json" in {item["path"] for item in manifest["artifacts"]}


def test_runtime_rejects_non_string_action_id(composed_backend_packs: dict[str, Path]) -> None:
    output = composed_backend_packs["isaac"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    action = copy.deepcopy(pack["campaign"]["actions"][0])
    action["action_id"] = []

    with pytest.raises(runtime.RuntimeContractError, match="action_id is absent or not a string"):
        runtime.validate_action(action, pack)


def test_composed_runtime_uses_selected_conditioning_parameters(tmp_path: Path) -> None:
    request = copy.deepcopy(REFERENCE_REQUIREMENT)
    condition_step = next(step for step in request["steps"] if step["step_id"] == "condition")
    parameter_values = {"duration_s": 42.0, "setpoint_percent": 65.0}
    for parameter in condition_step["parameters"]:
        parameter["value"] = parameter_values[parameter["name"]]
    requirement = tmp_path / "requirement.yaml"
    requirement.write_text(yaml.safe_dump(request, sort_keys=False), encoding="utf-8")

    composition = compose_files(requirement, REGISTRY, MANIFEST)
    assert composition.status == "COMPILED"
    output = compile_facility(
        MANIFEST,
        "isaac",
        tmp_path / "compiled",
        composition_result=composition,
    ).output_dir
    campaign = _json(output / "runtime_campaign.json")

    condition_action = next(
        action for action in campaign["actions"] if action["kind"] == "condition"
    )
    assert condition_action["parameters"] == {"duration_s": 42.0, "setpoint_percent": 65.0}


def test_isaac_emits_the_runtime_receipt_contract(
    backend_packs: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_fields = {
        "schema_version",
        "backend",
        "host_id",
        "backend_revision",
        "core_ir_sha256",
        "compiled_world_sha256",
        "execution_status",
        "trace_sha256",
        "receipt_complete",
        "intended_exit_code",
        "simulation_app_shutdown_requested",
        "runtime_error",
        "embodied_evidence_bound",
        "manual_gates",
        "artifacts",
    }
    output = backend_packs["isaac"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    evidence = tmp_path / "isaac"
    evidence.mkdir()
    trace = evidence / "campaign_trace.ndjson"
    trace.write_text("{}\n", encoding="utf-8")
    trace_hash = hashlib.sha256(trace.read_bytes()).hexdigest()
    launcher = _load_isaac_launcher(output, monkeypatch)
    launcher._write_receipt(evidence, pack, "passed", trace_hash)
    receipt = _json(evidence / "runtime_evidence.json")

    assert set(receipt) == expected_fields
    assert receipt["backend"] == "isaac_sim"
    assert receipt["receipt_complete"] is True
    assert receipt["intended_exit_code"] == 0
    assert receipt["runtime_error"] is None


def test_direct_embodied_replay_rejects_path_escape_and_forged_campaign(
    composed_backend_packs: dict[str, Path],
    tmp_path: Path,
) -> None:
    output = composed_backend_packs["isaac"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    writer = _canonical_writer(runtime, pack, evidence, run_id="direct-replay")
    trace = evidence / "campaign_trace.ndjson"
    writer.write(trace)

    def write_receipt(artifacts: list[dict[str, str]]) -> Path:
        receipt = evidence / "runtime_evidence.json"
        receipt.write_text(
            json.dumps(
                {
                    "schema_version": "dynamical.runtime-evidence.v1",
                    "backend": "isaac_sim",
                    "host_id": "test-host",
                    "backend_revision": "test-revision",
                    "core_ir_sha256": pack["manifest"]["core_ir_sha256"],
                    "compiled_world_sha256": pack["manifest"]["world_sha256"],
                    "execution_status": "passed",
                    "trace_sha256": hashlib.sha256(trace.read_bytes()).hexdigest(),
                    "receipt_complete": True,
                    "intended_exit_code": 0,
                    "simulation_app_shutdown_requested": True,
                    "runtime_error": None,
                    "embodied_evidence_bound": False,
                    "manual_gates": ["independent review required"],
                    "artifacts": artifacts,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return receipt

    trace_hash = hashlib.sha256(trace.read_bytes()).hexdigest()
    replay = evidence / "replay.ndjson"
    # Render evidence is an optional declared artifact class (T14): this portable
    # adapter's Isaac launcher renders nothing, so a receipt binding zero videos is no
    # longer itself a rejection -- reaching the embodied-provenance check confirms that,
    # rather than stopping earlier on a "runtime video" error the old hard requirement
    # would have raised here.
    receipt = write_receipt([{"path": trace.name, "sha256": trace_hash}])
    with pytest.raises(CampaignValidationError, match="embodied compiled-adapter provenance"):
        replay_trace(
            trace,
            replay,
            compiled_world=output,
            runtime_receipt=receipt,
        )
    # A *present* video artifact is still validated: empty is still rejected.
    empty_video = evidence / "run.mp4"
    empty_video.write_bytes(b"")
    empty_video_hash = hashlib.sha256(empty_video.read_bytes()).hexdigest()
    receipt = write_receipt(
        [
            {"path": trace.name, "sha256": trace_hash},
            {"path": empty_video.name, "sha256": empty_video_hash},
        ]
    )
    with pytest.raises(CampaignValidationError, match="video is empty"):
        replay_trace(
            trace,
            replay,
            compiled_world=output,
            runtime_receipt=receipt,
        )
    outside = tmp_path / "outside.json"
    outside.write_text("{}\n", encoding="utf-8")
    outside_hash = hashlib.sha256(outside.read_bytes()).hexdigest()
    write_receipt(
        [
            {"path": trace.name, "sha256": trace_hash},
            {"path": "../outside.json", "sha256": outside_hash},
        ]
    )
    with pytest.raises(CampaignValidationError, match="artifact path is unsafe"):
        replay_trace(
            trace,
            replay,
            compiled_world=output,
            runtime_receipt=receipt,
        )
    forged = copy.deepcopy(writer.events)
    condition = next(
        event
        for event in forged
        if isinstance(event.get("action"), dict) and event["action"].get("kind") == "condition"
    )
    condition["action"]["parameters"]["duration_s"] = 999.0
    trace.write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in forged),
        encoding="utf-8",
    )
    forged_hash = hashlib.sha256(trace.read_bytes()).hexdigest()
    write_receipt([{"path": trace.name, "sha256": forged_hash}])
    with pytest.raises(CampaignValidationError, match="differs from the compiled campaign"):
        replay_trace(
            trace,
            replay,
            compiled_world=output,
            runtime_receipt=receipt,
        )


def test_embodied_binding_rejects_missing_bound_backend_channel(
    composed_backend_packs: dict[str, Path],
) -> None:
    output = composed_backend_packs["isaac"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    action = next(item for item in pack["campaign"]["actions"] if item["kind"] == "condition")

    with pytest.raises(CampaignValidationError, match="no observation channels"):
        _expected_snapshot_channels({"observation": {}}, action, pack)


def test_embodied_snapshot_commanded_parameter_must_match_the_compiled_action(
    composed_backend_packs: dict[str, Path],
) -> None:
    """A snapshot whose commanded parameter differs from the parameter pinned
    to the compiled action is rejected -- otherwise a fabricated snapshot could
    reconstruct observation channels the campaign never commanded."""

    output = composed_backend_packs["isaac"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    action = next(item for item in pack["campaign"]["actions"] if item["kind"] == "condition")
    pinned = action["parameters"]["duration_s"]

    snapshot = {
        "commanded_parameters": {"parameters": {"duration_s": pinned + 1.0}},
        "observation": {},
    }
    with pytest.raises(CampaignValidationError, match="differs from the compiled campaign"):
        _expected_snapshot_channels(snapshot, action, pack)


def _unavailable_evidence(pack: dict[str, Any], constraint_id: str) -> dict[str, Any]:
    """Build one "unavailable" constraint-evidence record straight from its declaration.

    Used to fabricate an evidence record for a constraint genuinely unrelated to the
    action under test (e.g. "current-envelope", which belongs to "electrodeposit", not
    "condition"), so it is well-formed on its own but "extra" relative to that action's
    applicable set -- rather than reusing one of the action's own two constraints, which
    would just be a duplicate.
    """

    specification = next(
        item for item in pack["facility"]["constraints"] if item.get("id") == constraint_id
    )
    return {
        "constraint_id": constraint_id,
        "phase": specification["phase"],
        "passed": False,
        "outcome": "unavailable",
        "measured_value": None,
        "margin": None,
        "limit": {
            "operator": specification["operator"],
            "bound": specification["bound"],
            "unit": specification["unit"],
            "enforcement": specification["enforcement"],
        },
        "verifier": specification["verifier_binding_id"],
    }


def test_runtime_requires_bound_constraints_and_rejects_self_admission(
    backend_packs: dict[str, Path],
    tmp_path: Path,
) -> None:
    output = backend_packs["isaac"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    writer = _canonical_writer(runtime, pack, tmp_path, run_id="constraint-required")

    # events[3] is the "condition" action's event (transfer at events[1] has no
    # declared constraints), the only action in this two-step campaign that carries any.
    missing = copy.deepcopy(writer.events)
    missing[3]["constraints"] = []
    with pytest.raises(runtime.RuntimeContractError, match="constraint coverage differs"):
        runtime.validate_trace(missing, pack)

    extra = copy.deepcopy(writer.events)
    extra[3]["constraints"].append(_unavailable_evidence(pack, "current-envelope"))
    with pytest.raises(runtime.RuntimeContractError, match="extra=.*current-envelope"):
        runtime.validate_trace(extra, pack)

    self_admitted = copy.deepcopy(writer.events)
    self_admitted[0]["provenance"]["embodied_evidence_bound"] = True
    with pytest.raises(runtime.RuntimeContractError, match="cannot self-bind embodied evidence"):
        runtime.validate_trace(self_admitted, pack)


@pytest.mark.parametrize(
    ("field", "invalid_value", "message"),
    [
        ("limit", 0.0, "limit differs"),
        ("limit", {"operator": "gt"}, "limit differs"),
        ("phase", "runtime", "phase differs"),
        ("verifier", "self-approved", "verifier differs"),
    ],
)
def test_runtime_rejects_noncanonical_constraint_records(
    backend_packs: dict[str, Path],
    tmp_path: Path,
    field: str,
    invalid_value: object,
    message: str,
) -> None:
    output = backend_packs["isaac"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    writer = _canonical_writer(runtime, pack, tmp_path, run_id=f"invalid-{field}")
    events = copy.deepcopy(writer.events)
    events[3]["constraints"][0][field] = invalid_value

    with pytest.raises(runtime.RuntimeContractError, match=message):
        runtime.validate_trace(events, pack)


def test_runtime_recomputes_constraint_truth_from_measured_value(
    backend_packs: dict[str, Path],
    tmp_path: Path,
) -> None:
    output = backend_packs["isaac"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    writer = _canonical_writer(runtime, pack, tmp_path, run_id="forged-constraint")
    events = copy.deepcopy(writer.events)
    events[3]["constraints"][0]["measured_value"] = 5000.0

    with pytest.raises(runtime.RuntimeContractError, match="differs from its measured value"):
        runtime.validate_trace(events, pack)


def test_runtime_binds_snapshot_id_to_evidence_hash(
    backend_packs: dict[str, Path],
    tmp_path: Path,
) -> None:
    output = backend_packs["isaac"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    writer = _canonical_writer(runtime, pack, tmp_path, run_id="forged-evidence")
    events = copy.deepcopy(writer.events)
    events[2]["evidence"][0]["sha256"] = "0" * 64

    with pytest.raises(runtime.RuntimeContractError, match="content hash"):
        runtime.validate_trace(events, pack)


def test_runtime_forces_failure_for_a_reject_enforcement_pre_action_violation(
    backend_packs: dict[str, Path],
    tmp_path: Path,
) -> None:
    """A pre-action constraint failure -- reject or terminate -- must force campaign failure.

    Repair-2 defect 2: a pre-action constraint's whole point is to prevent its
    action from running at all, so noticing the violation only after the fact
    (or, worse, not requiring the campaign to admit failure at all) defeats it.
    ``conditioning-duration-envelope`` is declared "reject", not "terminate", so
    this specifically proves reject-enforcement is no longer exempt at the
    pre-action phase: a trace that self-reports "passed" despite this violation
    must fail validation. (Post-action/observation-phase reject constraints keep
    their original leniency -- see ``_handle_constraints`` in isaac_runtime.py --
    since an observation, by definition, is made after its action already ran
    and cannot retroactively prevent it.)
    """
    output = backend_packs["isaac"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    writer = _canonical_writer(runtime, pack, tmp_path, run_id="reject-enforcement")
    events = copy.deepcopy(writer.events)
    forged = events[3]["constraints"][0]
    assert forged["limit"]["enforcement"] == "reject"
    forged["measured_value"] = 5000.0
    forged["passed"] = False
    forged["outcome"] = "violated"

    with pytest.raises(runtime.RuntimeContractError, match="failed campaign status"):
        runtime.validate_trace(events, pack)


def test_runtime_rejects_misplaced_constraint_record(
    backend_packs: dict[str, Path],
    tmp_path: Path,
) -> None:
    output = backend_packs["isaac"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    writer = _canonical_writer(runtime, pack, tmp_path, run_id="misplaced-constraint")
    events = copy.deepcopy(writer.events)
    events[0]["constraints"] = copy.deepcopy(events[3]["constraints"])
    assert events[0]["constraints"]

    with pytest.raises(runtime.RuntimeContractError, match="campaign_start cannot carry"):
        runtime.validate_trace(events, pack)


def _rehash_composition(value: dict[str, Any]) -> CompositionResult:
    value.pop("sources", None)
    parsed = CompositionResult.model_validate(value)
    assert parsed.virtual_sdl is not None
    virtual_payload = parsed.virtual_sdl.model_dump(mode="json")
    virtual_payload.pop("virtual_sdl_sha256")
    virtual_hash = canonical_sha256(virtual_payload)
    value["virtual_sdl"]["virtual_sdl_sha256"] = virtual_hash
    value["composition_sha256"] = virtual_hash
    parsed = CompositionResult.model_validate(value)
    result_payload = parsed.model_dump(mode="json", exclude_none=True)
    result_payload.pop("resolution_sha256")
    value["resolution_sha256"] = canonical_sha256(result_payload)
    return CompositionResult.model_validate(value)


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("provider_id", "unrelated-provider", "not admitted"),
        ("selected_facility_id", "unrelated-facility", "facility is not admitted"),
        ("endpoint_id", "franka-agent", "endpoint differs"),
    ],
)
def test_compiler_rejects_composition_not_bound_to_facility_admission(
    tmp_path: Path,
    field: str,
    replacement: str,
    message: str,
) -> None:
    requirement = write_reference_requirement(tmp_path / "requirement.yaml")
    composition = compose_files(requirement, REGISTRY, MANIFEST)
    value = composition.model_dump(mode="json", exclude_none=True)
    value["virtual_sdl"]["operation_bindings"][1][field] = replacement
    forged = _rehash_composition(value)

    with pytest.raises(ValueError, match=message):
        compile_facility(
            MANIFEST,
            "isaac",
            tmp_path / field,
            composition_result=forged,
        )


def test_compiler_rejects_forged_adapter_and_safety_bindings(tmp_path: Path) -> None:
    requirement = write_reference_requirement(tmp_path / "requirement.yaml")
    composition = compose_files(requirement, REGISTRY, MANIFEST)
    value = composition.model_dump(mode="json", exclude_none=True)
    selected = value["virtual_sdl"]["operation_bindings"][1]
    selected["adapter_links"][0]["adapter_id"] = "unrelated-adapter"
    selected["policy"]["safety_limit_ids"] = ["nonexistent-safety-rule"]
    forged = _rehash_composition(value)

    with pytest.raises(ValueError, match="adapter links differ"):
        compile_facility(
            MANIFEST,
            "isaac",
            tmp_path / "forged-adapter",
            composition_result=forged,
        )
