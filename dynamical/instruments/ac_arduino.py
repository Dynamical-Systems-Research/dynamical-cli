"""SDL1 temperature and timed-ultrasound commands, without response predictions.

SDL1 2c5a911 src/openTron_electrodeposition/ardu.py:155-171 sets temperature;
246-263 and 350-359 command a timed relay. There is no power setpoint.
The admitted cartridge-1 35 C / 30 s recipe is example/main.py:134-141.
Cleaning commands are separate; cartridge-0 tool cleaning is not well conditioning.
These are source command points, not measured hardware validity ranges.
"""

from __future__ import annotations

from ..reasons import RuntimeReason
from . import InstrumentRequest, InstrumentResult, register


@register("condition-ultrasonic", "ac-arduino-simulator")
def condition_ultrasonic(request: InstrumentRequest) -> InstrumentResult:
    duration = float(request.parameters["duration_s"])
    temperature = float(request.parameters["temperature_setpoint_c"])
    valid = duration == 30.0 and temperature == 35.0
    reasons = (
        []
        if valid
        else [
            RuntimeReason(
                code="PARAMETER_OUT_OF_ENVELOPE",
                detail=(
                    "Source recipe admits temperature setpoint 35 C and ultrasound command "
                    "duration 30 s on cartridge 1; no power control or measured response "
                    "envelope is available."
                ),
                channel_id="instrument.temperature_setpoint_c",
                recoverable=True,
            )
        ]
    )
    return InstrumentResult(
        outputs={
            "instrument.ultrasound_commanded_s": duration if valid else None,
            "instrument.temperature_setpoint_c": temperature if valid else None,
            "instrument.temperature_observed_c": None,
        },
        # The runtime otherwise copies requested parameters into applied telemetry.
        # These adapters record commands, not physically applied settings.
        applied_parameters={name: None for name in request.parameters},
        uncertainty={},
        cost_usd=0.0,
        duration_s=0.0,
        reasons=reasons,
    )
