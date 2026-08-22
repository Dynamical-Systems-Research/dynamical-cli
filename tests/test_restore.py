from __future__ import annotations

import contextlib
import hashlib
import io
import json
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml
from test_campaign_contract import _transfer_contract

import dynamical.restore as restore_module
from dynamical.campaign import (
    CampaignValidationError,
    canonical_json,
    load_compiled_campaign_contract,
    run_composed_campaign,
    stable_hash,
    validate_path,
)
from dynamical.cli import DEFAULT_FACILITY, DEFAULT_REGISTRY, main
from dynamical.compiler import compile_facility
from dynamical.composition import compose_files
from dynamical.replay import replay_trace
from dynamical.restore import _prepare_restore
from dynamical.schema import load_facility_manifest

REPOSITORY = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class RestoreLab:
    parent_trace: Path
    parent_world: Path
    control_world: Path
    control_isaac_world: Path
    counter_world: Path
    control_requirement: Path
    at_event_id: str


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _invoke(args: list[str]) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()


def _write_trace(path: Path, events: list[dict[str, object]]) -> None:
    path.write_text("".join(canonical_json(event) + "\n" for event in events), encoding="utf-8")


def _child_requirement(name: str, *, counterfactual: bool = False) -> dict[str, object]:
    if counterfactual:
        operation_id, output_port, evidence_class = (
            "load-electrochemical-cell",
            "instrument.cell_seated",
            "simulator",
        )
        step = {
            "step_id": "reload-cell",
            "operation_id": operation_id,
            "minimum_evidence_class": evidence_class,
            "parameters": [
                {"name": "cell_id", "value_type": "string", "unit": "1", "value": "cell-1"},
                {"name": "seated", "value_type": "boolean", "unit": "1", "value": False},
            ],
            "input_bindings": [
                {
                    "target_port_id": "sample.state",
                    "source_kind": "campaign_input",
                    "source_id": "sample.state",
                }
            ],
            "depends_on": [],
            "required_policy_tags": ["simulation-only"],
        }
    else:
        operation_id, output_port, evidence_class = (
            "measure-oer",
            "overpotential_v",
            "calibrated_twin",
        )
        step = {
            "step_id": "measure-oer-10ma",
            "operation_id": operation_id,
            "minimum_evidence_class": evidence_class,
            "parameters": [
                {
                    "name": "current_density_a_cm2",
                    "value_type": "number",
                    "unit": "A/cm^2",
                    "value": 0.010,
                }
            ],
            "input_bindings": [
                {
                    "target_port_id": "sample.state",
                    "source_kind": "campaign_input",
                    "source_id": "sample.state",
                }
            ],
            "depends_on": [],
            "required_policy_tags": ["calibrated-twin", "frozen-prediction-table"],
        }
    return {
        "document_type": "dynamical.campaign-requirement",
        "schema_version": "0.1.0",
        "requirement_id": name,
        "objective": {
            "id": name,
            "statement": "Run one bounded virtual continuation.",
            "decision": "Compare the virtual result.",
            "proof_requirements": [
                {
                    "id": f"{name}-proof",
                    "operation_id": operation_id,
                    "output_port_ids": [output_port],
                    "minimum_evidence_class": evidence_class,
                    "acceptance_rule": "Record the declared output.",
                    "independent_verification_required": True,
                }
            ],
        },
        "inputs": [
            {
                "id": "sample.state",
                "state_type": "sample_state",
                "unit": "1",
                "value": "fastcat-reference-01",
                "facility_id": "squidstat-echem",
            }
        ],
        "steps": [step],
        "max_cost_usd": 0.0,
        "max_duration_s": 300.0,
    }


def _compile_requirement(root: Path, name: str, value: dict[str, object]) -> tuple[Path, Path]:
    requirement = root / f"{name}.yaml"
    composition = root / f"{name}.json"
    world = root / f"{name}-world"
    requirement.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    assert _invoke(["compose", str(requirement), "-o", str(composition)])[0] == 0
    assert _invoke(["compile", str(composition), "-o", str(world)])[0] == 0
    return requirement, world


