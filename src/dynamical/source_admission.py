"""Verify declared source digests against artifacts on disk.

This closes the repo's largest honesty gap: before this module, every declared
sha256 in the IR was decorative -- nothing in src/ ever opened, fetched or
digested a referenced artifact.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .sources import AssetSource

_CHUNK = 1024 * 1024


class SourceAdmissionError(ValueError):
    """A declared source could not be admitted. Always fails closed."""


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def admit_sources(sources: list[AssetSource], root: Path) -> dict[str, Any]:
    """Digest every source under ``root`` and refuse anything that does not verify."""

    root = Path(root)
    records: list[dict[str, Any]] = []
    for source in sorted(sources, key=lambda item: item.id):
        if source.admission != "admitted":
            raise SourceAdmissionError(
                f"{source.id}: not admitted (state {source.admission!r}); "
                "an unadmitted source cannot enter a compiled world"
            )
        artifact = root / source.id
        if not artifact.is_file():
            raise SourceAdmissionError(f"{source.id}: artifact is absent at {artifact}")
        actual = _digest(artifact)
        if actual != source.sha256:
            raise SourceAdmissionError(
                f"{source.id}: digest mismatch; declared {source.sha256}, measured {actual}"
            )
        records.append(
            {
                "id": source.id,
                "sha256": actual,
                "retrieval_uri": source.retrieval_uri,
                "revision": source.revision,
                "license": source.spdx_id or source.license_ref,
                "license_evidence": source.license_evidence,
                "conflict_notes": list(source.conflict_notes),
                "derived_from_source_id": source.derived_from_source_id,
                "conversion_tool": source.conversion_tool,
                "conversion_tolerance": source.conversion_tolerance,
            }
        )
    return {
        "schema_version": "dynamical.source-admission.v1",
        "admitted": [record["id"] for record in records],
        "records": records,
    }
