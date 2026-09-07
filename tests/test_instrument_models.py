"""Instrument models are physics, not policy: no objective, order or stopping rule."""

from __future__ import annotations

import pytest

from dynamical import instruments
from dynamical.instruments import InstrumentRequest
from dynamical.samples import Sample


def _request(sample=None, **parameters):
    return InstrumentRequest(parameters=parameters, inputs={}, sample=sample)


def _deposited_sample(
    thickness_um: float = 1.0, composition: dict[str, float] | None = None
) -> Sample:
    """A fixture carrying independently recorded deposit properties."""
    state: dict[str, float] = {"deposited_thickness_um": thickness_um}
    for metal, fraction in (composition or {}).items():
        state[f"deposited_fraction_{metal}"] = fraction
    return Sample(
        id="sample-under-test",
        station_id="squidstat-echem",
        custody_state="held",
        quantity=1.0,
        unit="1",
        created_by_step_id="deposit",
        state=state,
    )


@pytest.mark.parametrize("duration", [60.0])
def test_deposition_records_cathodic_commands_without_inventing_a_film(duration):
    model = instruments.resolve("electrodeposit-constant-current", "ac-squidstat-simulator")
    result = model(
        _request(
            _bath_sample(), current_a=-0.002827, duration_s=duration, temperature_setpoint_c=35.0
        )
    )
    assert result.outputs["commanded_charge_c"] == pytest.approx(-0.002827 * duration)
    assert result.outputs["current_density_a_cm2"] == pytest.approx(-0.010)
    assert result.outputs["deposited_mass_g"] is None
    assert result.outputs["deposited_thickness_um"] is None
    assert "deposited_thickness_um" not in result.sample.state
    assert result.sample.state["deposition_commanded"] == 1.0
    assert result.uncertainty == {}
    assert result.reasons == []


@pytest.mark.parametrize(
    "current,duration,temperature",
    [
        (0.002827, 60, 35),
        (-0.002827, 10, 35),
        (-0.002827, 600, 35),
        (-0.002827, 60, 25),
        (float("nan"), 60, 35),
        (-0.002827, float("inf"), 35),
    ],
)
def test_deposition_refuses_unadmitted_commands_without_state_mutation(
    current, duration, temperature
):
    model = instruments.resolve("electrodeposit-constant-current", "ac-squidstat-simulator")
    sample = _bath_sample()
    result = model(
        _request(sample, current_a=current, duration_s=duration, temperature_setpoint_c=temperature)
    )
    assert any(r.code == "PARAMETER_OUT_OF_ENVELOPE" for r in result.reasons)
    assert all(value is None for value in result.outputs.values())
    assert result.sample is None
    assert sample.state == {}


def test_fitted_overpotential_is_monotonic_in_current_density():
    model = instruments.resolve("estimate-oer", "ac-oer-simulator")
    sample = _deposited_sample(composition={"Ni": 1.0})
    low = model(_request(sample, current_density_a_cm2=0.020)).outputs["overpotential_v"]
    high = model(_request(sample, current_density_a_cm2=0.050)).outputs["overpotential_v"]
    assert high > low


def test_oer_declares_uncertainty_and_refuses_out_of_envelope_density():
    model = instruments.resolve("estimate-oer", "ac-oer-simulator")
    composed = _deposited_sample(composition={"Ni": 1.0})
    in_envelope = model(_request(composed, current_density_a_cm2=0.020))
    assert in_envelope.uncertainty["overpotential_v"] > 0.0
    assert in_envelope.reasons == []

    # Below the fitted basis {0.020, 0.050}: flagged, not silently extrapolated.
    out_of_envelope = model(_request(composed, current_density_a_cm2=0.005))
    assert any(r.code == "PARAMETER_OUT_OF_ENVELOPE" for r in out_of_envelope.reasons)


def test_oer_declines_a_measurement_it_cannot_attribute_to_a_deposit():
    """A measurement that ignored the sample would report the same number for
    every deposition condition, which is not a measurement of anything."""
    model = instruments.resolve("estimate-oer", "ac-oer-simulator")
    result = model(_request(current_density_a_cm2=0.005))
    assert any(r.code == "SAMPLE_STATE_UNAVAILABLE" for r in result.reasons)
    assert result.outputs["overpotential_v"] is None


