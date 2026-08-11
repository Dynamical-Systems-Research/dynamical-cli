"""The four lineage invariants must fail a run, not merely be recorded."""

import pytest
from _fixtures import _sample_action, _sample_trace

from dynamical.samples import Sample, SampleTransition, apply_transition, check_invariants


def _transfer(action_id, sample_id, from_station, to_station, ts):
    return _sample_action(
        action_id=action_id,
        actor_id="ac-transfer-model",
        station_id=to_station,
        sample_id=sample_id,
        transition={
            "kind": "transfer",
            "sample_id": sample_id,
            "from_station": from_station,
            "to_station": to_station,
            "quantity_delta": 1.0,
            "unit": "mL",
            "timestamp_s": ts,
            "arrival_confirmed": True,
            "parent_sample_ids": [],
        },
    )


def test_vacated_station_does_not_falsely_flag_a_sample_free_action():
    """After a sample transfers off station A, a later sample-free action at A
    must not be flagged: A is empty, so there is nothing to attribute. (Before
    the occupancy-sync fix, the departed sample lingered in the occupancy map
    and produced a false SAMPLE_ATTRIBUTION_MISSING.)"""

    events = _sample_trace(
        "vacated",
        [
            _transfer("materialize", "sample-1", "intake", "station-a", 1.0),
            _transfer("move", "sample-1", "station-a", "station-b", 2.0),
            _sample_action(
                action_id="idle", actor_id="ac-x", station_id="station-a", sample_id=None
            ),
        ],
    )
    reasons = check_invariants(events)
    assert not any(r.code == "SAMPLE_ATTRIBUTION_MISSING" for r in reasons), reasons
    assert reasons == []


def test_origin_then_dropped_sample_id_is_caught_as_attribution_missing():
    """Establishing a sample's origin at a station occupies it. A subsequent
    sample-free action there must be flagged rather than silently evading the
    attribution check by dropping sample_id after the origin."""

    events = _sample_trace(
        "origin-evasion",
        [
            _sample_action(
                action_id="deposit",
                actor_id="ac-squidstat-model",
                station_id="station-a",
                sample_id="sample-1",
            ),
            _sample_action(
                action_id="idle", actor_id="ac-x", station_id="station-a", sample_id=None
            ),
        ],
    )
    reasons = check_invariants(events)
    assert any(r.code == "SAMPLE_ATTRIBUTION_MISSING" for r in reasons), reasons


def test_sample_disappearing_fails(trace_with_vanished_sample):
    reasons = check_invariants(trace_with_vanished_sample)
    assert any(r.code == "SAMPLE_LOST" for r in reasons)


def test_sample_in_two_stations_fails(trace_with_forked_sample):
    reasons = check_invariants(trace_with_forked_sample)
    assert any(r.code == "SAMPLE_LOCATION_CONFLICT" for r in reasons)


def test_action_without_completed_transfer_fails(trace_with_missing_transfer):
    reasons = check_invariants(trace_with_missing_transfer)
    assert any(r.code == "SAMPLE_TRANSFER_MISSING" for r in reasons)


def test_clean_lineage_passes(trace_with_complete_lineage):
    assert check_invariants(trace_with_complete_lineage) == []


def test_two_samples_can_share_a_workstation():
    events = _sample_trace(
        "shared-workstation",
        [
            _transfer("move-a", "sample-a", "intake-a", "station-a", 1.0),
            _transfer("move-b", "sample-b", "intake-b", "station-a", 2.0),
        ],
    )

    assert check_invariants(events) == []


def test_moving_one_sample_keeps_the_other_sample_at_the_workstation():
    events = _sample_trace(
        "shared-workstation-attribution",
        [
            _transfer("move-a", "sample-a", "intake-a", "station-a", 1.0),
            _transfer("move-b", "sample-b", "intake-b", "station-a", 2.0),
            _transfer("move-b-out", "sample-b", "station-a", "station-b", 3.0),
            _sample_action(
                action_id="unattributed",
                actor_id="ac-x",
                station_id="station-a",
                sample_id=None,
            ),
        ],
    )

    reasons = check_invariants(events)
    assert [reason.code for reason in reasons] == ["SAMPLE_ATTRIBUTION_MISSING"]
    assert "sample-a" in reasons[0].detail


def test_aliquot_debits_the_parent():
    stock = Sample(
        id="electrolyte-stock",
        station_id="ot2-liquid-handling",
        custody_state="held",
        quantity=25.0,
        unit="mL",
        created_by_step_id="load",
    )
    transition = SampleTransition(
        kind="aliquot",
        sample_id="well-a1",
        parent_sample_ids=["electrolyte-stock"],
        from_station="ot2-liquid-handling",
        to_station="ot2-liquid-handling",
        quantity_delta=3.895,
        unit="mL",
        timestamp_s=12.0,
        arrival_confirmed=True,
    )
    samples = {"electrolyte-stock": stock}
    updated = apply_transition(samples, transition)
    assert updated["electrolyte-stock"].quantity == pytest.approx(21.105)
    assert updated["well-a1"].quantity == pytest.approx(3.895)
