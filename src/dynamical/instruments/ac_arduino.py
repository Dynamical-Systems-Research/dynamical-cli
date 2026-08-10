"""Arduino-controlled ultrasonic conditioning: bounded duration and setpoint.

Instrument physics only. No objective, no experiment order, no stopping
rule, and no invented reaction-progress metric -- conditioning state is
reported as the delivered duration and power setpoint, nothing else. The AC
SDL1 archive contains no experimental conditioning data, so these bounds and
uncertainties are declared engineering assumptions, not fitted or validated
figures.
"""

from __future__ import annotations

from ..reasons import RuntimeReason
from . import InstrumentRequest, InstrumentResult, register

DURATION_MIN_S = 0.0
DURATION_MAX_S = 1800.0  # 30 minutes, declared conditioning envelope

SETPOINT_MIN_PERCENT = 0.0
SETPOINT_MAX_PERCENT = 100.0

# Declared, not measured: relay/timer resolution assumptions.
DURATION_RELATIVE_UNCERTAINTY = 0.02
SETPOINT_ABSOLUTE_UNCERTAINTY_PERCENT = 2.0


@register("condition-ultrasonic", "ac-arduino-simulator")
def condition_ultrasonic(request: InstrumentRequest) -> InstrumentResult:
    duration_s = float(request.parameters["duration_s"])
    setpoint_percent = float(request.parameters.get("setpoint_percent", 100.0))
    reasons: list[RuntimeReason] = []
    if not DURATION_MIN_S <= duration_s <= DURATION_MAX_S:
        reasons.append(
            RuntimeReason(
                code="PARAMETER_OUT_OF_ENVELOPE",
                detail=(
                    f"duration {duration_s} s is outside the admitted envelope "
                    f"[{DURATION_MIN_S}, {DURATION_MAX_S}] s"
                ),
                channel_id="instrument.duration_s",
                recoverable=True,
            )
        )
    if not SETPOINT_MIN_PERCENT <= setpoint_percent <= SETPOINT_MAX_PERCENT:
        reasons.append(
            RuntimeReason(
                code="PARAMETER_OUT_OF_ENVELOPE",
                detail=(
                    f"setpoint {setpoint_percent}% is outside the admitted envelope "
                    f"[{SETPOINT_MIN_PERCENT}, {SETPOINT_MAX_PERCENT}]%"
                ),
                channel_id="instrument.setpoint_percent",
                recoverable=True,
            )
        )
    return InstrumentResult(
        outputs={
            "instrument.conditioning_duration_s": duration_s,
            "instrument.conditioning_setpoint_percent": setpoint_percent,
        },
        uncertainty={
            "instrument.conditioning_duration_s": max(
                0.05, duration_s * DURATION_RELATIVE_UNCERTAINTY
            ),
            "instrument.conditioning_setpoint_percent": SETPOINT_ABSOLUTE_UNCERTAINTY_PERCENT,
        },
        cost_usd=0.0,
        duration_s=duration_s,
        reasons=reasons,
    )
