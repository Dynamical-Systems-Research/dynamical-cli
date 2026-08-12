"""Calibrated-twin OER overpotential at 10 mA/cm^2 (ac-oer-twin).

Instrument identity is orthogonal to calibration lineage: this twin's
current calibration evidence is registries/calibration/fastcat-oer/; a
future recalibration changes evidence refs, never this identity.

Instrument prediction only. No objective, no experiment order, no stopping
rule. Frozen predictions of E@10mA (V vs RHE) for the admitted FastCat
composition domain, produced by the independently validated provider
(registries/calibration/fastcat-oer/: MAE 22.2 mV on a one-time sealed
27-composition cohort, all nine predeclared gates passed, split-conformal
90% half-width 0.104969 V). W2 applies to THIS output on THIS domain only.
A composition outside the frozen table fails closed with a typed reason:
the calibration does not extend to it.
"""

from __future__ import annotations

from ..reasons import RuntimeReason
from . import InstrumentRequest, InstrumentResult, register

DOPANTS = ("Cr", "Al", "Fe", "Co", "Mn", "Ni", "Cu", "Zn")
CURRENT_DENSITY_BASIS_A_CM2 = 0.010
EQUILIBRIUM_POTENTIAL_V = 1.229  # O2/H2O vs RHE; overpotential = E - 1.229
INTERVAL_HALFWIDTH_V = 0.104969
INTERVAL_COVERAGE_TARGET = 0.90
MATCH_TOLERANCE = 1e-6

