"""Provenance and license admission for external source artifacts.

A source URI is not evidence. An AssetSource is admitted only when its declared
digest matches the artifact on disk and a license is resolvable. Conflicting
license signals are recorded, never laundered into a single clean answer.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SourceAdmissionState = Literal["admitted", "pending", "unlicensed", "rejected"]

_UNRESOLVABLE_SPDX = {"NOASSERTION", "NONE", "UNKNOWN"}


class AssetSource(BaseModel):
    """One external artifact, its digest, its license, and its admission state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    retrieval_uri: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    admission: SourceAdmissionState
    authority_id: str = Field(min_length=1)

    spdx_id: str | None = None
    license_ref: str | None = None
    license_evidence: str | None = None
    conflict_notes: list[str] = Field(default_factory=list)

    revision: str | None = None
    retrieved_at: str | None = None

    derived_from_source_id: str | None = None
    conversion_tool: str | None = None
    conversion_tolerance: str | None = None

    @model_validator(mode="after")
    def license_is_resolvable_when_admitted(self) -> AssetSource:
        if self.admission != "admitted":
            return self
        if self.spdx_id is None and self.license_ref is None:
            raise ValueError("admitted source requires a license: set spdx_id or license_ref")
        if self.spdx_id is not None and self.spdx_id.upper() in _UNRESOLVABLE_SPDX:
            raise ValueError(f"unlicensed: {self.spdx_id!r} does not resolve to a license")
        if self.license_ref is not None and not self.license_evidence:
            raise ValueError("license_ref requires license_evidence quoting the source")
        return self

    @model_validator(mode="after")
    def derived_sources_declare_their_conversion(self) -> AssetSource:
        if self.derived_from_source_id is None:
            return self
        if not self.conversion_tool or not self.conversion_tolerance:
            raise ValueError(
                "a derived source requires conversion_tool and conversion_tolerance "
                "so the derivation is reproducible"
            )
        return self


def staged_asset_basename(source_id: str) -> str:
    """Return the flattened staged filename for one admitted derived layer.

    A compiled world stages derived layers at ``assets/<basename>``, not at
    ``assets/<full source id>``: source ids already begin with ``assets/...``,
    so keeping the full id would double that prefix into ``assets/assets/...``.
    Both the file the compiler copies into the world and the reference arc an
    asset's geometry emits must use this exact same basename, or the arc
    would point at a path nothing staged.
    """
    return PurePosixPath(source_id).name