def test_oer_declines_a_deposit_with_no_recorded_composition():
    """The fitted response needs the deposited composition; a bare thickness
    is no longer enough to attribute a prediction to a condition."""
    model = instruments.resolve("estimate-oer", "ac-oer-simulator")
    result = model(_request(_deposited_sample(), current_density_a_cm2=0.020))
    assert any(r.code == "SAMPLE_STATE_UNAVAILABLE" for r in result.reasons)
    assert result.outputs["overpotential_v"] is None


def test_oer_responds_to_the_deposited_composition():
    model = instruments.resolve("estimate-oer", "ac-oer-simulator")
    iron = model(_request(_deposited_sample(composition={"Fe": 1.0}), current_density_a_cm2=0.020))
    manganese = model(
        _request(_deposited_sample(composition={"Mn": 1.0}), current_density_a_cm2=0.020)
    )
    # The fitted coefficients order these two chemistries distinctly.
    assert iron.outputs["overpotential_v"] < manganese.outputs["overpotential_v"]
    assert iron.reasons == [] and manganese.reasons == []


def test_pipette_commands_accumulate_only_nominal_inventory_and_admit_koh():
    model = instruments.resolve("dispense-electrolyte", "ac-ot2-simulator")
    first = model(_request(_bath_sample(), volume_ml=2.0009, chemical="Ni"))
    assert first.sample.state["electrolyte_commanded.Ni_ml"] == 2.0009
    assert first.outputs["volume_commanded_ml"] == 2.0009
    assert first.outputs["volume_applied_ml"] is None
    second = model(_request(first.sample, volume_ml=1.0, chemical="KOH"))
    assert second.sample.state == {
        "electrolyte_commanded.Ni_ml": 2.0009,
        "electrolyte_commanded.KOH_ml": 1.0,
    }
    assert second.uncertainty == {}
    assert second.applied_parameters == {"volume_ml": None, "chemical": None}


@pytest.mark.parametrize(
    "volume,chemical",
    [(1.0, "Pt"), (4.0, "Ni"), (-1.0, "Ni"), (float("nan"), "Ni"), (float("inf"), "Ni")],
)
def test_dispense_refuses_invalid_commands_without_inventory_changes(volume, chemical):
    model = instruments.resolve("dispense-electrolyte", "ac-ot2-simulator")
    sample = _bath_sample()
    result = model(_request(sample, volume_ml=volume, chemical=chemical))
    assert any(r.code == "PARAMETER_OUT_OF_ENVELOPE" for r in result.reasons)
    assert result.sample is None
    assert sample.state == {}
    assert result.outputs["volume_commanded_ml"] is None
    assert result.outputs["volume_applied_ml"] is None


def test_pipette_refuses_cumulative_nominal_well_overflow():
    model = instruments.resolve("dispense-electrolyte", "ac-ot2-simulator")
    first = model(_request(_bath_sample(), volume_ml=3.0, chemical="Ni"))
    refused = model(_request(first.sample, volume_ml=1.0, chemical="Fe"))
    assert refused.sample is None
    assert any(r.code == "PARAMETER_OUT_OF_ENVELOPE" for r in refused.reasons)
    assert first.sample.state == {"electrolyte_commanded.Ni_ml": 3.0}


def test_commanded_precursors_are_not_measured_film_composition():
    dispense = instruments.resolve("dispense-electrolyte", "ac-ot2-simulator")
    deposit = instruments.resolve("electrodeposit-constant-current", "ac-squidstat-simulator")
    oer = instruments.resolve("estimate-oer", "ac-oer-simulator")
    sample = dispense(_request(_bath_sample(), volume_ml=3.0, chemical="Ni")).sample
    result = deposit(
        _request(sample, current_a=-0.002827, duration_s=60, temperature_setpoint_c=35)
    )
    assert result.sample.state["deposition_precursor_commanded.Ni_ml"] == 3.0
    assert "deposited_fraction_Ni" not in result.sample.state
    measurement = oer(_request(result.sample, current_density_a_cm2=0.020))
    assert measurement.outputs["overpotential_v"] is None
    assert any(r.code == "SAMPLE_STATE_UNAVAILABLE" for r in measurement.reasons)


