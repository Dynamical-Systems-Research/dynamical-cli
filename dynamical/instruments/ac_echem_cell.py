"""Electrochemical-cell loading: mount the working electrode in the cell.

Instrument physics only. No objective, no experiment order, no stopping rule.
Loading seats the sample's working electrode in the electrochemical cell
(body, cap, and foil base are exact source-derived geometry) so downstream
electrochemistry acts on a mounted electrode. This is a fixture state change
with declared seating assumptions, not a measured seal or contact model;
physical motion is supplied by the execution backend.
"""

from __future__ import annotations

from ..reasons import RuntimeReason
from . import InstrumentRequest, InstrumentResult, register

# Declared, not measured: a commanded loading seats correctly unless the
# caller explicitly reports otherwise.
SEATED_DEFAULT = True


@register("load-electrochemical-cell", "ac-cell-loading-simulator")
def load_electrochemical_cell(request: InstrumentRequest) -> InstrumentResult:
    cell_id = request.parameters.get("cell_id")
    reasons: list[RuntimeReason] = []
    if not cell_id:
        reasons.append(
            RuntimeReason(
                code="PARAMETER_OUT_OF_ENVELOPE",
                detail="load-electrochemical-cell requires a non-empty 'cell_id' parameter",
                channel_id="instrument.cell_id",
                recoverable=False,
            )
        )
        return InstrumentResult(
            outputs={}, uncertainty={}, cost_usd=0.0, duration_s=0.0, reasons=reasons
        )
    seated = bool(request.parameters.get("seated", SEATED_DEFAULT))
    loaded = None
    if request.sample is not None:
        loaded = request.sample.model_copy(
            update={"state": {**request.sample.state, "cell_loaded": 1.0 if seated else 0.0}}
        )
    else:
        reasons.append(
            RuntimeReason(
                code="SAMPLE_STATE_UNAVAILABLE",
                detail="no sample is in custody for this cell loading to act on",
                channel_id="sample.state",
                recoverable=True,
            )
        )
    return InstrumentResult(
        outputs={
            "instrument.cell_id": str(cell_id),
            "instrument.cell_seated": seated,
        },
        uncertainty={},
        cost_usd=0.0,
        duration_s=float(request.parameters.get("duration_s", 60.0)),
        reasons=reasons,
        sample=loaded,
    )
