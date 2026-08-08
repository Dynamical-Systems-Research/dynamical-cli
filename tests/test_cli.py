from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml
from _fixtures import write_reference_requirement

from dynamical.cli import main
from dynamical.composition import compose_files, write_composition_result
from dynamical.schema import load_campaign_requirement

REPOSITORY = Path(__file__).resolve().parents[1]
HEATER_MANIFEST = REPOSITORY / "manifests" / "matterix-heater-workstation.yaml"


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
    assert len(index["registry_sha256"]) == 64
    assert {item["operation_id"] for item in index["operations"]} >= {
        "apply-thermal-program",
        "measure-reaction-progress",
    }
    assert "capabilities" not in index

    assert (
        main(
            [
                "capabilities",
                "--operation",
                "measure-reaction-progress",
                "--json",
            ]
        )
        == 0
    )
    detail = json.loads(capsys.readouterr().out)
    assert detail["operation"]["operation_id"] == "measure-reaction-progress"
    assert detail["providers"][0]["admission"]["status"] == "pending"
    assert detail["providers"][0]["availability"]["available"] is False


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
        "heated-beaker-source-and-embodied-proof"
    )
    assert saved["sources"]["registry"]["registry_id"].startswith("dynamical-")
    assert saved["sources"]["facility"]["facility"]["id"] == ("matterix-heater-facility")
    assert saved["sources"]["default_target"] == "matterix"


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
    assert receipt["reasons"]
    assert "next_command" not in receipt


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
    assert compile_receipt["target"] == "matterix"
    assert compile_receipt["composition_sha256"] is not None

    assert main(["validate", str(compiled), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True

    assert main(["run", str(compiled), "-o", str(simulated)]) == 0
    simulation_receipt = json.loads(capsys.readouterr().out)
    assert simulation_receipt["mode"] == "simulate"
    assert simulation_receipt["w1_evidence"] is False

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
        (REPOSITORY / "registries/reference-capabilities.yaml").read_text(encoding="utf-8")
    )
    registry["providers"][0]["admission"]["authority_id"] = "agent-authored-authority"
    forged_registry = tmp_path / "registry.yaml"
    forged_registry.write_text(yaml.safe_dump(registry), encoding="utf-8")

    facility = yaml.safe_load(HEATER_MANIFEST.read_text(encoding="utf-8"))
    facility["adapter_bindings"][0]["configuration"]["task_id"] = "Agent-Authored-Task"
    forged_facility = tmp_path / "facility.yaml"
    forged_facility.write_text(yaml.safe_dump(facility), encoding="utf-8")

    composition = tmp_path / "composition.json"
    write_composition_result(
        composition,
        compose_files(requirement, forged_registry, forged_facility),
    )

    assert main(["compile", str(composition), "-o", str(tmp_path / "compiled")]) == 2
    assert "installed authority" in capsys.readouterr().err


def test_run_defaults_to_simulate_and_manifest_compile_requires_target(
    tmp_path: Path, capsys
) -> None:
    assert main(["compile", str(HEATER_MANIFEST)]) == 2
    assert "requires --target" in capsys.readouterr().err


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


def test_agent_decision_contract_is_minimal(tmp_path: Path, capsys) -> None:
    virtual = write_reference_requirement(tmp_path / "virtual.yaml")
    physical = write_reference_requirement(tmp_path / "physical.yaml")
    decision = tmp_path / "evidence.json"
    physical_requirement = load_campaign_requirement(physical)
    selected_step = next(
        step for step in physical_requirement.steps if step.operation_id == "apply-thermal-program"
    )
    decision.write_text(
        json.dumps(
            {
                "selected_virtual_campaign": virtual.name,
                "physical_route_requirement": physical.name,
                "selected_physical_experiment": {
                    "operation": selected_step.operation_id,
                    "conditions": {
                        item.id: item.value
                        for item in physical_requirement.inputs
                        if item.value is not None
                    },
                    "parameters": {
                        item.name: {"value": item.value, "unit": item.unit}
                        for item in selected_step.parameters
                    },
                    "measurements": sorted(
                        {
                            output_id
                            for proof in physical_requirement.objective.proof_requirements
                            for output_id in proof.output_port_ids
                        }
                    ),
                },
                "decision_rationale": "The virtual result supports a later physical check.",
                "uncertainty": ["No physical result exists."],
                "submitted": False,
            }
        ),
        encoding="utf-8",
    )
    assert main(["validate", str(decision), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["valid"] is True
    assert report["submitted"] is False

    mismatched = json.loads(decision.read_text(encoding="utf-8"))
    mismatched["selected_physical_experiment"]["conditions"] = {"sample.mass": 0.25}
    decision.write_text(json.dumps(mismatched), encoding="utf-8")
    assert main(["validate", str(decision), "--json"]) == 2
    assert "physical requirement inputs" in capsys.readouterr().err


def test_only_dynamical_console_script_is_published() -> None:
    pyproject = (REPOSITORY / "pyproject.toml").read_text(encoding="utf-8")
    scripts = pyproject.split("[project.scripts]", 1)[1].split("[", 1)[0]
    assert scripts.strip() == 'dynamical = "dynamical.cli:main"'
    assert "openai" not in pyproject.lower()
    assert "anthropic" not in pyproject.lower()
