from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
from _fixtures import write_reference_requirement

from dynamical.campaign import (
    ActionKind,
    ActionRequest,
    CampaignIdentity,
    CampaignValidationError,
    CompiledCampaignContract,
    EvidenceClass,
    ObservationChannel,
    ObservationFrame,
    ObservationOrigin,
    evaluate_action_constraints,
    load_compiled_campaign_contract,
    read_trace,
    run_cli,
    run_composed_campaign,
    stable_hash,
)
from dynamical.compiler import compile_facility
from dynamical.composition import compose_files

REPOSITORY = Path(__file__).resolve().parents[1]
HEATER_MANIFEST = REPOSITORY / "manifests" / "matterix-heater-workstation.yaml"


def _identity() -> CampaignIdentity:
    digest = stable_hash({"test": "campaign"})
    return CampaignIdentity(
        campaign_id="campaign-test",
        run_id="run-test",
        seed=7,
        backend_revision="source_bounded_python_process_model:not_embodied",
        ir_hash=digest,
        world_hash=digest,
        campaign_hash=digest,
        provenance={"embodied_backend": False, "w1_evidence": False},
    )


def _thermal_contract(agitation_rate: float) -> CompiledCampaignContract:
    digest = stable_hash({"test": "composed-thermal"})
    agitate_outputs = (
        ("instrument.agitation_rate_rpm", "rpm"),
        ("simulator.uncertainty.agitation_rate_rpm", "rpm"),
    )
    thermal_outputs = (
        ("instrument.heat_input_W", "W"),
        ("instrument.heat_flow_to_sample_W", "W"),
        ("simulator.uncertainty.heat_input_W", "W"),
        ("simulator.uncertainty.heat_flow_to_sample_W", "W"),
        ("thermal.plate_temperature_K", "K"),
        ("thermal.sample_wall_temperature_K", "K"),
        ("thermal.sample_core_temperature_K", "K"),
        ("thermal.sample_temperature_K", "K"),
        ("thermal.reaction_progress_estimate", "1"),
        ("thermal.time_above_reference_temperature_s", "s"),
    )

    def capability(
        operation_id: str,
        outputs: tuple[tuple[str, str], ...],
        parameters: list[dict[str, object]],
    ) -> dict[str, object]:
        return {
            "id": operation_id,
            "parameters": parameters,
            "precondition_constraint_ids": [],
            "postcondition_constraint_ids": [],
            "output_ports": [{"id": name, "unit": unit} for name, unit in outputs],
        }

    agitate_capability = capability(
        "agitate-sample",
        agitate_outputs,
        [
            {
                "name": "agitation-rate",
                "value_type": "number",
                "unit": "rpm",
                "required": True,
                "minimum": 0.0,
                "maximum": 600.0,
            }
        ],
    )
    thermal_capability = capability(
        "apply-thermal-program",
        thermal_outputs,
        [
            {
                "name": "target-temperature",
                "value_type": "number",
                "unit": "K",
                "required": True,
                "minimum": 303.15,
                "maximum": 343.15,
            },
            {
                "name": "dwell-time",
                "value_type": "number",
                "unit": "s",
                "required": True,
                "minimum": 0.0,
                "maximum": 1010.004,
            },
        ],
    )
    bindings = (
        {
            "step_id": "agitate",
            "operation_id": "agitate-sample",
            "provider_id": "thermal-agitation-simulator",
            "evidence_class": "simulator",
            "endpoint_id": "agitation-model",
            "parameters": [{"name": "agitation-rate", "value": agitation_rate}],
            "inputs": [
                {
                    "target_port_id": "material.mass_kg",
                    "target_unit": "kg",
                    "source_kind": "campaign_input",
                    "value": 0.25,
                }
            ],
            "capability_contract": agitate_capability,
            "duration": {"typical_s": 0.0},
            "policy": {"safety_limit_ids": []},
        },
        {
            "step_id": "heat",
            "operation_id": "apply-thermal-program",
            "provider_id": "thermal-program-simulator",
            "evidence_class": "simulator",
            "endpoint_id": "thermal-model",
            "parameters": [
                {"name": "dwell-time", "value": 300.0},
                {"name": "target-temperature", "value": 323.15},
            ],
            "inputs": [
                {
                    "target_port_id": "material.mass_kg",
                    "target_unit": "kg",
                    "source_kind": "campaign_input",
                    "value": 0.25,
                },
                {
                    "target_port_id": "material.temperature_K",
                    "target_unit": "K",
                    "source_kind": "campaign_input",
                    "value": 298.15,
                },
                {
                    "target_port_id": "instrument.agitation_rate_rpm",
                    "target_unit": "rpm",
                    "source_kind": "step_output",
                    "source_id": "agitate",
                    "source_port_id": "instrument.agitation_rate_rpm",
                },
            ],
            "capability_contract": thermal_capability,
            "duration": {"typical_s": 300.0},
            "policy": {"safety_limit_ids": []},
        },
    )
    capabilities = {
        ActionKind.AGITATE_SAMPLE: {
            **agitate_capability,
            "provider_id": "agitation-model",
        },
        ActionKind.APPLY_THERMAL_PROGRAM: {
            **thermal_capability,
            "provider_id": "thermal-model",
        },
    }
    return CompiledCampaignContract(
        target="matterix",
        manifest_sha256=digest,
        core_ir_sha256=digest,
        world_sha256=digest,
        adapter_pack_sha256=digest,
        facility_ir_sha256=digest,
        action_schema_sha256=digest,
        observation_schema_sha256=digest,
        action_kinds=frozenset(capabilities),
        observation_channels=frozenset(name for name, _ in (*agitate_outputs, *thermal_outputs)),
        channel_units=dict((*agitate_outputs, *thermal_outputs)),
        capability_by_action=capabilities,
        constraint_by_id={},
        composition_sha256=digest,
        operation_bindings=bindings,
    )


