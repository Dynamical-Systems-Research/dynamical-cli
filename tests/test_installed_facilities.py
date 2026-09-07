"""Separate source platforms retain independent installed authority and sample origins."""

import json
from pathlib import Path

import yaml

from dynamical.cli import main
from dynamical.installed import FASTCAT, SDL1
from dynamical.schema import load_capability_registry, load_facility_manifest


def fastcat_requirement(path: Path) -> Path:
    example = Path(__file__).resolve().parents[1] / "examples/fastcat-oer/requirement.yaml"
    path.write_bytes(example.read_bytes())
    return path


def test_platforms_have_disjoint_installed_provider_sets():
    sdl1 = load_facility_manifest(SDL1 / "facility.yaml")
    fastcat = load_facility_manifest(FASTCAT / "facility.yaml")
    assert [station.id for station in sdl1.workstations] == ["ot2-liquid-handling"]
    assert [station.id for station in fastcat.workstations] == ["fastcat-process"]
    providers = load_capability_registry(FASTCAT / "registry.yaml").providers
    assert {provider.provider_id for provider in providers} == {
        "ac-bath-simulator",
        "ac-oer-twin",
    }
    sdl1_operations = {
        capability.operation_id
        for capability in load_capability_registry(SDL1 / "registry.yaml").capabilities
    }
    assert (
        not {"transfer-sample", "load-electrochemical-cell", "deposit-chemical-bath"}
        & sdl1_operations
    )
    assert all("echem-cell" not in source.id for source in sdl1.asset_sources)


def test_fastcat_runs_without_inventing_transfer_or_cell_loading(tmp_path, capsys):
    requirement = fastcat_requirement(tmp_path / "requirement.yaml")
    composition, world, trace = (
        tmp_path / "composition.json",
        tmp_path / "world",
        tmp_path / "trace.ndjson",
    )
    assert main(["compose", str(requirement), "--facility", "fastcat", "-o", str(composition)]) == 0
    capsys.readouterr()
    assert main(["compile", str(composition), "-o", str(world)]) == 0
    capsys.readouterr()
    assert main(["run", str(world), "-o", str(trace)]) == 0
    capsys.readouterr()
    assert main(["validate", str(trace), "--json"]) == 0
    capsys.readouterr()
    events = [json.loads(line) for line in trace.read_text().splitlines()]
    actions = [event["action"] for event in events if event["event_type"] == "action"]
    assert [action["kind"] for action in actions] == ["deposit-chemical-bath", "measure-oer"]
    assert all(action["station_id"] == "fastcat-process" for action in actions)
    assert all("sample_transition" not in action["parameters"] for action in actions)
    channels = [
        channel
        for event in events
        if event["event_type"] == "observation"
        for channel in event["observation"]["channels"]
        if channel["name"] == "overpotential_v"
    ]
    assert len(channels) == 1
    assert abs(channels[0]["value"] - 0.263047) < 1e-12
    assert channels[0]["quality"] == "estimated"
    assert channels[0]["uncertainty"]["value"] == 0.104969


def test_facility_id_does_not_grant_modified_manifest_authority(tmp_path, capsys):
    requirement = fastcat_requirement(tmp_path / "requirement.yaml")
    payload = yaml.safe_load((FASTCAT / "facility.yaml").read_text())
    payload["facility"]["claim_boundary"] = ["Forged fully verified physical laboratory"]
    proposal = tmp_path / "facility.yaml"
    proposal.write_text(yaml.safe_dump(payload))
    assert main(["compose", str(requirement), "--facility", str(proposal)]) == 1
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "HOLD"
    assert receipt["authority_anchor"] == "installed_bundle"


