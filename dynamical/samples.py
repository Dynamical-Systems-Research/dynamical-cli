"""Sample identity, custody and a minimal conserved-quantity ledger.

Deliberately shallow: identity, one parent edge, station custody, and enough
quantity accounting to audit a Faraday-derived deposited mass against charge
passed and to debit a reservoir on aliquot. No split/merge graph, no
cross-campaign ledger.

A sample-moving action (a transfer, an aliquot, or a consume) declares its
``SampleTransition`` under ``action.parameters["sample_transition"]``. Any other
action that reads or acts on a sample already in custody declares only
``action.sample_id``; :func:`check_invariants` then requires that sample to be
held at the acting *workstation* (``action.station_id`` -- resolved at
campaign-build time from the selected provider's binding, not the acting
instrument's own endpoint id, which is a different vocabulary that never
matches a workstation id).

This module is the one place lineage is enforced. It is wired into
``dynamical.campaign.validate_events`` -- the existing trace validator -- rather
than standing up a separate validation subsystem, so both simulate and replay
gain the same four invariants for free.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .reasons import RuntimeReason

if TYPE_CHECKING:
    from .campaign import TraceEvent

CustodyState = Literal["held", "in_transit", "consumed"]
TransitionKind = Literal["transfer", "aliquot", "consume"]


def state_digest(state: Mapping[str, float]) -> str:
    """Canonical digest of a sample's scientific state (matches stable_hash)."""

    return hashlib.sha256(
        json.dumps(dict(state), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
            "utf-8"
        )
    ).hexdigest()


class SampleLineageError(ValueError):
    """A sample transition cannot be folded into the ledger as recorded."""


class Sample(BaseModel):
    """One physical sample's identity, custody, and remaining quantity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    station_id: str = Field(min_length=1)
    custody_state: CustodyState
    quantity: float = Field(ge=0.0)
    unit: str = Field(min_length=1)
    created_by_step_id: str = Field(min_length=1)
    parent_sample_ids: list[str] = Field(default_factory=list)
    # Process state written onto this sample by the instruments that acted on
    # it, keyed by the producing operation's output port id. This is what makes
    # a later measurement evidence about *this* material rather than about its
    # own requested parameters: without it, a measurement model has no way to
    # see what an upstream process did.
    state: dict[str, float] = Field(default_factory=dict)


class SampleTransition(BaseModel):
    """One custody-changing event: a transfer, an aliquot, or a consume.

    ``step_id`` is optional context (the action_id that produced the
    transition) used only to attribute a lost-sample reason to the step that
    last moved it; it is not part of the transition's own identity.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: TransitionKind
    sample_id: str = Field(min_length=1)
    from_station: str = Field(min_length=1)
    to_station: str = Field(min_length=1)
    quantity_delta: float
    unit: str = Field(min_length=1)
    timestamp_s: float = Field(ge=0.0)
    arrival_confirmed: bool
    parent_sample_ids: list[str] = Field(default_factory=list)
    step_id: str | None = None
    # Digest of the sample's scientific state after this transition, so a
    # trace's custody record also pins what the sample scientifically *was*
    # when it moved. Baked transitions inherit the compiled pack's action
    # equality check, which anchors this digest to the verified pack.
    state_sha256: str | None = None


def apply_transition(
    samples: Mapping[str, Sample], transition: SampleTransition
) -> dict[str, Sample]:
    """Fold one transition into the sample ledger, returning an updated copy.

    ``samples`` is never mutated; callers fold a trace one transition at a
    time and keep the returned mapping as the new current state.
    """

    updated = dict(samples)
    custody_state: CustodyState = "held" if transition.arrival_confirmed else "in_transit"

    if transition.kind == "aliquot":
        for parent_id in transition.parent_sample_ids:
            parent = updated.get(parent_id)
            if parent is None:
                raise SampleLineageError(f"aliquot references an unknown parent: {parent_id}")
            updated[parent_id] = parent.model_copy(
                update={"quantity": parent.quantity - transition.quantity_delta}
            )
        existing = updated.get(transition.sample_id)
        base_quantity = existing.quantity if existing is not None else 0.0
        updated[transition.sample_id] = Sample(
            id=transition.sample_id,
            parent_sample_ids=list(transition.parent_sample_ids),
            station_id=transition.to_station,
            custody_state=custody_state,
            quantity=base_quantity + transition.quantity_delta,
            unit=transition.unit,
            created_by_step_id=transition.step_id or transition.sample_id,
        )
        return updated

    if transition.kind == "transfer":
        existing = updated.get(transition.sample_id)
        if existing is not None:
            quantity = existing.quantity + transition.quantity_delta
            parent_ids = transition.parent_sample_ids or list(existing.parent_sample_ids)
            created_by = existing.created_by_step_id
            # A transfer changes custody, not chemistry: the sample's
            # scientific state travels with it.
            state = dict(existing.state)
        else:
            quantity = transition.quantity_delta
            parent_ids = list(transition.parent_sample_ids)
            created_by = transition.step_id or transition.sample_id
            state = {}
        updated[transition.sample_id] = Sample(
            id=transition.sample_id,
            parent_sample_ids=parent_ids,
            station_id=transition.to_station,
            custody_state=custody_state,
            quantity=quantity,
            unit=transition.unit,
            created_by_step_id=created_by,
            state=state,
        )
        return updated

    if transition.kind == "consume":
        existing = updated.get(transition.sample_id)
        if existing is None:
            raise SampleLineageError(
                f"consume references an unknown sample: {transition.sample_id}"
            )
        updated[transition.sample_id] = existing.model_copy(
            update={
                "quantity": max(0.0, existing.quantity - transition.quantity_delta),
                "custody_state": "consumed",
            }
        )
        return updated

    raise SampleLineageError(f"unsupported transition kind: {transition.kind}")


