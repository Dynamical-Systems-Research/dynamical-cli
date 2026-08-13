from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import yaml
from _fixtures import write_reference_requirement

from dynamical.cli import DEFAULT_REGISTRY, main
from dynamical.compiler import compile_facility
from dynamical.composition import compose_files, write_composition_result

REPOSITORY = Path(__file__).resolve().parents[1]
MANIFEST = REPOSITORY / "dynamical" / "bundle" / "facility.yaml"


def _write_measure_oer_requirement(path: Path) -> Path:
    """One-step measure-oer requirement used by the authority attack tests."""

    requirement = {
        "document_type": "dynamical.campaign-requirement",
        "schema_version": "0.1.0",
        "requirement_id": "forged-oer-measure",
        "objective": {
            "id": "oer-decision",
            "statement": "Estimate OER overpotential.",
            "decision": "Decide if OER performance merits a physical run.",
            "proof_requirements": [
                {
                    "id": "oer-proof",
                    "operation_id": "measure-oer",
                    "output_port_ids": ["overpotential_v"],
                    "minimum_evidence_class": "simulator",
                    "acceptance_rule": "overpotential_v is recorded",
                    "independent_verification_required": True,
                }
            ],
        },
        "inputs": [
            {
                "id": "campaign.sample-id",
                "state_type": "sample_state",
                "unit": "1",
                "value": "sample-nickel-01",
            }
        ],
        "steps": [
            {
                "step_id": "measure",
                "operation_id": "measure-oer",
                "minimum_evidence_class": "simulator",
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
                        "source_id": "campaign.sample-id",
                    }
                ],
                "depends_on": [],
                "required_policy_tags": [],
            }
        ],
        "max_cost_usd": 10.0,
        "max_duration_s": 2000.0,
    }
    path.write_text(yaml.safe_dump(requirement, sort_keys=False), encoding="utf-8")
    return path


def test_public_help_has_exactly_five_agent_commands() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "dynamical.cli", "--help"],
        cwd=REPOSITORY,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    usage = completed.stdout
    assert "{capabilities,compile,compose,run,validate}" in usage
    assert "dynamical-calibrate" not in usage
    assert "counterfactual" not in usage