def test_fastcat_requirement_cannot_use_sdl1_authority(tmp_path, capsys):
    requirement = fastcat_requirement(tmp_path / "requirement.yaml")
    assert main(["compose", str(requirement), "--facility", "sdl1"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "HOLD"


def _conditioning_contract(tmp_path):
    from _fixtures import write_reference_requirement

    from dynamical.campaign import load_compiled_campaign_contract
    from dynamical.compiler import compile_facility
    from dynamical.composition import compose_files

    requirement = write_reference_requirement(tmp_path / "condition.yaml")
    composition = compose_files(requirement, SDL1 / "registry.yaml", SDL1 / "facility.yaml")
    assert composition.status == "COMPILED", composition.reason_codes
    world = compile_facility(
        SDL1 / "facility.yaml",
        "openusd",
        tmp_path / "condition-world",
        composition_result=composition,
    ).output_dir
    return load_compiled_campaign_contract(world)


def test_plain_action_does_not_override_a_later_transfer_quantity(tmp_path):
    """An internal runner contract exercises generic transfer, outside SDL1 admission.

    Conditioning provides custody identity only. The real transfer model must
    still materialize the later command's 3.895 mL, not a fabricated 0 unitless.
    """
    import copy
    import hashlib
    from dataclasses import replace

    from dynamical.campaign import _execute_composed_campaign
    from dynamical.instruments import transfer

    contract = _conditioning_contract(tmp_path)
    binding = copy.deepcopy(contract.operation_bindings[0])
    parameters = [
        {"name": name, "value_type": kind, "unit": "1", "value": value}
        for name, kind, value in [
            ("to_station", "string", "generic-destination"),
            ("sample_id", "string", binding["sample_id"]),
            ("quantity", "number", 3.895),
            ("unit", "string", "mL"),
        ]
    ]
    binding.update(
        {
            "step_id": "generic-transfer",
            "operation_id": "transfer-sample",
            "endpoint_id": "generic-transfer-model",
            "provider_id": "ac-transfer-simulator",
            "parameters": parameters,
            "policy": {"safety_limit_ids": []},
            "validity_envelope": [],
            "capability_contract": {
                "operation_id": "transfer-sample",
                "kind": "transport",
                "input_ports": [],
                "parameters": [
                    {k: v for k, v in item.items() if k != "value"} for item in parameters
                ],
                "output_ports": [
                    {"id": name, "unit": "1"}
                    for name in [
                        "instrument.sample_station_id",
                        "instrument.arrival_confirmed",
                        "sample.state.transferred",
                    ]
                ],
            },
        }
    )
    contract = replace(
        contract,
        operation_bindings=(*contract.operation_bindings, binding),
        model_binding_by_id={
            **contract.model_binding_by_id,
            "generic-transfer-model": {
                "implementation_sha256": hashlib.sha256(
                    Path(transfer.__file__).read_bytes()
                ).hexdigest()
            },
        },
    )
    events, samples = _execute_composed_campaign(contract, seed=7)
    sample = samples[binding["sample_id"]]
    assert sample.quantity == 3.895
    assert sample.unit == "mL"
    transitions = [
        event.action.parameters["sample_transition"]
        for event in events
        if event.action and "sample_transition" in event.action.parameters
    ]
    assert len(transitions) == 1
    assert transitions[0]["from_station"] == "ot2-liquid-handling"
    assert transitions[0]["to_station"] == "generic-destination"
    assert transitions[0]["quantity_delta"] == 3.895
    assert transitions[0]["unit"] == "mL"


def test_unchanged_model_sample_is_a_read_not_a_custody_transition(tmp_path, monkeypatch):
    """Returning the request sample unchanged cannot create quantity or custody evidence."""
    from dataclasses import replace

    from dynamical import campaign
    from dynamical.instruments.ac_arduino import condition_ultrasonic
    from dynamical.samples import Sample

    contract = _conditioning_contract(tmp_path)
    # This test double varies only the returned sample. Module-binding integrity
    # is independently tested; do not describe this wrapper as the admitted model.
    contract = replace(contract, model_binding_by_id={})

    def unchanged_sample(request):
        result = condition_ultrasonic(request)
        return replace(result, sample=request.sample)

    monkeypatch.setattr(campaign.instruments, "resolve", lambda *args: unchanged_sample)
    events, samples = campaign._execute_composed_campaign(contract, seed=7)
    assert samples == {}, "an unchanged ephemeral origin must not seed measured quantity"
    origin = next(event.provenance for event in events if event.observation)
    assert origin["sample_id"] == contract.operation_bindings[0]["sample_id"]
    assert origin["sample_state_written"] is False
    assert all(
        "sample_transition" not in event.action.parameters for event in events if event.action
    )

    initial = Sample(
        id=contract.operation_bindings[0]["sample_id"],
        station_id="ot2-liquid-handling",
        custody_state="held",
        quantity=3.895,
        unit="mL",
        created_by_step_id="fixture",
        state={"deposited_mass_g": 0.002},
    )
    events, samples = campaign._execute_composed_campaign(
        contract, seed=7, initial_samples=[initial]
    )
    assert samples[initial.id] == initial
    assert all(
        "sample_transition" not in event.action.parameters for event in events if event.action
    )
    observation = next(event for event in events if event.observation)
    assert observation.provenance["sample_state_written"] is False


def test_declared_station_selects_same_artifact_as_explicit_facility(tmp_path, capsys):
    requirement = fastcat_requirement(tmp_path / "requirement.yaml")
    implicit, explicit = tmp_path / "implicit.json", tmp_path / "explicit.json"
    assert main(["compose", str(requirement), "-o", str(implicit)]) == 0
    capsys.readouterr()
    assert main(["compose", str(requirement), "--facility", "fastcat", "-o", str(explicit)]) == 0
    capsys.readouterr()
    assert implicit.read_bytes() == explicit.read_bytes()


def test_wrong_facility_hold_supplies_runnable_recovery(tmp_path, capsys):
    import shlex

    requirement = fastcat_requirement(tmp_path / "requirement with spaces.yaml")
    output = tmp_path / "composition with spaces.json"
    assert main(["compose", str(requirement), "--facility", "sdl1", "-o", str(output)]) == 1
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["facility_id"] == "ac-electrodeposition-cell"
    assert receipt["installed_facilities"] == ["sdl1", "fastcat"]
    recovery = shlex.split(receipt["next_command"])
    assert recovery[0] == "dynamical"
    assert main(recovery[1:]) == 0


def test_ambiguous_declared_facilities_do_not_silently_route(tmp_path, capsys):
    requirement = fastcat_requirement(tmp_path / "requirement.yaml")
    data = yaml.safe_load(requirement.read_text())
    data["inputs"].append(
        {
            "id": "other.sample",
            "state_type": "sample_state",
            "unit": "1",
            "value": "other",
            "facility_id": "ot2-liquid-handling",
        }
    )
    requirement.write_text(yaml.safe_dump(data))
    assert main(["compose", str(requirement)]) == 2
    assert "multiple installed facilities" in capsys.readouterr().err


def test_unknown_operation_and_facility_explain_installed_selection(capsys):
    assert main(["capabilities", "--operation", "deposit-chemical-bath", "--json"]) == 2
    error = capsys.readouterr().err
    assert "--facility fastcat --operation deposit-chemical-bath --json" in error
    for facility in ("sdl1", "fastcat"):
        assert main(["capabilities", "--facility", facility, "--operation", "not-installed"]) == 2
        error = capsys.readouterr().err
        recovery = error.split("Next: ", 1)[1].strip().split()
        assert "--operation" not in recovery
        assert "--json" not in recovery
        assert main(recovery[1:]) == 0
        assert "Registry:" in capsys.readouterr().out
    assert main(["capabilities", "--facility", "bogus"]) == 2
    assert "installed facilities: sdl1, fastcat" in capsys.readouterr().err
