"""Standalone runtime contract copied into each compiled backend pack.

This module uses only the Python standard library. Target launchers import the
copied file inside a matched Isaac environment.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_VERSION = "dynamical.campaign.v0.1"
EVIDENCE_CLASSES = {"simulator", "calibrated_twin", "shadow", "physical"}
COMPILE_MANIFEST_FIELDS = {
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


class RuntimeContractError(RuntimeError):
    """Compiled runtime input or evidence is invalid."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_artifact_path(value: Any) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise RuntimeContractError(
            "compiled artifact path must be a normalized POSIX relative path"
        )
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or value != relative.as_posix()
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise RuntimeContractError(f"unsafe compiled artifact path: {value!r}")
    return relative


def _artifact_file(root: Path, relative: PurePosixPath) -> Path:
    candidate = root.joinpath(*relative.parts)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise RuntimeContractError(
                f"compiled artifact path contains a symbolic link: {relative}"
            )
    if not candidate.resolve(strict=False).is_relative_to(root.resolve()):
        raise RuntimeContractError(f"compiled artifact escapes its root: {relative}")
    return candidate


def _manifest_records(manifest: Any) -> dict[str, str]:
    if not isinstance(manifest, dict) or set(manifest) != COMPILE_MANIFEST_FIELDS:
        raise RuntimeContractError("compiled manifest fields are invalid")
    if manifest.get("schema_version") != "dynamical.compile-manifest.v1":
        raise RuntimeContractError("compiled manifest schema version is unsupported")
    if manifest.get("compiler_version") != "0.2.0":
        raise RuntimeContractError("compiled manifest compiler version is unsupported")
    if manifest.get("target") not in {"isaac", "openusd"}:
        raise RuntimeContractError("compiled manifest target is unsupported")
    for field in ("core_ir_sha256", "adapter_pack_sha256", "world_sha256"):
        if not _valid_hash(manifest.get(field)):
            raise RuntimeContractError(f"compiled manifest {field} is invalid")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise RuntimeContractError("compiled manifest artifacts must be a list")
    records: dict[str, str] = {}
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {"path", "sha256"}:
            raise RuntimeContractError("compiled artifact records are malformed")
        relative = _normalized_artifact_path(artifact.get("path")).as_posix()
        digest = artifact.get("sha256")
        if relative in records:
            raise RuntimeContractError(f"duplicate compiled artifact path: {relative}")
        if not _valid_hash(digest):
            raise RuntimeContractError(f"compiled artifact hash is invalid: {relative}")
        records[relative] = digest
    artifact_count = manifest.get("artifact_count")
    if (
        not isinstance(artifact_count, int)
        or isinstance(artifact_count, bool)
        or artifact_count != len(records)
    ):
        raise RuntimeContractError("compiled artifact count does not match")
    root_stage = _normalized_artifact_path(manifest.get("root_stage")).as_posix()
    if root_stage != "root.usda" or root_stage not in records:
        raise RuntimeContractError("compiled root stage is invalid")
    if stable_hash(records) != manifest.get("world_sha256"):
        raise RuntimeContractError("compiled world hash does not match")
    return records


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeContractError(f"expected an object: {path}")
    return value


def _schema_fields(value: dict[str, Any], *, label: str) -> tuple[set[str], set[str]]:
    properties = value.get("properties")
    required = value.get("required")
    if not isinstance(properties, dict) or not isinstance(required, list):
        raise RuntimeContractError(f"compiled {label} schema is incomplete")
    return set(properties), {str(name) for name in required}


def _validate_parameter_value(specification: dict[str, Any], value: Any) -> None:
    name = str(specification.get("name", ""))
    value_type = specification.get("value_type")
    numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
    valid_type = {
        "number": numeric,
        "duration": numeric,
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "string": isinstance(value, str),
        "asset_id": isinstance(value, str),
    }.get(value_type, False)
    if not valid_type:
        raise RuntimeContractError(f"action parameter {name!r} has an invalid type")
    if numeric and not math.isfinite(float(value)):
        raise RuntimeContractError(f"action parameter {name!r} must be finite")
    if specification.get("enum") is not None and value not in specification["enum"]:
        raise RuntimeContractError(f"action parameter {name!r} is outside its enum")
    if specification.get("minimum") is not None and float(value) < float(specification["minimum"]):
        raise RuntimeContractError(f"action parameter {name!r} is below its minimum")
    if specification.get("maximum") is not None and float(value) > float(specification["maximum"]):
        raise RuntimeContractError(f"action parameter {name!r} is above its maximum")


_SAMPLE_TRANSITION_REQUIRED_FIELDS = {
    "kind",
    "sample_id",
    "from_station",
    "to_station",
    "quantity_delta",
    "unit",
    "timestamp_s",
    "arrival_confirmed",
    "parent_sample_ids",
}
_SAMPLE_TRANSITION_FIELDS = _SAMPLE_TRANSITION_REQUIRED_FIELDS | {"step_id", "state_sha256"}