def test_new_deposition_invalidates_previous_film_observations():
    prior = _deposited_sample(composition={"Ni": 1.0})
    deposit = instruments.resolve("electrodeposit-constant-current", "ac-squidstat-simulator")
    oer = instruments.resolve("estimate-oer", "ac-oer-simulator")
    result = deposit(_request(prior, current_a=-0.002827, duration_s=60, temperature_setpoint_c=35))
    assert not any(key.startswith("deposited_") for key in result.sample.state)
    assert prior.state["deposited_thickness_um"] == 1.0
    measurement = oer(_request(result.sample, current_density_a_cm2=0.020))
    assert measurement.outputs["overpotential_v"] is None
    assert any(reason.code == "SAMPLE_STATE_UNAVAILABLE" for reason in measurement.reasons)


@pytest.mark.parametrize(
    "acid,dwell,water,drain,ultrasound",
    [
        (True, 30.0, 2.0, 14.0, 25.0),
        (True, 0.1, 2.0, 14.0, 25.0),
        (False, 0.0, 1.0, 8.0, 10.0),
    ],
)
def test_well_cleaning_preserves_deposit_and_exposes_unknown_residual(
    acid, dwell, water, drain, ultrasound
):
    model = instruments.resolve("clean-electrode", "ac-cleaning-simulator")
    dirty = _deposited_sample(composition={"Ni": 1.0}).model_copy(
        update={
            "state": {
                "deposited_thickness_um": 1.0,
                "deposited_fraction_Ni": 1.0,
                "electrolyte_commanded.Ni_ml": 2.0009,
                "deposition_commanded": 1.0,
            }
        }
    )
    result = model(_request(dirty, use_acid=acid, acid_dwell_s=dwell))
    assert result.reasons == []
    assert result.sample.state == {
        "deposited_thickness_um": 1.0,
        "deposited_fraction_Ni": 1.0,
        "deposition_commanded": 1.0,
    }
    assert result.outputs["instrument.water_commanded_ml"] == water
    assert result.outputs["instrument.acid_commanded_ml"] == (0.5 if acid else 0)
    assert result.outputs["instrument.drain_commanded_ml"] == drain
    assert result.outputs["instrument.ultrasound_commanded_s"] == ultrasound
    assert result.outputs["instrument.residual_volume_ml"] is None
    assert result.uncertainty == {}


@pytest.mark.parametrize("acid,dwell", [(False, 30), (True, 600), (True, float("nan")), (1, 30)])
def test_cleaning_refuses_unadmitted_sequence_without_resetting_state(acid, dwell):
    model = instruments.resolve("clean-electrode", "ac-cleaning-simulator")
    sample = _deposited_sample()
    result = model(_request(sample, use_acid=acid, acid_dwell_s=dwell))
    assert any(r.code == "PARAMETER_OUT_OF_ENVELOPE" for r in result.reasons)
    assert result.sample is None
    assert all(value is None for value in result.outputs.values())
    assert sample.state["deposited_thickness_um"] == 1.0


def test_removed_cell_loading_is_not_registered():
    assert instruments.resolve("load-electrochemical-cell", "ac-cell-loading-simulator") is None


@pytest.mark.parametrize("operation", ["dispense-electrolyte", "aliquot-to-well"])
def test_pipette_operations_report_commands_without_measured_delivery(operation):
    model = instruments.resolve(operation, "ac-ot2-simulator")
    result = model(_request(volume_ml=3.895))
    assert result.outputs == {
        "volume_requested_ml": 3.895,
        "volume_commanded_ml": 3.895,
        "volume_applied_ml": None,
    }
    assert result.applied_parameters == {"volume_ml": None}
    assert result.uncertainty == {}


