"""SDL1 source-protocol fidelity and refusal of unsupported physical evidence."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest
from _fixtures import REFERENCE_LAB, REFERENCE_REQUIREMENT

from dynamical import instruments
from dynamical.campaign import (
    load_compiled_campaign_contract,
    run_composed_campaign,
    validate_events,
)
from dynamical.compiler import compile_facility
from dynamical.composition import compose_virtual_sdl
from dynamical.instruments import InstrumentRequest, ac_sdl1_oer
from dynamical.schema import CampaignRequirement, load_capability_registry


def _request(**parameters):
    return InstrumentRequest(parameters=parameters, inputs={}, sample=None)


def test_protocol_reports_no_response_or_applied_physical_settings():
    result = ac_sdl1_oer.measure_oer(_request(protocol_id=ac_sdl1_oer.PROTOCOL_ID))
    assert result.outputs == {
        "potential_at_10ma_cm2_v": None,
        "corrected_potential_at_10ma_cm2_v": None,
        "ohmic_resistance_ohm": None,
        "overpotential_v": None,
        "current_density_a_cm2": 0.010,
    }
    assert result.applied_parameters == {"protocol_id": None}
    assert result.uncertainty == {}
    assert result.duration_s == 0
    assert {reason.code for reason in result.reasons} == {
        "PROTOCOL_RESPONSE_UNAVAILABLE",
        "REFERENCE_SCALE_UNVERIFIED",
    }


@pytest.mark.parametrize(
    "parameters",
    [
        {"protocol_id": "other"},
        {"protocol_id": ac_sdl1_oer.PROTOCOL_ID, "ohmic_resistance_ohm": 1.0},
        {"protocol_id": ac_sdl1_oer.PROTOCOL_ID, "current_density_a_cm2": 0.020},
    ],
)
def test_caller_cannot_replace_source_protocol_or_supply_unprovenanced_response(parameters):
    result = ac_sdl1_oer.measure_oer(_request(**parameters))
    assert all(value is None for value in result.outputs.values())
    assert [reason.code for reason in result.reasons] == ["PARAMETER_OUT_OF_ENVELOPE"]


def test_caller_response_input_is_not_accepted_as_observation():
    request = InstrumentRequest(
        parameters={"protocol_id": ac_sdl1_oer.PROTOCOL_ID},
        inputs={"ohmic_resistance_ohm": 1.0},
        sample=None,
    )
    result = ac_sdl1_oer.measure_oer(request)
    assert all(value is None for value in result.outputs.values())
    assert result.reasons[0].code == "PARAMETER_OUT_OF_ENVELOPE"


def test_changed_source_profile_fails_closed(monkeypatch):
    original = ac_sdl1_oer._protocol_bytes()
    assert hashlib.sha256(original).hexdigest() == ac_sdl1_oer.PROTOCOL_SHA256
    monkeypatch.setattr(ac_sdl1_oer, "_protocol_bytes", lambda: original + b" ")
    result = ac_sdl1_oer.measure_oer(_request(protocol_id=ac_sdl1_oer.PROTOCOL_ID))
    assert all(value is None for value in result.outputs.values())
    assert [reason.code for reason in result.reasons] == ["PROTOCOL_PROFILE_UNVERIFIED"]


def test_missing_source_profile_fails_closed(monkeypatch):
    def missing():
        raise FileNotFoundError("source protocol is missing")

    monkeypatch.setattr(ac_sdl1_oer, "_protocol_bytes", missing)
    result = ac_sdl1_oer.measure_oer(_request(protocol_id=ac_sdl1_oer.PROTOCOL_ID))
    assert all(value is None for value in result.outputs.values())
    assert result.reasons[0].code == "PROTOCOL_PROFILE_UNVERIFIED"


def test_ampere_alias_preserves_historical_response_implementation():
    assert instruments.resolve("estimate-oer", "ac-oer-simulator") is instruments.resolve(
        "measure-oer", "ac-oer-simulator"
    )
    assert instruments.resolve("measure-oer", "ac-sdl1-oer-protocol") is ac_sdl1_oer.measure_oer


def test_protocol_campaign_cannot_satisfy_measured_potential_proof(tmp_path: Path):
    raw = copy.deepcopy(REFERENCE_REQUIREMENT)
    proof = raw["objective"]["proof_requirements"][0]
    proof.update(operation_id="measure-oer", output_port_ids=["corrected_potential_at_10ma_cm2_v"])
    step = raw["steps"][0]
    step.update(
        operation_id="measure-oer",
        step_id="measure",
        parameters=[
            {
                "name": "protocol_id",
                "value_type": "string",
                "unit": "1",
                "value": ac_sdl1_oer.PROTOCOL_ID,
            }
        ],
    )
    requirement = CampaignRequirement.model_validate(raw)
    registry = load_capability_registry(REFERENCE_LAB / "registry.yaml")
    composition = compose_virtual_sdl(requirement, registry)
    assert composition.status == "COMPILED", composition.reason_codes
    compiled = compile_facility(
        REFERENCE_LAB / "facility.yaml",
        "openusd",
        tmp_path / "compiled",
        composition_result=composition,
    ).output_dir
    events, _ = run_composed_campaign(
        load_compiled_campaign_contract(compiled),
        tmp_path / "trace.ndjson",
        seed=11,
    )
    action_event = next(event for event in events if event.action is not None)
    assert action_event.provenance["envelope_in_force"]["protocol_id"]["enum"] == [
        ac_sdl1_oer.PROTOCOL_ID
    ]
    result = validate_events(events)
    assert result["valid"] is False
    assert result["execution_status"] == "failed"
    codes = {reason["code"] for reason in result["validation_reasons"]}
    assert {"PROTOCOL_RESPONSE_UNAVAILABLE", "REFERENCE_SCALE_UNVERIFIED"} <= codes
    assert "PARAMETER_OUT_OF_ENVELOPE" not in codes
    assert any(
        reason["code"] == "PROOF_OUTPUT_UNAVAILABLE" for reason in result["validation_reasons"]
    )
    frames = [event.observation for event in events if event.observation is not None]
    headline = next(
        channel
        for frame in frames
        for channel in frame.channels
        if channel.name == "corrected_potential_at_10ma_cm2_v"
    )
    assert headline.value is None
    assert headline.quality == "unavailable"


def test_source_profile_preserves_all_thirteen_stages_and_command_units():
    profile = ac_sdl1_oer.load_protocol()
    stages = profile["stages"]
    assert [stage["stage"] for stage in stages] == list(range(13))
    assert all(stage["response"] is None for stage in stages)
    assert stages[0]["parameters"] == {
        "current_density_a_cm2": 0.2,
        "current_a": 0.05654,
        "duration_s": 60,
        "sampling_interval_s": 0.05,
    }
    assert [stage["parameters"]["current_density_a_cm2"] for stage in stages[4:11]] == [
        0.1,
        0.05,
        0.02,
        0.01,
        0.005,
        0.002,
        0.001,
    ]
    for stage in stages[4:11]:
        parameters = stage["parameters"]
        assert parameters["duration_s"] == 70
        assert parameters["sampling_interval_s"] == 0.05
        assert parameters["current_a"] == pytest.approx(
            parameters["current_density_a_cm2"] * 0.2827
        )
    assert stages[1]["parameters"] == {
        "start_voltage_v": 0.8,
        "first_voltage_limit_v": 2.3,
        "second_voltage_limit_v": 0.8,
        "end_voltage_v": 0.8,
        "scan_rate_v_s": 0.2,
        "sampling_interval_s": 0.05,
        "cycles": 25,
    }
    assert (
        stages[2]["parameters"]
        == stages[11]["parameters"]
        == {
            "start_voltage_v": 0.8,
            "first_voltage_limit_v": 2.3,
            "second_voltage_limit_v": 0.8,
            "end_voltage_v": 0.8,
            "scan_rate_v_s": 0.01,
            "sampling_interval_s": 0.2,
            "cycles": 2,
        }
    )
    assert stages[3]["parameters"] == {
        "start_frequency_hz": 500000,
        "end_frequency_hz": 1,
        "points_per_decade": 10,
        "voltage_bias_v": 1.5,
        "voltage_amplitude_v": 0.01,
        "number_of_runs": 0,
    }
    assert stages[12]["parameters"] == {
        "start_voltage_v": 0.8,
        "first_voltage_limit_v": -0.2,
        "second_voltage_limit_v": 0,
        "end_voltage_v": -0.2,
        "scan_rate_v_s": 0.01,
        "sampling_interval_s": 0.2,
        "cycles": 2,
    }
    assert [stage["stage"] for stage in stages if stage["ir_corrected"]] == list(range(4, 13))


def test_source_profile_preserves_resistance_selection_and_last_third_potential():
    profile = ac_sdl1_oer.load_protocol()
    extraction = profile["ohmic_resistance_extraction"]
    operations = extraction["ordered_operations"]
    assert extraction["input_stage"] == 3
    assert [item["operation"] for item in operations] == [
        "select_first_rows",
        "retain_rows_where_both_real_and_imaginary_are_nonzero",
        "select_index_of_minimum_absolute_imaginary",
        "return_real_at_selected_index_rounded",
    ]
    assert operations[0]["count"] == 10
    assert operations[2]["skipna"] is True
    assert operations[3]["decimal_places"] == 3
    assert extraction["value"] is None
    correction = profile["ir_correction"]
    assert correction["factor"] == 0.9
    assert correction["input_current_column"] == "Working Electrode Current [A]"
    assert correction["expression"] == "E_corrected_V = E_WE_V - 0.9 * R_ohm * I_WE_A"
    assert correction["applied_stages"] == list(range(4, 13))
    headline = profile["headline"]
    assert headline["stage"] == 7
    assert headline["current_density_a_cm2"] == 0.01
    assert headline["statistic"] == (
        "mean of final int(len(dc_data) / 3) rows of Corrected Working Electrode Voltage [V]"
    )
    assert headline["equilibrium_potential_subtracted_upstream"] is False
    assert headline["value"] is None
    unknowns = {item["quantity"]: item for item in profile["unknowns"]}
    assert unknowns["reference_electrode_identity_and_potential_scale"]["value"] is None
    assert unknowns["EIS_number_of_runs_zero_vendor_semantics"]["value"] is None
