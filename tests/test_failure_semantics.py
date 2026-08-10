"""A failed run must be reported as failed by every surface an agent reads."""

from dynamical.campaign import validate_events
from dynamical.reasons import RuntimeReason


def test_reason_code_shape():
    reason = RuntimeReason(
        code="MEASUREMENT_UNAVAILABLE",
        detail="channel material.temperature_K produced no value",
        step_id="deposit",
        channel_id="material.temperature_K",
        recoverable=True,
    )
    assert reason.code == "MEASUREMENT_UNAVAILABLE"
    assert reason.recoverable is True


def test_failed_execution_status_makes_validation_invalid(failed_trace_events):
    result = validate_events(failed_trace_events)
    assert result["valid"] is False
    assert result["execution_status"] == "failed"
    assert result["reasons"], "a failed run must carry at least one typed reason"


def test_truncated_trace_fails_step_coverage(truncated_trace_events):
    result = validate_events(truncated_trace_events)
    assert result["valid"] is False
    assert any(r["code"] == "STEP_COVERAGE_INCOMPLETE" for r in result["reasons"])