@pytest.mark.parametrize("duration", [30])
def test_arduino_records_temperature_and_timed_relay_commands(duration):
    model = instruments.resolve("condition-ultrasonic", "ac-arduino-simulator")
    result = model(_request(duration_s=duration, temperature_setpoint_c=35))
    assert result.outputs == {
        "instrument.ultrasound_commanded_s": duration,
        "instrument.temperature_setpoint_c": 35,
        "instrument.temperature_observed_c": None,
    }
    assert result.uncertainty == {}
    assert result.reasons == []


@pytest.mark.parametrize(
    "duration,temperature", [(5, 35), (15, 35), (10000, 35), (30, 80), (float("inf"), 35)]
)
def test_arduino_refuses_unadmitted_commands(duration, temperature):
    model = instruments.resolve("condition-ultrasonic", "ac-arduino-simulator")
    result = model(_request(duration_s=duration, temperature_setpoint_c=temperature))
    assert any(r.code == "PARAMETER_OUT_OF_ENVELOPE" for r in result.reasons)
    assert all(value is None for value in result.outputs.values())


def test_transfer_materializes_a_new_sample_when_none_is_in_custody():
    model = instruments.resolve("transfer-sample", "ac-transfer-simulator")
    result = model(_request(sample_id="sample-1", to_station="bench-a", quantity=5.0, unit="mL"))
    assert result.sample is not None
    assert result.sample.id == "sample-1"
    assert result.sample.station_id == "bench-a"
    assert result.sample.custody_state == "held"
    assert result.reasons == []


def test_transfer_moves_a_sample_already_in_custody():
    model = instruments.resolve("transfer-sample", "ac-transfer-simulator")
    existing = Sample(
        id="sample-1",
        station_id="ot2-liquid-handling",
        custody_state="held",
        quantity=5.0,
        unit="mL",
        created_by_step_id="prep",
    )
    result = model(_request(sample=existing, to_station="squidstat-echem"))
    assert result.sample.station_id == "squidstat-echem"
    assert result.sample.quantity == pytest.approx(5.0)
    assert result.sample.id == "sample-1"


def test_transfer_refuses_a_missing_destination():
    model = instruments.resolve("transfer-sample", "ac-transfer-simulator")
    result = model(_request(sample_id="sample-1"))
    assert any(r.code == "PARAMETER_OUT_OF_ENVELOPE" for r in result.reasons)
    assert result.sample is None


def _bath_sample() -> Sample:
    return Sample(
        id="film-under-test",
        station_id="ac-bath-station",
        custody_state="held",
        quantity=1.0,
        unit="1",
        created_by_step_id="prepare",
        state={},
    )


def _twin_table_entry() -> tuple[dict[str, float], float]:
    import json
    from importlib import resources
    from pathlib import Path

    packaged = resources.files("dynamical").joinpath(
        "bundle/fastcat/calibration/fastcat-oer/prediction_table.json"
    )
    repo = (
        Path(__file__).resolve().parents[1]
        / "dynamical/bundle/fastcat/calibration/fastcat-oer/prediction_table.json"
    )
    text = packaged.read_text() if packaged.is_file() else repo.read_text()
    entry = json.loads(text)["entries"][0]
    return entry["composition"], entry["predicted_e10_v"]


def test_bath_deposits_requested_fractions_onto_the_sample():
    model = instruments.resolve("deposit-chemical-bath", "ac-bath-simulator")
    result = model(_request(sample=_bath_sample(), fraction_ni=0.5, fraction_fe=0.5))
    assert result.outputs["deposited_fraction_Ni"] == 0.5
    assert result.outputs["bath_synthesis_time_s"] is not None
    assert result.sample.state["deposited_fraction_Fe"] == 0.5


def test_bath_admits_recorded_boundary_sums_and_refuses_violations():
    model = instruments.resolve("deposit-chemical-bath", "ac-bath-simulator")
    for ni, fe in ((0.499, 0.499), (0.5, 0.501)):  # sums 0.998 / 1.001 as recorded
        ok = model(_request(sample=_bath_sample(), fraction_ni=ni, fraction_fe=fe))
        assert ok.outputs["deposited_fraction_Ni"] == ni
    for bad in ({"fraction_ni": 0.95}, {"fraction_ni": 1.2, "fraction_fe": -0.2}):
        refused = model(_request(sample=_bath_sample(), **bad))
        assert refused.outputs["deposited_fraction_Ni"] is None
        assert any(r.code == "PARAMETER_OUT_OF_ENVELOPE" for r in refused.reasons)


