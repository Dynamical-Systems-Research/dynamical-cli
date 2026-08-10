"""The four lineage invariants must fail a run, not merely be recorded."""

import pytest

from dynamical.samples import Sample, SampleTransition, apply_transition, check_invariants


def test_sample_disappearing_fails(trace_with_vanished_sample):
    reasons = check_invariants(trace_with_vanished_sample)
    assert any(r.code == "SAMPLE_LOST" for r in reasons)


def test_identity_change_without_evidence_fails(trace_with_silent_identity_change):
    reasons = check_invariants(trace_with_silent_identity_change)
    assert any(r.code == "SAMPLE_IDENTITY_UNEXPLAINED" for r in reasons)


def test_sample_in_two_stations_fails(trace_with_forked_sample):
    reasons = check_invariants(trace_with_forked_sample)
    assert any(r.code == "SAMPLE_LOCATION_CONFLICT" for r in reasons)


def test_action_without_completed_transfer_fails(trace_with_missing_transfer):
    reasons = check_invariants(trace_with_missing_transfer)
    assert any(r.code == "SAMPLE_TRANSFER_MISSING" for r in reasons)


def test_clean_lineage_passes(trace_with_complete_lineage):
    assert check_invariants(trace_with_complete_lineage) == []


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