def test_composed_runtime_executes_authored_order_and_prior_outputs(tmp_path: Path) -> None:
    still, _ = run_composed_campaign(
        _thermal_contract(0.0),
        tmp_path / "still.ndjson",
        seed=3,
    )
    mixed, _ = run_composed_campaign(
        _thermal_contract(600.0),
        tmp_path / "mixed.ndjson",
        seed=3,
    )

    assert [event.action.kind for event in mixed if event.action is not None] == [
        ActionKind.AGITATE_SAMPLE,
        ActionKind.APPLY_THERMAL_PROGRAM,
    ]
    still_heat = next(
        event.observation
        for event in still
        if event.observation is not None and event.observation.frame_id == "frame-after-heat"
    )
    mixed_heat = next(
        event.observation
        for event in mixed
        if event.observation is not None and event.observation.frame_id == "frame-after-heat"
    )
    still_values = {channel.name: channel.value for channel in still_heat.channels}
    mixed_values = {channel.name: channel.value for channel in mixed_heat.channels}
    assert (
        mixed_values["thermal.sample_core_temperature_K"]
        > (still_values["thermal.sample_core_temperature_K"])
    )


def test_independent_verifier_rejects_out_of_range_heater_action() -> None:
    request = ActionRequest(
        action_id="a-test",
        kind=ActionKind.SET_HEATER,
        actor_id="heater",
        provider_id="figure5-source-condition-simulator",
        evidence_class=EvidenceClass.SIMULATOR,
        parameters={"enabled": True, "setpoint_c": 71.0},
    )

    declarations = {
        "setpoint": {
            "phase": "pre_action",
            "channel_id": "heater/setpoint",
            "operator": "between",
            "bound": {"minimum": 30.0, "maximum": 70.0},
            "unit": "degC",
            "enforcement": "reject",
            "verifier_binding_id": "independent-verifier",
        }
    }
    evaluations = evaluate_action_constraints(
        request,
        declared_constraints=declarations,
        constraint_ids=("setpoint",),
        measured_channels={"heater/setpoint": (71.0, "degC")},
    )

    assert len(evaluations) == 1
    assert evaluations[0].passed is False
    assert evaluations[0].verifier == "independent-verifier"


def test_observation_rejects_duplicate_channels() -> None:
    channel = ObservationChannel(
        name="process/progress",
        value=0.2,
        unit="mol/kg",
        quality="valid",
        origin=ObservationOrigin.SOURCE_MODEL,
        provider_id="figure5-source-condition-simulator",
        evidence_class=EvidenceClass.SIMULATOR,
    )

    with pytest.raises(CampaignValidationError, match="unique"):
        ObservationFrame(
            frame_id="duplicate",
            logical_time_s=0.0,
            provider_id="figure5-source-condition-simulator",
            evidence_class=EvidenceClass.SIMULATOR,
            channels=(channel, channel),
        )


