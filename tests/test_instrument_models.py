"""Instrument models are physics, not policy: no objective, order or stopping rule."""

from __future__ import annotations

import pytest

from dynamical import instruments
from dynamical.instruments import InstrumentRequest
from dynamical.samples import Sample

FARADAY = 96485.332_12  # C/mol, CODATA


def _request(sample=None, **parameters):
    return InstrumentRequest(parameters=parameters, inputs={}, sample=sample)


def _deposited_sample(
    thickness_um: float = 1.0, composition: dict[str, float] | None = None
) -> Sample:
    """A sample carrying a recorded deposit, as the potentiostat leaves it."""
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


def test_faraday_mass_matches_the_closed_form():
    model = instruments.resolve("electrodeposit-constant-current", "ac-squidstat-simulator")
    result = model(_request(current_a=0.002827, duration_s=600.0))
    charge = 0.002827 * 600.0
    expected = charge * 58.6934 / (2 * FARADAY)  # nickel, n=2
    assert result.outputs["deposited_mass_g"] == pytest.approx(expected, rel=1e-9)
    assert result.outputs["charge_c"] == pytest.approx(charge)
    assert result.uncertainty["deposited_mass_g"] > 0.0


def test_deposition_refuses_current_outside_the_envelope():
    model = instruments.resolve("electrodeposit-constant-current", "ac-squidstat-simulator")
    result = model(_request(current_a=5.0, duration_s=10.0))
    assert any(r.code == "PARAMETER_OUT_OF_ENVELOPE" for r in result.reasons)


def test_deposition_thickness_and_current_density_are_derived_consistently():
    model = instruments.resolve("electrodeposit-constant-current", "ac-squidstat-simulator")
    result = model(_request(current_a=0.002827, duration_s=600.0))
    assert result.outputs["current_density_a_cm2"] == pytest.approx(0.010, rel=1e-6)
    assert result.outputs["deposited_thickness_um"] > 0.0
    assert result.uncertainty["deposited_thickness_um"] > 0.0


def test_fitted_overpotential_is_monotonic_in_current_density():
    model = instruments.resolve("measure-oer", "ac-oer-simulator")
    sample = _deposited_sample(composition={"Ni": 1.0})
    low = model(_request(sample, current_density_a_cm2=0.020)).outputs["overpotential_v"]
    high = model(_request(sample, current_density_a_cm2=0.050)).outputs["overpotential_v"]
    assert high > low


def test_oer_declares_uncertainty_and_refuses_out_of_envelope_density():
    model = instruments.resolve("measure-oer", "ac-oer-simulator")
    composed = _deposited_sample(composition={"Ni": 1.0})
    in_envelope = model(_request(composed, current_density_a_cm2=0.030))
    assert in_envelope.uncertainty["overpotential_v"] > 0.0
    assert in_envelope.reasons == []

    # Below the fitted basis {0.020, 0.050}: flagged, not silently extrapolated.
    out_of_envelope = model(_request(composed, current_density_a_cm2=0.005))
    assert any(r.code == "PARAMETER_OUT_OF_ENVELOPE" for r in out_of_envelope.reasons)


def test_oer_declines_a_measurement_it_cannot_attribute_to_a_deposit():
    """A measurement that ignored the sample would report the same number for
    every deposition condition, which is not a measurement of anything."""
    model = instruments.resolve("measure-oer", "ac-oer-simulator")
    result = model(_request(current_density_a_cm2=0.005))
    assert any(r.code == "SAMPLE_STATE_UNAVAILABLE" for r in result.reasons)
    assert result.outputs["overpotential_v"] is None


def test_oer_declines_a_deposit_with_no_recorded_composition():
    """The fitted response needs the deposited composition; a bare thickness
    is no longer enough to attribute a prediction to a condition."""
    model = instruments.resolve("measure-oer", "ac-oer-simulator")
    result = model(_request(_deposited_sample(), current_density_a_cm2=0.020))
    assert any(r.code == "SAMPLE_STATE_UNAVAILABLE" for r in result.reasons)
    assert result.outputs["overpotential_v"] is None


def test_oer_responds_to_the_deposited_composition():
    model = instruments.resolve("measure-oer", "ac-oer-simulator")
    iron = model(_request(_deposited_sample(composition={"Fe": 1.0}), current_density_a_cm2=0.020))
    manganese = model(
        _request(_deposited_sample(composition={"Mn": 1.0}), current_density_a_cm2=0.020)
    )
    # The fitted coefficients order these two chemistries distinctly.
    assert iron.outputs["overpotential_v"] < manganese.outputs["overpotential_v"]
    assert iron.reasons == [] and manganese.reasons == []


def test_dispense_with_a_named_chemical_accumulates_electrolyte_state():
    model = instruments.resolve("dispense-electrolyte", "ac-ot2-simulator")
    sample = _deposited_sample()
    first = model(_request(sample, volume_ml=2.0, chemical="Ni"))
    assert first.sample is not None
    accumulated = first.sample.state["electrolyte.Ni_ml"]
    assert accumulated == pytest.approx(first.outputs["volume_applied_ml"])
    second = model(_request(first.sample, volume_ml=1.0, chemical="Fe"))
    assert second.sample.state["electrolyte.Ni_ml"] == pytest.approx(accumulated)
    assert second.sample.state["electrolyte.Fe_ml"] > 0.0


