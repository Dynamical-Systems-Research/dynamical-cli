"""SDL1 pipette command bookkeeping; physical delivered volumes are unknown.

SDL1 2c5a911 example/experiment.py:1145-1154 truncates mixture volumes to
integer uL; 1180-1185 and 1198-1235 issue pipette strokes of at most 1000 uL.
The electrolyte path also uses the pipette (1432-1500), never a rinse pump.
No volume accuracy or elapsed-time measurement is supplied by this adapter.
"""

from __future__ import annotations

import math

from ..reasons import RuntimeReason
from . import InstrumentRequest, InstrumentResult, register

# Destination geometry, not a measured delivery tolerance or a stock capacity.
DISPENSE_VOLUME_MIN_ML = ALIQUOT_VOLUME_MIN_ML = 0.0
DISPENSE_VOLUME_MAX_ML = ALIQUOT_VOLUME_MAX_ML = 3.895
# SDL1 src/openTron_electrodeposition/parameters.py:74-96.
ADMITTED_CHEMICALS = ("Ni", "Fe", "Cr", "Mn", "Co", "Zn", "Cu", "NH4OH", "NaCi", "KOH")


def _dispense(request: InstrumentRequest, *, integer_ul: bool) -> InstrumentResult:
    volume = float(request.parameters["volume_ml"])
    chemical = request.parameters.get("chemical")
    reasons: list[RuntimeReason] = []
    valid = math.isfinite(volume) and 0.0 <= volume <= DISPENSE_VOLUME_MAX_ML
    if chemical is not None and chemical not in ADMITTED_CHEMICALS:
        valid = False
    commanded = (int(volume * 1000) / 1000 if integer_ul else volume) if valid else None
    state = dict(request.sample.state) if request.sample is not None else {}
    prior_volume = sum(v for k, v in state.items() if k.startswith("electrolyte_commanded."))
    if commanded is not None and prior_volume + commanded > DISPENSE_VOLUME_MAX_ML:
        valid = False
        commanded = None
    if not valid:
        reasons.append(
            RuntimeReason(
                code="PARAMETER_OUT_OF_ENVELOPE",
                detail=(
                    "Pipette command requires finite nonnegative volume within the 3.895 mL "
                    "destination geometry and an admitted stock; cumulative nominal "
                    "inventory must fit the well."
                ),
                channel_id="instrument.volume_ml",
                recoverable=True,
            )
        )
    sample = None
    if valid and request.sample is not None:
        key = f"electrolyte_commanded.{chemical if chemical is not None else 'unspecified'}_ml"
        state[key] = state.get(key, 0.0) + commanded
        sample = request.sample.model_copy(update={"state": state})
    return InstrumentResult(
        outputs={
            "volume_requested_ml": volume if math.isfinite(volume) else None,
            "volume_commanded_ml": commanded,
            "volume_applied_ml": None,
        },
        # The runtime otherwise copies requested parameters into applied telemetry.
        # These adapters record commands, not physically applied settings.
        applied_parameters={name: None for name in request.parameters},
        uncertainty={},
        cost_usd=0.0,
        duration_s=0.0,
        reasons=reasons,
        sample=sample,
    )


@register("dispense-electrolyte", "ac-ot2-simulator")
def dispense_electrolyte(request: InstrumentRequest) -> InstrumentResult:
    return _dispense(request, integer_ul=False)


@register("aliquot-to-well", "ac-ot2-simulator")
def aliquot_to_well(request: InstrumentRequest) -> InstrumentResult:
    return _dispense(request, integer_ul=True)
