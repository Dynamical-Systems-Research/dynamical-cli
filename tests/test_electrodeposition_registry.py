"""The registry must expose AC capabilities and keep every physical route on HOLD."""

from pathlib import Path

import pytest

from dynamical.campaign import (
    load_compiled_campaign_contract,
    run_composed_campaign,
    validate_events,
)
from dynamical.campaign import validate_path as campaign_validate_path
from dynamical.compiler import compile_facility
from dynamical.composition import compose_virtual_sdl
from dynamical.schema import CampaignRequirement, load_capability_registry

REGISTRY = "dynamical/bundle/reference-lab/registry.yaml"
MANIFEST = "dynamical/bundle/reference-lab/facility.yaml"


def _parameter(name: str, value_type: str, unit: str, value: object) -> dict[str, object]:
    return {"name": name, "value_type": value_type, "unit": unit, "value": value}


def _sample_input_binding(source_id: str = "campaign.sample-id") -> dict[str, object]:
    """A step's ``sample.state`` input, bound to the campaign's declared initial sample."""

    return {
        "target_port_id": "sample.state",
        "source_kind": "campaign_input",
        "source_id": source_id,
    }


def _coverage_requirement(
    *,
    current_a: float = -0.002827,
    chemical: str = "Ni",
) -> CampaignRequirement:
    """Exercise five SDL1 instruments on one stationary sample.

    Inputs follow source command points; physical material response remains unknown.
    """

    return CampaignRequirement.model_validate(
        {
            "document_type": "dynamical.campaign-requirement",
            "schema_version": "0.1.0",
            "requirement_id": "ac-module-coverage",
            "objective": {
                "id": "module-coverage-check",
                "statement": (
                    "Exercise each admitted AC module once with harness-selected, "
                    "in-envelope parameters."
                ),
                "decision": (
                    "Confirm the returned evidence validates and stays within the "
                    "declared envelopes."
                ),
                "proof_requirements": [
                    {
                        "id": "oer-proof",
                        "operation_id": "measure-oer",
                        "output_port_ids": ["overpotential_v"],
                        "minimum_evidence_class": "simulator",
                        "acceptance_rule": "overpotential_v is recorded",
                        "independent_verification_required": True,
                    },
                ],
            },
            "inputs": [
                {
                    "id": "campaign.sample-id",
                    "state_type": "sample_state",
                    "unit": "1",
                    "value": "sample-harness-01",
                    "facility_id": "ot2-liquid-handling",
                }
            ],
            "steps": [
                {
                    "step_id": "dispense",
                    "operation_id": "dispense-electrolyte",
                    "minimum_evidence_class": "simulator",
                    "parameters": [
                        _parameter("volume_ml", "number", "mL", 3.0),
                        _parameter("chemical", "string", "1", chemical),
                    ],
                    "input_bindings": [_sample_input_binding()],
                    "depends_on": [],
                    "required_policy_tags": [],
                },
                {
                    "step_id": "condition",
                    "operation_id": "condition-ultrasonic",
                    "minimum_evidence_class": "simulator",
                    "parameters": [
                        _parameter("duration_s", "number", "s", 30.0),
                        _parameter("temperature_setpoint_c", "number", "degC", 35.0),
                    ],
                    "input_bindings": [_sample_input_binding()],
                    "depends_on": ["dispense"],
                    "required_policy_tags": [],
                },
                {
                    "step_id": "deposit",
                    "operation_id": "electrodeposit-constant-current",
                    "minimum_evidence_class": "simulator",
                    "parameters": [
                        _parameter("current_a", "number", "A", current_a),
                        _parameter("duration_s", "number", "s", 60.0),
                        _parameter("temperature_setpoint_c", "number", "degC", 35.0),
                    ],
                    "input_bindings": [_sample_input_binding()],
                    "depends_on": ["condition"],
                    "required_policy_tags": [],
                },
                {
                    "step_id": "clean",
                    "operation_id": "clean-electrode",
                    "minimum_evidence_class": "simulator",
                    "parameters": [
                        _parameter("use_acid", "boolean", "1", True),
                        _parameter("acid_dwell_s", "number", "s", 0.1),
                    ],
                    "input_bindings": [_sample_input_binding()],
                    "depends_on": ["deposit"],
                    "required_policy_tags": [],
                },
                {
                    "step_id": "measure",
                    "operation_id": "measure-oer",
                    "minimum_evidence_class": "simulator",
                    "parameters": [_parameter("current_density_a_cm2", "number", "A/cm^2", 0.020)],
                    "input_bindings": [_sample_input_binding()],
                    "depends_on": ["clean"],
                    "required_policy_tags": [],
                },
            ],
            "max_cost_usd": 10.0,
            "max_duration_s": 2500.0,
        }
    )