def _validate_sample_transition(value: Any) -> None:
    """Structural check for ``action.parameters["sample_transition"]``.

    ``dynamical.samples.SampleTransition`` (pydantic) is the full semantic
    validator, run when ``dynamical.campaign`` processes a complete trace
    (``check_invariants``). This module stays dependency-free -- stdlib only,
    copied standalone into every compiled pack -- so it mirrors that model's
    shape rather than importing it.
    """

    if not isinstance(value, dict):
        raise RuntimeContractError("action sample_transition must be an object")
    if not set(value).issubset(_SAMPLE_TRANSITION_FIELDS):
        unknown = sorted(set(value) - _SAMPLE_TRANSITION_FIELDS)
        raise RuntimeContractError(f"action sample_transition has unknown fields: {unknown}")
    missing = sorted(_SAMPLE_TRANSITION_REQUIRED_FIELDS - set(value))
    if missing:
        raise RuntimeContractError(f"action sample_transition is missing fields: {missing}")
    if value.get("kind") not in {"transfer", "aliquot", "consume"}:
        raise RuntimeContractError("action sample_transition kind is invalid")
    for name in ("sample_id", "from_station", "to_station", "unit"):
        field_value = value.get(name)
        if not isinstance(field_value, str) or not field_value:
            raise RuntimeContractError(
                f"action sample_transition {name} must be a non-empty string"
            )
    if not isinstance(value.get("arrival_confirmed"), bool):
        raise RuntimeContractError("action sample_transition arrival_confirmed must be a boolean")
    quantity_delta = value.get("quantity_delta")
    if (
        isinstance(quantity_delta, bool)
        or not isinstance(quantity_delta, (int, float))
        or not math.isfinite(float(quantity_delta))
    ):
        raise RuntimeContractError(
            "action sample_transition quantity_delta must be a finite number"
        )
    timestamp_s = value.get("timestamp_s")
    if (
        isinstance(timestamp_s, bool)
        or not isinstance(timestamp_s, (int, float))
        or float(timestamp_s) < 0
    ):
        raise RuntimeContractError(
            "action sample_transition timestamp_s must be a non-negative number"
        )
    parent_ids = value.get("parent_sample_ids")
    if not isinstance(parent_ids, list) or not all(
        isinstance(item, str) and item for item in parent_ids
    ):
        raise RuntimeContractError(
            "action sample_transition parent_sample_ids must be an array of non-empty strings"
        )
    step_id = value.get("step_id")
    if step_id is not None and (not isinstance(step_id, str) or not step_id):
        raise RuntimeContractError(
            "action sample_transition step_id must be a non-empty string or null"
        )


def _validate_parameters(
    parameters: Any,
    specifications: list[dict[str, Any]],
) -> None:
    if not isinstance(parameters, dict):
        raise RuntimeContractError("action parameters must be an object")
    # "sample_transition" is reserved custody bookkeeping, not a requested
    # capability parameter (see dynamical.samples' module docstring: a
    # sample-moving action declares its SampleTransition under this exact
    # key). Validated on its own shape above, then excluded from the
    # declared-parameter closed-set check below, exactly as campaign.py's
    # ActionRequest.parameters never treats it as a requested parameter either.
    if "sample_transition" in parameters:
        _validate_sample_transition(parameters["sample_transition"])
    requested = {name: value for name, value in parameters.items() if name != "sample_transition"}
    declared = {str(item.get("name", "")): item for item in specifications}
    extra = sorted(set(requested) - set(declared))
    if extra:
        raise RuntimeContractError(f"action has undeclared parameters: {extra}")
    missing = sorted(
        name
        for name, specification in declared.items()
        if specification.get("required", True) and name not in requested
    )
    if missing:
        raise RuntimeContractError(f"action is missing required parameters: {missing}")
    for name, value in requested.items():
        _validate_parameter_value(declared[name], value)


def _validate_optional_string_field(record: dict[str, Any], field_name: str, *, label: str) -> None:
    """A field that is either absent/null or a non-empty string."""

    value = record.get(field_name)
    if value is not None and (not isinstance(value, str) or not value):
        raise RuntimeContractError(f"{label} {field_name} must be a non-empty string or null")


def _validate_sample_fields(record: dict[str, Any], *, label: str) -> None:
    """Type-check the optional sample_id/sample_lineage lineage fields.

    Both fields are optional (absent means "not sample-bearing"), but when
    present they must be well-typed: a non-empty string or null for
    sample_id, an array of non-empty strings for sample_lineage. Presence and
    additionalProperties are already enforced generically from the compiled
    schema; this adds the type checks the schema's "type" keyword alone
    would also express, kept explicit here to fail with a precise message.
    """

    _validate_optional_string_field(record, "sample_id", label=label)
    sample_lineage = record.get("sample_lineage", [])
    if not isinstance(sample_lineage, list) or not all(
        isinstance(item, str) and item for item in sample_lineage
    ):
        raise RuntimeContractError(f"{label} sample_lineage must be an array of non-empty strings")


