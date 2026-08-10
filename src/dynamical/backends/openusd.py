"""Portable OpenUSD target receipt and validation commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import BackendArtifact, artifact_manifest, stage_reference, write_json_artifact


def emit_openusd(
    document: Any,
    output_dir: Path,
    *,
    ir_hash: str,
    stage_path: str | Path | None = None,
) -> list[BackendArtifact]:
    """Emit the OpenUSD target contract around the core-composed stage."""

    stage = stage_reference(output_dir, stage_path)
    config = {
        "schema_version": "dynamical.openusd-target.v1",
        "core_ir_sha256": ir_hash,
        "target": "openusd",
        "stage": stage,
        "stage_contract": {
            "meters_per_unit": 1.0,
            "up_axis": "Z",
            "default_prim": "/Facility",
            "layers": ["layout", "physics", "semantics", "calibration"],
            "portable_material": "UsdPreviewSurface",
        },
        "validation": [
            {"tool": "usdchecker", "command": ["usdchecker", stage]},
            {
                "tool": "usdrecord",
                "command": ["usdrecord", stage, "preview.png"],
            },
        ],
        "claim_boundary": (
            "A valid rendered stage proves scene composition and visual inspection only. "
            "It is not an embodied workflow or a W1 claim."
        ),
    }
    artifacts = [write_json_artifact(output_dir, "backend_config", "backend_config.json", config)]
    artifacts.append(
        write_json_artifact(
            output_dir,
            "backend_receipt",
            "backend_receipt.json",
            artifact_manifest("openusd", ir_hash, artifacts),
        )
    )
    return artifacts
