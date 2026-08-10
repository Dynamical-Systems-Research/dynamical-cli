"""The trace must let an agent reconstruct what happened and why, without the runner."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dynamical.campaign import (
    CompiledCampaignContract,
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


def _six_step_contract() -> CompiledCampaignContract:
    """A hand-built contract mirroring the real AC electrodeposition six-step
    campaign (``registries/electrodeposition-capabilities.yaml`` +
    ``manifests/ac-electrodeposition-cell.yaml``): dispense -> transfer ->
    condition -> transfer -> deposit -> measure, using the real registered
    instrument models and the real declared parameter units and constraint
    bounds. Built directly (as ``_deposit_ordering_contract``/``_transfer_contract`` in
    test_campaign_contract.py do) rather than through composition, so the
    telemetry contract can be exercised deterministically and completely.

    One addition beyond the real registry: the first transfer step declares a
    ``sample_id`` parameter so ``transfer-sample`` materializes a sample
    instead of erroring for lack of one. The real six-step
    CampaignRequirement (tests/test_electrodeposition_registry.py) does not
    supply this -- see the Task 13 report for why that stops the actual
    acceptance campaign at the first transfer, a separate, already-existing
    gap this fixture works around only for telemetry-contract testing.
    """

    digest = stable_hash({"test": "telemetry-six-step"})

    dispense_capability = _capability(
        "dispense-electrolyte",
        [
            {
                "name": "volume_ml",
                "value_type": "number",
                "unit": "mL",
                "required": True,
                "minimum": 0.0,
                "maximum": 25.0,
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
                "minimum": 0.0,
                "maximum": 1800.0,
            },
            {
                "name": "setpoint_percent",
                "value_type": "number",
                "unit": "%",
                "required": False,
                "minimum": 0.0,
                "maximum": 100.0,
            },
        ],
        [
            {"id": "instrument.conditioning_duration_s", "unit": "s"},
            {"id": "instrument.conditioning_setpoint_percent", "unit": "%"},
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
                "minimum": 0.0,
                "maximum": 0.010,
            },
            {
                "name": "duration_s",
                "value_type": "number",
                "unit": "s",
                "required": True,
                "minimum": 0.0,
                "maximum": 3600.0,
            },
        ],
        [
            {"id": "charge_c", "unit": "C"},
            {"id": "deposited_mass_g", "unit": "g"},
            {"id": "deposited_thickness_um", "unit": "um"},
            {"id": "current_density_a_cm2", "unit": "A/cm^2"},
        ],
    )
    measure_capability = _capability(
        "measure-oer",
        [
            {
                "name": "current_density_a_cm2",
                "value_type": "number",
                "unit": "A/cm^2",
                "required": True,
                "minimum": 0.0001,
                "maximum": 0.10,
            }
        ],
        [
            {"id": "overpotential_v", "unit": "V"},
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
                {"name": "volume_ml", "value": 3.895},
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
                {"name": "duration_s", "value": 60.0},
                {"name": "setpoint_percent", "value": 80.0},
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
                {"name": "current_a", "value": 0.002827},
                {"name": "duration_s", "value": 600.0},
            ],
            "inputs": [],
            "capability_contract": deposit_capability,
            "duration": {"typical_s": 600.0},
            "policy": {"safety_limit_ids": ["current-envelope", "deposition-duration-envelope"]},
        },
        {
            "step_id": "measure",
            "operation_id": "measure-oer",
            "provider_id": "ac-oer-simulator",
            "evidence_class": "simulator",
            "endpoint_id": "ac-oer-model",
            "sample_id": "sample-1",
            "parameters": [{"name": "current_density_a_cm2", "value": 0.010}],
            # A synthetic step_output edge (deposit's own echoed current density
            # feeding measure's) so the dataflow-edges test exercises a real
            # producer/consumer pair. The real registry declares no input_ports
            # for either operation; this is illustrative wiring for the
            # telemetry mechanism, not a claim about the real registry's data.
            "inputs": [
                {
                    "target_port_id": "material.current_density_a_cm2",
                    "target_unit": "A/cm^2",
                    "source_kind": "step_output",
                    "source_id": "deposit",
                    "source_port_id": "current_density_a_cm2",
                }
            ],
            "capability_contract": measure_capability,
            "duration": {"typical_s": 120.0},
            "policy": {"safety_limit_ids": ["oer-current-density-envelope"]},
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
        "measure-oer": {**measure_capability, "provider_id": "ac-oer-model"},
    }
    channel_units = {
        "volume_requested_ml": "mL",
        "volume_applied_ml": "mL",
        "instrument.sample_station_id": "1",
        "instrument.arrival_confirmed": "1",
        "sample.state.transferred": "1",
        "instrument.conditioning_duration_s": "s",
        "instrument.conditioning_setpoint_percent": "%",
        "charge_c": "C",
        "deposited_mass_g": "g",
        "deposited_thickness_um": "um",
        "current_density_a_cm2": "A/cm^2",
        "overpotential_v": "V",
    }
    constraint_by_id = {
        "dispense-volume-envelope": _constraint(
            "ot2.dispense_volume_requested_ml",
            "mL",
            0.0,
            25.0,
            constrained_parameter_name="volume_ml",
        ),
        "conditioning-duration-envelope": _constraint(
            "arduino.conditioning_duration_s",
            "s",
            0.0,
            1800.0,
            constrained_parameter_name="duration_s",
        ),
        "conditioning-setpoint-envelope": _constraint(
            "arduino.conditioning_setpoint_percent",
            "%",
            0.0,
            100.0,
            constrained_parameter_name="setpoint_percent",
        ),
        "current-envelope": _constraint(
            "squidstat.current_a", "A", 0.0, 0.010, constrained_parameter_name="current_a"
        ),
        "deposition-duration-envelope": _constraint(
            "squidstat.duration_s", "s", 0.0, 3600.0, constrained_parameter_name="duration_s"
        ),
        "oer-current-density-envelope": _constraint(
            "squidstat.current_density_a_cm2",
            "A/cm^2",
            0.0001,
            0.10,
            constrained_parameter_name="current_density_a_cm2",
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


@pytest.fixture
def completed_trace_path(tmp_path: Path) -> Path:
    output = tmp_path / "telemetry-run.ndjson"
    events, _ = run_composed_campaign(_six_step_contract(), output, seed=11)
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
    assert dispense.parameters["volume_ml"]["requested"] is not None
    assert dispense.parameters["volume_ml"]["applied"] is not None


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
    assert reported, "at least one channel must report a numeric uncertainty"
    for channel in reported:
        assert channel.uncertainty["value"] >= 0.0
        assert channel.uncertainty["kind"] in {"declared", "propagated", "measured"}
        assert channel.uncertainty["origin"]


def test_envelope_in_force_is_recorded(completed_trace_path):
    events = read_trace(completed_trace_path)
    actions = [e for e in events if e.action is not None]
    assert actions[0].provenance["envelope_in_force"]


def test_consumed_cost_and_duration_are_actuals(completed_trace_path):
    events = read_trace(completed_trace_path)
    end = events[-1].provenance
    assert end["cost_consumed_usd"] >= 0.0
    assert end["duration_consumed_s"] > 0.0