def validate_action(action: Any, pack: dict[str, Any]) -> None:
    """Validate one action against the compiled schema and facility capabilities."""

    if not isinstance(action, dict):
        raise RuntimeContractError("trace action must be an object")
    action_schema = pack["action_schema"]
    allowed_fields, required_fields = _schema_fields(action_schema, label="action")
    missing_fields = sorted(required_fields - set(action))
    if missing_fields:
        raise RuntimeContractError(f"action is missing fields: {missing_fields}")
    if action_schema.get("additionalProperties") is False:
        extra_fields = sorted(set(action) - allowed_fields)
        if extra_fields:
            raise RuntimeContractError(f"action has undeclared fields: {extra_fields}")

    provider_id = action.get("provider_id")
    if not isinstance(provider_id, str) or not provider_id:
        raise RuntimeContractError("action provider_id is absent")
    evidence_class = action.get("evidence_class")
    if evidence_class not in EVIDENCE_CLASSES:
        raise RuntimeContractError("action evidence_class is invalid")
    _validate_sample_fields(action, label="action")
    _validate_optional_string_field(action, "station_id", label="action")

    kind = action.get("kind")
    kind_schema = action_schema.get("properties", {}).get("kind", {})
    allowed_kinds = kind_schema.get("enum", [])
    if kind not in allowed_kinds:
        raise RuntimeContractError(f"action kind is not declared by the compiled schema: {kind!r}")
    capabilities = pack["facility"].get("capabilities", [])
    kind_capabilities = [
        item for item in capabilities if isinstance(item, dict) and item.get("action_type") == kind
    ]
    actor_id = action.get("actor_id")
    actor_capabilities = [item for item in kind_capabilities if item.get("provider_id") == actor_id]

    provider_bindings = pack["campaign"].get("provider_bindings")
    if not isinstance(provider_bindings, dict):
        raise RuntimeContractError("runtime campaign provider bindings are absent")
    # Bindings are keyed by action_id: each action validates against the binding
    # compiled for its own step, so repeated action kinds with different
    # providers or parameter contracts are each checked correctly (and a trace
    # action whose id is not in the compiled campaign fails closed here). Reject a
    # malformed (non-string) action_id through the contract's normal failure path
    # rather than letting an unhashable value raise a raw TypeError on lookup.
    action_id = action.get("action_id")
    if not isinstance(action_id, str) or not action_id:
        raise RuntimeContractError("action action_id is absent or not a string")
    provider_binding = provider_bindings.get(action_id)
    if not isinstance(provider_binding, dict):
        raise RuntimeContractError(f"runtime provider binding is absent for action {action_id!r}")
    if not actor_capabilities and provider_binding.get("binding_scope") != "virtual_sdl":
        expected = sorted({str(item.get("provider_id")) for item in kind_capabilities})
        if not kind_capabilities:
            raise RuntimeContractError(
                f"action kind has no compiled facility or virtual SDL capability: {kind!r}"
            )
        raise RuntimeContractError(
            f"action actor {actor_id!r} does not provide {kind!r}; expected one of {expected}"
        )
    if (
        provider_binding.get("endpoint_id") != actor_id
        or provider_binding.get("provider_id") != provider_id
        or provider_binding.get("evidence_class") != evidence_class
    ):
        raise RuntimeContractError("action does not match the selected capability provider")

    if not actor_capabilities:
        specifications = provider_binding.get("execution_parameters")
        if not isinstance(specifications, list):
            raise RuntimeContractError("virtual SDL execution parameters are invalid")
        _validate_parameters(action.get("parameters"), specifications)
        return

    errors = []
    for capability in actor_capabilities:
        specifications = capability.get("parameters", [])
        if not isinstance(specifications, list):
            raise RuntimeContractError("compiled capability parameters are invalid")
        try:
            _validate_parameters(action.get("parameters"), specifications)
            return
        except RuntimeContractError as exc:
            errors.append(str(exc))
    raise RuntimeContractError(errors[0])


def verify_compiled_pack(compiled_world: str | Path) -> dict[str, Any]:
    """Verify all compiler-declared files and linked target hashes."""

    source_root = Path(compiled_world)
    if source_root.is_symlink() or not source_root.is_dir():
        raise RuntimeContractError("compiled pack root must be a real directory")
    root = source_root.resolve()
    required = {
        "action_schema.json",
        "adapter_pack.json",
        "backend_config.json",
        "campaign_trace.schema.json",
        "compile_manifest.json",
        "core_ir.json",
        "facility_ir.json",
        "observation_schema.json",
        "root.usda",
        "runtime_campaign.json",
    }
    manifest_path = root / "compile_manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RuntimeContractError("compiled manifest is absent or is a symbolic link")
    manifest = _read_object(manifest_path)
    records = _manifest_records(manifest)
    if not (required - {"compile_manifest.json"}).issubset(records):
        raise RuntimeContractError("required pack files are not declared by the manifest")
    actual: dict[str, str] = {}
    for relative, expected in records.items():
        path = _artifact_file(root, PurePosixPath(relative))
        if not path.is_file():
            raise RuntimeContractError(f"compiled artifact is absent: {relative}")
        digest = file_sha256(path)
        if digest != expected:
            raise RuntimeContractError(f"compiled artifact hash mismatch: {relative}")
        actual[relative] = digest
    actual_files: set[str] = set()
    for item in root.rglob("*"):
        if item.is_symlink():
            raise RuntimeContractError(f"compiled pack contains a symbolic link: {item}")
        if item.is_file():
            # No exemptions: a planted .pyc could shadow the hashed contract
            # source, so generated bytecode is an unexpected file like any
            # other (every launcher and loader sets dont_write_bytecode).
            actual_files.add(item.relative_to(root).as_posix())
    if actual_files != set(records) | {"compile_manifest.json"}:
        raise RuntimeContractError("compiled pack has missing or unexpected files")

    backend = _read_object(root / "backend_config.json")
    adapter = _read_object(root / "adapter_pack.json")
    campaign = _read_object(root / "runtime_campaign.json")
    facility = _read_object(root / "facility_ir.json")
    core = _read_object(root / "core_ir.json")
    action_schema = _read_object(root / "action_schema.json")
    observation_schema = _read_object(root / "observation_schema.json")
    trace_schema = _read_object(root / "campaign_trace.schema.json")
    if trace_schema.get("properties", {}).get("schema_version", {}).get("const") != (
        SCHEMA_VERSION
    ):
        raise RuntimeContractError("compiled trace schema does not match the runtime contract")

    core_hash = str(manifest.get("core_ir_sha256", ""))
    if stable_hash(core) != core_hash:
        raise RuntimeContractError("compiled core IR hash does not match")
    core_projection = dict(facility)
    core_projection.pop("adapter_bindings", None)
    if core_projection != core:
        raise RuntimeContractError("compiled facility IR does not project to the core IR")
    facility_bindings = facility.get("adapter_bindings")
    if not isinstance(facility_bindings, list):
        raise RuntimeContractError("compiled facility adapter bindings are invalid")
    expected_bindings = sorted(
        [
            binding
            for binding in facility_bindings
            if isinstance(binding, dict) and binding.get("target") == manifest.get("target")
        ],
        key=lambda binding: str(binding.get("id", "")),
    )
    expected_adapter_hash = stable_hash(
        {
            "schema_version": facility.get("schema_version"),
            "target": manifest.get("target"),
            "bindings": expected_bindings,
        }
    )
    if set(adapter) != {
        "schema_version",
        "target",
        "core_ir_sha256",
        "adapter_pack_sha256",
        "bindings",
    }:
        raise RuntimeContractError("compiled adapter pack fields are invalid")
    if (
        adapter.get("schema_version") != facility.get("schema_version")
        or adapter.get("target") != manifest.get("target")
        or adapter.get("bindings") != expected_bindings
        or manifest.get("adapter_pack_sha256") != expected_adapter_hash
        or adapter.get("adapter_pack_sha256") != expected_adapter_hash
    ):
        raise RuntimeContractError("compiled adapter pack does not match the facility IR")
    if backend.get("core_ir_sha256") != core_hash or adapter.get("core_ir_sha256") != core_hash:
        raise RuntimeContractError("target pack core IR hashes do not match")
    if campaign.get("core_ir_sha256") != core_hash:
        raise RuntimeContractError("runtime campaign core IR hash does not match")
    capabilities = facility.get("capabilities", [])
    if not isinstance(capabilities, list):
        raise RuntimeContractError("compiled facility capabilities are invalid")
    facility_action_kinds = {
        str(item.get("action_type")) for item in capabilities if isinstance(item, dict)
    }
    schema_action_kinds = {
        str(item) for item in action_schema.get("x-dynamical-declared-capability-action-types", [])
    }
    if schema_action_kinds != facility_action_kinds:
        raise RuntimeContractError("compiled action schema and facility capabilities differ")

    facility_channels = {
        str(channel.get("id"))
        for device in facility.get("devices", [])
        if isinstance(device, dict)
        for channel in device.get("state_channels", [])
        if isinstance(channel, dict)
    } | {
        str(channel.get("channel_id"))
        for material in facility.get("material_states", [])
        if isinstance(material, dict)
        for channel in material.get("initial_channels", [])
        if isinstance(channel, dict)
    }
    schema_channels = {
        str(item) for item in observation_schema.get("x-dynamical-declared-channel-ids", [])
    }
    if schema_channels != facility_channels:
        raise RuntimeContractError("compiled observation schema and facility channels differ")

    pack = {
        "root": root,
        "manifest": manifest,
        "backend": backend,
        "adapter": adapter,
        "campaign": campaign,
        "facility": facility,
        "action_schema": action_schema,
        "observation_schema": observation_schema,
        "trace_schema": trace_schema,
    }
    actions = campaign.get("actions")
    if not isinstance(actions, list):
        raise RuntimeContractError("compiled runtime campaign actions are invalid")
    for action in actions:
        validate_action(action, pack)
    if campaign.get("execution_status") == "requires_external_runtime_gate" and not (
        1 <= len(actions) <= 32
    ):
        raise RuntimeContractError("executable runtime campaign needs 1 to 32 actions")
    return pack