def _terminal_transfer_requirement(destination: str) -> CampaignRequirement:
    requirement = _coverage_requirement()
    transfer = requirement.steps[0].model_copy(
        update={
            "step_id": "legacy-transfer",
            "operation_id": "transfer-sample",
            "parameters": [],
        }
    )
    parameters = [_parameter("to_station", "string", "1", destination)]
    # Validate the deliberately unsupported historical operation through the public schema.
    transfer = type(transfer).model_validate({**transfer.model_dump(), "parameters": parameters})
    proof = requirement.objective.proof_requirements[0].model_copy(
        update={
            "operation_id": "transfer-sample",
            "output_port_ids": ["sample.state.transferred"],
            "minimum_evidence_class": "simulator",
        }
    )
    return requirement.model_copy(
        update={
            "objective": requirement.objective.model_copy(update={"proof_requirements": [proof]}),
            "steps": [transfer],
        }
    )


@pytest.mark.parametrize(
    "destination", ["squidstat-echem", "arduino-conditioning", "unsupported-station"]
)
def test_legacy_transfer_holds_for_removed_sdl1_topology(destination: str) -> None:
    result = compose_virtual_sdl(
        _terminal_transfer_requirement(destination), load_capability_registry(REGISTRY)
    )
    assert result.status == "HOLD"
    assert result.virtual_sdl is None
    assert "MISSING_CAPABILITY" in result.reason_codes


def test_one_sample_stays_on_the_ot2_deck_across_instruments():
    result = compose_virtual_sdl(_coverage_requirement(), load_capability_registry(REGISTRY))
    assert result.status == "COMPILED", result.reason_codes
    assert result.virtual_sdl is not None
    assert result.virtual_sdl.transport_bindings == []
    assert [b.step_id for b in result.virtual_sdl.operation_bindings] == [
        "dispense",
        "condition",
        "deposit",
        "clean",
        "measure",
    ]
    for binding in result.virtual_sdl.operation_bindings:
        assert binding.selected_facility_id == "ot2-liquid-handling"
        sample_input = next(
            item for item in binding.inputs if item.target_port_id == "sample.state"
        )
        assert sample_input.source_kind == "campaign_input"
        assert sample_input.source_id == "campaign.sample-id"


def _run_coverage_campaign(
    tmp_path: Path,
    *,
    current_a: float = -0.002827,
    chemical: str = "Ni",
):
    registry = load_capability_registry(REGISTRY)
    requirement = _coverage_requirement(current_a=current_a, chemical=chemical)
    composition = compose_virtual_sdl(requirement, registry)
    assert composition.status == "COMPILED", composition.reason_codes

    compiled = compile_facility(
        MANIFEST, "openusd", tmp_path / "compiled", composition_result=composition
    ).output_dir
    contract = load_compiled_campaign_contract(compiled)
    events, _ = run_composed_campaign(contract, tmp_path / "trace.ndjson", seed=11)
    summary = dict(validate_events(events))
    for event in events:
        if event.observation is None:
            continue
        for channel in event.observation.channels:
            for name in (
                "deposited_mass_g",
                "overpotential_v",
                "volume_applied_ml",
                "instrument.temperature_observed_c",
                "instrument.residual_volume_ml",
                "commanded_charge_c",
            ):
                if channel.name == name:
                    summary[name] = channel.value
    return summary


def test_coverage_campaign_fails_closed_on_unknown_physical_response(tmp_path: Path):
    """Source commands do not manufacture the physical observations required by proof."""

    result = _run_coverage_campaign(tmp_path)

    assert result["execution_status"] == "failed"
    assert result["valid"] is False
    assert result["event_count"] == 12
    assert any(
        reason["code"] == "PROOF_OUTPUT_UNAVAILABLE" for reason in result["validation_reasons"]
    )
    for name in (
        "deposited_mass_g",
        "overpotential_v",
        "volume_applied_ml",
        "instrument.temperature_observed_c",
        "instrument.residual_volume_ml",
    ):
        assert result[name] is None
    assert result["commanded_charge_c"] == pytest.approx(-0.002827 * 60)
    from dynamical.campaign import read_trace
    from dynamical.samples import check_invariants

    events = read_trace(tmp_path / "trace.ndjson")
    assert check_invariants(events) == []


def test_sample_declared_at_removed_station_holds_before_run():
    requirement = _coverage_requirement()
    requirement = requirement.model_copy(
        update={
            "inputs": [
                requirement.inputs[0].model_copy(update={"facility_id": "arduino-conditioning"})
            ]
        }
    )
    result = compose_virtual_sdl(requirement, load_capability_registry(REGISTRY))
    assert result.status == "HOLD"
    assert "TRANSPORT_REQUIRED" in result.reason_codes