@pytest.fixture(scope="module")
def restore_lab(tmp_path_factory: pytest.TempPathFactory) -> RestoreLab:
    root = tmp_path_factory.mktemp("restore-lab")
    parent_composition = root / "parent.json"
    parent_world = root / "parent-world"
    parent_requirement = REPOSITORY / "examples" / "fastcat-oer" / "requirement.yaml"
    assert _invoke(["compose", str(parent_requirement), "-o", str(parent_composition)])[0] == 0
    assert _invoke(["compile", str(parent_composition), "-o", str(parent_world)])[0] == 0

    control_requirement, control_world = _compile_requirement(
        root, "control", _child_requirement("control")
    )
    control_isaac_world = root / "control-isaac-world"
    assert (
        _invoke(
            [
                "compile",
                str(root / "control.json"),
                "--target",
                "isaac",
                "-o",
                str(control_isaac_world),
            ]
        )[0]
        == 0
    )
    _, counter_world = _compile_requirement(
        root, "counter", _child_requirement("counter", counterfactual=True)
    )
    parent_trace = root / "parent.ndjson"
    assert _invoke(["run", str(parent_world), "-o", str(parent_trace)])[0] == 0
    parent_events = [json.loads(line) for line in parent_trace.read_text().splitlines()]
    at_event_id = next(
        event["event_id"]
        for event in parent_events
        if (event.get("observation") or {}).get("frame_id") == "frame-after-load-cell"
    )
    return RestoreLab(
        parent_trace,
        parent_world,
        control_world,
        control_isaac_world,
        counter_world,
        control_requirement,
        at_event_id,
    )


def _restore_args(
    lab: RestoreLab,
    child_world: Path,
    output: Path | None = None,
    *,
    source_trace: Path | None = None,
    source_world: Path | None = None,
    at_event_id: str | None = None,
) -> list[str]:
    args = [
        "run",
        str(child_world),
        "--restore-from",
        str(source_trace or lab.parent_trace),
        "--restore-world",
        str(source_world or lab.parent_world),
        "--restore-at-event",
        at_event_id or lab.at_event_id,
    ]
    return [*args, "-o", str(output)] if output is not None else args


def _observation(events: list[dict[str, object]], frame_id: str) -> dict[str, object]:
    return next(
        event for event in events if (event.get("observation") or {}).get("frame_id") == frame_id
    )


def test_non_restore_simulate_and_replay_bytes_are_stable(tmp_path: Path) -> None:
    simulated = tmp_path / "simulate.ndjson"
    replayed = tmp_path / "replay.ndjson"
    run_composed_campaign(_transfer_contract(), simulated, seed=5)
    replay_trace(simulated, replayed)
    assert (len(simulated.read_bytes()), _sha256(simulated)) == (
        23_456,
        "9233e23c8439ad675d914bf1018ad51de961e538d984833b7f12f199d44b7847",
    )
    assert (len(replayed.read_bytes()), _sha256(replayed)) == (
        24_278,
        "47005005a11a9473b4ac5826d6b0a59af063509797240fb87bf894adece64462",
    )


def test_restore_continuation_counterfactual_and_rejects_restored_source(
    restore_lab: RestoreLab, tmp_path: Path
) -> None:
    control_trace = tmp_path / "control.ndjson"
    code, stdout, _ = _invoke(_restore_args(restore_lab, restore_lab.control_world, control_trace))
    control_receipt = json.loads(stdout)
    assert code == 0 and control_receipt["reused"] is False
    assert control_receipt["source_evidence_classes"] == ["simulator"]
    assert validate_path(control_trace)["valid"] is True

    parent_events = [json.loads(line) for line in restore_lab.parent_trace.read_text().splitlines()]
    control_events = [json.loads(line) for line in control_trace.read_text().splitlines()]
    parent_measurement = _observation(parent_events, "frame-after-measure-oer-10ma")
    child_measurement = _observation(control_events, "frame-after-measure-oer-10ma")
    assert (
        child_measurement["observation"]["channels"]
        == parent_measurement["observation"]["channels"]
    )
    assert (
        child_measurement["provenance"]["sample_state_sha256"]
        == parent_measurement["provenance"]["sample_state_sha256"]
    )

    counter_trace = tmp_path / "counter.ndjson"
    code, stdout, _ = _invoke(_restore_args(restore_lab, restore_lab.counter_world, counter_trace))
    assert code == 0 and json.loads(stdout)["reused"] is False
    counter_events = [json.loads(line) for line in counter_trace.read_text().splitlines()]
    counter_observation = _observation(counter_events, "frame-after-reload-cell")
    seated_channel = next(
        channel
        for channel in counter_observation["observation"]["channels"]
        if channel["name"] == "instrument.cell_seated"
    )
    assert seated_channel["value"] is False
    assert (
        counter_observation["provenance"]["sample_state_sha256"]
        != child_measurement["provenance"]["sample_state_sha256"]
    )

    control_at = next(
        event["event_id"] for event in control_events if event["event_type"] == "observation"
    )
    chained_trace = tmp_path / "chained.ndjson"
    chained_args = _restore_args(
        restore_lab,
        restore_lab.counter_world,
        chained_trace,
        source_trace=control_trace,
        source_world=restore_lab.control_world,
        at_event_id=control_at,
    )
    code, _, stderr = _invoke(chained_args)
    assert code == 2 and "restored traces cannot be restore sources" in stderr
    assert not chained_trace.exists()


