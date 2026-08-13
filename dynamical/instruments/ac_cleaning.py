"""Cleaning-station electrode reset: rinse and drain at the cleaning cartridge.

Instrument physics only. No objective, no experiment order, no stopping rule.
Cleaning resets the working electrode between runs, as on the physical
AMPERE-2 platform: it clears the accumulated electrolyte volumes and the
recorded deposit from the sample's scientific state. The rinse-volume
envelope is bounded by the cleaning cartridge's two-well geometry, and the
ultrasound envelope reuses the same relay bound as conditioning; both are
declared engineering assumptions, not measured cleaning efficacy.
"""

from __future__ import annotations

from ..reasons import RuntimeReason
from . import InstrumentRequest, InstrumentResult, register

RINSE_VOLUME_MIN_ML = 0.0
RINSE_VOLUME_MAX_ML = 12.0  # bounded by the 25 x 59.7 x 24 mm two-well cartridge
ULTRASOUND_MIN_S = 0.0
ULTRASOUND_MAX_S = 1800.0  # same declared relay envelope as conditioning

# Declared, not measured: residual-liquid bound after a drain.
RESIDUAL_VOLUME_UNCERTAINTY_ML = 0.1

_CLEARED_STATE_PREFIXES = ("electrolyte.", "deposited_")


@register("clean-electrode", "ac-cleaning-simulator")
def clean_electrode(request: InstrumentRequest) -> InstrumentResult:
    rinse_volume_ml = float(request.parameters["rinse_volume_ml"])
    ultrasound_s = float(request.parameters.get("ultrasound_s", 0.0))
    reasons: list[RuntimeReason] = []
    if not RINSE_VOLUME_MIN_ML <= rinse_volume_ml <= RINSE_VOLUME_MAX_ML:
        reasons.append(
            RuntimeReason(
                code="PARAMETER_OUT_OF_ENVELOPE",
                detail=(
                    f"rinse volume {rinse_volume_ml} mL is outside the admitted envelope "
                    f"[{RINSE_VOLUME_MIN_ML}, {RINSE_VOLUME_MAX_ML}] mL"
                ),
                channel_id="instrument.rinse_volume_ml",
                recoverable=True,
            )
        )
    if not ULTRASOUND_MIN_S <= ultrasound_s <= ULTRASOUND_MAX_S:
        reasons.append(
            RuntimeReason(
                code="PARAMETER_OUT_OF_ENVELOPE",
                detail=(
                    f"ultrasound time {ultrasound_s} s is outside the admitted envelope "
                    f"[{ULTRASOUND_MIN_S}, {ULTRASOUND_MAX_S}] s"
                ),
                channel_id="instrument.ultrasound_s",
                recoverable=True,
            )
        )
    cleaned = None
    if request.sample is not None:
        kept = {
            key: value
            for key, value in request.sample.state.items()
            if not key.startswith(_CLEARED_STATE_PREFIXES)
        }
        cleaned = request.sample.model_copy(update={"state": kept})
    elif not reasons:
        reasons.append(
            RuntimeReason(
                code="SAMPLE_STATE_UNAVAILABLE",
                detail="no sample is in custody for this cleaning to act on",
                channel_id="sample.state",
                recoverable=True,
            )
        )
    return InstrumentResult(
        outputs={
            "instrument.rinse_volume_ml": rinse_volume_ml,
            "instrument.ultrasound_s": ultrasound_s,
        },
        uncertainty={"instrument.rinse_volume_ml": RESIDUAL_VOLUME_UNCERTAINTY_ML},
        cost_usd=0.0,
        duration_s=ultrasound_s + 30.0,
        reasons=reasons,
        sample=cleaned,
    )
