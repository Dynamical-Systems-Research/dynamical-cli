"""The trace must let an agent reconstruct what happened and why, without the runner."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dynamical import instruments
from dynamical.campaign import (
    CampaignValidationError,
    CompiledCampaignContract,
    _envelope_in_force,
    read_trace,
    run_composed_campaign,
    stable_hash,
)


def _capability(
    operation_id: str,
    parameters: list[dict[str, Any]],
    output_ports: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "id": operation_id,
        "parameters": parameters,
        "precondition_constraint_ids": [],
        "postcondition_constraint_ids": [],
        "output_ports": output_ports,
    }


def _constraint(
    channel_id: str, unit: str, minimum: float, maximum: float, *, constrained_parameter_name: str
) -> dict[str, Any]:
    return {
        "phase": "pre_action",
        "channel_id": channel_id,
        "operator": "between",
        "bound": {"minimum": minimum, "maximum": maximum},
        "unit": unit,
        "enforcement": "reject",
        "verifier_binding_id": "bounded-constraint-verifier",
        "constrained_parameter_name": constrained_parameter_name,
    }


def _coverage_contract() -> CompiledCampaignContract:
    """A hand-built multi-instrument contract that exercises the telemetry
    contract deterministically: it uses the real registered instrument models
    and declared parameter units with explicit test constraint bounds across a
    synthetic instrument sequence, built directly (as
    ``_deposit_ordering_contract``/``_transfer_contract`` in
    test_campaign_contract.py do) rather than through composition. The step
    order is harness-selected coverage, not a recommended experiment.

    The first transfer step declares a ``sample_id`` parameter so
    ``transfer-sample`` materializes a sample instead of erroring for lack of
    one; this is a fixture convenience for exercising the telemetry mechanism.
    """

    digest = stable_hash({"test": "telemetry-coverage"})

    dispense_capability = _capability(
        "dispense-electrolyte",
        [
            {
                "name": "volume_ml",
                "value_type": "number",
                "unit": "mL",
                "required": True,
                "minimum": 0.0,
                "maximum": 3.895,
            },
            {
                "name": "chemical",
                "value_type": "string",
                "unit": "1",
                "required": False,
            },
        ],
        [
            {"id": "volume_requested_ml", "unit": "mL"},
            {"id": "volume_commanded_ml", "unit": "mL"},
            {"id": "volume_applied_ml", "unit": "mL"},
        ],
    )
    transfer_capability = _capability(
        "transfer-sample",
        [
            {"name": "to_station", "value_type": "string", "unit": "1", "required": True},
            {"name": "sample_id", "value_type": "string", "unit": "1", "required": False},
        ],
        [
            {"id": "instrument.sample_station_id", "unit": "1"},
            {"id": "instrument.arrival_confirmed", "unit": "1"},
            {"id": "sample.state.transferred", "unit": "1"},
        ],
    )
    condition_capability = _capability(
        "condition-ultrasonic",
        [
            {
                "name": "duration_s",
                "value_type": "number",
                "unit": "s",
                "required": True,
                "enum": [5.0, 15.0, 30.0],
            },
            {
                "name": "temperature_setpoint_c",
                "value_type": "number",
                "unit": "degC",
                "required": True,
                "enum": [35.0],
            },
        ],
        [
            {"id": "instrument.ultrasound_commanded_s", "unit": "s"},
            {"id": "instrument.temperature_setpoint_c", "unit": "degC"},
            {"id": "instrument.temperature_observed_c", "unit": "degC"},
        ],
    )
    deposit_capability = _capability(
        "electrodeposit-constant-current",
        [
            {
                "name": "current_a",
                "value_type": "number",
                "unit": "A",
                "required": True,
                "minimum": -0.002827,
                "maximum": -0.002827,
            },
            {
                "name": "duration_s",
                "value_type": "number",
                "unit": "s",
                "required": True,
                "enum": [10.0, 60.0],
            },
            {
                "name": "temperature_setpoint_c",
                "value_type": "number",
                "unit": "degC",
                "required": True,
                "enum": [35.0],
            },
        ],
        [
            {"id": "commanded_charge_c", "unit": "C"},
            {"id": "deposited_mass_g", "unit": "g"},
            {"id": "deposited_thickness_um", "unit": "um"},
            {"id": "current_density_a_cm2", "unit": "A/cm^2"},
        ],
    )
    bindings = (
        {
            "step_id": "to-arduino",
            "operation_id": "transfer-sample",
            "provider_id": "ac-transfer-simulator",
            "evidence_class": "simulator",
            "endpoint_id": "ac-transfer-model",
            "parameters": [
                {"name": "to_station", "value": "arduino-conditioning"},
                {"name": "sample_id", "value": "sample-1"},
            ],
            "inputs": [],
            "capability_contract": transfer_capability,
            "duration": {"typical_s": 30.0},
            "policy": {"safety_limit_ids": []},
        },
        {
            "step_id": "dispense",
            "operation_id": "dispense-electrolyte",
            "provider_id": "ac-ot2-simulator",
            "evidence_class": "simulator",
            "endpoint_id": "ac-opentron-model",
            "sample_id": "sample-1",
            "parameters": [
                {"name": "volume_ml", "value": 3.8949},
                {"name": "chemical", "value": "Ni"},
            ],
            "inputs": [],
            "capability_contract": dispense_capability,
            "duration": {"typical_s": 12.5},
            "policy": {"safety_limit_ids": ["dispense-volume-envelope"]},
        },
        {
            "step_id": "condition",
            "operation_id": "condition-ultrasonic",
            "provider_id": "ac-arduino-simulator",
            "evidence_class": "simulator",
            "endpoint_id": "ac-arduino-model",
            "sample_id": "sample-1",
            "parameters": [
                {"name": "duration_s", "value": 30.0},
                {"name": "temperature_setpoint_c", "value": 35.0},
            ],
            "inputs": [],
            "capability_contract": condition_capability,
            "duration": {"typical_s": 60.0},
            "policy": {
                "safety_limit_ids": [
                    "conditioning-duration-envelope",
                    "conditioning-setpoint-envelope",
                ]
            },
        },
        {
            "step_id": "to-squidstat",
            "operation_id": "transfer-sample",
            "provider_id": "ac-transfer-simulator",
            "evidence_class": "simulator",
            "endpoint_id": "ac-transfer-model",
            "sample_id": "sample-1",
            "parameters": [{"name": "to_station", "value": "squidstat-echem"}],
            "inputs": [],
            "capability_contract": transfer_capability,
            "duration": {"typical_s": 30.0},
            "policy": {"safety_limit_ids": []},
        },
        {
            "step_id": "deposit",
            "operation_id": "electrodeposit-constant-current",
            "provider_id": "ac-squidstat-simulator",
            "evidence_class": "simulator",
            "endpoint_id": "ac-potentiostat-model",
            "sample_id": "sample-1",
            "parameters": [
                {"name": "current_a", "value": -0.002827},
                {"name": "duration_s", "value": 60.0},
                {"name": "temperature_setpoint_c", "value": 35.0},
            ],
            "inputs": [],
            "capability_contract": deposit_capability,
            "duration": {"typical_s": 600.0},
            "policy": {"safety_limit_ids": ["current-envelope", "deposition-duration-envelope"]},
        },
        {
            "step_id": "condition-after-deposit",
            "operation_id": "condition-ultrasonic",
            "provider_id": "ac-arduino-simulator",
            "evidence_class": "simulator",
            "endpoint_id": "ac-arduino-model",
            "sample_id": "sample-1",
            "parameters": [
                {"name": "duration_s", "value": 5.0},
                {"name": "temperature_setpoint_c", "value": 35.0},
            ],
            # Synthetic numeric wiring exercises producer/consumer telemetry;
            # the conditioning model does not consume this input as a control.
            "inputs": [
                {
                    "target_port_id": "material.current_density_a_cm2",
                    "target_unit": "A/cm^2",
                    "source_kind": "step_output",
                    "source_id": "deposit",
                    "source_port_id": "current_density_a_cm2",
                }
            ],
            "capability_contract": condition_capability,
            "duration": {"typical_s": 120.0},
            "policy": {"safety_limit_ids": ["conditioning-duration-envelope"]},
        },
    )

    capabilities = {
        "dispense-electrolyte": {**dispense_capability, "provider_id": "ac-opentron-model"},
        "transfer-sample": {**transfer_capability, "provider_id": "ac-transfer-model"},
        "condition-ultrasonic": {**condition_capability, "provider_id": "ac-arduino-model"},
        "electrodeposit-constant-current": {
            **deposit_capability,
            "provider_id": "ac-potentiostat-model",
        },
    }
    channel_units = {
        "volume_requested_ml": "mL",
        "volume_commanded_ml": "mL",
        "volume_applied_ml": "mL",
        "instrument.sample_station_id": "1",
        "instrument.arrival_confirmed": "1",
        "sample.state.transferred": "1",
        "instrument.ultrasound_commanded_s": "s",
        "instrument.temperature_setpoint_c": "degC",
        "instrument.temperature_observed_c": "degC",
        "commanded_charge_c": "C",
        "deposited_mass_g": "g",
        "deposited_thickness_um": "um",
        "current_density_a_cm2": "A/cm^2",
    }
    constraint_by_id = {
        "dispense-volume-envelope": _constraint(
            "ot2.dispense_volume_requested_ml",
            "mL",
            0.0,
            3.895,
            constrained_parameter_name="volume_ml",
        ),
        "conditioning-duration-envelope": _constraint(
            "arduino.conditioning_duration_s",
            "s",
            0.0,
            30.0,
            constrained_parameter_name="duration_s",
        ),
        "conditioning-setpoint-envelope": _constraint(
            "arduino.temperature_setpoint_c",
            "degC",
            35.0,
            35.0,
            constrained_parameter_name="temperature_setpoint_c",
        ),
        "current-envelope": _constraint(
            "squidstat.current_a", "A", -0.002827, -0.002827, constrained_parameter_name="current_a"
        ),
        "deposition-duration-envelope": _constraint(
            "squidstat.duration_s", "s", 10.0, 60.0, constrained_parameter_name="duration_s"
        ),
    }

    return CompiledCampaignContract(
        target="openusd",
        manifest_sha256=digest,
        core_ir_sha256=digest,
        world_sha256=digest,
        adapter_pack_sha256=digest,
        facility_ir_sha256=digest,
        action_schema_sha256=digest,
        observation_schema_sha256=digest,
        action_kinds=frozenset(capabilities),
        observation_channels=frozenset(channel_units),
        channel_units=channel_units,
        capability_by_action=capabilities,
        constraint_by_id=constraint_by_id,
        composition_sha256=digest,
        operation_bindings=bindings,
    )


def _same_unit_contract(
    parameter_name: str,
    requested: float,
    observation_name: str,
) -> tuple[CompiledCampaignContract, str, str]:
    operation_id = f"test-{parameter_name.replace('_', '-')}"
    provider_id = f"{operation_id}-provider"
    digest = stable_hash(
        {
            "test": "same-unit-command-provenance",
            "parameter": parameter_name,
            "observation": observation_name,
        }
    )
    capability = _capability(
        operation_id,
        [
            {
                "name": parameter_name,
                "value_type": "number",
                "unit": "K",
                "required": True,
            }
        ],
        [{"id": observation_name, "unit": "K"}],
    )
    binding = {
        "step_id": operation_id,
        "operation_id": operation_id,
        "provider_id": provider_id,
        "evidence_class": "simulator",
        "endpoint_id": f"{operation_id}-model",
        "parameters": [{"name": parameter_name, "value": requested}],
        "inputs": [],
        "capability_contract": capability,
        "duration": {"typical_s": 1.0},
        "policy": {"safety_limit_ids": []},
    }
    contract = CompiledCampaignContract(
        target="openusd",
        manifest_sha256=digest,
        core_ir_sha256=digest,
        world_sha256=digest,
        adapter_pack_sha256=digest,
        facility_ir_sha256=digest,
        action_schema_sha256=digest,
        observation_schema_sha256=digest,
        action_kinds=frozenset({operation_id}),
        observation_channels=frozenset({observation_name}),
        channel_units={observation_name: "K"},
        capability_by_action={operation_id: {**capability, "provider_id": provider_id}},
        constraint_by_id={},
        composition_sha256=digest,
        operation_bindings=(binding,),
    )
    return contract, operation_id, provider_id


@pytest.fixture
def completed_trace_path(tmp_path: Path) -> Path:
    output = tmp_path / "telemetry-run.ndjson"
    events, _ = run_composed_campaign(_coverage_contract(), output, seed=11)
    assert events[-1].provenance["execution_status"] == "passed", events[-1].provenance["reasons"]
    return output


def test_dataflow_edges_are_recoverable_from_the_trace(completed_trace_path):
    events = read_trace(completed_trace_path)
    edges = events[0].provenance["dataflow_edges"]
    assert edges, "step-to-step wiring must survive the run"
    for edge in edges:
        assert {"from_step", "from_port", "to_step", "to_port"} <= set(edge)


def test_requested_and_applied_parameters_are_distinguishable(completed_trace_path):
    events = read_trace(completed_trace_path)
    actions = [e.action for e in events if e.action is not None]
    dispense = next(a for a in actions if a.kind == "dispense-electrolyte")
    assert dispense.parameters["volume_ml"]["requested"] == 3.8949
    assert dispense.parameters["volume_ml"]["applied"] is None
    frame = next(
        e.observation
        for e in events
        if e.observation and e.observation.frame_id == "frame-after-dispense"
    )
    values = {channel.name: channel.value for channel in frame.channels}
    assert values == {
        "volume_requested_ml": 3.8949,
        "volume_commanded_ml": 3.894,
        "volume_applied_ml": None,
    }


@pytest.mark.parametrize(
    ("parameter_name", "requested", "observation_name", "observed"),
    [
        ("preheat_k", 353.0, "peak_temperature_k", 1840.6339285714287),
        ("noise_sigma_k", 0.0, "apparent_temperature_k", 1712.9028777567585),
    ],
)
def test_same_unit_observations_do_not_supply_applied_parameters(
    monkeypatch,
    tmp_path,
    parameter_name,
    requested,
    observation_name,
    observed,
):
    contract, operation_id, provider_id = _same_unit_contract(
        parameter_name,
        requested,
        observation_name,
    )

    def observation_model(request):
        assert request.parameters[parameter_name] == requested
        return instruments.InstrumentResult(
            outputs={observation_name: observed},
            uncertainty={observation_name: 1.0},
            cost_usd=0.0,
            duration_s=1.0,
        )

    monkeypatch.setitem(
        instruments._MODELS,
        (operation_id, provider_id),
        observation_model,
    )
    events, _ = run_composed_campaign(
        contract,
        tmp_path / f"{parameter_name}.ndjson",
        seed=11,
    )

    action = next(event.action for event in events if event.action is not None)
    assert action.parameters[parameter_name] == {
        "requested": requested,
        "applied": requested,
    }
    observation = next(event.observation for event in events if event.observation is not None)
    channels = {channel.name: channel.value for channel in observation.channels}
    assert channels[observation_name] == observed
    assert observation_name not in action.parameters


def test_unknown_explicit_applied_parameter_fails_before_trace_creation(
    monkeypatch,
    tmp_path,
):
    model = instruments.resolve("dispense-electrolyte", "ac-ot2-simulator")
    assert model is not None

    def reports_unknown_applied_parameter(request):
        result = model(request)
        return instruments.InstrumentResult(
            outputs=result.outputs,
            uncertainty=result.uncertainty,
            cost_usd=result.cost_usd,
            duration_s=result.duration_s,
            reasons=result.reasons,
            sample=result.sample,
            applied_parameters={"not_commanded": 1.0},
        )

    monkeypatch.setitem(
        instruments._MODELS,
        ("dispense-electrolyte", "ac-ot2-simulator"),
        reports_unknown_applied_parameter,
    )
    output = tmp_path / "unknown-applied.ndjson"
    with pytest.raises(CampaignValidationError, match="not commanded.*not_commanded"):
        run_composed_campaign(_coverage_contract(), output, seed=11)
    assert not output.exists()


def test_constraints_record_their_margin(completed_trace_path):
    events = read_trace(completed_trace_path)
    evaluations = [c for e in events for c in e.constraints]
    assert evaluations
    assert all(c.margin is not None for c in evaluations)


def test_observations_carry_typed_uncertainty(completed_trace_path):
    events = read_trace(completed_trace_path)
    frames = [e.observation for e in events if e.observation is not None]
    reported = [
        channel
        for frame in frames
        for channel in frame.channels
        if channel.uncertainty["value"] is not None
    ]
    physical_unknowns = {
        "volume_applied_ml",
        "instrument.temperature_observed_c",
        "deposited_mass_g",
        "deposited_thickness_um",
    }
    unknown_channels = [
        channel
        for frame in frames
        for channel in frame.channels
        if channel.name in physical_unknowns
    ]
    assert {channel.name for channel in unknown_channels} == physical_unknowns
    for channel in unknown_channels:
        assert channel.value is None
        assert channel.quality == "unavailable"
        assert channel.uncertainty["value"] is None
        assert channel.uncertainty["origin"]
    for channel in reported:
        assert channel.uncertainty["value"] >= 0.0
        assert channel.uncertainty["kind"] in {"declared", "propagated", "measured"}
        assert channel.uncertainty["origin"]


def test_envelope_in_force_is_recorded(completed_trace_path):
    events = read_trace(completed_trace_path)
    actions = [e for e in events if e.action is not None]
    assert actions[0].provenance["envelope_in_force"]
    condition = next(event for event in actions if event.action.action_id == "condition")
    assert condition.provenance["envelope_in_force"]["duration_s"]["enum"] == [5.0, 15.0, 30.0]
    assert condition.provenance["envelope_in_force"]["temperature_setpoint_c"]["enum"] == [35.0]
    dispense = next(event for event in actions if event.action.action_id == "dispense")
    assert dispense.provenance["envelope_in_force"]["volume_ml"] == {
        "unit": "mL",
        "minimum": 0.0,
        "maximum": 3.895,
    }


def test_fixed_protocol_selector_is_preserved_in_envelope_receipt():
    envelope = _envelope_in_force(
        {
            "parameters": [
                {
                    "name": "protocol_id",
                    "value_type": "string",
                    "unit": "1",
                    "enum": ["fixture-fixed-protocol"],
                }
            ]
        }
    )
    assert envelope["protocol_id"] == {
        "unit": "1",
        "minimum": None,
        "maximum": None,
        "enum": ["fixture-fixed-protocol"],
    }


def test_consumed_cost_and_duration_are_simulator_bookkeeping(completed_trace_path):
    events = read_trace(completed_trace_path)
    end = events[-1].provenance
    # These totals describe model execution, not measured physical cost or time.
    assert end["cost_consumed_usd"] == 0.0
    assert end["duration_consumed_s"] == 0.0