def test_twin_reports_table_prediction_with_declared_uncertainty():
    composition, e10 = _twin_table_entry()
    bath = instruments.resolve("deposit-chemical-bath", "ac-bath-simulator")
    fractions = {f"fraction_{el.lower()}": v for el, v in composition.items()}
    film = bath(_request(sample=_bath_sample(), **fractions)).sample
    twin = instruments.resolve("measure-oer", "ac-oer-twin")
    result = twin(_request(sample=film, current_density_a_cm2=0.010))
    assert result.outputs["overpotential_v"] == pytest.approx(e10 - 1.229)
    assert result.uncertainty["overpotential_v"] == pytest.approx(0.104969)


def test_twin_fails_closed_off_basis_and_out_of_domain():
    twin = instruments.resolve("measure-oer", "ac-oer-twin")
    bath = instruments.resolve("deposit-chemical-bath", "ac-bath-simulator")
    film = bath(_request(sample=_bath_sample(), fraction_ni=1.0)).sample
    off_basis = twin(_request(sample=film, current_density_a_cm2=0.020))
    assert off_basis.outputs["overpotential_v"] is None
    unknown = bath(_request(sample=_bath_sample(), fraction_cu=0.5, fraction_zn=0.5)).sample
    out_of_domain = twin(_request(sample=unknown, current_density_a_cm2=0.010))
    assert out_of_domain.outputs["overpotential_v"] is None
    assert any(r.code == "PARAMETER_OUT_OF_ENVELOPE" for r in out_of_domain.reasons)


@pytest.mark.parametrize(
    "operation,provider,parameters,command_port,expected",
    [
        (
            "dispense-electrolyte",
            "ac-ot2-simulator",
            {"volume_ml": 1.0, "chemical": "Ni"},
            "volume_commanded_ml",
            1.0,
        ),
        (
            "condition-ultrasonic",
            "ac-arduino-simulator",
            {"duration_s": 30, "temperature_setpoint_c": 35},
            "instrument.temperature_setpoint_c",
            35,
        ),
        (
            "electrodeposit-constant-current",
            "ac-squidstat-simulator",
            {"current_a": -0.002827, "duration_s": 60, "temperature_setpoint_c": 35},
            "commanded_charge_c",
            -0.002827 * 60,
        ),
        (
            "clean-electrode",
            "ac-cleaning-simulator",
            {"use_acid": True, "acid_dwell_s": 0.1},
            "instrument.water_commanded_ml",
            2.0,
        ),
    ],
)
def test_command_bookkeeping_does_not_claim_physically_applied_parameters(
    operation, provider, parameters, command_port, expected
):
    model = instruments.resolve(operation, provider)
    result = model(_request(_bath_sample(), **parameters))
    assert result.applied_parameters == {name: None for name in parameters}
    assert result.outputs[command_port] == pytest.approx(expected)
    assert result.reasons == []


def test_aliquot_truncation_is_distinct_from_electrolyte_dispensing():
    request = _request(_bath_sample(), volume_ml=1.0009, chemical="Ni")
    aliquot = instruments.resolve("aliquot-to-well", "ac-ot2-simulator")(request)
    dispense = instruments.resolve("dispense-electrolyte", "ac-ot2-simulator")(request)
    assert aliquot.outputs["volume_commanded_ml"] == 1.0
    assert dispense.outputs["volume_commanded_ml"] == 1.0009


@pytest.mark.parametrize("current", [0.0, -0.02, 0.03, 0.2, float("nan")])
def test_legacy_ampere_refuses_unsupported_currents_without_a_prediction(current):
    model = instruments.resolve("estimate-oer", "ac-oer-simulator")
    result = model(
        _request(_deposited_sample(composition={"Ni": 1.0}), current_density_a_cm2=current)
    )
    assert result.outputs["overpotential_v"] is None
    assert result.uncertainty == {}
    assert any(r.code == "PARAMETER_OUT_OF_ENVELOPE" for r in result.reasons)
