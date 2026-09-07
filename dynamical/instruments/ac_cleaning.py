"""SDL1 well rinse/drain command sequence; deposit history is retained.

SDL1 2c5a911 example/experiment.py:895-1073 cleans the stationary well.
The recipe invokes this between deposition and test (2044-2063), without
removing the working electrode or declaring its deposit erased. The carried
Ni tool's cartridge cleaning (1358-1386) is a separate procedure.
Command inventory resets do not establish zero residual liquid or unchanged
physical film properties. No cleaning efficacy or pump accuracy is inferred.
"""

from __future__ import annotations

from ..reasons import RuntimeReason
from . import InstrumentRequest, InstrumentResult, register


@register("clean-electrode", "ac-cleaning-simulator")
def clean_electrode(request: InstrumentRequest) -> InstrumentResult:
    use_acid = request.parameters["use_acid"]
    dwell = float(request.parameters["acid_dwell_s"])
    # Calls at experiment.py:2025,2050,2066: acid/30, acid/0.1, water-only/0.
    valid = isinstance(use_acid, bool) and (
        (use_acid and dwell in (0.1, 30.0)) or (not use_acid and dwell == 0.0)
    )
    reasons = (
        []
        if valid
        else [
            RuntimeReason(
                code="PARAMETER_OUT_OF_ENVELOPE",
                detail=(
                    "Source well-cleaning calls use acid with 30 or 0.1 s dwell, or water "
                    "only with 0 s acid dwell."
                ),
                channel_id="instrument.acid_dwell_s",
                recoverable=True,
            )
        ]
    )
    # Four 1 mL initial drains, then two water/2 mL drain cycles;
    # acid mode adds acid plus two water cycles, each with a 2 mL drain.
    outputs = {
        "instrument.water_commanded_ml": (2.0 if use_acid else 1.0) if valid else None,
        "instrument.acid_commanded_ml": (0.5 if use_acid else 0.0) if valid else None,
        "instrument.drain_commanded_ml": (14.0 if use_acid else 8.0) if valid else None,
        "instrument.ultrasound_commanded_s": (25.0 if use_acid else 10.0) if valid else None,
        "instrument.residual_volume_ml": None,
    }
    sample = None
    if valid and request.sample is not None:
        state = {
            k: v
            for k, v in request.sample.state.items()
            if not k.startswith("electrolyte_commanded.")
        }
        sample = request.sample.model_copy(update={"state": state})
    elif valid:
        reasons.append(
            RuntimeReason(
                code="SAMPLE_STATE_UNAVAILABLE",
                detail="No sample is in custody for well cleaning.",
                channel_id="sample.state",
                recoverable=True,
            )
        )
    return InstrumentResult(
        outputs=outputs,
        # The runtime otherwise copies requested parameters into applied telemetry.
        # These adapters record commands, not physically applied settings.
        applied_parameters={name: None for name in request.parameters},
        uncertainty={},
        cost_usd=0.0,
        duration_s=0.0,
        reasons=reasons,
        sample=sample,
    )
