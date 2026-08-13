"""Independent bounded numeric constraint evaluation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def evaluate_numeric_constraint(constraint: Any, observed_value: float) -> bool:
    """Evaluate one validated IR comparator without executing source text."""

    if hasattr(constraint, "model_dump"):
        data = constraint.model_dump(mode="json")
    elif isinstance(constraint, Mapping):
        data = dict(constraint)
    else:
        raise TypeError("constraint must be a validated model or mapping")
    operator = data["operator"]
    bound = data["bound"]
    value = float(observed_value)
    if operator == "between":
        if not isinstance(bound, Mapping):
            raise ValueError("between needs a minimum and maximum")
        return float(bound["minimum"]) <= value <= float(bound["maximum"])
    scalar = float(bound)
    return {
        "lt": value < scalar,
        "le": value <= scalar,
        "eq": value == scalar,
        "ge": value >= scalar,
        "gt": value > scalar,
    }[operator]