# Frozen prediction table: composition tuple (Cr, Al, Fe, Co, Mn, Ni, Cu,
# Zn) -> predicted E@10mA (V). Generated from the admitted provider under
# the frozen conditioning protocol; provenance hashes in
# registries/calibration/fastcat-oer/prediction_table.json.
PREDICTED_E10_V = {
    (0.149, 0.308, 0.251, 0.292, 0.0, 0.0, 0.0, 0.0): 1.534605,
    (0.223, 0.28, 0.444, 0.053, 0.0, 0.0, 0.0, 0.0): 1.531349,
    (0.25, 0.25, 0.25, 0.25, 0.0, 0.0, 0.0, 0.0): 1.528593,
    (0.35, 0.05, 0.05, 0.1, 0.2, 0.05, 0.0, 0.2): 1.567876,
    (0.36, 0.294, 0.102, 0.244, 0.0, 0.0, 0.0, 0.0): 1.537291,
    (0.3, 0.1, 0.3, 0.3, 0.0, 0.0, 0.0, 0.0): 1.50995,
    (0.3, 0.3, 0.1, 0.1, 0.1, 0.1, 0.0, 0.0): 1.553175,
    (0.499, 0.167, 0.167, 0.167, 0.0, 0.0, 0.0, 0.0): 1.540894,
    (0.5, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0): 1.605275,
    (0.059, 0.059, 0.176, 0.176, 0.118, 0.118, 0.118, 0.176): 1.575839,
    (0.05, 0.1, 0.25, 0.15, 0.05, 0.25, 0.15, 0.0): 1.543586,
    (0.05, 0.1, 0.25, 0.15, 0.0, 0.1, 0.3, 0.05): 1.576224,
    (0.136, 0.136, 0.136, 0.136, 0.136, 0.091, 0.091, 0.136): 1.589169,
    (0.1, 0.0, 0.4, 0.1, 0.0, 0.4, 0.0, 0.0): 1.50318,
    (0.25, 0.1, 0.25, 0.05, 0.0, 0.1, 0.0, 0.25): 1.544423,
    (0.2, 0.1, 0.25, 0.2, 0.0, 0.25, 0.0, 0.0): 1.5182,
    (0.0, 0.1, 0.25, 0.1, 0.0, 0.3, 0.2, 0.05): 1.556249,
    (0.0, 0.25, 0.45, 0.05, 0.0, 0.2, 0.0, 0.05): 1.528388,
    (0.05, 0.35, 0.2, 0.0, 0.0, 0.4, 0.0, 0.0): 1.539424,
    (0.05, 0.0, 0.15, 0.35, 0.0, 0.45, 0.0, 0.0): 1.506204,
    (0.05, 0.0, 0.35, 0.05, 0.0, 0.5, 0.0, 0.05): 1.518318,
    (0.05, 0.0, 0.0, 0.1, 0.0, 0.6, 0.0, 0.25): 1.600141,
    (0.25, 0.0, 0.25, 0.05, 0.0, 0.45, 0.0, 0.0): 1.492047,
    (0.2, 0.05, 0.15, 0.1, 0.0, 0.5, 0.0, 0.0): 1.497546,
    (0.2, 0.0, 0.05, 0.15, 0.0, 0.6, 0.0, 0.0): 1.525675,
    (0.2, 0.0, 0.1, 0.1, 0.0, 0.55, 0.0, 0.05): 1.527394,
    (0.0, 0.0, 0.0, 0.0, 0.5, 0.5, 0.0, 0.0): 1.608763,
    (0.05, 0.0, 0.3, 0.5, 0.0, 0.0, 0.1, 0.05): 1.547469,
    (0.101, 0.252, 0.334, 0.313, 0.0, 0.0, 0.0, 0.0): 1.527131,
    (0.2, 0.05, 0.4, 0.35, 0.0, 0.0, 0.0, 0.0): 1.50347,
    (0.326, 0.022, 0.326, 0.326, 0.0, 0.0, 0.0, 0.0): 1.500998,
    (0.3, 0.0, 0.25, 0.45, 0.0, 0.0, 0.0, 0.0): 1.502543,
    (0.0, 0.05, 0.4, 0.3, 0.05, 0.2, 0.0, 0.0): 1.497947,
    (0.0, 0.05, 0.4, 0.3, 0.2, 0.05, 0.0, 0.0): 1.506073,
    (0.0, 0.0, 0.25, 0.4, 0.3, 0.05, 0.0, 0.0): 1.525439,
    (0.0, 0.0, 0.5, 0.5, 0.0, 0.0, 0.0, 0.0): 1.495283,
    (0.1, 0.1, 0.15, 0.1, 0.0, 0.0, 0.55, 0.0): 1.581473,
    (0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.5, 0.0): 1.629263,
    (0.0, 0.2, 0.05, 0.15, 0.2, 0.0, 0.25, 0.15): 1.623082,
    (0.0, 0.0, 0.1, 0.15, 0.0, 0.3, 0.45, 0.0): 1.556517,
    (0.0, 0.0, 0.0, 0.3, 0.05, 0.0, 0.65, 0.0): 1.625363,
    (0.0, 0.0, 0.0, 0.0, 0.2, 0.05, 0.75, 0.0): 1.666101,
    (0.0, 0.0, 0.0, 0.0, 0.5, 0.0, 0.5, 0.0): 1.672059,
    (0.0, 0.0, 0.0, 0.0, 0.0, 0.35, 0.65, 0.0): 1.640153,
    (0.0, 0.0, 0.0, 0.0, 0.0, 0.5, 0.5, 0.0): 1.62762,
    (0.029, 0.029, 0.029, 0.912, 0.0, 0.0, 0.0, 0.0): 1.518033,
    (0.032, 0.283, 0.062, 0.623, 0.0, 0.0, 0.0, 0.0): 1.54909,
    (0.05, 0.1, 0.0, 0.4, 0.0, 0.0, 0.45, 0.0): 1.61624,
    (0.05, 0.0, 0.0, 0.95, 0.0, 0.0, 0.0, 0.0): 1.562554,
    (0.15, 0.0, 0.0, 0.35, 0.1, 0.05, 0.3, 0.05): 1.618984,
    (0.1, 0.05, 0.0, 0.5, 0.0, 0.0, 0.35, 0.0): 1.596026,
    (0.0, 0.5, 0.0, 0.5, 0.0, 0.0, 0.0, 0.0): 1.577068,
    (0.0, 0.0, 0.2, 0.5, 0.0, 0.0, 0.25, 0.05): 1.558102,
    (0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0): 1.553105,
    (0.05, 0.0, 0.05, 0.0, 0.8, 0.0, 0.0, 0.1): 1.622584,
    (0.15, 0.15, 0.15, 0.15, 0.15, 0.1, 0.1, 0.05): 1.586594,
    (0.25, 0.35, 0.0, 0.4, 0.0, 0.0, 0.0, 0.0): 1.577476,
    (0.269, 0.437, 0.006, 0.287, 0.0, 0.0, 0.0, 0.0): 1.580763,
    (0.3, 0.02, 0.32, 0.1, 0.2, 0.06, 0.0, 0.0): 1.521602,
    (0.3, 0.0, 0.2, 0.0, 0.0, 0.45, 0.05, 0.0): 1.521581,
    (0.55, 0.0, 0.1, 0.0, 0.0, 0.0, 0.05, 0.3): 1.563907,
    (0.0, 0.5, 0.0, 0.0, 0.5, 0.0, 0.0, 0.0): 1.635967,
    (0.0, 0.0, 0.0, 0.0, 0.5, 0.0, 0.0, 0.5): 1.652072,
    (0.05, 0.0, 0.25, 0.1, 0.0, 0.3, 0.0, 0.3): 1.537003,
    (0.05, 0.0, 0.0, 0.0, 0.35, 0.0, 0.55, 0.05): 1.685171,
    (0.167, 0.499, 0.167, 0.167, 0.0, 0.0, 0.0, 0.0): 1.555345,
    (0.1, 0.0, 0.4, 0.05, 0.0, 0.2, 0.15, 0.1): 1.54812,
    (0.206, 0.441, 0.177, 0.177, 0.0, 0.0, 0.0, 0.0): 1.553537,
    (0.35, 0.18, 0.291, 0.179, 0.0, 0.0, 0.0, 0.0): 1.533506,
    (0.0, 0.5, 0.0, 0.0, 0.05, 0.2, 0.0, 0.25): 1.625266,
    (0.0, 0.0, 0.1, 0.0, 0.65, 0.05, 0.2, 0.0): 1.599701,
    (0.0, 0.0, 0.0, 0.5, 0.0, 0.0, 0.0, 0.5): 1.592016,
}


