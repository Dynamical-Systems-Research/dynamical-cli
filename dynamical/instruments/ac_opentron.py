"""Opentrons OT-2 liquid handling: requested volume vs. pump-applied volume.

Instrument physics only. No objective, no experiment order, no stopping
rule. The AC SDL1 archive's own ``parameters.py`` flags its pump-timing
constants ``slope`` and ``intercept`` ``# XXX "Change this to real life
number if pumps are calibrated"`` -- i.e. they are declared placeholders,
not calibrated hardware constants. This model carries that fact forward as
an inflated declared uncertainty and this claim-boundary note, rather than
silently adopting an invented pair of "real" numbers. Volume is converted
through a source-shaped pump relation ``time_on = slope * volume_ml +
intercept``, quantized to the pump controller's time step, and inverted
back to an applied volume -- the same shape the upstream archive documents,
with the same unfitted status.
"""

from __future__ import annotations

from ..reasons import RuntimeReason
from . import InstrumentRequest, InstrumentResult, register

# Declared engineering assumptions, not sourced calibration data. The
# upstream AC SDL1 archive documents this pump relation's *form*
# (``time_on = slope * V + intercept``) but flags both constants as
# uncalibrated placeholders. Substituting plausible-looking numbers here
# would misrepresent them as measured, so these are kept explicitly
# declared and the resulting uncertainty is inflated accordingly.
PUMP_TIME_SLOPE_S_PER_ML = 12.0
PUMP_TIME_INTERCEPT_S = 0.5
PUMP_STEP_S = 0.1  # smallest pump-controller time increment, declared

# Inflated relative uncertainty on applied volume. Not a documented
# instrument tolerance -- it is inflated specifically because the upstream
# slope/intercept are themselves unfitted placeholders.
VOLUME_RELATIVE_UNCERTAINTY = 0.10

CLAIM_BOUNDARY = (
    "Applied-volume uncertainty is inflated because the upstream AC SDL1 pump "
    'calibration constants are declared placeholders (`# XXX "Change this to '
    'real life number if pumps are calibrated"`), not measured hardware '
    "coefficients. This model is not fitted to, or validated against, any "
    "measured dispense; the AC SDL1 archive contains no experimental data."
)

DISPENSE_VOLUME_MIN_ML = 0.0
DISPENSE_VOLUME_MAX_ML = 25.0  # source-verified stock reservoir capacity

ALIQUOT_VOLUME_MIN_ML = 0.0
ALIQUOT_VOLUME_MAX_ML = 3.895  # source-verified test-plate well capacity (3895 uL)

# The stock chemicals the AMPERE-2 platform mixes by volume: seven metal
# chloride solutions and two complexing agents. A dispense may name which
# stock it draws from; the delivered volume then accumulates on the sample's
# electrolyte state so a downstream deposition knows the nominal precursor
# composition it deposited from.
ADMITTED_CHEMICALS = ("Ni", "Fe", "Cr", "Mn", "Co", "Zn", "Cu", "NH4OH", "NaCi")


def _apply_volume(volume_ml: float) -> tuple[float, float]:
    """Round a requested volume through the pump's quantized time control."""

    time_on_s = PUMP_TIME_SLOPE_S_PER_ML * volume_ml + PUMP_TIME_INTERCEPT_S
    quantized_time_s = round(time_on_s / PUMP_STEP_S) * PUMP_STEP_S
    applied_ml = max(0.0, (quantized_time_s - PUMP_TIME_INTERCEPT_S) / PUMP_TIME_SLOPE_S_PER_ML)
    half_step_ml = (PUMP_STEP_S / 2.0) / PUMP_TIME_SLOPE_S_PER_ML
    uncertainty_ml = max(half_step_ml, VOLUME_RELATIVE_UNCERTAINTY * applied_ml)
    return applied_ml, uncertainty_ml


def _envelope_reasons(
    volume_ml: float, minimum: float, maximum: float, channel_id: str
) -> list[RuntimeReason]:
    if minimum <= volume_ml <= maximum:
        return []
    return [
        RuntimeReason(
            code="PARAMETER_OUT_OF_ENVELOPE",
            detail=(
                f"volume {volume_ml} mL is outside the admitted envelope [{minimum}, {maximum}] mL"
            ),
            channel_id=channel_id,
            recoverable=True,
        )
    ]


def _dispense(request: InstrumentRequest, reasons: list[RuntimeReason]) -> InstrumentResult:
    volume_ml = float(request.parameters["volume_ml"])
    applied_ml, uncertainty_ml = _apply_volume(volume_ml)
    chemical = request.parameters.get("chemical")
    sample = None
    if chemical is not None:
        chemical = str(chemical)
        if chemical not in ADMITTED_CHEMICALS:
            reasons = [
                *reasons,
                RuntimeReason(
                    code="PARAMETER_OUT_OF_ENVELOPE",
                    detail=(
                        f"chemical {chemical!r} is not an admitted stock; admitted stocks are "
                        f"{list(ADMITTED_CHEMICALS)}"
                    ),
                    channel_id="instrument.chemical",
                    recoverable=True,
                ),
            ]
        elif request.sample is not None:
            key = f"electrolyte.{chemical}_ml"
            sample = request.sample.model_copy(
                update={
                    "state": {
                        **request.sample.state,
                        key: request.sample.state.get(key, 0.0) + applied_ml,
                    }
                }
            )
    return InstrumentResult(
        outputs={
            "volume_requested_ml": volume_ml,
            "volume_applied_ml": applied_ml,
        },
        uncertainty={"volume_applied_ml": uncertainty_ml},
        cost_usd=0.0,
        duration_s=max(0.0, PUMP_TIME_SLOPE_S_PER_ML * volume_ml + PUMP_TIME_INTERCEPT_S),
        reasons=reasons,
        sample=sample,
    )


@register("dispense-electrolyte", "ac-ot2-simulator")
def dispense_electrolyte(request: InstrumentRequest) -> InstrumentResult:
    volume_ml = float(request.parameters["volume_ml"])
    reasons = _envelope_reasons(
        volume_ml, DISPENSE_VOLUME_MIN_ML, DISPENSE_VOLUME_MAX_ML, "instrument.volume_ml"
    )
    return _dispense(request, reasons)


@register("aliquot-to-well", "ac-ot2-simulator")
def aliquot_to_well(request: InstrumentRequest) -> InstrumentResult:
    volume_ml = float(request.parameters["volume_ml"])
    reasons = _envelope_reasons(
        volume_ml, ALIQUOT_VOLUME_MIN_ML, ALIQUOT_VOLUME_MAX_ML, "instrument.volume_ml"
    )
    return _dispense(request, reasons)
