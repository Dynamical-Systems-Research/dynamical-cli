"""Adding an instrument must not require editing core execution code."""

import pytest

from dynamical import instruments


def test_unregistered_operation_resolves_to_none():
    assert instruments.resolve("no-such-operation", "no-such-provider") is None


def test_registered_model_is_resolvable():
    @instruments.register("test-operation", "test-provider")
    def _model(request):
        return instruments.InstrumentResult(
            outputs={"x": 1.0}, uncertainty={"x": 0.1}, cost_usd=0.0, duration_s=1.0
        )

    resolved = instruments.resolve("test-operation", "test-provider")
    assert resolved is not None
    result = resolved(instruments.InstrumentRequest(parameters={}, inputs={}, sample=None))
    assert result.outputs["x"] == 1.0


def test_duplicate_registration_is_refused():
    @instruments.register("dup-operation", "dup-provider")
    def _first(request):
        raise NotImplementedError

    with pytest.raises(ValueError, match="already registered"):

        @instruments.register("dup-operation", "dup-provider")
        def _second(request):
            raise NotImplementedError
