"""Typed, machine-readable runtime feedback.

Mirrors the open-code pattern already used by CompositionReason: the code set is
not closed, but every code is a stable, greppable identifier rather than prose.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

REASON_CODE_PATTERN = r"^[A-Z][A-Z0-9_]*$"


class RuntimeReason(BaseModel):
    """One machine-readable reason a run degraded or failed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(pattern=REASON_CODE_PATTERN)
    detail: str = Field(min_length=1)
    step_id: str | None = None
    channel_id: str | None = None
    recoverable: bool = False
