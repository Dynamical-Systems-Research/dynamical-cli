"""Resolution from (operation_id, provider_id) to an instrument model.

Registration happens by import. There is no plugin framework and no entry
points: an instrument module is added to the imports at the bottom of this file
and nothing in core execution changes.

The trailing imports register the admitted AC SDL1 instrument models
(liquid handling, ultrasonic conditioning, sample transfer, electrodeposition,
cleaning, electrochemical-cell loading, OER measurement). No compatibility
layers are registered here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ..reasons import RuntimeReason
from ..samples import Sample


@dataclass(frozen=True)
class InstrumentRequest:
    parameters: dict[str, float]
    inputs: dict[str, float]
    sample: Sample | None


@dataclass(frozen=True)
class InstrumentResult:
    outputs: dict[str, float]
    uncertainty: dict[str, float]
    cost_usd: float
    duration_s: float
    reasons: list[RuntimeReason] = field(default_factory=list)
    sample: Sample | None = None


InstrumentModel = Callable[[InstrumentRequest], InstrumentResult]

_MODELS: dict[tuple[str, str], InstrumentModel] = {}


def register(operation_id: str, provider_id: str) -> Callable[[InstrumentModel], InstrumentModel]:
    def decorate(model: InstrumentModel) -> InstrumentModel:
        key = (operation_id, provider_id)
        if key in _MODELS:
            raise ValueError(f"{key} is already registered")
        _MODELS[key] = model
        return model

    return decorate


def resolve(operation_id: str, provider_id: str) -> InstrumentModel | None:
    return _MODELS.get((operation_id, provider_id))


def registered_operations() -> list[tuple[str, str]]:
    return sorted(_MODELS)


# Trailing imports populate the table at import time and must stay last.
from . import (  # noqa: E402,F401
    ac_arduino,
    ac_bath,
    ac_cleaning,
    ac_echem_cell,
    ac_oer,
    ac_oer_twin,
    ac_opentron,
    ac_potentiostat,
    transfer,
)