def _valid_hash(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_channel(
    channel: dict[str, Any],
    observation_schema: dict[str, Any],
    *,
    provider_id: str,
    evidence_class: str,
) -> None:
    required = {
        "name",
        "value",
        "unit",
        "quality",
        "origin",
        "provider_id",
        "evidence_class",
        "uncertainty",
    }
    if set(channel) != required:
        raise RuntimeContractError("observation channel fields do not match the contract")
    channel_schema = observation_schema.get("properties", {}).get("channels", {}).get("items", {})
    allowed_origins = channel_schema.get("properties", {}).get("origin", {}).get("enum", [])
    if channel["origin"] not in allowed_origins:
        raise RuntimeContractError("observation origin is invalid")
    if channel["quality"] not in {"valid", "estimated", "degraded", "unavailable"}:
        raise RuntimeContractError("observation quality is invalid")
    if channel.get("provider_id") != provider_id or channel.get("evidence_class") != evidence_class:
        raise RuntimeContractError("observation channel provider binding is invalid")
    value = channel["value"]
    if isinstance(value, float) and not math.isfinite(value):
        raise RuntimeContractError("observation values must be finite")
    declared_channels = observation_schema.get("x-dynamical-declared-channel-ids", [])
    if channel["name"] not in declared_channels:
        raise RuntimeContractError(
            f"observation channel is not declared by the compiled schema: {channel['name']!r}"
        )


def _constraint_ids(
    value: Any,
    *,
    label: str,
    declarations: dict[str, dict[str, Any]],
) -> set[str]:
    if not isinstance(value, list):
        raise RuntimeContractError(f"{label} constraints must be a list")
    identifiers: list[str] = []
    required = {
        "constraint_id",
        "phase",
        "passed",
        "outcome",
        "measured_value",
        "margin",
        "limit",
        "verifier",
    }
    for record in value:
        if not isinstance(record, dict) or set(record) != required:
            raise RuntimeContractError(f"{label} constraint evidence is incomplete")
        identifier = record.get("constraint_id")
        if not isinstance(identifier, str) or not identifier:
            raise RuntimeContractError(f"{label} constraint_id is invalid")
        specification = declarations.get(identifier)
        if not isinstance(specification, dict):
            raise RuntimeContractError(f"{label} constraint is not declared: {identifier}")
        if not isinstance(record.get("passed"), bool):
            raise RuntimeContractError(f"{label} constraint result is invalid")
        if record.get("outcome") not in {"passed", "violated", "unavailable"}:
            raise RuntimeContractError(f"{label} constraint outcome is invalid")
        if record.get("passed") is not (record.get("outcome") == "passed"):
            raise RuntimeContractError(f"{label} constraint passed flag disagrees with its outcome")
        expected_limit = {
            "operator": specification.get("operator"),
            "bound": specification.get("bound"),
            "unit": specification.get("unit"),
            "enforcement": specification.get("enforcement"),
        }
        if record.get("limit") != expected_limit:
            raise RuntimeContractError(f"{label} constraint limit differs from its declaration")
        if record.get("phase") != specification.get("phase"):
            raise RuntimeContractError(f"{label} constraint phase differs from its declaration")
        if record.get("verifier") != specification.get("verifier_binding_id"):
            raise RuntimeContractError(f"{label} constraint verifier differs from its declaration")
        expected_outcome = _evaluate_constraint_outcome(
            operator=specification.get("operator"),
            bound=specification.get("bound"),
            measured_value=record.get("measured_value"),
        )
        if record.get("outcome") != expected_outcome:
            raise RuntimeContractError(f"{label} constraint result differs from its measured value")
        identifiers.append(identifier)
    if len(identifiers) != len(set(identifiers)):
        raise RuntimeContractError(f"{label} constraint evidence contains duplicates")
    return set(identifiers)


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _evaluate_constraint_outcome(
    *,
    operator: Any,
    bound: Any,
    measured_value: Any,
) -> str:
    """Classify one declared constraint result as passed, violated, or unavailable.

    A ``None`` measured value means the channel produced no value at all; every
    other non-numeric or non-finite value is a genuine (if malformed) measurement
    and is reported as ``violated``, not ``unavailable``.
    """

    if measured_value is None:
        return "unavailable"
    measured = _finite_number(measured_value)
    if operator == "eq":
        numeric_bound = _finite_number(bound)
        if measured is not None or numeric_bound is not None:
            ok = measured is not None and numeric_bound is not None and measured == numeric_bound
        else:
            ok = measured_value == bound
        return "passed" if ok else "violated"
    if measured is None:
        return "violated"
    if operator == "between":
        if not isinstance(bound, dict) or set(bound) != {"minimum", "maximum"}:
            raise RuntimeContractError("between constraint bound is invalid")
        minimum = _finite_number(bound.get("minimum"))
        maximum = _finite_number(bound.get("maximum"))
        if minimum is None or maximum is None or minimum > maximum:
            raise RuntimeContractError("between constraint bound is invalid")
        return "passed" if minimum <= measured <= maximum else "violated"
    numeric_bound = _finite_number(bound)
    if numeric_bound is None:
        raise RuntimeContractError(f"{operator!r} constraint bound is invalid")
    if operator == "gt":
        ok = measured > numeric_bound
    elif operator == "ge":
        ok = measured >= numeric_bound
    elif operator == "lt":
        ok = measured < numeric_bound
    elif operator == "le":
        ok = measured <= numeric_bound
    else:
        raise RuntimeContractError(f"constraint operator is unsupported: {operator!r}")
    return "passed" if ok else "violated"


def _evaluate_constraint(
    *,
    operator: Any,
    bound: Any,
    measured_value: Any,
) -> bool:
    """Backward-compatible boolean view: only a ``passed`` outcome is True."""

    return (
        _evaluate_constraint_outcome(operator=operator, bound=bound, measured_value=measured_value)
        == "passed"
    )


def _constraint_margin(operator: Any, bound: Any, measured_value: Any) -> float | None:
    """Signed distance from a measured value to its declared bound, in the constraint's unit.

    Positive means the bound is satisfied by that much; zero means exactly at the
    bound; negative means it is violated by that much. ``None`` when the measurement
    is missing or non-numeric -- the same case ``_evaluate_constraint_outcome`` reports
    as ``unavailable``, not a fabricated distance. Mirrors ``campaign._constraint_margin``;
    duplicated (not imported) because this module is copied standalone into every
    compiled backend pack and must stay dependency-free.
    """

    measured = _finite_number(measured_value)
    if measured is None:
        return None
    if operator == "between":
        if not isinstance(bound, dict) or set(bound) != {"minimum", "maximum"}:
            raise RuntimeContractError("between constraint bound is invalid")
        minimum = _finite_number(bound.get("minimum"))
        maximum = _finite_number(bound.get("maximum"))
        if minimum is None or maximum is None or minimum > maximum:
            raise RuntimeContractError("between constraint bound is invalid")
        return min(measured - minimum, maximum - measured)
    numeric_bound = _finite_number(bound)
    if numeric_bound is None:
        raise RuntimeContractError(f"{operator!r} constraint bound is invalid")
    if operator in {"gt", "ge"}:
        return measured - numeric_bound
    if operator in {"lt", "le"}:
        return numeric_bound - measured
    if operator == "eq":
        return -abs(measured - numeric_bound)
    raise RuntimeContractError(f"constraint operator is unsupported: {operator!r}")


def _applicable_constraint_ids(
    action: dict[str, Any],
    pack: dict[str, Any],
    *,
    phase: str,
) -> set[str]:
    campaign_bindings = pack["campaign"].get("constraint_bindings")
    if isinstance(campaign_bindings, dict):
        action_bindings = campaign_bindings.get(action.get("action_id"))
        if not isinstance(action_bindings, dict):
            raise RuntimeContractError("campaign action constraint binding is absent")
        values = action_bindings.get(phase)
        if not isinstance(values, list):
            raise RuntimeContractError("campaign action constraint binding is invalid")
        declared = {
            str(item.get("id"))
            for item in pack["facility"].get("constraints", [])
            if isinstance(item, dict)
        }
        requested = {str(value) for value in values}
        unknown = sorted(requested - declared)
        if unknown:
            raise RuntimeContractError(f"campaign binds undeclared constraints: {unknown}")
        return requested
    matches = [
        capability
        for capability in pack["facility"].get("capabilities", [])
        if isinstance(capability, dict)
        and capability.get("action_type") == action.get("kind")
        and capability.get("provider_id") == action.get("actor_id")
    ]
    if len(matches) != 1:
        raise RuntimeContractError("action capability binding is not unique")
    field = (
        "precondition_constraint_ids" if phase == "pre_action" else "postcondition_constraint_ids"
    )
    values = matches[0].get(field, [])
    if not isinstance(values, list):
        raise RuntimeContractError("capability constraint bindings are invalid")
    return {str(value) for value in values}


def constraint_evidence(
    action: dict[str, Any],
    pack: dict[str, Any],
    *,
    phase: str,
    channels: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate the declared constraints bound to one compiled action phase."""

    if phase not in {"pre_action", "post_action"}:
        raise RuntimeContractError("constraint phase is invalid")
    constraint_ids = _applicable_constraint_ids(action, pack, phase=phase)
    declared = {
        str(item.get("id")): item
        for item in pack["facility"].get("constraints", [])
        if isinstance(item, dict)
    }
    values = {
        str(channel.get("name")): channel.get("value")
        for channel in channels or []
        if isinstance(channel, dict)
    }
    for material in pack["facility"].get("material_states", []):
        if not isinstance(material, dict):
            continue
        for channel in material.get("initial_channels", []):
            if isinstance(channel, dict):
                values.setdefault(str(channel.get("channel_id")), channel.get("value"))
    evidence = []
    for identifier in sorted(constraint_ids):
        specification = declared[identifier]
        channel_id = str(specification.get("channel_id"))
        value = values.get(channel_id)
        bound = specification.get("bound")
        operator = specification.get("operator")
        outcome = _evaluate_constraint_outcome(
            operator=operator,
            bound=bound,
            measured_value=value,
        )
        evidence.append(
            {
                "constraint_id": identifier,
                "phase": str(specification.get("phase")),
                "passed": outcome == "passed",
                "outcome": outcome,
                "measured_value": value,
                "margin": _constraint_margin(operator, bound, value),
                "limit": {
                    "operator": operator,
                    "bound": bound,
                    "unit": specification.get("unit"),
                    "enforcement": specification.get("enforcement"),
                },
                "verifier": str(specification.get("verifier_binding_id")),
            }
        )
    return evidence


def _validate_evidence_record(
    record: Any,
    *,
    provider_id: str,
    evidence_class: str,
) -> None:
    required = {
        "evidence_id",
        "uri",
        "sha256",
        "media_type",
        "role",
        "provider_id",
        "evidence_class",
    }
    if not isinstance(record, dict) or set(record) != required:
        raise RuntimeContractError("evidence fields do not match the runtime contract")
    if not _valid_hash(record.get("sha256")):
        raise RuntimeContractError("evidence hash is invalid")
    if record.get("role") == "raw_backend_observation":
        expected_id = f"snapshot-{record['sha256'][:16]}"
        if record.get("evidence_id") != expected_id:
            raise RuntimeContractError("evidence ID does not bind to its content hash")
    if record.get("provider_id") != provider_id or record.get("evidence_class") != evidence_class:
        raise RuntimeContractError("evidence does not match the selected capability provider")


def validate_trace(events: list[dict[str, Any]], pack: dict[str, Any]) -> None:
    """Validate campaign invariants against the loaded compiled-pack schemas.

    A constraint's declared ``enforcement`` controls what a failure means, not just
    whether it happened: ``terminate`` is the only level that requires the campaign's
    terminal status to be ``failed`` here. ``reject`` (refuse the action but continue
    the campaign) and ``report`` (record only) are honored by not raising on their
    account; actually refusing a rejected action's effect on world state happens in
    the target runtime loop that produces the trace, not in this validator.
    """

    allowed_event_types = {"campaign_start", "action", "observation", "campaign_end"}
    unknown_event_types = sorted(
        {
            str(event.get("event_type"))
            for event in events
            if event.get("event_type") not in allowed_event_types
        }
    )
    if unknown_event_types:
        raise RuntimeContractError(f"trace has unknown event types: {unknown_event_types}")
    if not events or events[0].get("event_type") != "campaign_start":
        raise RuntimeContractError("trace must start with campaign_start")
    if events[-1].get("event_type") != "campaign_end":
        raise RuntimeContractError("trace must end with campaign_end")
    campaign_actions = pack["campaign"].get("actions", [])
    expected_event_types = ["campaign_start"]
    for _ in campaign_actions:
        expected_event_types.extend(["action", "observation"])
    expected_event_types.append("campaign_end")
    actual_event_types = [event.get("event_type") for event in events]
    if actual_event_types != expected_event_types:
        raise RuntimeContractError(
            "trace event sequence does not match the complete compiled campaign"
        )

    identity_fields = (
        "campaign_id",
        "run_id",
        "mode",
        "seed",
        "backend_revision",
        "ir_hash",
        "world_hash",
        "campaign_hash",
    )
    identity = tuple(events[0].get(name) for name in identity_fields)
    expected_bindings = {
        "campaign_id": str(pack["campaign"]["campaign_id"]),
        "ir_hash": str(pack["manifest"]["core_ir_sha256"]),
        "world_hash": str(pack["manifest"]["world_sha256"]),
        "campaign_hash": stable_hash(pack["campaign"]),
    }
    for name, expected in expected_bindings.items():
        if events[0].get(name) != expected:
            raise RuntimeContractError(f"trace {name} does not bind to the verified compiled pack")
    # Required proof outputs come from the verified compiled pack: the trace's
    # own declared proof block must equal the pack's, so a trace cannot remove
    # or redefine what it is required to prove.
    start_provenance = events[0].get("provenance") or {}
    if start_provenance.get("proof_requirements") != pack["campaign"].get("proof_requirements", []):
        raise RuntimeContractError(
            "trace proof requirements do not bind to the verified compiled pack"
        )
    expected_step_ids = [str(action["action_id"]) for action in pack["campaign"].get("actions", [])]
    declared_step_ids = start_provenance.get("declared_step_ids")
    if declared_step_ids is not None and declared_step_ids != expected_step_ids:
        raise RuntimeContractError("trace declared steps do not bind to the verified compiled pack")
    last_time = -1.0
    action_count = 0
    previous_action: dict[str, Any] | None = None
    terminating_constraint_failed = False
    constraint_declarations = {
        str(item.get("id")): item
        for item in pack["facility"].get("constraints", [])
        if isinstance(item, dict)
    }
    for sequence, event in enumerate(events):
        if event.get("schema_version") != SCHEMA_VERSION:
            raise RuntimeContractError("trace schema version is invalid")
        if event.get("sequence") != sequence:
            raise RuntimeContractError("trace sequence is not contiguous")
        logical_time = float(event.get("logical_time_s", -1.0))
        if logical_time < last_time:
            raise RuntimeContractError("trace logical time is not monotonic")
        last_time = logical_time
        if tuple(event.get(name) for name in identity_fields) != identity:
            raise RuntimeContractError("trace identity changed")
        if not all(
            _valid_hash(event.get(name)) for name in ("ir_hash", "world_hash", "campaign_hash")
        ):
            raise RuntimeContractError("trace content hashes are invalid")
        provenance = event.get("provenance")
        if not isinstance(provenance, dict):
            raise RuntimeContractError("trace provenance is invalid")
        if provenance.get("w1_admitted") is True or provenance.get("w1_evidence") is True:
            raise RuntimeContractError(
                "a runtime trace cannot self-admit W1 from provenance booleans"
            )
        if (
            provenance.get("embodied_backend") is True
            and provenance.get("compiled_adapter") is not True
        ):
            raise RuntimeContractError(
                "embodied runtime provenance needs a verified compiled adapter"
            )
        event_type = event.get("event_type")
        if event_type in {"campaign_start", "campaign_end"} and (
            event.get("action") is not None
            or event.get("observation") is not None
            or event.get("constraints")
            or event.get("evidence")
        ):
            raise RuntimeContractError(
                f"{event_type} cannot carry action, observation, constraint, or evidence records"
            )
        if event_type == "action":
            if event.get("observation") is not None or event.get("evidence"):
                raise RuntimeContractError(
                    "action events cannot carry observations or evidence records"
                )
            campaign_index = action_count
            action_count += 1
            action = event.get("action") or {}
            validate_action(action, pack)
            if (
                campaign_index >= len(campaign_actions)
                or action != campaign_actions[campaign_index]
            ):
                raise RuntimeContractError("trace action differs from the compiled campaign")
            if sequence + 1 >= len(events) or events[sequence + 1].get("event_type") != (
                "observation"
            ):
                raise RuntimeContractError("each action must be followed by an observation")
            expected_constraints = _applicable_constraint_ids(
                action,
                pack,
                phase="pre_action",
            )
            found_constraints = _constraint_ids(
                event.get("constraints"),
                label="action",
                declarations=constraint_declarations,
            )
            if found_constraints != expected_constraints:
                missing = sorted(expected_constraints - found_constraints)
                extra = sorted(found_constraints - expected_constraints)
                raise RuntimeContractError(
                    "action constraint coverage differs from the compiled campaign: "
                    f"missing={missing}, extra={extra}"
                )
            # Unlike a post-action (observation-phase) constraint, which can only be
            # noticed after its action already ran, a pre-action constraint's whole
            # point is to prevent that action from running at all -- so ANY failed
            # pre-action constraint (not only a terminate-enforcement one) must force
            # a failed campaign status here, matching the live runtime contract both
            # backends now enforce (see isaac_runtime.py's main() and campaign.py's
            # run_composed_campaign).
            terminating_constraint_failed = terminating_constraint_failed or any(
                not item["passed"] for item in event.get("constraints", [])
            )
            previous_action = action
        if event_type == "observation":
            if event.get("action") is not None:
                raise RuntimeContractError("observation events cannot carry action records")
            if previous_action is None:
                raise RuntimeContractError("observation has no preceding action")
            observation = event.get("observation") or {}
            observation_schema = pack["observation_schema"]
            allowed_fields, required_fields = _schema_fields(
                observation_schema, label="observation"
            )
            missing_fields = sorted(required_fields - set(observation))
            if missing_fields:
                raise RuntimeContractError(f"observation is missing fields: {missing_fields}")
            if observation_schema.get("additionalProperties") is False:
                extra_fields = sorted(set(observation) - allowed_fields)
                if extra_fields:
                    raise RuntimeContractError(f"observation has undeclared fields: {extra_fields}")
            if observation.get("provider_id") != previous_action.get("provider_id"):
                raise RuntimeContractError("observation provider_id differs from its action")
            if observation.get("evidence_class") != previous_action.get("evidence_class"):
                raise RuntimeContractError("observation evidence_class differs from its action")
            if observation.get("evidence_class") not in EVIDENCE_CLASSES:
                raise RuntimeContractError("observation evidence_class is invalid")
            _validate_sample_fields(observation, label="observation")
            channels = observation.get("channels", [])
            if not channels:
                raise RuntimeContractError("observation has no channels")
            for channel in channels:
                _validate_channel(
                    channel,
                    observation_schema,
                    provider_id=str(observation["provider_id"]),
                    evidence_class=str(observation["evidence_class"]),
                )
            raw_evidence_ids = observation.get("evidence_ids", [])
            if not isinstance(raw_evidence_ids, list) or any(
                not isinstance(identifier, str) or not identifier for identifier in raw_evidence_ids
            ):
                raise RuntimeContractError("observation evidence IDs are invalid")
            if len(raw_evidence_ids) != len(set(raw_evidence_ids)):
                raise RuntimeContractError("observation evidence IDs contain duplicates")
            evidence_ids = set(raw_evidence_ids)
            evidence_records = event.get("evidence", [])
            if not isinstance(evidence_records, list):
                raise RuntimeContractError("observation evidence records are invalid")
            for record in evidence_records:
                _validate_evidence_record(
                    record,
                    provider_id=str(observation["provider_id"]),
                    evidence_class=str(observation["evidence_class"]),
                )
            available_values = [item.get("evidence_id") for item in evidence_records]
            available = set(available_values)
            if len(available_values) != len(available):
                raise RuntimeContractError("observation evidence records contain duplicates")
            if evidence_ids != available:
                raise RuntimeContractError("observation evidence bindings are not exact")
            expected_constraints = _applicable_constraint_ids(
                previous_action,
                pack,
                phase="post_action",
            )
            found_constraints = _constraint_ids(
                event.get("constraints"),
                label="observation",
                declarations=constraint_declarations,
            )
            if found_constraints != expected_constraints:
                missing = sorted(expected_constraints - found_constraints)
                extra = sorted(found_constraints - expected_constraints)
                raise RuntimeContractError(
                    "observation constraint coverage differs from the compiled campaign: "
                    f"missing={missing}, extra={extra}"
                )
            terminating_constraint_failed = terminating_constraint_failed or any(
                not item["passed"] and item.get("limit", {}).get("enforcement") == "terminate"
                for item in event.get("constraints", [])
            )
            previous_action = None
    if action_count != len(campaign_actions):
        raise RuntimeContractError("trace action count does not match the compiled campaign")
    terminal_provenance = events[-1].get("provenance", {})
    terminal_status = terminal_provenance.get("execution_status")
    if terminal_status not in {"passed", "failed"}:
        raise RuntimeContractError("campaign_end needs an explicit execution_status")
    if terminating_constraint_failed and terminal_status != "failed":
        raise RuntimeContractError(
            "a failed pre-action or terminate-enforcement constraint needs failed campaign status"
        )


class TraceWriter:
    """Build a canonical Dynamical trace from target runtime records."""

    def __init__(
        self,
        pack: dict[str, Any],
        *,
        run_id: str,
        seed: int,
        backend_revision: str,
        provenance: dict[str, Any],
        output_path: str | Path | None = None,
    ) -> None:
        manifest = pack["manifest"]
        campaign = pack["campaign"]
        self.identity = {
            "campaign_id": str(campaign["campaign_id"]),
            "run_id": run_id,
            "mode": "simulate",
            "seed": seed,
            "backend_revision": backend_revision,
            "ir_hash": str(manifest["core_ir_sha256"]),
            "world_hash": str(manifest["world_sha256"]),
            "campaign_hash": stable_hash(campaign),
        }
        constraint_contract = {
            "declarations": {
                str(item["id"]): dict(item)
                for item in sorted(
                    pack["facility"].get("constraints", []),
                    key=lambda item: str(item.get("id", "")),
                )
                if isinstance(item, dict)
            },
            "applicability": {
                str(action["action_id"]): {
                    "pre_action": sorted(
                        _applicable_constraint_ids(action, pack, phase="pre_action")
                    ),
                    "observation": sorted(
                        _applicable_constraint_ids(action, pack, phase="post_action")
                    ),
                }
                for action in campaign.get("actions", [])
            },
        }
        declared_step_ids = [str(action["action_id"]) for action in campaign.get("actions", [])]
        self.provenance = {
            **provenance,
            "constraint_contract": constraint_contract,
            "constraint_contract_sha256": stable_hash(constraint_contract),
            "provider_contract": {
                str(action["action_id"]): {
                    "provider_id": action["provider_id"],
                    "evidence_class": action["evidence_class"],
                }
                for action in campaign.get("actions", [])
            },
            # Resolved at compile time (see _runtime_pack.py's
            # _resolved_proof_requirements) into each requirement's own concrete
            # action_ids -- carried here so dynamical.campaign.validate_events can
            # check proof completeness for this backend's trace on the same terms
            # as every other backend, rather than trusting this launcher's own
            # execution_status claim. The per-event proof_contract_sha256 binds
            # this block so a trace cannot drop or redefine it undetected.
            "declared_step_ids": declared_step_ids,
            "proof_requirements": campaign.get("proof_requirements", []),
            "proof_contract_sha256": stable_hash(
                {
                    "declared_step_ids": declared_step_ids,
                    "proof_requirements": campaign.get("proof_requirements", []),
                }
            ),
        }
        self.pack = pack
        self.events: list[dict[str, Any]] = []
        self.output_path = Path(output_path) if output_path is not None else None
        if self.output_path is not None:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            self.output_path.write_text("", encoding="utf-8")

    def add(
        self,
        event_type: str,
        logical_time_s: float,
        *,
        action: dict[str, Any] | None = None,
        observation: dict[str, Any] | None = None,
        constraints: list[dict[str, Any]] | None = None,
        evidence: list[dict[str, Any]] | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        sequence = len(self.events)
        event = {
            "schema_version": SCHEMA_VERSION,
            "event_type": event_type,
            "event_id": f"{self.identity['run_id']}:event:{sequence:06d}",
            **self.identity,
            "sequence": sequence,
            "logical_time_s": float(logical_time_s),
            "provenance": {**self.provenance, **(provenance or {})},
            "source_trace_sha256": None,
            "action": action,
            "observation": observation,
            "constraints": constraints or [],
            "evidence": evidence or [],
        }
        self.events.append(event)
        if self.output_path is not None:
            with self.output_path.open("a", encoding="utf-8") as stream:
                stream.write(canonical_json(event) + "\n")
                stream.flush()
        return event

    def write(self, path: str | Path) -> str:
        validate_trace(self.events, self.pack)
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        content = "".join(canonical_json(event) + "\n" for event in self.events)
        if self.output_path is not None and target.resolve() == self.output_path.resolve():
            if target.read_text(encoding="utf-8") != content:
                raise RuntimeContractError("streamed trace differs from in-memory events")
        else:
            target.write_text(content, encoding="utf-8")
        return hashlib.sha256(content.encode("utf-8")).hexdigest()


def write_snapshot(
    path: str | Path,
    value: Any,
    *,
    provider_id: str,
    evidence_class: str,
) -> dict[str, Any]:
    """Write one canonical raw target snapshot and return an evidence record."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(canonical_json(value) + "\n", encoding="utf-8")
    digest = file_sha256(target)
    return {
        "evidence_id": f"snapshot-{digest[:16]}",
        "uri": target.as_posix(),
        "sha256": digest,
        "media_type": "application/json",
        "role": "raw_backend_observation",
        "provider_id": provider_id,
        "evidence_class": evidence_class,
    }
