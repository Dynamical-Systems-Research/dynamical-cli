"""SDL1 cathodic deposition commands, not a calibrated film-response model.

SDL1 2c5a911 parameters.py:4-6 supplies area and current magnitude;
example/experiment.py:603-607 applies its negative with 0.1 s sampling.
example/main.py:138-139 requests 60 s at 35 C; experiment.py:568-572 also
exposes the 10 s default. Deposition is followed by OCP for 120 s at 0.2 s
sampling (experiment.py:628). No delivered charge, current efficiency,
composition transfer, mass, thickness, or elapsed-time measurement is inferred.
"""

from __future__ import annotations

from ..reasons import RuntimeReason
from . import InstrumentRequest, InstrumentResult, register

ELECTRODE_AREA_CM2 = 0.2827
CURRENT_MIN_A = CURRENT_MAX_A = -0.002827


@register("electrodeposit-constant-current", "ac-squidstat-simulator")
def electrodeposit(request: InstrumentRequest) -> InstrumentResult:
    current = float(request.parameters["current_a"])
    duration = float(request.parameters["duration_s"])
    temperature = float(request.parameters["temperature_setpoint_c"])
    valid = current == CURRENT_MIN_A and duration in (10.0, 60.0) and temperature == 35.0
    reasons = (
        []
        if valid
        else [
            RuntimeReason(
                code="PARAMETER_OUT_OF_ENVELOPE",
                detail=(
                    "SDL1 source command points are -0.002827 A, 10 or 60 s, and the "
                    "reference recipe temperature setpoint 35 C; physical film response "
                    "remains unknown."
                ),
                channel_id="instrument.current_a",
                recoverable=True,
            )
        ]
    )
    outputs = {
        "commanded_charge_c": current * duration if valid else None,
        "current_density_a_cm2": current / ELECTRODE_AREA_CM2 if valid else None,
        "deposited_mass_g": None,
        "deposited_thickness_um": None,
    }
    sample = None
    if valid and request.sample is not None:
        # A new processing command makes prior current-film observations stale.
        # Their historical trace remains; they cannot qualify this new film.
        state = {
            key: value
            for key, value in request.sample.state.items()
            if not key.startswith(("deposited_", "deposition_precursor_commanded."))
        }
        state.update(
            {
                "deposition_commanded": 1.0,
                "deposition_commanded_current_a": current,
                "deposition_commanded_duration_s": duration,
                "deposition_temperature_setpoint_c": temperature,
                "deposition_commanded_charge_c": current * duration,
            }
        )
        for key, value in request.sample.state.items():
            if key.startswith("electrolyte_commanded."):
                state[
                    key.replace("electrolyte_commanded.", "deposition_precursor_commanded.", 1)
                ] = value
        sample = request.sample.model_copy(update={"state": state})
    return InstrumentResult(
        outputs=outputs,
        sample=sample,
        # The runtime otherwise copies requested parameters into applied telemetry.
        # These adapters record commands, not physically applied settings.
        applied_parameters={name: None for name in request.parameters},
        uncertainty={},
        cost_usd=0.0,
        duration_s=0.0,
        reasons=reasons,
    )
