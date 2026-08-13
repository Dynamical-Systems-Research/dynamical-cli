"""Runtime-only contracts that stay independent from planners."""

from .constraints import evaluate_numeric_constraint

__all__ = ["evaluate_numeric_constraint"]