def build_transition(
    moved: Sample,
    *,
    current_sample: Sample | None,
    from_station_hint: str,
    timestamp_s: float,
    step_id: str,
) -> SampleTransition:
    """Build the ``SampleTransition`` a sample-moving instrument model's returned
    ``Sample`` implies, given the sample's prior custody (if any is already known).

    The one place this construction happens, so a runner that computes it live
    (``campaign.run_composed_campaign``) and one that bakes it in at compile
    time from the same registered instrument model, over already-resolved,
    fixed parameters (``backends._runtime_pack.runtime_campaign``, whose
    ``transfer-sample`` operation carries no live/measured dependency Isaac
    would need to supply) cannot drift apart on what a transition means.

    ``from_station_hint`` is used only when there is no ``current_sample`` (the
    sample's first-ever mention in this run): the caller's best declared guess
    at where it started (e.g. an explicit ``from_station`` parameter, a
    last-known station, or the acting workstation itself).
    """

    from_station = current_sample.station_id if current_sample is not None else from_station_hint
    prior_quantity = current_sample.quantity if current_sample is not None else 0.0
    return SampleTransition(
        kind="transfer",
        sample_id=moved.id,
        from_station=from_station,
        to_station=moved.station_id,
        quantity_delta=moved.quantity - prior_quantity,
        unit=moved.unit,
        timestamp_s=timestamp_s,
        arrival_confirmed=moved.custody_state == "held",
        parent_sample_ids=list(moved.parent_sample_ids),
        state_sha256=state_digest(moved.state),
        step_id=step_id,
    )


def establish_origin(
    samples: Mapping[str, Sample], *, sample_id: str, station_id: str, step_id: str
) -> dict[str, Sample]:
    """Record a sample's first-known custody at the acting station, if it has none yet.

    A sample's very first mention in a trace often has no upstream transfer --
    a campaign-declared ``sample_state`` input has no "create sample"
    operation, it is simply given -- so that first mention establishes the
    sample's origin at the station where it was honestly reported, rather than
    being treated as proof of a missing transfer. A later action reporting the
    *same* sample at a *different* station is still a real mismatch; callers
    must check for that themselves before calling this (it is a no-op when
    ``sample_id`` is already known, deliberately not "fixing" a conflict).
    """

    if sample_id in samples:
        return dict(samples)
    updated = dict(samples)
    updated[sample_id] = Sample(
        id=sample_id,
        station_id=station_id,
        custody_state="held",
        quantity=0.0,
        unit="1",
        created_by_step_id=step_id,
    )
    return updated


def _embedded_transition(parameters: Mapping[str, Any]) -> SampleTransition | None:
    raw = parameters.get("sample_transition")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise SampleLineageError("action.parameters['sample_transition'] must be an object")
    return SampleTransition.model_validate(dict(raw))


def _seed_invariant_state(
    initial_samples: Sequence[Sample],
) -> tuple[dict[str, Sample], dict[str, set[str]], dict[str, str]]:
    samples = {sample.id: sample for sample in initial_samples}
    station_occupants: dict[str, set[str]] = {}
    for sample in initial_samples:
        if sample.custody_state == "held":
            station_occupants.setdefault(sample.station_id, set()).add(sample.id)
    return (
        samples,
        station_occupants,
        {sample.id: state_digest(sample.state) for sample in initial_samples},
    )


