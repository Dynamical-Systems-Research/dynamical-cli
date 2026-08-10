"""Sample transfer between workstations: a custody change, not a physics model.

Isaac Sim supplies the physical motion -- pose, contact, transport -- for a
transfer. This model supplies only what Isaac's geometry engine does not
itself track as a scientific-process outcome: the confirmed destination
station and custody state of the sample that moved. It carries no numeric
operating envelope of its own; custody and lineage validity are enforced
downstream by ``dynamical.samples.check_invariants``, not by this model. No
objective, no experiment order, no stopping rule.
"""

from __future__ import annotations

from ..reasons import RuntimeReason
from ..samples import Sample
from . import InstrumentRequest, InstrumentResult, register

# Declared, not measured: an engineering assumption that a commanded
# transfer arrives as planned unless the caller explicitly reports
# otherwise via the ``arrival_confirmed`` parameter.
ARRIVAL_CONFIRMED_DEFAULT = True


@register("transfer-sample", "ac-transfer-simulator")
def transfer_sample(request: InstrumentRequest) -> InstrumentResult:
    reasons: list[RuntimeReason] = []
    to_station = request.parameters.get("to_station")
    if not to_station:
        reasons.append(
            RuntimeReason(
                code="PARAMETER_OUT_OF_ENVELOPE",
                detail="transfer-sample requires a non-empty 'to_station' parameter",
                channel_id="instrument.to_station",
                recoverable=False,
            )
        )
        return InstrumentResult(
            outputs={}, uncertainty={}, cost_usd=0.0, duration_s=0.0, reasons=reasons
        )
    to_station = str(to_station)
    arrival_confirmed = bool(request.parameters.get("arrival_confirmed", ARRIVAL_CONFIRMED_DEFAULT))
    custody_state = "held" if arrival_confirmed else "in_transit"

    existing = request.sample
    if existing is not None:
        updated = existing.model_copy(
            update={"station_id": to_station, "custody_state": custody_state}
        )
    else:
        sample_id = request.parameters.get("sample_id")
        if not sample_id:
            reasons.append(
                RuntimeReason(
                    code="PARAMETER_OUT_OF_ENVELOPE",
                    detail=(
                        "transfer-sample has no sample in custody and no 'sample_id' "
                        "parameter to materialize one from"
                    ),
                    channel_id="instrument.sample_id",
                    recoverable=False,
                )
            )
            return InstrumentResult(
                outputs={}, uncertainty={}, cost_usd=0.0, duration_s=0.0, reasons=reasons
            )
        sample_id = str(sample_id)
        updated = Sample(
            id=sample_id,
            station_id=to_station,
            custody_state=custody_state,
            quantity=float(request.parameters.get("quantity", 0.0)),
            unit=str(request.parameters.get("unit", "mL")),
            created_by_step_id=sample_id,
        )

    return InstrumentResult(
        outputs={
            "instrument.sample_station_id": to_station,
            "instrument.arrival_confirmed": arrival_confirmed,
            "sample.state.transferred": updated.id,
        },
        uncertainty={},
        cost_usd=0.0,
        duration_s=float(request.parameters.get("duration_s", 0.0)),
        reasons=reasons,
        sample=updated,
    )