@register("measure-oer", "ac-oer-twin")
def measure_oer_twin(request: InstrumentRequest) -> InstrumentResult:
    j = float(request.parameters.get("current_density_a_cm2", 0.010))
    reasons: list[RuntimeReason] = []
    outputs: dict[str, float | None] = {
        "current_density_a_cm2": j,
        "overpotential_v": None,
    }
    uncertainty: dict[str, float] = {}
    if abs(j - CURRENT_DENSITY_BASIS_A_CM2) > 1e-9:
        reasons.append(
            RuntimeReason(
                code="PARAMETER_OUT_OF_ENVELOPE",
                detail=(
                    f"the calibrated basis is exactly "
                    f"{CURRENT_DENSITY_BASIS_A_CM2} A/cm^2; received {j}"
                ),
                channel_id="instrument.current_density_a_cm2",
                recoverable=True,
            )
        )
        return InstrumentResult(outputs=outputs, uncertainty=uncertainty,
                                cost_usd=0.0, duration_s=60.0, reasons=reasons)
    state = request.sample.state if request.sample is not None else {}
    if request.sample is None or state.get("bath_synthesis") is None:
        reasons.append(
            RuntimeReason(
                code="SAMPLE_STATE_UNAVAILABLE",
                detail=(
                    "no bath-deposited film is recorded on the measured sample, "
                    "so the calibrated prediction cannot be attributed"
                ),
                channel_id="sample.state",
                recoverable=True,
            )
        )
        return InstrumentResult(outputs=outputs, uncertainty=uncertainty,
                                cost_usd=0.0, duration_s=60.0, reasons=reasons)
    composition = tuple(
        round(float(state.get(f"deposited_fraction_{el}", 0.0)), 6) for el in DOPANTS
    )
    match = None
    for key, value in PREDICTED_E10_V.items():
        if all(abs(a - b) <= MATCH_TOLERANCE for a, b in zip(key, composition)):
            match = value
            break
    if match is None:
        reasons.append(
            RuntimeReason(
                code="PARAMETER_OUT_OF_ENVELOPE",
                detail=(
                    "the deposited composition is outside the frozen calibrated "
                    "domain of this twin; no prediction is made for it"
                ),
                channel_id="sample.deposited_composition",
                recoverable=True,
            )
        )
    else:
        outputs["overpotential_v"] = float(match) - EQUILIBRIUM_POTENTIAL_V
        uncertainty["overpotential_v"] = INTERVAL_HALFWIDTH_V
    return InstrumentResult(outputs=outputs, uncertainty=uncertainty,
                            cost_usd=0.0, duration_s=60.0, reasons=reasons)