def test_dry_run_never_writes_and_preserves_a_matching_output(
    restore_lab: RestoreLab, tmp_path: Path
) -> None:
    output = tmp_path / "child.ndjson"
    dry_args = [*_restore_args(restore_lab, restore_lab.control_world, output), "--dry-run"]
    code, stdout, _ = _invoke(dry_args)
    receipt = json.loads(stdout)
    assert code == 0 and receipt["status"] == "ready"
    assert receipt["execution_status"] == "not_executed"
    assert not output.exists()

    assert _invoke(_restore_args(restore_lab, restore_lab.control_world, output))[0] == 0
    before = output.read_bytes()
    code, stdout, _ = _invoke(dry_args)
    assert code == 0 and json.loads(stdout)["expected_run_id"].startswith("simulate-")
    assert output.read_bytes() == before


def test_safe_reuse_and_full_child_identity_check(restore_lab: RestoreLab, tmp_path: Path) -> None:
    output = tmp_path / "child.ndjson"
    args = _restore_args(restore_lab, restore_lab.control_world, output)
    code, stdout, _ = _invoke(args)
    assert code == 0 and json.loads(stdout)["reused"] is False
    before = output.read_bytes()
    code, stdout, _ = _invoke(args)
    assert code == 0 and json.loads(stdout)["reused"] is True
    assert output.read_bytes() == before

    modified_events = [json.loads(line) for line in before.splitlines()]
    modified_observation = next(
        event for event in modified_events if event["event_type"] == "observation"
    )
    modified_observation["observation"]["channels"][0]["value"] = 987654321
    _write_trace(output, modified_events)
    modified = output.read_bytes()
    assert validate_path(output)["valid"] is True
    code, _, stderr = _invoke(args)
    assert code == 2 and "conflicts with the expected run" in stderr
    assert output.read_bytes() == modified

    alternate = tmp_path / "alternate-target.ndjson"
    original_events = [json.loads(line) for line in before.splitlines()]
    alternate_contract = load_compiled_campaign_contract(restore_lab.control_isaac_world)
    for event in original_events:
        event["backend_revision"] = (
            f"compiled_target={alternate_contract.target};"
            f"adapter={alternate_contract.adapter_pack_sha256};"
            "composed_virtual_sdl:not_embodied"
        )
        event["ir_hash"] = alternate_contract.core_ir_sha256
        event["world_hash"] = alternate_contract.world_sha256
    _write_trace(alternate, original_events)
    assert validate_path(alternate)["valid"] is True
    code, _, stderr = _invoke(_restore_args(restore_lab, restore_lab.control_world, alternate))
    assert code == 2 and "conflicts with the expected run" in stderr


def test_embodied_child_is_rejected_before_output(restore_lab: RestoreLab, tmp_path: Path) -> None:
    output = tmp_path / "must-not-exist.ndjson"
    code, _, stderr = _invoke(_restore_args(restore_lab, restore_lab.control_isaac_world, output))
    assert code == 2 and "embodied restore routes are unsupported" in stderr
    assert not output.exists()

    context = _prepare_restore(
        source_trace=restore_lab.parent_trace,
        source_world=restore_lab.parent_world,
        child_world=restore_lab.control_world,
        at_event_id=restore_lab.at_event_id,
        output=None,
        seed=0,
    )
    binding = stable_hash(context.restore)
    with pytest.raises(CampaignValidationError, match="must remain virtual"):
        run_composed_campaign(
            load_compiled_campaign_contract(restore_lab.control_isaac_world),
            output,
            seed=0,
            initial_samples=context.initial_samples,
            restore={
                "source_trace_sha256": context.source_trace_sha256,
                "restore_binding_sha256": binding,
                "restore": context.restore,
            },
        )
    assert not output.exists()


