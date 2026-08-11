"""Deterministic compilation from FacilityDocument to target artifacts."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from .openusd import write_openusd_layers
from .schema import (
    FacilityDocument,
    canonical_sha256,
    load_facility_manifest,
)
from .source_admission import SourceAdmissionError, admit_sources
from .sources import AssetSource, staged_asset_basename

COMPILER_VERSION = "0.2.0"
Target = Literal["isaac", "openusd"]

# Asset source ids (e.g. "assets/usd/vial-rack-v3.usdc") are declared relative to the
# repository root, not to the manifest file's directory or the process cwd -- manifests
# live under manifests/ and vendored sources under assets/usd/ and sources/, siblings of
# each other. This is the dev/editable-install root: a repo checkout has every source id
# at exactly this relative path.
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
# A real (non-editable) wheel install never has _REPOSITORY_ROOT on disk at all --
# Path(__file__).resolve().parents[2] then lands somewhere under site-packages/lib,
# which is why compile_facility must not use it alone. pyproject.toml force-includes
# each derived USD layer at "dynamical/data/<source id>", preserving the source id's
# own relative path unchanged, so this is the matching packaged-install root: the same
# pattern src/dynamical/cli.py already uses for its own packaged-data fallback
# (PACKAGED_REGISTRY / PACKAGED_FACILITY).
_PACKAGED_DATA_ROOT = Path(str(files("dynamical").joinpath("data")))

_COMPILE_MANIFEST_FIELDS = {
    "schema_version",
    "compiler_version",
    "target",
    "core_ir_sha256",
    "adapter_pack_sha256",
    "world_sha256",
    "root_stage",
    "artifact_count",
    "artifacts",
}
_OWNERSHIP_ARTIFACTS = {
    "action_schema.json",
    "adapter_pack.json",
    "calibration.usda",
    "calibration_bindings.json",
    "campaign_trace.schema.json",
    "capability_graph.json",
    "core_ir.json",
    "facility_ir.json",
    "fidelity_report.json",
    "layout.usda",
    "observation_schema.json",
    "physics.usda",
    "root.usda",
    "semantics.usda",
    "source_admission.json",
}

@dataclass(frozen=True)
class CompileResult:
    output_dir: Path
    target: str
    core_ir_sha256: str
    adapter_pack_sha256: str
    world_sha256: str
    stage_path: Path
    manifest_path: Path


def _write_json(path: Path, value: Any) -> None:
    rendered = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
    path.write_bytes(rendered.encode() + b"\n")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _normalized_artifact_path(value: Any) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("compiled artifact path must be a normalized POSIX relative path")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or value != relative.as_posix()
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError(f"unsafe compiled artifact path: {value!r}")
    return relative


def _artifact_file(root: Path, relative: PurePosixPath) -> Path:
    candidate = root.joinpath(*relative.parts)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"compiled artifact path contains a symbolic link: {relative}")
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve(strict=False)
    if not resolved_candidate.is_relative_to(resolved_root):
        raise ValueError(f"compiled artifact escapes its root: {relative}")
    return candidate


def _resolve_asset_root(source_ids: list[str]) -> Path:
    """Return the one directory every id in ``source_ids`` resolves under.

    Tries the packaged install's data directory first, then the repository
    checkout, and commits to whichever root has *every* requested id -- the
    two are never mixed in one call, since a real install has exactly one of
    them available. If any id resolves under neither, raises
    ``SourceAdmissionError`` naming that id and both full paths tried: a
    missing asset must never be silently swallowed into "no assets
    admitted", the exact honesty gap this release exists to close. A future
    asset added at a new repo path that pyproject.toml's force-include list
    forgets to mirror will fail loudly here instead of resolving to nothing.
    """
    if all((_PACKAGED_DATA_ROOT / source_id).is_file() for source_id in source_ids):
        return _PACKAGED_DATA_ROOT
    if all((_REPOSITORY_ROOT / source_id).is_file() for source_id in source_ids):
        return _REPOSITORY_ROOT
    for source_id in source_ids:
        packaged_candidate = _PACKAGED_DATA_ROOT / source_id
        repo_candidate = _REPOSITORY_ROOT / source_id
        if not packaged_candidate.is_file() and not repo_candidate.is_file():
            raise SourceAdmissionError(
                f"{source_id}: artifact is absent at both {packaged_candidate} and {repo_candidate}"
            )
    raise SourceAdmissionError(
        "admitted asset sources resolve inconsistently: some ids resolve only under "
        f"{_PACKAGED_DATA_ROOT}, others only under {_REPOSITORY_ROOT}; expected every "
        f"id to resolve under exactly one shared root. ids={source_ids!r}"
    )


def _staged_asset_basenames(sources: list[AssetSource]) -> dict[str, str]:
    """Map each admitted derived layer's flattened basename back to its source id.

    Flattening drops the source id's directory namespace, so two admitted
    derived layers with the same basename (e.g. two vendors both shipping a
    ``part.usdc``) would silently overwrite each other on disk. Fail closed
    and name both ids instead of silently picking one.
    """
    basenames: dict[str, str] = {}
    for source in sorted(sources, key=lambda item: item.id):
        if source.derived_from_source_id is None:
            continue  # provenance-only record (e.g. raw CAD); nothing to stage
        basename = staged_asset_basename(source.id)
        colliding_id = basenames.get(basename)
        if colliding_id is not None:
            raise ValueError(
                "two admitted asset sources flatten to the same staged basename "
                f"{basename!r}: {colliding_id!r} and {source.id!r}"
            )
        basenames[basename] = source.id
    return basenames


def _compile_manifest_records(manifest: Any) -> dict[str, str]:
    if not isinstance(manifest, dict) or set(manifest) != _COMPILE_MANIFEST_FIELDS:
        raise ValueError("compile manifest fields do not match dynamical.compile-manifest.v1")
    if manifest.get("schema_version") != "dynamical.compile-manifest.v1":
        raise ValueError("compile manifest schema version is unsupported")
    if manifest.get("compiler_version") != COMPILER_VERSION:
        raise ValueError("compile manifest compiler version is unsupported")
    if manifest.get("target") not in {"isaac", "openusd"}:
        raise ValueError("compile manifest target is unsupported")
    for field in ("core_ir_sha256", "adapter_pack_sha256", "world_sha256"):
        if not _is_sha256(manifest.get(field)):
            raise ValueError(f"compile manifest {field} is invalid")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("compile manifest artifacts must be a list")
    records: dict[str, str] = {}
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {"path", "sha256"}:
            raise ValueError("compile artifact records require only path and sha256")
        relative = _normalized_artifact_path(artifact.get("path")).as_posix()
        digest = artifact.get("sha256")
        if relative in records:
            raise ValueError(f"duplicate compiled artifact path: {relative}")
        if not _is_sha256(digest):
            raise ValueError(f"compiled artifact hash is invalid: {relative}")
        records[relative] = digest
    artifact_count = manifest.get("artifact_count")
    if (
        not isinstance(artifact_count, int)
        or isinstance(artifact_count, bool)
        or artifact_count != len(records)
    ):
        raise ValueError("compile manifest artifact count is invalid")
    if not _OWNERSHIP_ARTIFACTS.issubset(records):
        missing = sorted(_OWNERSHIP_ARTIFACTS - set(records))
        raise ValueError(f"compile manifest lacks ownership artifacts: {missing}")
    root_stage = _normalized_artifact_path(manifest.get("root_stage")).as_posix()
    if root_stage != "root.usda" or root_stage not in records:
        raise ValueError("compile manifest root stage is invalid")
    if canonical_sha256(records) != manifest["world_sha256"]:
        raise ValueError("compile manifest world hash is invalid")
    return records


def _verify_compile_receipt(path: Path, *, allow_unexpected: bool) -> dict[str, Any]:
    """Verify a complete Dynamical compile receipt without reading outside its root."""

    if path.is_symlink() or not path.is_dir():
        raise ValueError("compiled world root must be a real directory")
    manifest_path = path / "compile_manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("compiled manifest is absent or is a symbolic link")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = _compile_manifest_records(manifest)
    for relative, expected in records.items():
        artifact = _artifact_file(path, PurePosixPath(relative))
        if not artifact.is_file():
            raise ValueError(f"compiled artifact is absent: {relative}")
        if _sha256_file(artifact) != expected:
            raise ValueError(f"compiled artifact hash mismatch: {relative}")

    actual_files: set[str] = set()
    for item in path.rglob("*"):
        if item.is_symlink():
            raise ValueError(f"compiled world contains a symbolic link: {item}")
        if item.is_file():
            relative_item = item.relative_to(path)
            if (
                len(relative_item.parts) == 2
                and relative_item.parts[0] == "__pycache__"
                and relative_item.suffix == ".pyc"
            ):
                continue
            actual_files.add(relative_item.as_posix())
    declared_files = set(records) | {"compile_manifest.json"}
    if not allow_unexpected and actual_files != declared_files:
        unexpected = sorted(actual_files - declared_files)
        missing = sorted(declared_files - actual_files)
        raise ValueError(
            f"compiled world file set differs; unexpected={unexpected}, missing={missing}"
        )

    facility = json.loads((path / "facility_ir.json").read_text(encoding="utf-8"))
    document = FacilityDocument.model_validate(facility)
    core = json.loads((path / "core_ir.json").read_text(encoding="utf-8"))
    if core != document.canonical_payload(include_adapters=False):
        raise ValueError("compiled core IR is not the facility IR projection")
    actual_core_hash = canonical_sha256(core)
    if actual_core_hash != manifest["core_ir_sha256"]:
        raise ValueError("compiled core IR hash mismatch")
    adapter = json.loads((path / "adapter_pack.json").read_text(encoding="utf-8"))
    if not isinstance(adapter, dict) or set(adapter) != {
        "schema_version",
        "target",
        "core_ir_sha256",
        "adapter_pack_sha256",
        "bindings",
    }:
        raise ValueError("compiled adapter pack is invalid")
    expected_bindings = [
        binding.model_dump(mode="json", exclude_none=True)
        for binding in sorted(document.adapter_bindings, key=lambda item: item.id)
        if binding.target == manifest["target"]
    ]
    expected_adapter_hash = document.adapter_pack_sha256(manifest["target"])
    if (
        adapter.get("schema_version") != document.schema_version
        or adapter.get("target") != manifest["target"]
        or adapter.get("core_ir_sha256") != actual_core_hash
        or adapter.get("bindings") != expected_bindings
        or expected_adapter_hash != manifest["adapter_pack_sha256"]
        or adapter.get("adapter_pack_sha256") != expected_adapter_hash
    ):
        raise ValueError("compiled adapter pack does not match the manifest")
    if "composition_result.json" in records:
        from .composition import validate_composition_result

        composition = json.loads((path / "composition_result.json").read_text(encoding="utf-8"))
        selected = validate_composition_result(composition)
        if selected.status != "COMPILED":
            raise ValueError("compiled world contains a HOLD composition")
        if "selected_capability_graph.json" not in records:
            raise ValueError("compiled composition lacks its selected capability graph")
        selected_graph = json.loads(
            (path / "selected_capability_graph.json").read_text(encoding="utf-8")
        )
        if selected_graph != _selected_capability_graph(selected):
            raise ValueError("selected capability graph does not match the composition")
    elif "selected_capability_graph.json" in records:
        raise ValueError("selected capability graph has no composition result")
    return {
        "manifest": manifest,
        "records": records,
        "core_ir_sha256": actual_core_hash,
    }


def action_schema(document: FacilityDocument) -> dict[str, Any]:
    declared = sorted({capability.action_type for capability in document.capabilities})
    control_actions = {
        "observe",
        "pick",
        "place",
        "stop",
        "wait",
    }
    allowed_actions = sorted(control_actions | set(declared))
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://dynamical.systems/schemas/dynamical/action-request-0.1.0.json",
        "title": "Dynamical ActionRequest 0.1.0",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "action_id",
            "kind",
            "actor_id",
            "provider_id",
            "evidence_class",
            "parameters",
        ],
        "properties": {
            "action_id": {"type": "string", "minLength": 1},
            "kind": {"enum": allowed_actions},
            "actor_id": {"type": "string", "minLength": 1},
            "provider_id": {"type": "string", "minLength": 1},
            "evidence_class": {"enum": ["simulator", "calibrated_twin", "shadow", "physical"]},
            "parameters": {"type": "object"},
            "sample_id": {"anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}]},
            "sample_lineage": {"type": "array", "items": {"type": "string"}},
            "station_id": {"anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}]},
        },
        "x-dynamical-declared-capability-action-types": declared,
    }


def observation_schema(document: FacilityDocument) -> dict[str, Any]:
    channels = sorted(
        {channel.id for device in document.devices for channel in device.state_channels}
        | {
            channel.channel_id
            for material in document.material_states
            for channel in material.initial_channels
        }
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://dynamical.systems/schemas/dynamical/observation-frame-0.1.0.json",
        "title": "Dynamical ObservationFrame 0.1.0",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "frame_id",
            "logical_time_s",
            "provider_id",
            "evidence_class",
            "channels",
            "evidence_ids",
        ],
        "properties": {
            "frame_id": {"type": "string", "minLength": 1},
            "logical_time_s": {"type": "number", "minimum": 0},
            "provider_id": {"type": "string", "minLength": 1},
            "evidence_class": {"enum": ["simulator", "calibrated_twin", "shadow", "physical"]},
            "channels": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "name",
                        "value",
                        "unit",
                        "quality",
                        "origin",
                        "provider_id",
                        "evidence_class",
                        "uncertainty",
                    ],
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "value": {"type": ["number", "integer", "string", "boolean", "null"]},
                        "unit": {"type": "string", "minLength": 1},
                        "quality": {"type": "string", "minLength": 1},
                        "origin": {
                            "enum": [
                                "runtime_sensor",
                                "backend_state",
                                "source_model",
                                "physical_source",
                            ]
                        },
                        "provider_id": {"type": "string", "minLength": 1},
                        "evidence_class": {
                            "enum": [
                                "simulator",
                                "calibrated_twin",
                                "shadow",
                                "physical",
                            ]
                        },
                        "uncertainty": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["value", "kind", "origin"],
                            "properties": {
                                "value": {"type": ["number", "null"], "minimum": 0},
                                "kind": {"enum": ["declared", "propagated", "measured"]},
                                "origin": {"type": "string", "minLength": 1},
                            },
                        },
                    },
                },
            },
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
            "sample_id": {"anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}]},
            "sample_lineage": {"type": "array", "items": {"type": "string"}},
        },
        "x-dynamical-declared-channel-ids": channels,
    }


def campaign_trace_schema() -> dict[str, Any]:
    from .campaign import trace_event_json_schema

    return trace_event_json_schema()


def _capability_graph(document: FacilityDocument) -> dict[str, Any]:
    nodes: list[dict[str, str]] = []
    edges: list[dict[str, str]] = []
    for device in sorted(document.devices, key=lambda item: item.id):
        nodes.append({"id": device.id, "kind": "device"})
    for agent in sorted(document.agents, key=lambda item: item.id):
        nodes.append({"id": agent.id, "kind": "agent"})
    for capability in sorted(document.capabilities, key=lambda item: item.id):
        nodes.append({"id": capability.id, "kind": "capability"})
        edges.append(
            {"source": capability.provider_id, "target": capability.id, "kind": "provides"}
        )
        for constraint_id in sorted(
            capability.precondition_constraint_ids + capability.postcondition_constraint_ids
        ):
            edges.append(
                {"source": capability.id, "target": constraint_id, "kind": "constrained_by"}
            )
    return {"schema_version": "0.1.0", "nodes": nodes, "edges": edges}


def _selected_capability_graph(composition: Any) -> dict[str, Any]:
    """Return the selected scientific graph without legacy facility ownership edges."""

    virtual_sdl = composition.virtual_sdl
    if virtual_sdl is None:  # guarded by CompositionResult, kept fail closed here
        raise ValueError("compiled composition has no virtual SDL")
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, str]] = []
    for binding in virtual_sdl.operation_bindings:
        operation_node = f"operation:{binding.operation_id}"
        provider_node = f"provider:{binding.provider_id}"
        step_node = f"step:{binding.step_id}"
        facility_node = f"facility:{binding.selected_facility_id}"
        nodes[operation_node] = {
            "id": operation_node,
            "kind": "scientific_capability",
            "operation_id": binding.operation_id,
        }
        nodes[provider_node] = {
            "id": provider_node,
            "kind": "capability_provider",
            "provider_id": binding.provider_id,
            "evidence_class": binding.evidence_class,
        }
        nodes[step_node] = {
            "id": step_node,
            "kind": "operation_instance",
            "step_id": binding.step_id,
        }
        nodes[facility_node] = {
            "id": facility_node,
            "kind": "facility",
            "facility_id": binding.selected_facility_id,
        }
        edges.extend(
            [
                {"source": provider_node, "target": operation_node, "kind": "implements"},
                {"source": operation_node, "target": step_node, "kind": "instantiates"},
                {"source": facility_node, "target": provider_node, "kind": "hosts"},
            ]
        )
    for dependency in virtual_sdl.dependency_edges:
        edges.append(
            {
                "source": f"step:{dependency.source_step_id}",
                "target": f"step:{dependency.target_step_id}",
                "kind": "precedes",
            }
        )
    for transport in virtual_sdl.transport_bindings:
        provider_node = f"provider:{transport.provider_id}"
        nodes[provider_node] = {
            "id": provider_node,
            "kind": "transport_provider",
            "provider_id": transport.provider_id,
            "evidence_class": transport.evidence_class,
        }
        edges.append(
            {
                "source": provider_node,
                "target": f"step:{transport.target_step_id}",
                "kind": "transports_to",
            }
        )
    return {
        "schema_version": "dynamical.selected-capability-graph.v1",
        "composition_sha256": composition.composition_sha256,
        "nodes": [nodes[key] for key in sorted(nodes)],
        "edges": sorted(
            edges,
            key=lambda item: (item["source"], item["target"], item["kind"]),
        ),
    }


def evaluate_calibration(document: FacilityDocument) -> dict[str, Any]:
    """Evaluate declared calibration evidence into a bounded W2 decision.

    W2 is admitted for a model binding's named channels and condition domain
    only when its evidence comes from independent physical facility runs,
    carries at least one gated metric on the independent_test split, and
    every gated metric passes its frozen threshold (recomputed here, never
    trusted from the record). Anything else -- no evidence, no held-out
    gates, any failed gate, or a within-campaign validation design -- keeps
    W2 closed. Every applicable closure reason is recorded, not just the
    first, so an evidence record that both fails its gates and lacks
    independent-facility validation shows both.
    """

    grants: list[dict[str, Any]] = []
    closed: list[dict[str, Any]] = []
    for evidence in sorted(document.calibration_evidence, key=lambda value: value.id):
        gated = [metric for metric in evidence.metrics if metric.gate_passed() is not None]
        held_out_gates = [metric for metric in gated if metric.split == "independent_test"]
        failed = [metric.name for metric in gated if metric.gate_passed() is False]
        record = {
            "calibration_evidence_id": evidence.id,
            "model_binding_id": evidence.applies_to_model_binding_id,
            "channel_ids": list(evidence.supported_channel_ids),
            "condition_domain": dict(evidence.condition_domain),
        }
        reasons: list[str] = []
        if evidence.validation_design != "independent_facility_runs":
            reasons.append(
                "validation design is not independent_facility_runs "
                f"(declared: {evidence.validation_design})"
            )
        if not held_out_gates:
            reasons.append("no gated metric on the independent_test split")
        if failed:
            reasons.append(f"failed frozen gates: {failed}")
        if reasons:
            closed.append({**record, "reasons": reasons, "failed_metrics": failed})
        else:
            grants.append(record)
    return {"w2_admitted": bool(grants), "w2_grants": grants, "w2_closed": closed}


def _fidelity_template(document: FacilityDocument, target: str) -> dict[str, Any]:
    calibration = evaluate_calibration(document)
    if calibration["w2_admitted"]:
        granted = calibration["w2_grants"]
        calibration_statement = (
            "Bounded W2 is admitted solely for the granted channels and condition domains: "
            f"{granted}. Everything else remains W1."
        )
        w2_admission = {"status": "admitted_bounded", "reason": calibration_statement}
    elif calibration["w2_closed"]:
        closure_reasons = [
            reason for item in calibration["w2_closed"] for reason in item["reasons"]
        ]
        calibration_statement = (
            "W2 is not admitted. Declared calibration evidence is closed for: "
            f"{closure_reasons}. The evidence record is preserved in calibration_bindings.json."
        )
        w2_admission = {"status": "not_admitted", "reason": calibration_statement}
    else:
        calibration_statement = (
            "No physical calibration evidence; W2 is not admitted. Independent physical "
            "facility runs remain required."
        )
        w2_admission = {"status": "not_admitted", "reason": calibration_statement}
    return {
        "schema_version": "0.1.0",
        "rubric_owner": "Dynamical Systems",
        "rubric_kind": "internal",
        "target": target,
        "authoring_basis": document.facility.authoring_basis,
        "admission": {
            "W0": {
                "status": "candidate",
                "reason": "Compilation must pass independent schema and artifact validation.",
            },
            "W1": {
                "status": "not_admitted",
                "reason": "A real rendered embodied workflow and trace are required.",
            },
            "W2": w2_admission,
            "W3": {"status": "not_admitted", "reason": "No live read-only facility link."},
            "W4": {"status": "not_admitted", "reason": "No approved physical submission path."},
            "W5": {"status": "not_admitted", "reason": "No physical-result update loop."},
        },
        "claim_boundary": document.facility.claim_boundary,
        "calibration_statement": calibration_statement,
    }


def _is_owned_compile_directory(path: Path) -> bool:
    """Return true only for an output with a valid Dynamical ownership receipt."""
    try:
        _verify_compile_receipt(path, allow_unexpected=True)
    except (ValueError, json.JSONDecodeError, OSError):
        return False
    return True


def _check_output_destination(path: Path) -> None:
    """Reject broad targets and directories that Dynamical does not own."""

    resolved = path.resolve(strict=False)
    forbidden = {
        Path(resolved.anchor),
        Path.cwd().resolve(),
        Path.home().resolve(),
    }
    if resolved in forbidden:
        raise ValueError(f"refusing broad compiler output path: {resolved}")
    if path.is_symlink():
        raise ValueError(f"compiler output must not be a symbolic link: {path}")
    if path.exists() and not path.is_dir():
        raise ValueError(f"compiler output is not a directory: {path}")
    if path.is_dir() and any(path.iterdir()) and not _is_owned_compile_directory(path):
        raise ValueError(
            f"compiler output is non-empty and has no valid Dynamical ownership receipt: {path}"
        )


def _install_compiled_output(staged: Path, destination: Path, temporary_root: Path) -> None:
    """Install one complete staged output and keep the prior output on swap failure."""

    _check_output_destination(destination)
    previous = temporary_root / "previous"
    had_previous = destination.exists()
    if had_previous:
        destination.rename(previous)
    try:
        staged.rename(destination)
    except Exception:
        if had_previous and previous.exists() and not destination.exists():
            previous.rename(destination)
        raise


def _validate_composition_facility_bindings(
    document: FacilityDocument,
    composition: Any,
) -> None:
    """Fail closed unless each selected provider matches a facility admission."""

    virtual_sdl = composition.virtual_sdl
    if virtual_sdl is None:
        raise ValueError("compiled composition has no virtual SDL")
    admitted = {
        (binding.provider_id, binding.operation_id): binding
        for binding in document.provider_admission_bindings
    }
    selected_bindings = [
        *virtual_sdl.operation_bindings,
        *virtual_sdl.transport_bindings,
    ]
    for selected in selected_bindings:
        key = (selected.provider_id, selected.operation_id)
        binding = admitted.get(key)
        if binding is None:
            raise ValueError(
                "composition provider is not admitted by the facility: "
                f"provider={selected.provider_id!r}, operation={selected.operation_id!r}"
            )
        if selected.evidence_class not in binding.evidence_classes:
            raise ValueError(
                f"composition evidence class is not admitted for {selected.provider_id!r}"
            )
        if selected.selected_facility_id not in binding.facility_ids:
            raise ValueError(f"composition facility is not admitted for {selected.provider_id!r}")
        if set(selected.facility_ids) != set(binding.facility_ids):
            raise ValueError(
                f"composition facility set differs from the facility admission for "
                f"{selected.provider_id!r}"
            )
        if selected.endpoint_id != binding.endpoint_id:
            raise ValueError(
                f"composition endpoint differs from the facility admission for "
                f"{selected.provider_id!r}"
            )
        selected_links = {
            canonical_sha256(link.model_dump(mode="json", exclude_none=True))
            for link in selected.adapter_links
        }
        admitted_links = {
            canonical_sha256(link.model_dump(mode="json", exclude_none=True))
            for link in binding.adapter_links
        }
        if selected_links != admitted_links:
            raise ValueError(
                f"composition adapter links differ from the facility admission for "
                f"{selected.provider_id!r}"
            )
        selected_validity = {
            canonical_sha256(item.model_dump(mode="json", exclude_none=True))
            for item in selected.validity_envelope
        }
        admitted_validity = {
            canonical_sha256(item.model_dump(mode="json", exclude_none=True))
            for item in binding.validity_envelope
        }
        if selected_validity != admitted_validity:
            raise ValueError(
                f"composition validity envelope differs from the facility admission for "
                f"{selected.provider_id!r}"
            )
        policy = selected.policy
        if set(policy.safety_limit_ids) != set(binding.safety_limit_ids):
            raise ValueError(
                f"composition safety limits differ from the facility admission for "
                f"{selected.provider_id!r}"
            )
        if set(policy.policy_tags) != set(binding.policy_tags):
            raise ValueError(
                f"composition policy tags differ from the facility admission for "
                f"{selected.provider_id!r}"
            )
        if set(policy.required_approval_ids) != set(binding.required_approval_ids):
            raise ValueError(
                f"composition approvals differ from the facility admission for "
                f"{selected.provider_id!r}"
            )


def compile_facility(
    manifest_path: str | Path | FacilityDocument,
    target: Target,
    output_dir: str | Path | None = None,
    composition_result: Any | None = None,
) -> CompileResult:
    """Validate and compile one facility manifest into deterministic artifacts."""

    if target not in {"isaac", "openusd"}:
        raise ValueError(f"unsupported target: {target}")
    document = (
        manifest_path
        if isinstance(manifest_path, FacilityDocument)
        else load_facility_manifest(manifest_path)
    )
    target_bindings = [binding for binding in document.adapter_bindings if binding.target == target]
    if not target_bindings:
        raise ValueError(f"manifest has no adapter binding for target {target!r}")

    source_name = (
        document.facility.id
        if isinstance(manifest_path, FacilityDocument)
        else Path(manifest_path).stem
    )
    destination = (
        Path(output_dir) if output_dir is not None else Path("build") / source_name / target
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    _check_output_destination(destination)
    core_hash = document.core_ir_sha256()
    adapter_hash = document.adapter_pack_sha256(target)

    prefix = f".{destination.name}.dynamical-build-"
    with tempfile.TemporaryDirectory(prefix=prefix, dir=destination.parent) as temporary:
        temporary_root = Path(temporary)
        staged = temporary_root / "output"
        staged.mkdir()
        _write_json(staged / "facility_ir.json", document.canonical_payload())
        _write_json(staged / "core_ir.json", document.canonical_payload(include_adapters=False))
        _write_json(
            staged / "adapter_pack.json",
            {
                "schema_version": document.schema_version,
                "target": target,
                "core_ir_sha256": core_hash,
                "adapter_pack_sha256": adapter_hash,
                "bindings": [
                    binding.model_dump(mode="json", exclude_none=True)
                    for binding in sorted(target_bindings, key=lambda item: item.id)
                ],
            },
        )
        # Only the derived USD layers this compiled world actually stages need runtime
        # re-verification. Raw upstream CAD (assets whose AssetSource has no
        # derived_from_source_id) is provenance-only: it is never staged, never
        # referenced by a reference arc, and pyproject.toml deliberately does not ship
        # it in the wheel -- its digest is checked once, at vendoring time, by
        # tests/test_asset_provenance.py against the repo tree, not on every compile.
        derived_sources = [
            source for source in document.asset_sources if source.derived_from_source_id is not None
        ]
        asset_root = _resolve_asset_root([source.id for source in derived_sources])
        admission = admit_sources(derived_sources, asset_root)
        _write_json(staged / "source_admission.json", admission)
        staged_basenames = _staged_asset_basenames(derived_sources)
        if staged_basenames:
            (staged / "assets").mkdir(parents=True, exist_ok=True)
        for basename, source_id in sorted(staged_basenames.items()):
            shutil.copy2(asset_root / source_id, staged / "assets" / basename)
        write_openusd_layers(document, staged, core_hash)
        _write_json(staged / "capability_graph.json", _capability_graph(document))
        _write_json(staged / "action_schema.json", action_schema(document))
        _write_json(staged / "observation_schema.json", observation_schema(document))
        _write_json(staged / "campaign_trace.schema.json", campaign_trace_schema())
        _write_json(
            staged / "calibration_bindings.json",
            {
                "schema_version": "0.1.0",
                "bindings": [
                    item.model_dump(mode="json", exclude_none=True)
                    for item in sorted(document.calibration_evidence, key=lambda value: value.id)
                ],
                **evaluate_calibration(document),
            },
        )
        _write_json(staged / "fidelity_report.json", _fidelity_template(document, target))
        if composition_result is not None:
            from .composition import CompositionResult, validate_composition_result

            candidate = (
                composition_result
                if isinstance(composition_result, CompositionResult)
                else CompositionResult.model_validate(composition_result)
            )
            selected = validate_composition_result(candidate)
            if selected.status != "COMPILED":
                raise ValueError("a HOLD composition cannot compile an embodied facility")
            _validate_composition_facility_bindings(document, selected)
            _write_json(
                staged / "composition_result.json",
                selected.model_dump(mode="json", exclude_none=True),
            )
            _write_json(
                staged / "selected_capability_graph.json",
                _selected_capability_graph(selected),
            )

        try:
            from .backends import emit_backend
        except ImportError as exc:  # pragma: no cover - packaging failure path
            raise RuntimeError("Dynamical target adapters are not installed") from exc
        emit_backend(
            document,
            staged,
            target=target,
            ir_hash=core_hash,
            stage_path=staged / "root.usda",
        )

        artifact_hashes = {
            str(path.relative_to(staged)): _sha256_file(path)
            for path in sorted(staged.rglob("*"))
            if path.is_file() and path.name != "compile_manifest.json"
        }
        world_hash = canonical_sha256(artifact_hashes)
        manifest = {
            "schema_version": "dynamical.compile-manifest.v1",
            "compiler_version": COMPILER_VERSION,
            "target": target,
            "core_ir_sha256": core_hash,
            "adapter_pack_sha256": adapter_hash,
            "world_sha256": world_hash,
            "root_stage": "root.usda",
            "artifact_count": len(artifact_hashes),
            "artifacts": [
                {"path": name, "sha256": digest} for name, digest in sorted(artifact_hashes.items())
            ],
        }
        _write_json(staged / "compile_manifest.json", manifest)
        _install_compiled_output(staged, destination, temporary_root)

    manifest_path_out = destination / "compile_manifest.json"
    return CompileResult(
        output_dir=destination,
        target=target,
        core_ir_sha256=core_hash,
        adapter_pack_sha256=adapter_hash,
        world_sha256=world_hash,
        stage_path=destination / "root.usda",
        manifest_path=manifest_path_out,
    )


def validate_compiled_world(path: str | Path) -> dict[str, Any]:
    """Verify a compiled world's content hashes and portable stage."""

    destination = Path(path)
    failures: list[str] = []
    try:
        receipt = _verify_compile_receipt(destination, allow_unexpected=False)
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        receipt = None
        failures.append(str(exc))
    manifest = receipt["manifest"] if receipt is not None else {}
    actual_core_hash = receipt["core_ir_sha256"] if receipt is not None else None
    actual_world_hash = manifest.get("world_sha256")

    usd_result: dict[str, Any] = {"status": "not_run", "reason": "usdchecker not found"}
    checker = shutil.which("usdchecker") or (
        "/usr/bin/usdchecker" if Path("/usr/bin/usdchecker").is_file() else None
    )
    stage = destination / "root.usda"
    if checker and stage.is_file():
        process = subprocess.run(
            [checker, "-t", str(stage)],
            capture_output=True,
            text=True,
            check=False,
        )
        usd_result = {
            "status": "passed" if process.returncode == 0 else "failed",
            "command": [checker, "-t", str(stage)],
            "returncode": process.returncode,
            "output": (process.stdout + process.stderr).strip(),
        }
        if process.returncode != 0:
            failures.append("usdchecker failed")
    elif not stage.is_file():
        failures.append(f"root stage is absent: {stage.name}")

    return {
        "valid": not failures,
        "path": str(destination),
        "target": manifest.get("target"),
        "core_ir_sha256": actual_core_hash,
        "world_sha256": actual_world_hash,
        "usdchecker": usd_result,
        "failures": failures,
    }


def validate_path(path: str | Path) -> dict[str, Any]:
    """Validate a source manifest, compiled world, or campaign artifact."""

    source = Path(path)
    if source.is_dir() and (source / "compile_manifest.json").is_file():
        return validate_compiled_world(source)
    if source.is_file() and source.suffix.lower() in {".json", ".yaml", ".yml"}:
        if source.suffix.lower() == ".json":
            raw = json.loads(source.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("schema_version") == (
                "dynamical.composition-result.v1"
            ):
                from .composition import validate_composition_result

                result = validate_composition_result(raw)
                return {
                    "valid": True,
                    "kind": "composition_result",
                    "path": str(source),
                    "status": result.status,
                    "resolution_sha256": result.resolution_sha256,
                }
        document = load_facility_manifest(source)
        return {
            "valid": True,
            "kind": "facility_manifest",
            "path": str(source),
            "core_ir_sha256": document.core_ir_sha256(),
        }
    try:
        from .campaign import validate_path as validate_campaign_path
    except ImportError as exc:
        raise ValueError(f"unsupported validation path: {source}") from exc
    result = validate_campaign_path(source)
    return {"kind": "campaign", **result}
