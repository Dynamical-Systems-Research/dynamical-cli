"""Resolution from (operation_id, provider_id) to an instrument model.

Registration happens by import. There is no plugin framework and no entry
points: an instrument module is added to the imports at the bottom of this file
and nothing in core execution changes.

The trailing imports register SDL1 source-command adapters, the source-defined
SDL1 measurement protocol, and separately attributed DTU response models.
Historical transfer and echem-cell models remain importable; import registration
alone does not admit them to a facility registry.
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
    applied_parameters: dict[str, float] = field(default_factory=dict)


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
    ac_sdl1_oer,
    transfer,
)