def test_each_command_has_one_copyable_example() -> None:
    for command in ("capabilities", "compose", "compile", "run", "validate"):
        completed = subprocess.run(
            [sys.executable, "-m", "dynamical.cli", command, "--help"],
            cwd=REPOSITORY,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0
        assert f"dynamical {command}" in completed.stdout


def test_capability_index_is_compact_and_detail_is_complete(capsys) -> None:
    assert main(["capabilities", "--json"]) == 0
    output = capsys.readouterr().out
    assert "\n" not in output.rstrip("\n")
    index = json.loads(output)
    assert index["schema_version"] == "dynamical.capability-index.v1"
    assert index["registry_role"] == "installed_authority"
    assert index["execution_status"] == "not_executed"
    assert index["authority_anchor"] == "installed_bundle"
    assert index["validation_reasons"] == []
    assert len(index["registry_sha256"]) == 64
    assert {item["operation_id"] for item in index["operations"]} >= {
        "dispense-electrolyte",
        "measure-oer",
    }
    assert "capabilities" not in index

    assert (
        main(
            [
                "capabilities",
                "--operation",
                "measure-oer",
                "--json",
            ]
        )
        == 0
    )
    detail = json.loads(capsys.readouterr().out)
    assert detail["operation"]["operation_id"] == "measure-oer"
    physical = next(item for item in detail["providers"] if item["evidence_class"] == "physical")
    assert physical["admission"]["status"] == "pending"
    assert physical["availability"]["available"] is False


def test_compose_receipt_is_compact_and_saved_sources_are_self_contained(
    tmp_path: Path, capsys
) -> None:
    requirement = write_reference_requirement(tmp_path / "requirement.yaml")
    composition = tmp_path / "composition.json"

    assert main(["compose", str(requirement), "-o", str(composition)]) == 0
    receipt_output = capsys.readouterr().out
    assert "\n" not in receipt_output.rstrip("\n")
    receipt = json.loads(receipt_output)
    saved = json.loads(composition.read_text(encoding="utf-8"))
    assert receipt["status"] == "COMPILED"
    assert receipt["composition_sha256"] == saved["composition_sha256"]
    assert saved["sources"]["requirement"]["requirement_id"] == (
        "electrodeposition-transfer-and-conditioning-proof"
    )
    assert saved["sources"]["registry"]["registry_id"].startswith("dynamical-")
    assert saved["sources"]["facility"]["facility"]["id"] == ("ac-electrodeposition-cell")
    assert saved["sources"]["default_target"] == "openusd"
    assert main(["validate", str(composition), "--json"]) == 0
    validation = json.loads(capsys.readouterr().out)
    assert validation["execution_status"] == "not_executable"
    assert validation["authority_anchor"] == "not_evaluated"


def test_hold_receipt_has_reasons_and_no_compile_instruction(tmp_path: Path, capsys) -> None:
    requirement = yaml.safe_load(
        write_reference_requirement(tmp_path / "requirement.yaml").read_text(encoding="utf-8")
    )
    requirement["steps"][1]["minimum_evidence_class"] = "physical"
    hold_requirement = tmp_path / "physical.yaml"
    hold_requirement.write_text(yaml.safe_dump(requirement, sort_keys=False), encoding="utf-8")

    composition = tmp_path / "hold.json"
    assert main(["compose", str(hold_requirement), "-o", str(composition)]) == 1
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "HOLD"
    assert receipt["reason_codes"]
    assert receipt["validation_reasons"]
    assert "next_command" not in receipt

    assert main(["validate", str(composition), "--json"]) == 0
    validation = json.loads(capsys.readouterr().out)
    assert validation["execution_status"] == "blocked"
    assert validation["validation_reasons"] == receipt["validation_reasons"]


def test_saved_composition_compiles_runs_and_validates_without_extra_flags(
    tmp_path: Path, capsys
) -> None:
    requirement = write_reference_requirement(tmp_path / "requirement.yaml")
    composition = tmp_path / "composition.json"
    compiled = tmp_path / "compiled"
    simulated = tmp_path / "simulate.ndjson"
    replayed = tmp_path / "replay.ndjson"

    assert main(["compose", str(requirement), "-o", str(composition)]) == 0
    capsys.readouterr()
    assert main(["compile", str(composition), "-o", str(compiled)]) == 0
    compile_output = capsys.readouterr().out
    assert "\n" not in compile_output.rstrip("\n")
    compile_receipt = json.loads(compile_output)
    assert compile_receipt["target"] == "openusd"
    assert compile_receipt["composition_sha256"] is not None

    assert main(["validate", str(compiled), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True

    assert main(["run", str(compiled), "-o", str(simulated)]) == 0
    simulation_receipt = json.loads(capsys.readouterr().out)
    assert simulation_receipt["mode"] == "simulate"
    assert simulation_receipt["embodied_evidence_bound"] is False
    assert simulation_receipt["authority_anchor"] == "installed_bundle"
    assert simulation_receipt["validation_reasons"] == []

    assert main(["run", str(simulated), "--mode", "replay", "-o", str(replayed)]) == 0
    replay_receipt = json.loads(capsys.readouterr().out)
    assert replay_receipt["mode"] == "replay"
    assert replay_receipt["source_trace_sha256"] == simulation_receipt["trace_sha256"]

    for trace in (simulated, replayed):
        assert main(["validate", str(trace), "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["valid"] is True


def test_saved_composition_tampering_fails_closed(tmp_path: Path, capsys) -> None:
    requirement = write_reference_requirement(tmp_path / "requirement.yaml")
    composition = tmp_path / "composition.json"
    assert main(["compose", str(requirement), "-o", str(composition)]) == 0
    capsys.readouterr()

    saved = json.loads(composition.read_text(encoding="utf-8"))
    saved["sources"]["registry"]["providers"][0]["admission"]["status"] = "pending"
    composition.write_text(json.dumps(saved), encoding="utf-8")
    assert main(["compile", str(composition), "-o", str(tmp_path / "compiled")]) == 2
    error = capsys.readouterr().err
    assert "source hash" in error or "resolution hash" in error


def test_coordinated_authority_rehash_fails_closed(tmp_path: Path, capsys) -> None:
    requirement = write_reference_requirement(tmp_path / "requirement.yaml")
    registry = yaml.safe_load(
        (REPOSITORY / "dynamical/bundle/registry.yaml").read_text(encoding="utf-8")
    )
    registry["providers"][0]["admission"]["authority_id"] = "agent-authored-authority"
    forged_registry = tmp_path / "registry.yaml"
    forged_registry.write_text(yaml.safe_dump(registry), encoding="utf-8")

    facility = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    facility["adapter_bindings"][0]["configuration"]["task_id"] = "Agent-Authored-Task"
    forged_facility = tmp_path / "facility.yaml"
    forged_facility.write_text(yaml.safe_dump(facility), encoding="utf-8")

    composition = tmp_path / "composition.json"
    write_composition_result(
        composition,
        compose_files(requirement, forged_registry, forged_facility),
    )

    compile_rc = main(["compile", str(composition), "-o", str(tmp_path / "compiled")])
    receipt = json.loads(capsys.readouterr().out)
    assert compile_rc == 1
    assert receipt["status"] == "HOLD"
    assert "AUTHORITY_MODIFIED" in receipt["reason_codes"]
    assert "AUTHORITY_UNRECOGNIZED" in receipt["reason_codes"]
    assert not (tmp_path / "compiled").exists()


def test_self_admitted_physical_provider_is_demoted_not_trusted(tmp_path: Path, capsys) -> None:
    """Attack repro: an agent flips every measure-oer provider to ``evidence_class:
    physical`` in its own registry and passes that forged registry to both
    ``compose`` and ``compile``.

    Before the fix this composes (COMPILED), compiles, runs, and validates,
    producing a simulator's output masquerading as ``evidence_class: physical``
    with ``uncertainty.kind: measured``. After the fix, compose demotes the
    self-admitted claim -- visibly, via a legible ``PROVIDER_SELF_ADMITTED``
    reason -- and compile independently refuses the forged authority too, so no
    compiled world, run, or validated trace is ever reachable.
    """

    registry = yaml.safe_load(
        (REPOSITORY / "dynamical/bundle/registry.yaml").read_text(encoding="utf-8")
    )
    for provider in registry["providers"]:
        if provider["provider_id"] in {"ac-oer-simulator", "ac-oer-twin"}:
            provider["evidence_class"] = "physical"
            provider["validity_envelope"].append(
                {
                    "subject_kind": "input",
                    "subject_id": "sample.state",
                    "value_type": "sample_state",
                    "unit": "1",
                    "enum": ["sample-nickel-01"],
                }
            )
    forged_registry = tmp_path / "registry.yaml"
    forged_registry.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")

    unchecked_requirement = write_reference_requirement(tmp_path / "unchecked-requirement.yaml")
    unchecked = compose_files(unchecked_requirement, forged_registry, MANIFEST)
    assert unchecked.status == "COMPILED"
    api_world = tmp_path / "api-registry-proposal-world"
    api_result = compile_facility(
        MANIFEST,
        "openusd",
        api_world,
        composition_result=unchecked,
    )
    assert api_result.authority_anchor == "unverified_proposal"
    assert api_result.execution_status == "not_executable"
    assert main(["run", str(api_world), "-o", str(tmp_path / "api-registry-trace.ndjson")]) == 2
    assert "validation-only" in capsys.readouterr().err

    assert main(["capabilities", "--registry", str(forged_registry), "--json"]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["registry_role"] == "proposal"
    for forged_id in ("ac-oer-simulator", "ac-oer-twin"):
        provider = next(
            item
            for operation in inspected["operations"]
            for item in operation["providers"]
            if item["provider_id"] == forged_id
        )
        assert provider["admission"] == "pending"
        assert provider["proposed_admission"] == "admitted"
    assert any(item["code"] == "PROVIDER_SELF_ADMITTED" for item in inspected["validation_reasons"])

    requirement_path = _write_measure_oer_requirement(tmp_path / "requirement.yaml")
    composition_path = tmp_path / "composition.json"
    compose_rc = main(
        [
            "compose",
            str(requirement_path),
            "--registry",
            str(forged_registry),
            "-o",
            str(composition_path),
        ]
    )
    receipt = json.loads(capsys.readouterr().out)
    assert compose_rc == 1
    assert receipt["status"] == "HOLD"
    assert "PROVIDER_NOT_ADMITTED" in receipt["reason_codes"]
    untrusted = receipt["untrusted_admissions"]
    for forged_id in ("ac-oer-simulator", "ac-oer-twin"):
        assert any(
            item["code"] == "PROVIDER_SELF_ADMITTED" and item["provider_id"] == forged_id
            for item in untrusted
        )

    compiled_dir = tmp_path / "compiled"
    compile_rc = main(["compile", str(composition_path), "-o", str(compiled_dir)])
    capsys.readouterr()
    assert compile_rc != 0
    assert not compiled_dir.exists()


def test_known_provider_with_modified_safety_fields_is_not_trusted(tmp_path: Path, capsys) -> None:
    """Named authority attack: a known, installed provider identity whose
    safety-bearing fields were changed must never compose or compile as
    admitted. The identity tuple still matches the installed record, so only
    a full authority-record comparison catches it."""

    registry = yaml.safe_load(
        (REPOSITORY / "dynamical/bundle/registry.yaml").read_text(encoding="utf-8")
    )
    for provider in registry["providers"]:
        if provider["provider_id"] in {"ac-oer-simulator", "ac-oer-twin"}:
            provider["policy"]["safety_limit_ids"] = []
    forged_registry = tmp_path / "registry.yaml"
    forged_registry.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")

    requirement_path = _write_measure_oer_requirement(tmp_path / "requirement.yaml")
    composition_path = tmp_path / "composition.json"
    compose_rc = main(
        [
            "compose",
            str(requirement_path),
            "--registry",
            str(forged_registry),
            "-o",
            str(composition_path),
        ]
    )
    receipt = json.loads(capsys.readouterr().out)
    assert compose_rc == 1
    assert receipt["status"] == "HOLD"
    assert "PROVIDER_NOT_ADMITTED" in receipt["reason_codes"]
    for forged_id in ("ac-oer-simulator", "ac-oer-twin"):
        assert any(
            item["code"] == "PROVIDER_AUTHORITY_MODIFIED" and item["provider_id"] == forged_id
            for item in receipt["untrusted_admissions"]
        )

    compiled_dir = tmp_path / "compiled"
    compile_rc = main(["compile", str(composition_path), "-o", str(compiled_dir)])
    receipt = json.loads(capsys.readouterr().out)
    assert compile_rc == 1
    assert receipt["status"] == "HOLD"
    assert not compiled_dir.exists()


def test_modified_model_hash_in_agent_facility_is_refused(tmp_path: Path, capsys) -> None:
    """Named authority attack: modified model code with a matching
    agent-authored facility hash. The declared implementation hash is an
    authority record; an agent facility that redeclares it differs from the
    installed authority and holds at compose and at compile."""

    facility = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    for binding in facility["model_bindings"]:
        if binding["id"] == "ac-oer-model":
            binding["implementation_sha256"] = "0" * 64
    forged_facility = tmp_path / "facility.yaml"
    forged_facility.write_text(yaml.safe_dump(facility, sort_keys=False), encoding="utf-8")

    requirement_path = write_reference_requirement(tmp_path / "requirement.yaml")
    compose_rc = main(["compose", str(requirement_path), "--facility", str(forged_facility)])
    receipt = json.loads(capsys.readouterr().out)
    assert compose_rc == 1
    assert receipt["status"] == "HOLD"
    assert receipt["reason_codes"] == ["AUTHORITY_MODIFIED"]
    assert any("facility document" in item["detail"] for item in receipt["validation_reasons"])

    composition_path = tmp_path / "composition.json"
    write_composition_result(
        composition_path,
        compose_files(requirement_path, DEFAULT_REGISTRY, forged_facility),
    )
    compiled_dir = tmp_path / "compiled"
    compile_rc = main(["compile", str(composition_path), "-o", str(compiled_dir)])
    receipt = json.loads(capsys.readouterr().out)
    assert compile_rc == 1
    assert receipt["status"] == "HOLD"
    assert "AUTHORITY_MODIFIED" in receipt["reason_codes"]
    assert not compiled_dir.exists()


def test_stripped_proof_requirements_fail_validation_and_replay(tmp_path: Path, capsys) -> None:
    """Named authority attack: a trace cannot remove or redefine its required
    proof outputs. Stripping proof requirements from campaign_start must fail
    both standalone validation and replay."""

    requirement = write_reference_requirement(tmp_path / "requirement.yaml")
    composition = tmp_path / "composition.json"
    compiled = tmp_path / "compiled"
    trace = tmp_path / "trace.ndjson"
    assert main(["compose", str(requirement), "-o", str(composition)]) == 0
    capsys.readouterr()
    assert main(["compile", str(composition), "-o", str(compiled)]) == 0
    capsys.readouterr()
    assert main(["run", str(compiled), "-o", str(trace)]) == 0
    capsys.readouterr()

    lines = trace.read_text(encoding="utf-8").splitlines()
    start = json.loads(lines[0])
    start["provenance"]["proof_requirements"] = []
    lines[0] = json.dumps(start, sort_keys=True, separators=(",", ":"))
    tampered = tmp_path / "tampered.ndjson"
    tampered.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert main(["validate", str(tampered), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["valid"] is False
    assert any(
        reason.get("code") == "PROOF_CONTRACT_MISMATCH"
        for reason in report.get("validation_reasons", [])
    )

    replayed = tmp_path / "replay.ndjson"
    assert main(["run", str(tampered), "--mode", "replay", "-o", str(replayed)]) != 0


def test_injected_material_state_holds_at_compile(tmp_path: Path, capsys) -> None:
    """A fabricated facility record outside the installed authority is a
    proposal, even in a section (material_states) the agent may otherwise
    leave empty: it seeds scientific conditions and must not compile silently.
    An agent-supplied facility that injects a material_states record holds at
    compile with a typed AUTHORITY_MODIFIED reason."""

    facility = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    facility["material_states"] = [
        {
            "id": "forged-material",
            "container_asset_id": "ot2-test-plate",
            "initial_channels": [
                {"channel_id": "forged.injected_channel", "value": 1.0, "unit": "1"}
            ],
        }
    ]
    forged_facility = tmp_path / "facility.yaml"
    forged_facility.write_text(yaml.safe_dump(facility, sort_keys=False), encoding="utf-8")

    requirement = write_reference_requirement(tmp_path / "requirement.yaml")
    composition_path = tmp_path / "composition.json"
    write_composition_result(
        composition_path, compose_files(requirement, DEFAULT_REGISTRY, forged_facility)
    )

    compiled_dir = tmp_path / "compiled"
    compile_rc = main(["compile", str(composition_path), "-o", str(compiled_dir)])
    receipt = json.loads(capsys.readouterr().out)
    assert compile_rc == 1
    assert receipt["status"] == "HOLD"
    assert "AUTHORITY_MODIFIED" in receipt["reason_codes"]
    assert any("facility document" in item["detail"] for item in receipt["validation_reasons"])
    assert not compiled_dir.exists()


def test_run_defaults_to_simulate_and_manifest_compile_requires_target(
    tmp_path: Path, capsys
) -> None:
    assert main(["compile", str(MANIFEST)]) == 2
    assert "requires --target" in capsys.readouterr().err

    compiled = tmp_path / "manifest-world"
    assert main(["compile", str(MANIFEST), "--target", "openusd", "-o", str(compiled)]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["execution_status"] == "not_executable"
    assert receipt["authority_anchor"] == "installed_bundle"
    assert receipt["next_command"] == f"dynamical validate {compiled} --json"
    trace = tmp_path / "trace.ndjson"
    assert main(["run", str(compiled), "-o", str(trace)]) == 2
    assert "validation-only" in capsys.readouterr().err
    assert not trace.exists()


def test_modified_claim_boundary_is_a_proposal(tmp_path: Path, capsys) -> None:
    facility = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    facility["facility"]["claim_boundary"] = ["Agent-authored authority claim."]
    proposed_facility = tmp_path / "facility.yaml"
    proposed_facility.write_text(yaml.safe_dump(facility, sort_keys=False), encoding="utf-8")

    requirement = write_reference_requirement(tmp_path / "requirement.yaml")
    composition = tmp_path / "composition.json"
    assert (
        main(
            [
                "compose",
                str(requirement),
                "--facility",
                str(proposed_facility),
                "-o",
                str(composition),
            ]
        )
        == 1
    )
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "HOLD"
    assert receipt["authority_anchor"] == "installed_bundle"
    assert "Agent-authored authority claim." not in receipt["claim_boundary"]
    assert "AUTHORITY_MODIFIED" in receipt["reason_codes"]
    assert not composition.exists()

    compiled = tmp_path / "proposal-world"
    assert (
        main(
            [
                "compile",
                str(proposed_facility),
                "--target",
                "openusd",
                "-o",
                str(compiled),
            ]
        )
        == 0
    )
    compile_receipt = json.loads(capsys.readouterr().out)
    assert compile_receipt["execution_status"] == "not_executable"
    assert compile_receipt["authority_anchor"] == "unverified_proposal"
    assert "Agent-authored authority claim." not in compile_receipt["claim_boundary"]
    evidence_report = json.loads((compiled / "evidence_report.json").read_text(encoding="utf-8"))
    assert evidence_report["authority_anchor"] == "unverified_proposal"
    assert "Agent-authored authority claim." not in evidence_report["claim_boundary"]
    assert main(["validate", str(compiled), "--json"]) == 0
    validation = json.loads(capsys.readouterr().out)
    assert validation["authority_anchor"] == "unverified_proposal"
    assert validation["execution_status"] == "not_executable"
    assert "Agent-authored authority claim." not in validation["claim_boundary"]

    unchecked = compose_files(requirement, DEFAULT_REGISTRY, proposed_facility)
    assert unchecked.status == "COMPILED"
    api_world = tmp_path / "api-proposal-world"
    api_result = compile_facility(
        proposed_facility,
        "openusd",
        api_world,
        composition_result=unchecked,
    )
    assert api_result.authority_anchor == "unverified_proposal"
    assert api_result.execution_status == "not_executable"
    assert main(["run", str(api_world), "-o", str(tmp_path / "api-trace.ndjson")]) == 2
    assert "validation-only" in capsys.readouterr().err


def test_missing_cli_inputs_name_the_absent_path(tmp_path: Path, capsys) -> None:
    absent = tmp_path / "absent.json"
    cases = (
        (["compose", str(absent)], "campaign requirement does not exist"),
        (["compile", str(absent)], "compile input does not exist"),
        (["run", str(absent)], "run input does not exist"),
        (["validate", str(absent)], "validation input does not exist"),
    )
    for arguments, expected in cases:
        assert main(arguments) == 2
        error = capsys.readouterr().err
        assert expected in error
        assert str(absent) in error

    malformed = tmp_path / "malformed.yaml"
    malformed.write_text("facility: [unterminated\n", encoding="utf-8")
    for arguments in (
        ["capabilities", "--registry", str(malformed), "--json"],
        ["compile", str(malformed), "--target", "openusd"],
    ):
        assert main(arguments) == 2
        error = capsys.readouterr().err
        assert "malformed YAML" in error
        assert "Traceback" not in error


def test_registry_sha256_is_recomputable_from_the_emitted_bytes(capsys):
    main(["capabilities", "--json"])
    payload = json.loads(capsys.readouterr().out)
    declared = payload.pop("registry_sha256")
    recomputed = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert recomputed == declared


def test_operation_filter_is_honoured_without_json(capsys):
    main(["capabilities", "--operation", "measure-oer"])
    out = capsys.readouterr().out
    assert "measure-oer" in out
    assert "dispense-electrolyte" not in out


def test_index_carries_everything_needed_to_avoid_a_hold(capsys):
    main(["capabilities", "--json"])
    operation = json.loads(capsys.readouterr().out)["operations"][0]
    provider = operation["providers"][0]
    assert {"policy", "cost", "duration", "validity_envelope"} <= set(provider)