def test_restore_rejects_custody_without_complete_sample_state(
    restore_lab: RestoreLab, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    execute = restore_module._execute_composed_campaign

    def omit_ledger(*args, **kwargs):
        events, _ = execute(*args, **kwargs)
        return events, {}

    monkeypatch.setattr(restore_module, "_execute_composed_campaign", omit_ledger)
    output = tmp_path / "must-not-exist.ndjson"
    code, _, stderr = _invoke(_restore_args(restore_lab, restore_lab.control_world, output))
    assert code == 2 and "custody without complete sample state" in stderr
    assert not output.exists()


def test_invalid_normal_run_identity_does_not_truncate_output(
    restore_lab: RestoreLab, tmp_path: Path
) -> None:
    output = tmp_path / "existing.ndjson"
    output.write_bytes(b"do not truncate")
    code, _, stderr = _invoke(
        ["run", str(restore_lab.control_world), "--seed", "-1", "-o", str(output)]
    )
    assert code == 2 and "campaign identity" in stderr
    assert output.read_bytes() == b"do not truncate"


def test_source_trace_hash_is_part_of_child_run_identity(
    restore_lab: RestoreLab, tmp_path: Path
) -> None:
    original_args = [*_restore_args(restore_lab, restore_lab.control_world), "--dry-run"]
    code, stdout, _ = _invoke(original_args)
    assert code == 0
    original = json.loads(stdout)

    changed_source = tmp_path / "changed-suffix.ndjson"
    events = [json.loads(line) for line in restore_lab.parent_trace.read_text().splitlines()]
    events[-1]["provenance"]["suffix_marker"] = "different valid source bytes"
    _write_trace(changed_source, events)
    changed_args = [
        *_restore_args(
            restore_lab,
            restore_lab.control_world,
            source_trace=changed_source,
        ),
        "--dry-run",
    ]
    code, stdout, _ = _invoke(changed_args)
    assert code == 0
    changed = json.loads(stdout)
    assert changed["source_prefix_sha256"] == original["source_prefix_sha256"]
    assert changed["restored_state_sha256"] == original["restored_state_sha256"]
    assert changed["source_trace_sha256"] != original["source_trace_sha256"]
    assert changed["expected_run_id"] != original["expected_run_id"]


def test_reuse_receipt_uses_the_validated_output_snapshot(
    restore_lab: RestoreLab, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "child.ndjson"
    args = _restore_args(restore_lab, restore_lab.control_world, output)
    assert _invoke(args)[0] == 0
    expected_hash = _sha256(output)
    original_read_bytes = Path.read_bytes
    output_reads = 0

    def replace_after_read(path: Path) -> bytes:
        nonlocal output_reads
        data = original_read_bytes(path)
        if path.resolve() == output.resolve():
            output_reads += 1
            if output_reads == 1:
                path.write_bytes(b"replacement after validated snapshot")
        return data

    monkeypatch.setattr(Path, "read_bytes", replace_after_read)
    code, stdout, _ = _invoke(args)
    receipt = json.loads(stdout)
    assert code == 0 and receipt["reused"] is True
    assert receipt["trace_sha256"] == expected_hash
    assert output_reads == 1


@pytest.mark.parametrize("late_entry", ["file", "symlink"])
def test_restore_output_creation_is_exclusive_after_preflight(
    restore_lab: RestoreLab, tmp_path: Path, late_entry: str
) -> None:
    output = tmp_path / "race.ndjson"
    context = _prepare_restore(
        source_trace=restore_lab.parent_trace,
        source_world=restore_lab.parent_world,
        child_world=restore_lab.control_world,
        at_event_id=restore_lab.at_event_id,
        output=output,
        seed=0,
    )
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"do not change")
    if late_entry == "file":
        output.write_bytes(sentinel.read_bytes())
    else:
        output.symlink_to(sentinel)
    binding = stable_hash(context.restore)
    with pytest.raises(CampaignValidationError, match="appeared after preflight"):
        run_composed_campaign(
            context.child_contract,
            output,
            seed=0,
            initial_samples=context.initial_samples,
            restore={
                "source_trace_sha256": context.source_trace_sha256,
                "restore_binding_sha256": binding,
                "restore": context.restore,
            },
        )
    assert sentinel.read_bytes() == b"do not change"
    if late_entry == "file":
        assert output.read_bytes() == b"do not change"


def test_executed_samples_must_match_the_bound_restore_state(
    restore_lab: RestoreLab, tmp_path: Path
) -> None:
    output = tmp_path / "must-not-exist.ndjson"
    context = _prepare_restore(
        source_trace=restore_lab.parent_trace,
        source_world=restore_lab.parent_world,
        child_world=restore_lab.control_world,
        at_event_id=restore_lab.at_event_id,
        output=output,
        seed=0,
    )
    changed = context.initial_samples[0].model_copy(update={"quantity": 0.5})
    binding = stable_hash(context.restore)
    with pytest.raises(CampaignValidationError, match="differ from bound restore metadata"):
        run_composed_campaign(
            context.child_contract,
            output,
            seed=0,
            initial_samples=(changed,),
            restore={
                "source_trace_sha256": context.source_trace_sha256,
                "restore_binding_sha256": binding,
                "restore": context.restore,
            },
        )
    assert not output.exists()


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ("partial", "partial"),
        ("failed", "did not complete with passed validation"),
        ("bad-anchor", "source prefix differs"),
        ("prefix", "source prefix differs"),
        ("physical", "non-embodied simulate trace"),
        ("late-restore", "restore metadata is allowed only on a restored trace"),
    ],
)
def test_source_mutations_fail_before_output(
    restore_lab: RestoreLab, tmp_path: Path, mutation: str, error: str
) -> None:
    source = tmp_path / f"{mutation}.ndjson"
    events = [json.loads(line) for line in restore_lab.parent_trace.read_text().splitlines()]
    if mutation == "partial":
        source.write_bytes(restore_lab.parent_trace.read_bytes()[:-1])
    else:
        if mutation == "failed":
            events[-1]["provenance"]["execution_status"] = "failed"
        elif mutation == "bad-anchor":
            events[0]["provenance"]["authority_anchor"] = "untrusted-string"
        elif mutation == "prefix":
            selected = next(
                event for event in events if event["event_id"] == restore_lab.at_event_id
            )
            selected["observation"]["channels"][0]["value"] = "tampered-cell"
        elif mutation == "late-restore":
            events[1]["provenance"]["restore_binding_sha256"] = "0" * 64
        else:
            action = next(event for event in events if event["event_type"] == "action")
            action["action"]["evidence_class"] = "physical"
            events[0]["provenance"]["provider_contract"][action["action"]["action_id"]][
                "evidence_class"
            ] = "physical"
            observation = events[action["sequence"] + 1]["observation"]
            observation["evidence_class"] = "physical"
            for channel in observation["channels"]:
                channel["evidence_class"] = "physical"
        _write_trace(source, events)
    output = tmp_path / "must-not-exist.ndjson"
    code, _, stderr = _invoke(
        _restore_args(
            restore_lab,
            restore_lab.control_world,
            output,
            source_trace=source,
        )
    )
    assert code == 2 and error in stderr
    assert not output.exists()