def check_invariants(
    events: Sequence[TraceEvent], initial_samples: Sequence[Sample] = ()
) -> list[RuntimeReason]:
    """Walk one trace and enforce sample identity, custody, and transfer lineage.

    Four invariants, one reason code each:

    - ``SAMPLE_LOST``: a sample departs a station (a transition with
      ``arrival_confirmed=False``) and the trace ends without ever confirming
      its arrival anywhere.
    - ``SAMPLE_LOCATION_CONFLICT``: a transfer claims to depart a station other
      than the sample's last confirmed location -- the trace's own bookkeeping
      would leave the sample resident at two stations at once.
    - ``SAMPLE_TRANSFER_MISSING``: an action reads or acts on a sample_id that
      is known to be held at a *different* workstation than the one it acted
      at. A sample's first-ever mention in the trace (no prior custody
      recorded anywhere) establishes its origin at the acting workstation
      instead of being flagged -- see :func:`establish_origin`.
    """

    reasons: list[RuntimeReason] = []
    samples, station_occupants, last_state_digest = _seed_invariant_state(initial_samples)

    for event in events:
        action = event.action
        if action is None:
            continue
        step_id = action.action_id
        transition = _embedded_transition(action.parameters)

        if transition is not None:
            if transition.kind == "transfer":
                current = samples.get(transition.sample_id)
                if current is not None and current.station_id != transition.from_station:
                    reasons.append(
                        RuntimeReason(
                            code="SAMPLE_LOCATION_CONFLICT",
                            detail=(
                                f"sample {transition.sample_id!r} transfer departs "
                                f"{transition.from_station!r} but was last confirmed at "
                                f"{current.station_id!r}"
                            ),
                            step_id=step_id,
                            recoverable=False,
                        )
                    )

            samples = apply_transition(samples, transition)
            # Keep station occupancy synchronized with custody: a departing
            # sample vacates its source station, so a later action there is not
            # falsely attributed to a sample that has moved on. Clear the source
            # before recording the destination so a same-station transfer still
            # leaves the sample resident.
            station_occupants.get(transition.from_station, set()).discard(transition.sample_id)
            sample = samples[transition.sample_id]
            if sample.custody_state == "held":
                station_occupants.setdefault(sample.station_id, set()).add(sample.id)
            continue

        sample_id = action.sample_id
        if sample_id is None:
            # An unattributed action is only innocent if no sample is present
            # where it ran. If a sample is held at this workstation and the
            # action does not name it, custody for that action is unverifiable
            # -- and simply dropping sample_id would otherwise be a way to make
            # any lineage violation disappear.
            occupants = station_occupants.get(action.station_id, set())
            if occupants:
                reasons.append(
                    RuntimeReason(
                        code="SAMPLE_ATTRIBUTION_MISSING",
                        detail=(
                            f"action {step_id!r} ran at {action.station_id!r} where samples "
                            f"{sorted(occupants)!r} are held, but names no sample, so what it "
                            "acted on cannot be established"
                        ),
                        step_id=step_id,
                        recoverable=False,
                    )
                )
            continue
        sample = samples.get(sample_id)
        if sample is None:
            if action.station_id is None:
                reasons.append(
                    RuntimeReason(
                        code="SAMPLE_TRANSFER_MISSING",
                        detail=(
                            f"action {step_id!r} operates on sample {sample_id!r} with no "
                            "resolved acting workstation, so its custody cannot be confirmed"
                        ),
                        step_id=step_id,
                        recoverable=False,
                    )
                )
            else:
                samples = establish_origin(
                    samples, sample_id=sample_id, station_id=action.station_id, step_id=step_id
                )
                # Record the sample as occupying the station where it originated,
                # so a subsequent sample-free action there is attribution-checked
                # rather than evadable by dropping sample_id after the origin.
                station_occupants.setdefault(action.station_id, set()).add(sample_id)
        elif sample.station_id != action.station_id or sample.custody_state != "held":
            reasons.append(
                RuntimeReason(
                    code="SAMPLE_TRANSFER_MISSING",
                    detail=(
                        f"action {step_id!r} operates on sample {sample_id!r} at workstation "
                        f"{action.station_id!r}, but it is currently {sample.custody_state} at "
                        f"{sample.station_id!r}"
                    ),
                    step_id=step_id,
                    recoverable=False,
                )
            )

    for sample in samples.values():
        if sample.custody_state == "in_transit":
            reasons.append(
                RuntimeReason(
                    code="SAMPLE_LOST",
                    detail=(
                        f"sample {sample.id!r} departed for {sample.station_id!r} and never "
                        "confirmed arrival"
                    ),
                    step_id=sample.created_by_step_id,
                    recoverable=False,
                )
            )

    # SAMPLE_STATE_DISCONTINUOUS: scientific state must be continuous across
    # every instrument action. An observation that only *read* a sample
    # (``sample_state_written`` false) must carry exactly the state digest the
    # previous writer recorded -- otherwise the measurement claims to have
    # read a state no upstream process produced.
    for event in events:
        provenance = event.provenance or {}
        sample_id = provenance.get("sample_id")
        digest = provenance.get("sample_state_sha256")
        if not sample_id or not digest:
            continue
        previous = last_state_digest.get(str(sample_id))
        is_read = not provenance.get("sample_state_written")
        if is_read and previous is not None and digest != previous:
            reasons.append(
                RuntimeReason(
                    code="SAMPLE_STATE_DISCONTINUOUS",
                    detail=(
                        f"an observation reads sample {sample_id!r} at state {digest!r}, "
                        "but the last recorded state written to that sample was "
                        f"{previous!r}; the measurement cannot be attributed to any "
                        "recorded process state"
                    ),
                    recoverable=False,
                )
            )
        last_state_digest[str(sample_id)] = str(digest)

    return reasons
