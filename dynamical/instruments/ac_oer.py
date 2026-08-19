"""OER overpotential response fitted to AMPERE-2 physical chronopotentiometry.

Instrument physics only. No objective, no experiment order, no stopping rule.

The response surface is an ordinary-least-squares fit of iR-corrected,
final-measurement (last-third mean) potentials from physical
chronopotentiometry at 20 and 50 mA/cm^2 in the AMPERE-2 dataset
(DOI 10.11583/DTU.27446925), under the frozen protocol recorded in
``dynamical/bundle/reference-lab/calibration/ampere2-oer/``. Inputs are the deposited film's
nominal precursor composition (written onto the sample by the upstream
electrodeposition from accumulated electrolyte volumes) and the requested
OER current density.

The frozen held-out calibration gates FAILED (held-out MAE and candidate-order
preservation; see ``calibration_report.json``), so this model supplies simulator
evidence only. The declared uncertainty is the fit-split residual standard
deviation the data earned, and no calibrated-twin claim is made or implied.
"""

from __future__ import annotations

import math

from ..reasons import RuntimeReason
from . import InstrumentRequest, InstrumentResult, register

# Frozen fit constants from dynamical/bundle/reference-lab/calibration/ampere2-oer/fit.json.
# Fitted once under the frozen protocol; never revised against held-out data.
INTERCEPT_V = 1.214239114588155
LOG10_J_COEFFICIENT_V = 0.30187979289520034
METAL_COEFFICIENTS_V = {
    "Ni": -0.11294989843011162,
    "Fe": -0.33227754709643814,
    "Cr": -0.05127468248150762,
    "Mn": 1.0611381342707886,
    "Co": -0.31513566354441347,
    "Zn": -0.0963993624009567,
    "Cu": 1.0611381342707906,
}
COMPLEXING_COEFFICIENTS_V = {
    "NH4OH": -1.587317299140995,
    "NaCi": 1.8060519823326926,
}
# Fit-split residual standard deviation. Large, honestly: the admitted
# physical data includes runs at the instrument compliance rail, and the
# frozen protocol does not permit excluding them after the fact.
OVERPOTENTIAL_UNCERTAINTY_V = 1.8073715433658781

# The admitted instrument envelope for the requested current density. The
# fitted basis is exactly {0.020, 0.050} A/cm^2, so the envelope is restricted
# to that basis: a request outside it is flagged out-of-envelope rather than
# silently extrapolating the two-point log10(j) fit.
CURRENT_DENSITY_MIN_A_CM2 = 0.020
CURRENT_DENSITY_MAX_A_CM2 = 0.050


@register("measure-oer", "ac-oer-simulator")
def measure_oer(request: InstrumentRequest) -> InstrumentResult:
    j = float(request.parameters["current_density_a_cm2"])
    reasons: list[RuntimeReason] = []
    if not CURRENT_DENSITY_MIN_A_CM2 <= j <= CURRENT_DENSITY_MAX_A_CM2:
        reasons.append(
            RuntimeReason(
                code="PARAMETER_OUT_OF_ENVELOPE",
                detail=(
                    f"current density {j} A/cm^2 is outside the admitted envelope "
                    f"[{CURRENT_DENSITY_MIN_A_CM2}, {CURRENT_DENSITY_MAX_A_CM2}] A/cm^2"
                ),
                channel_id="instrument.current_density_a_cm2",
                recoverable=True,
            )
        )

    # The measurement is only evidence about a deposited film if it reads the
    # film. The fitted response needs the deposited composition; a sample with
    # no recorded deposit, or one with no recorded composition, fails closed
    # with a typed reason instead of reporting an invented number.
    state = request.sample.state if request.sample is not None else {}
    fractions = {metal: state.get(f"deposited_fraction_{metal}") for metal in METAL_COEFFICIENTS_V}
    recorded = {metal: value for metal, value in fractions.items() if value is not None}
    # A refused prediction reports its port as None -- the runner maps that to
    # an unavailable channel with unknown uncertainty, which is what proof
    # validation checks against.
    outputs: dict[str, float | None] = {"current_density_a_cm2": j, "overpotential_v": None}
    uncertainty: dict[str, float] = {}
    if request.sample is None or state.get("deposited_thickness_um") is None:
        reasons.append(
            RuntimeReason(
                code="SAMPLE_STATE_UNAVAILABLE",
                detail=(
                    "no deposited film is recorded on the measured sample, so this "
                    "measurement cannot be attributed to a deposition condition"
                ),
                channel_id="sample.state",
                recoverable=True,
            )
        )
    elif not recorded:
        reasons.append(
            RuntimeReason(
                code="SAMPLE_STATE_UNAVAILABLE",
                detail=(
                    "the deposited film carries no recorded precursor composition "
                    "(no upstream dispense named its stock chemical), so the fitted "
                    "composition response cannot be evaluated for it"
                ),
                channel_id="sample.deposited_composition",
                recoverable=True,
            )
        )
    else:
        log_argument = j if j > 0.0 else CURRENT_DENSITY_MIN_A_CM2
        overpotential = INTERCEPT_V + LOG10_J_COEFFICIENT_V * math.log10(log_argument)
        for metal, coefficient in METAL_COEFFICIENTS_V.items():
            overpotential += coefficient * float(recorded.get(metal, 0.0))
        for agent, coefficient in COMPLEXING_COEFFICIENTS_V.items():
            overpotential += coefficient * float(state.get(f"deposited_complexing_{agent}", 0.0))
        outputs["overpotential_v"] = overpotential
        uncertainty["overpotential_v"] = OVERPOTENTIAL_UNCERTAINTY_V

    return InstrumentResult(
        outputs=outputs,
        uncertainty=uncertainty,
        cost_usd=0.0,
        duration_s=120.0,
        reasons=reasons,
    )