def test_simulate_rejects_missing_compiled_world(tmp_path: Path) -> None:
    args = argparse.Namespace(
        input=tmp_path / "missing-compiled-world",
        mode="simulate",
        output=tmp_path / "run.ndjson",
        seed=0,
    )

    with pytest.raises(CampaignValidationError, match="path is absent"):
        run_cli(args)


def test_simulate_rejects_invalid_compiled_pack(tmp_path: Path) -> None:
    compiled = compile_facility(HEATER_MANIFEST, "openusd", tmp_path / "compiled").output_dir
    action_schema_path = compiled / "action_schema.json"
    action_schema = json.loads(action_schema_path.read_text(encoding="utf-8"))
    action_schema["x-dynamical-declared-capability-action-types"] = ["wait"]
    action_schema_path.write_text(json.dumps(action_schema), encoding="utf-8")

    with pytest.raises(CampaignValidationError, match="validation failed"):
        load_compiled_campaign_contract(compiled)


def test_compiled_trace_actions_and_channels_are_declared_subsets(tmp_path: Path) -> None:
    requirement = write_reference_requirement(tmp_path / "requirement.yaml")
    composition = compose_files(
        requirement,
        REPOSITORY / "registries" / "reference-capabilities.yaml",
        HEATER_MANIFEST,
    )
    compiled = compile_facility(
        HEATER_MANIFEST,
        "openusd",
        tmp_path / "compiled",
        composition_result=composition,
    ).output_dir
    output = tmp_path / "bound.ndjson"
    args = argparse.Namespace(input=compiled, mode="simulate", output=output, seed=19)

    assert run_cli(args) == 0
    events = read_trace(output)
    observation_schema = json.loads(
        (compiled / "observation_schema.json").read_text(encoding="utf-8")
    )
    manifest = json.loads((compiled / "compile_manifest.json").read_text(encoding="utf-8"))
    declared_channels = set(observation_schema["x-dynamical-declared-channel-ids"])
    emitted_actions = [event.action for event in events if event.action]
    emitted_channels = {
        channel.name
        for event in events
        if event.observation
        for channel in event.observation.channels
    }

    assert composition.virtual_sdl is not None
    selected_bindings = {
        binding.operation_id: binding for binding in composition.virtual_sdl.operation_bindings
    }
    assert {action.kind.value for action in emitted_actions} == set(selected_bindings)
    assert emitted_channels <= declared_channels
    assert all(
        action.actor_id == selected_bindings[action.kind.value].endpoint_id
        and action.provider_id == selected_bindings[action.kind.value].provider_id
        and action.evidence_class.value == selected_bindings[action.kind.value].evidence_class
        for action in emitted_actions
    )
    assert all(
        set(action.parameters)
        == {item.name for item in selected_bindings[action.kind.value].parameters}
        for action in emitted_actions
    )
    assert events[0].ir_hash == manifest["core_ir_sha256"]
    assert events[0].world_hash == manifest["world_sha256"]
    assert events[0].provenance["compiled_contract_bound"] is True
    assert events[0].provenance["embodied_backend"] is False
    assert events[0].provenance["w1_evidence"] is False


def test_constraint_verifier_rejects_unit_mismatch() -> None:
    action = ActionRequest(
        action_id="unit-test",
        kind=ActionKind.WAIT,
        actor_id="heater",
        provider_id="figure5-source-condition-simulator",
        evidence_class=EvidenceClass.SIMULATOR,
        parameters={"duration_s": 1.0},
    )
    declarations = {
        "dwell": {
            "phase": "pre_action",
            "channel_id": "dwell",
            "operator": "between",
            "bound": {"minimum": 0.0, "maximum": 10.0},
            "unit": "s",
            "enforcement": "reject",
            "verifier_binding_id": "verifier",
        }
    }

    with pytest.raises(CampaignValidationError, match="unit mismatch"):
        evaluate_action_constraints(
            action,
            declared_constraints=declarations,
            constraint_ids=("dwell",),
            measured_channels={"dwell": (1.0, "mol/kg")},
        )
