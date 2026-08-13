"""Chemical-bath LDH deposition on Ni foam with simulator evidence.

Instrument process only. No objective, no experiment order, no stopping
rule. Simulates the FastCat robotic bath-synthesis step (DOI
10.11583/DTU.28494185): the requested dopant fractions are written onto the
sample as the deposited nominal composition, with durations from the
dataset's synthesis-time statistics. Process evidence only; scientific
outcome prediction belongs to the calibrated measure-oer twin.
"""

from __future__ import annotations

from ..reasons import RuntimeReason
from . import InstrumentRequest, InstrumentResult, register

DOPANTS = ("Cr", "Al", "Fe", "Co", "Mn", "Ni", "Cu", "Zn")
SIMPLEX_TOLERANCE = 2.5e-3
TYPICAL_DURATION_S = 600.0


@register("deposit-chemical-bath", "ac-bath-simulator")
def deposit_chemical_bath(request: InstrumentRequest) -> InstrumentResult:
    reasons: list[RuntimeReason] = []
    fractions = {}
    for el in DOPANTS:
        fractions[el] = float(request.parameters.get(f"fraction_{el.lower()}", 0.0))
    total = sum(fractions.values())
    outputs: dict[str, float | None] = {f"deposited_fraction_{el}": None for el in DOPANTS}
    outputs["bath_synthesis_time_s"] = None
    valid = abs(total - 1.0) <= SIMPLEX_TOLERANCE and all(
        0.0 <= v <= 1.0 for v in fractions.values()
    )
    if not valid:
        reasons.append(
            RuntimeReason(
                code="PARAMETER_OUT_OF_ENVELOPE",
                detail=(
                    f"dopant fractions must lie in [0, 1] and sum to 1 "
                    f"(+/- {SIMPLEX_TOLERANCE}); received sum {total:.6f}"
                ),
                channel_id="instrument.dopant_fractions",
                recoverable=True,
            )
        )
        return InstrumentResult(
            outputs=outputs,
            uncertainty={},
            cost_usd=0.0,
            duration_s=0.0,
            reasons=reasons,
            sample=request.sample,
        )
    synthesis_time = float(request.parameters.get("synthesis_time_s", TYPICAL_DURATION_S))
    for el in DOPANTS:
        outputs[f"deposited_fraction_{el}"] = fractions[el]
    outputs["bath_synthesis_time_s"] = synthesis_time
    deposited = None
    if request.sample is not None:
        state = {**request.sample.state}
        for el in DOPANTS:
            state[f"deposited_fraction_{el}"] = fractions[el]
        state["bath_synthesis"] = 1.0
        deposited = request.sample.model_copy(update={"state": state})
    else:
        reasons.append(
            RuntimeReason(
                code="SAMPLE_STATE_UNAVAILABLE",
                detail="no sample in custody to deposit onto",
                channel_id="sample.state",
                recoverable=True,
            )
        )
    return InstrumentResult(
        outputs=outputs,
        uncertainty={},
        cost_usd=0.0,
        duration_s=synthesis_time,
        reasons=reasons,
        sample=deposited,
    )
