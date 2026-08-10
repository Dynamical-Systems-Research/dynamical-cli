"""Shared backend adapter interface.

The core compiler owns IR validation and the core IR hash.  Adapters only
translate that validated document into target-specific, deterministic files.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class BackendError(ValueError):
    """Raised when a validated facility cannot be mapped to a target."""


@dataclass(frozen=True)
class BackendArtifact:
    """A target artifact and its content digest."""

    role: str
    path: Path
    sha256: str


def document_mapping(document: Any) -> dict[str, Any]:
    """Return a JSON-compatible mapping from Pydantic or plain input."""

    if hasattr(document, "model_dump"):
        value = document.model_dump(mode="json", exclude_none=True)
    elif isinstance(document, Mapping):
        value = dict(document)
    else:
        raise BackendError("backend input must be a facility model or mapping")
    if not isinstance(value, dict):
        raise BackendError("backend input did not produce a JSON object")
    return value


def canonical_json(value: Any) -> str:
    """Serialize target configuration in a stable form."""

    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def write_text_artifact(output_dir: Path, role: str, name: str, text: str) -> BackendArtifact:
    """Write one adapter artifact and return its digest."""

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / name
    path.write_text(text, encoding="utf-8")
    return BackendArtifact(
        role=role, path=path, sha256=hashlib.sha256(text.encode("utf-8")).hexdigest()
    )


def write_json_artifact(output_dir: Path, role: str, name: str, value: Any) -> BackendArtifact:
    """Write one canonical JSON artifact."""

    return write_text_artifact(output_dir, role, name, canonical_json(value))


def emit_backend(
    document: Any,
    output_dir: str | Path,
    *,
    target: str,
    ir_hash: str,
    stage_path: str | Path | None = None,
) -> list[BackendArtifact]:
    """Emit one supported target without changing the source document."""

    normalized = target.lower().replace("-", "_")
    destination = Path(output_dir)
    if normalized in {"isaac", "isaac_sim"}:
        from .isaac_sim import emit_isaac_sim

        return emit_isaac_sim(document, destination, ir_hash=ir_hash, stage_path=stage_path)
    if normalized in {"openusd", "usd"}:
        from .openusd import emit_openusd

        return emit_openusd(document, destination, ir_hash=ir_hash, stage_path=stage_path)
    raise BackendError(f"unsupported target: {target}")


def artifact_manifest(
    target: str, ir_hash: str, artifacts: list[BackendArtifact]
) -> dict[str, Any]:
    """Build a stable adapter receipt."""

    return {
        "schema_version": "dynamical.backend-receipt.v1",
        "target": target,
        "core_ir_sha256": ir_hash,
        "artifacts": [
            {"role": item.role, "path": item.path.name, "sha256": item.sha256}
            for item in sorted(artifacts, key=lambda item: (item.role, item.path.name))
        ],
    }


def stage_reference(output_dir: Path, stage_path: str | Path | None) -> str:
    """Return a relocatable reference to a stage in the target pack."""

    if stage_path is None:
        return "root.usda"
    path = Path(stage_path)
    try:
        return path.resolve().relative_to(output_dir.resolve()).as_posix()
    except ValueError:
        return path.name