def test_tampered_instrument_module_fails_closed_on_declared_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defect 2 repro: a manifest's declared ``implementation_sha256`` must bind the
    module that actually executes, not merely be present alongside it. Point the
    OER model's already-resolved module at tampered bytes -- simulating someone
    modifying the installed instrument after the facility declared its hash -- and
    confirm the run fails closed with a typed ``MODEL_IMPLEMENTATION_MISMATCH``
    reason rather than silently executing the tampered module and reporting its
    output as if the declared hash still bound it.
    """

    import dynamical.instruments.ac_oer as ac_oer

    tampered = tmp_path / "ac_oer_tampered.py"
    tampered.write_text(
        Path(ac_oer.__file__).read_text(encoding="utf-8") + "\n# tampered\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ac_oer, "__file__", str(tampered))

    result = _run_coverage_campaign(tmp_path)

    assert result["execution_status"] == "failed"
    assert result["valid"] is False
    assert any(
        reason["code"] == "MODEL_IMPLEMENTATION_MISMATCH" for reason in result["validation_reasons"]
    )


def test_repeated_action_kinds_each_get_their_own_provider_binding(tmp_path: Path):
    """Provider bindings are keyed by action_id, not action kind. The coverage
    campaign repeats ``dispense-electrolyte``; each dispense must
    have its own compiled binding, and every action must validate against its
    own binding -- not against whichever same-kind step compiled last."""

    import importlib.util
    import json as json_module

    registry = load_capability_registry(REGISTRY)
    requirement = _coverage_requirement()
    second_dispense = requirement.steps[0].model_copy(
        update={"step_id": "dispense-again", "depends_on": ["dispense-first"]}
    )
    condition = requirement.steps[1].model_copy(update={"depends_on": ["dispense-again"]})
    requirement = requirement.model_copy(
        update={
            "steps": [
                requirement.steps[0].model_copy(update={"step_id": "dispense-first"}),
                second_dispense,
                condition,
                *requirement.steps[2:],
            ]
        }
    )
    composition = compose_virtual_sdl(requirement, registry)
    assert composition.status == "COMPILED", composition.reason_codes
    compiled = compile_facility(
        MANIFEST, "isaac", tmp_path / "isaac", composition_result=composition
    ).output_dir

    campaign = json_module.loads((compiled / "runtime_campaign.json").read_text(encoding="utf-8"))
    bindings = campaign["provider_bindings"]
    action_ids = [a["action_id"] for a in campaign["actions"]]
    repeated_kind = campaign["actions"][0]["kind"]
    dispense_ids = [a["action_id"] for a in campaign["actions"] if a["kind"] == repeated_kind]

    # One binding per action, not one collapsed entry per kind.
    assert dispense_ids == ["dispense-first", "dispense-again"]
    assert set(bindings) == set(action_ids)
    assert all(step_id in bindings for step_id in dispense_ids)
    assert repeated_kind not in bindings  # the old kind-keyed collapse is gone

    # Every action validates against the verified pack via its own binding.
    spec = importlib.util.spec_from_file_location(
        "_rt_p1", compiled / "dynamical_runtime_contract.py"
    )
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    pack = runtime.verify_compiled_pack(compiled)
    for action in campaign["actions"]:
        runtime.validate_action(action, pack)  # must not raise

    # A malformed (unhashable) action_id fails through the runtime contract's
    # normal path (the pack's own RuntimeContractError), not a raw TypeError on
    # the binding lookup.
    malformed = dict(campaign["actions"][0])
    malformed["action_id"] = ["not", "a", "string"]
    with pytest.raises(runtime.RuntimeContractError):
        runtime.validate_action(malformed, pack)


def test_cross_surface_identity_binds_one_composition_everywhere(tmp_path: Path):
    """Criterion 6: simulation, Isaac compilation, replay, and validation all
    carry the identical composition identity -- no surface silently re-hashes
    its own copy of the campaign contract."""

    import json as json_module

    from test_runtime_pack import FASTCAT_LAB, _model_backed_requirement

    from dynamical.replay import replay_trace

    registry = load_capability_registry(FASTCAT_LAB / "registry.yaml")
    composition = compose_virtual_sdl(_model_backed_requirement(), registry)
    assert composition.status == "COMPILED", composition.reason_codes
    expected = composition.composition_sha256

    packs = {
        target: compile_facility(
            FASTCAT_LAB / "facility.yaml", target, tmp_path / target, composition_result=composition
        ).output_dir
        for target in ("openusd", "isaac")
    }
    for target, pack in packs.items():
        saved = json_module.loads((pack / "composition_result.json").read_text(encoding="utf-8"))
        assert saved["composition_sha256"] == expected, target
        graph = json_module.loads(
            (pack / "selected_capability_graph.json").read_text(encoding="utf-8")
        )
        assert graph["composition_sha256"] == expected, target

    contract = load_compiled_campaign_contract(packs["openusd"])
    trace_path = tmp_path / "trace.ndjson"
    events, _ = run_composed_campaign(contract, trace_path, seed=3)
    assert events[0].provenance["compiled_pack"]["composition_sha256"] == expected

    replay_path = tmp_path / "replay.ndjson"
    replay_trace(trace_path, replay_path)
    replay_start = json_module.loads(replay_path.read_text(encoding="utf-8").splitlines()[0])
    assert replay_start["provenance"]["compiled_pack"]["composition_sha256"] == expected

    for surface in (trace_path, replay_path):
        report = campaign_validate_path(surface)
        assert report["valid"] is True, report["validation_reasons"]


def test_tampered_sample_state_digest_fails_validation(tmp_path: Path):
    """Criterion 5 evidence: scientific state is continuous across every
    instrument action, verifiably in the trace itself. An observation whose
    recorded read-state digest does not match the last written state must
    fail validation with a typed reason."""

    import json as json_module

    from test_runtime_pack import FASTCAT_LAB, _model_backed_requirement

    registry = load_capability_registry(FASTCAT_LAB / "registry.yaml")
    composition = compose_virtual_sdl(_model_backed_requirement(), registry)
    assert composition.status == "COMPILED", composition.reason_codes
    compiled = compile_facility(
        FASTCAT_LAB / "facility.yaml",
        "openusd",
        tmp_path / "compiled",
        composition_result=composition,
    ).output_dir
    contract = load_compiled_campaign_contract(compiled)
    trace_path = tmp_path / "trace.ndjson"
    run_composed_campaign(contract, trace_path, seed=11)

    tampered_lines = []
    flipped = False
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        event = json_module.loads(line)
        provenance = event.get("provenance") or {}
        if (
            not flipped
            and event.get("event_type") == "observation"
            and provenance.get("sample_state_written") is False
            and provenance.get("sample_state_sha256")
        ):
            provenance["sample_state_sha256"] = "0" * 64
            flipped = True
        tampered_lines.append(json_module.dumps(event, sort_keys=True, separators=(",", ":")))
    assert flipped, "the coverage trace must contain at least one pure-read observation"
    tampered_path = tmp_path / "tampered.ndjson"
    tampered_path.write_text("\n".join(tampered_lines) + "\n", encoding="utf-8")

    result = campaign_validate_path(tampered_path)
    assert result["valid"] is False
    assert any(
        reason["code"] == "SAMPLE_STATE_DISCONTINUOUS" for reason in result["validation_reasons"]
    )


@pytest.mark.parametrize("current_a", [0.002827, -0.001, 0.0])
def test_non_source_deposition_current_holds_during_composition(current_a):
    result = compose_virtual_sdl(
        _coverage_requirement(current_a=current_a), load_capability_registry(REGISTRY)
    )
    assert result.status == "HOLD"
    assert any(
        reason.code == "VALUE_OUT_OF_RANGE"
        and reason.step_id == "deposit"
        and reason.provider_id == "ac-squidstat-simulator"
        for reason in result.reasons
    )


def test_well_cleaning_preserves_deposition_commands_and_existing_film():
    from dynamical.instruments import InstrumentRequest
    from dynamical.instruments.ac_cleaning import clean_electrode
    from dynamical.samples import Sample

    sample = Sample(
        id="well-1",
        station_id="ot2-liquid-handling",
        custody_state="held",
        quantity=0,
        unit="1",
        created_by_step_id="recorded-measurement",
        state={
            "deposited_mass_g": 0.002,
            "deposition_commanded_current_a": -0.002827,
            "deposition_precursor_commanded.Ni_ml": 0.5,
            "electrolyte_commanded.Ni_ml": 0.5,
        },
    )
    result = clean_electrode(
        InstrumentRequest(
            parameters={"use_acid": True, "acid_dwell_s": 0.1}, inputs={}, sample=sample
        )
    )
    assert result.reasons == []
    assert result.sample is not None
    assert result.sample.state == {
        key: value
        for key, value in sample.state.items()
        if not key.startswith("electrolyte_commanded.")
    }
    assert result.outputs["instrument.residual_volume_ml"] is None
    assert result.outputs["instrument.acid_commanded_ml"] == 0.5