def test_dispense_refuses_an_unadmitted_chemical():
    model = instruments.resolve("dispense-electrolyte", "ac-ot2-simulator")
    result = model(_request(_deposited_sample(), volume_ml=1.0, chemical="Pt"))
    assert any(r.code == "PARAMETER_OUT_OF_ENVELOPE" for r in result.reasons)
    assert result.sample is None


def test_deposition_records_composition_from_the_electrolyte():
    model = instruments.resolve("electrodeposit-constant-current", "ac-squidstat-simulator")
    sample = Sample(
        id="sample-under-test",
        station_id="squidstat-echem",
        custody_state="held",
        quantity=1.0,
        unit="1",
        created_by_step_id="dispense",
        state={"electrolyte.Ni_ml": 3.0, "electrolyte.Fe_ml": 1.0, "electrolyte.NaCi_ml": 2.0},
    )
    result = model(_request(sample, current_a=0.002827, duration_s=600.0))
    state = result.sample.state
    assert state["deposited_fraction_Ni"] == pytest.approx(0.75)
    assert state["deposited_fraction_Fe"] == pytest.approx(0.25)
    assert state["deposited_complexing_NaCi"] == pytest.approx(0.5)


def test_cleaning_clears_process_state_and_stays_in_envelope():
    model = instruments.resolve("clean-electrode", "ac-cleaning-simulator")
    dirty = _deposited_sample(composition={"Ni": 1.0}).model_copy(
        update={"state": {"deposited_thickness_um": 1.0, "electrolyte.Ni_ml": 2.0}}
    )
    result = model(_request(dirty, rinse_volume_ml=6.0, ultrasound_s=60.0))
    assert result.reasons == []
    assert result.sample is not None
    assert result.sample.state == {}
    assert result.outputs["instrument.rinse_volume_ml"] == pytest.approx(6.0)


def test_cleaning_refuses_an_out_of_envelope_rinse_volume():
    model = instruments.resolve("clean-electrode", "ac-cleaning-simulator")
    result = model(_request(_deposited_sample(), rinse_volume_ml=50.0))
    assert any(r.code == "PARAMETER_OUT_OF_ENVELOPE" for r in result.reasons)


def test_cell_loading_seats_the_electrode_and_writes_cell_state():
    model = instruments.resolve("load-electrochemical-cell", "ac-cell-loading-simulator")
    result = model(_request(_deposited_sample(), cell_id="echem-cell-main-body"))
    assert result.reasons == []
    assert result.outputs["instrument.cell_id"] == "echem-cell-main-body"
    assert result.outputs["instrument.cell_seated"] is True
    assert result.sample.state["cell_loaded"] == 1.0


def test_cell_loading_refuses_an_empty_cell_id():
    model = instruments.resolve("load-electrochemical-cell", "ac-cell-loading-simulator")
    result = model(_request(_deposited_sample(), cell_id=""))
    assert any(r.code == "PARAMETER_OUT_OF_ENVELOPE" for r in result.reasons)
    assert result.outputs == {}


def test_dispense_reports_requested_and_applied_volume():
    model = instruments.resolve("dispense-electrolyte", "ac-ot2-simulator")
    result = model(_request(volume_ml=3.8951234))
    assert result.outputs["volume_requested_ml"] == pytest.approx(3.8951234)
    assert result.outputs["volume_applied_ml"] != result.outputs["volume_requested_ml"]
    assert result.uncertainty["volume_applied_ml"] > 0.0


def test_dispense_refuses_a_volume_above_the_reservoir_envelope():
    model = instruments.resolve("dispense-electrolyte", "ac-ot2-simulator")
    result = model(_request(volume_ml=100.0))
    assert any(r.code == "PARAMETER_OUT_OF_ENVELOPE" for r in result.reasons)


def test_aliquot_reports_requested_and_applied_volume():
    model = instruments.resolve("aliquot-to-well", "ac-ot2-simulator")
    result = model(_request(volume_ml=3.895))
    assert result.outputs["volume_requested_ml"] == pytest.approx(3.895)
    assert result.uncertainty["volume_applied_ml"] > 0.0


def test_aliquot_refuses_a_volume_above_the_well_envelope():
    model = instruments.resolve("aliquot-to-well", "ac-ot2-simulator")
    result = model(_request(volume_ml=10.0))
    assert any(r.code == "PARAMETER_OUT_OF_ENVELOPE" for r in result.reasons)


def test_condition_ultrasonic_declares_uncertainty_and_stays_in_envelope():
    model = instruments.resolve("condition-ultrasonic", "ac-arduino-simulator")
    result = model(_request(duration_s=300.0, setpoint_percent=80.0))
    assert result.outputs["instrument.conditioning_duration_s"] == pytest.approx(300.0)
    assert result.uncertainty["instrument.conditioning_duration_s"] > 0.0
    assert result.uncertainty["instrument.conditioning_setpoint_percent"] > 0.0
    assert result.reasons == []


def test_condition_ultrasonic_refuses_an_out_of_envelope_duration():
    model = instruments.resolve("condition-ultrasonic", "ac-arduino-simulator")
    result = model(_request(duration_s=10_000.0))
    assert any(r.code == "PARAMETER_OUT_OF_ENVELOPE" for r in result.reasons)


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