def test_current_model_mismatch_breaks_exact_prefix_reproduction(
    restore_lab: RestoreLab, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dynamical.instruments import ac_echem_cell

    changed_model = tmp_path / "changed_model.py"
    changed_model.write_text("# changed implementation\n", encoding="utf-8")
    monkeypatch.setattr(ac_echem_cell, "__file__", str(changed_model))
    output = tmp_path / "must-not-exist.ndjson"
    code, _, stderr = _invoke(_restore_args(restore_lab, restore_lab.control_world, output))
    assert code == 2 and "source prefix differs" in stderr
    assert not output.exists()


@pytest.mark.parametrize(
    "mutation", ["sample", "state-hash", "binding", "identity", "physical-child"]
)
def test_restored_trace_rejects_tampered_state_binding_and_identity(
    restore_lab: RestoreLab, tmp_path: Path, mutation: str
) -> None:
    trace = tmp_path / "child.ndjson"
    assert _invoke(_restore_args(restore_lab, restore_lab.control_world, trace))[0] == 0
    events = [json.loads(line) for line in trace.read_text().splitlines()]
    restore = events[0]["provenance"]["restore"]
    if mutation == "sample":
        restore["initial_samples"][0]["quantity"] += 1.0
    elif mutation == "state-hash":
        restore["restored_state_sha256"] = "0" * 64
    elif mutation == "binding":
        events[1]["provenance"]["restore_binding_sha256"] = "0" * 64
        _write_trace(trace, events)
        assert _invoke(["validate", str(trace), "--json"])[0] == 2
        return
    elif mutation == "identity":
        for event in events:
            event["campaign_id"] = "composed-wrong"
            event["run_id"] = "simulate-wrong"
            event["event_id"] = f"simulate-wrong:event:{event['sequence']:06d}"
        _write_trace(trace, events)
        assert _invoke(["validate", str(trace), "--json"])[0] == 2
        return
    else:
        action = next(event for event in events if event["event_type"] == "action")
        action["action"]["evidence_class"] = "physical"
        events[0]["provenance"]["provider_contract"][action["action"]["action_id"]][
            "evidence_class"
        ] = "physical"
        observation = events[action["sequence"] + 1]
        observation["observation"]["evidence_class"] = "physical"
        for channel in observation["observation"]["channels"]:
            channel["evidence_class"] = "physical"
        for evidence in observation["evidence"]:
            evidence["evidence_class"] = "physical"
        _write_trace(trace, events)
        assert _invoke(["validate", str(trace), "--json"])[0] == 2
        return
    binding = stable_hash(restore)
    for event in events:
        event["provenance"]["restore_binding_sha256"] = binding
    _write_trace(trace, events)
    assert _invoke(["validate", str(trace), "--json"])[0] == 2


@pytest.mark.parametrize("collision", ["file", "source", "artifact", "symlink"])
def test_output_collision_and_alias_never_modify_the_target(
    restore_lab: RestoreLab, tmp_path: Path, collision: str
) -> None:
    if collision == "file":
        output = tmp_path / "existing.ndjson"
        output.write_bytes(b"existing output")
    elif collision == "source":
        output = restore_lab.parent_trace
    elif collision == "artifact":
        output = restore_lab.control_world / "root.usda"
    else:
        output = tmp_path / "source-link.ndjson"
        output.symlink_to(restore_lab.parent_trace)
    before = output.resolve().read_bytes()
    code, _, _ = _invoke(_restore_args(restore_lab, restore_lab.control_world, output))
    assert code == 2
    assert output.resolve().read_bytes() == before


def test_modified_authority_is_rejected(restore_lab: RestoreLab, tmp_path: Path) -> None:
    registry = yaml.safe_load(DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    twin = next(item for item in registry["providers"] if item["provider_id"] == "ac-oer-twin")
    twin["policy"]["safety_limit_ids"] = []
    forged_registry = tmp_path / "registry.yaml"
    forged_registry.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
    facility = yaml.safe_load(DEFAULT_FACILITY.read_text(encoding="utf-8"))
    admission = next(
        item
        for item in facility["provider_admission_bindings"]
        if item["provider_id"] == "ac-oer-twin"
    )
    admission["safety_limit_ids"] = []
    forged_facility = tmp_path / "facility.yaml"
    forged_facility.write_text(yaml.safe_dump(facility, sort_keys=False), encoding="utf-8")
    composition = compose_files(restore_lab.control_requirement, forged_registry, forged_facility)
    assert composition.status == "COMPILED"
    forged_world = tmp_path / "forged-world"
    compile_facility(
        load_facility_manifest(forged_facility),
        "openusd",
        forged_world,
        composition_result=composition,
    )
    output = tmp_path / "must-not-exist.ndjson"
    code, _, stderr = _invoke(_restore_args(restore_lab, forged_world, output))
    assert code == 2 and "compiled world is validation-only" in stderr
    assert not output.exists()


@pytest.mark.parametrize(
    "args",
    [
        ["run", "WORLD", "--restore-from", "TRACE"],
        ["run", "WORLD", "--dry-run"],
        [
            "run",
            "WORLD",
            "--mode",
            "replay",
            "--restore-from",
            "TRACE",
            "--restore-world",
            "SOURCE_WORLD",
            "--restore-at-event",
            "EVENT",
        ],
    ],
)
def test_restore_argument_shape_errors(restore_lab: RestoreLab, args: list[str]) -> None:
    replacements = {
        "WORLD": str(restore_lab.control_world),
        "TRACE": str(restore_lab.parent_trace),
        "SOURCE_WORLD": str(restore_lab.parent_world),
        "EVENT": restore_lab.at_event_id,
    }
    code, _, stderr = _invoke([replacements.get(item, item) for item in args])
    assert code == 2 and "Example: dynamical run child-world" in stderr
