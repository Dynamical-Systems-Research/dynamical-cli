"""Stable campaign records and the deterministic composed campaign runner."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from . import instruments
from .instruments import InstrumentRequest, InstrumentResult
from .reasons import RuntimeReason
from .samples import Sample, apply_transition, build_transition, check_invariants, state_digest

SCHEMA_VERSION = "dynamical.campaign.v0.1"
EVENT_KEYS = {
    "schema_version",
    "event_type",
    "event_id",
    "campaign_id",
    "run_id",
    "sequence",
    "logical_time_s",
    "mode",
    "seed",
    "backend_revision",
    "ir_hash",
    "world_hash",
    "campaign_hash",
    "provenance",
    "source_trace_sha256",
    "action",
    "observation",
    "constraints",
    "evidence",
}
RESTORE_KEYS = frozenset(
    {
        "at_event_id",
        "source_prefix_sha256",
        "source_world_sha256",
        "source_registry_sha256",
        "source_facility_sha256",
        "source_logical_time_s",
        "initial_samples",
        "restored_state_sha256",
        "source_evidence_classes",
    }
)


class CampaignValidationError(ValueError):
    """A campaign record does not meet the v0.1 contract."""


class RunMode(StrEnum):
    SIMULATE = "simulate"
    REPLAY = "replay"


ACTION_KIND_PATTERN = r"^[a-z][a-z0-9]*([-_][a-z0-9]+)*$"
_ACTION_KIND_RE = re.compile(ACTION_KIND_PATTERN)


def _validate_action_kind(value: str, name: str) -> str:
    """Validate one action-kind identifier against the open vocabulary pattern.

    There is no closed, global enum of action kinds. Which identifiers are
    admitted for one compiled pack is enforced per pack: the declared facility
    capabilities must exactly match the pack's own action_schema.json enum
    (see load_compiled_campaign_contract), and which operations are actually
    executable is decided by dynamical.instruments.resolve at run time.
    """

    if not isinstance(value, str) or not _ACTION_KIND_RE.match(value):
        raise CampaignValidationError(f"{name} must match {ACTION_KIND_PATTERN!r}: {value!r}")
    return value


class ObservationOrigin(StrEnum):
    RUNTIME_SENSOR = "runtime_sensor"
    BACKEND_STATE = "backend_state"
    SOURCE_MODEL = "source_model"
    PHYSICAL_SOURCE = "physical_source"


class EvidenceClass(StrEnum):
    """Provider class for one action, observation, or evidence record."""

    SIMULATOR = "simulator"
    CALIBRATED_TWIN = "calibrated_twin"
    SHADOW = "shadow"
    PHYSICAL = "physical"


def canonical_json(value: Any) -> str:
    """Return deterministic JSON used for all campaign hashes."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def trace_event_json_schema() -> dict[str, Any]:
    """Return the JSON Schema emitted by every compiled world."""

    hash_schema = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    scalar_schema = {"type": ["number", "integer", "string", "boolean", "null"]}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://dynamical.systems/schemas/dynamical/campaign-event-v0.1.json",
        "title": "Dynamical Campaign TraceEvent v0.1",
        "type": "object",
        "additionalProperties": False,
        "required": sorted(EVENT_KEYS),
        "properties": {
            "schema_version": {"const": SCHEMA_VERSION},
            "event_type": {"enum": ["campaign_start", "action", "observation", "campaign_end"]},
            "event_id": {"type": "string", "minLength": 1},
            "campaign_id": {"type": "string", "minLength": 1},
            "run_id": {"type": "string", "minLength": 1},
            "sequence": {"type": "integer", "minimum": 0},
            "logical_time_s": {"type": "number", "minimum": 0},
            "mode": {"enum": ["simulate", "replay"]},
            "seed": {"type": "integer", "minimum": 0},
            "backend_revision": {"type": "string", "minLength": 1},
            "ir_hash": hash_schema,
            "world_hash": hash_schema,
            "campaign_hash": hash_schema,
            "provenance": {"type": "object"},
            "source_trace_sha256": {"anyOf": [hash_schema, {"type": "null"}]},
            "action": {
                "anyOf": [
                    {"$ref": "#/$defs/action"},
                    {"type": "null"},
                ]
            },
            "observation": {
                "anyOf": [
                    {"$ref": "#/$defs/observation"},
                    {"type": "null"},
                ]
            },
            "constraints": {
                "type": "array",
                "items": {"$ref": "#/$defs/constraint"},
            },
            "evidence": {"type": "array", "items": {"$ref": "#/$defs/evidence"}},
        },
        "$defs": {
            "action": {
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
                    "kind": {"type": "string", "pattern": ACTION_KIND_PATTERN},
                    "actor_id": {"type": "string", "minLength": 1},
                    "provider_id": {"type": "string", "minLength": 1},
                    "evidence_class": {"enum": [item.value for item in EvidenceClass]},
                    "parameters": {"type": "object"},
                    "sample_id": {"anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}]},
                    "sample_lineage": {"type": "array", "items": {"type": "string"}},
                    "station_id": {"anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}]},
                },
            },
            "observation_channel": {
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
                    "value": scalar_schema,
                    "unit": {"type": "string", "minLength": 1},
                    "quality": {"enum": ["valid", "estimated", "degraded", "unavailable"]},
                    "origin": {"enum": [item.value for item in ObservationOrigin]},
                    "provider_id": {"type": "string", "minLength": 1},
                    "evidence_class": {"enum": [item.value for item in EvidenceClass]},
                    "uncertainty": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["value", "kind", "origin"],
                        "properties": {
                            "value": {"type": ["number", "null"], "minimum": 0},
                            "kind": {"enum": sorted(UNCERTAINTY_KINDS)},
                            "origin": {"type": "string", "minLength": 1},
                        },
                    },
                },
            },
            "observation": {
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
                    "evidence_class": {"enum": [item.value for item in EvidenceClass]},
                    "channels": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"$ref": "#/$defs/observation_channel"},
                    },
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "sample_id": {"anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}]},
                    "sample_lineage": {"type": "array", "items": {"type": "string"}},
                },
            },
            "constraint": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "constraint_id",
                    "phase",
                    "passed",
                    "outcome",
                    "measured_value",
                    "margin",
                    "limit",
                    "verifier",
                ],
                "properties": {
                    "constraint_id": {"type": "string", "minLength": 1},
                    "phase": {"enum": ["pre_action", "runtime", "post_action"]},
                    "passed": {"type": "boolean"},
                    "outcome": {"enum": ["passed", "violated", "unavailable"]},
                    "measured_value": scalar_schema,
                    "margin": {"anyOf": [{"type": "number"}, {"type": "null"}]},
                    "limit": {"type": "object"},
                    "verifier": {"type": "string", "minLength": 1},
                },
            },
            "evidence": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "evidence_id",
                    "uri",
                    "sha256",
                    "media_type",
                    "role",
                    "provider_id",
                    "evidence_class",
                ],
                "properties": {
                    "evidence_id": {"type": "string", "minLength": 1},
                    "uri": {"type": "string", "minLength": 1},
                    "sha256": hash_schema,
                    "media_type": {"type": "string", "minLength": 1},
                    "role": {"type": "string", "minLength": 1},
                    "provider_id": {"type": "string", "minLength": 1},
                    "evidence_class": {"enum": [item.value for item in EvidenceClass]},
                },
            },
        },
    }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _finite_nonnegative(value: float, name: str) -> None:
    if not math.isfinite(value) or value < 0:
        raise CampaignValidationError(f"{name} must be finite and non-negative")


def _strict_mapping(value: Mapping[str, Any], allowed: set[str], name: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise CampaignValidationError(f"unknown {name} fields: {sorted(unknown)}")


def _require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CampaignValidationError(f"{name} must be an object")
    return value


def _require_list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise CampaignValidationError(f"{name} must be an array")
    return value


def _require_string(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise CampaignValidationError(f"{name} must be a string")
    return value


def _require_integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CampaignValidationError(f"{name} must be an integer")
    return value


def _require_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CampaignValidationError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise CampaignValidationError(f"{name} must be finite")
    return result


def _require_boolean(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise CampaignValidationError(f"{name} must be a boolean")
    return value


def _read_json_object(path: Path, name: str) -> Mapping[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CampaignValidationError(f"compiled pack is missing {name}: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CampaignValidationError(f"compiled pack has invalid {name}: {exc}") from exc
    return _require_mapping(raw, name)


@dataclass(frozen=True)
class EvidenceReference:
    evidence_id: str
    uri: str
    sha256: str
    media_type: str
    role: str
    provider_id: str
    evidence_class: EvidenceClass

    def __post_init__(self) -> None:
        if not self.evidence_id or not self.uri or not self.role or not self.provider_id:
            raise CampaignValidationError("evidence id, uri, role, and provider are required")
        if not _is_sha256(self.sha256):
            raise CampaignValidationError("evidence sha256 must be 64 lowercase hex characters")

    def to_dict(self) -> dict[str, str]:
        return {
            "evidence_id": self.evidence_id,
            "uri": self.uri,
            "sha256": self.sha256,
            "media_type": self.media_type,
            "role": self.role,
            "provider_id": self.provider_id,
            "evidence_class": self.evidence_class.value,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> EvidenceReference:
        keys = {
            "evidence_id",
            "uri",
            "sha256",
            "media_type",
            "role",
            "provider_id",
            "evidence_class",
        }
        _strict_mapping(value, keys, "evidence")
        if set(value) != keys:
            raise CampaignValidationError("evidence record is incomplete")
        return cls(
            evidence_id=_require_string(value["evidence_id"], "evidence.evidence_id"),
            uri=_require_string(value["uri"], "evidence.uri"),
            sha256=_require_string(value["sha256"], "evidence.sha256"),
            media_type=_require_string(value["media_type"], "evidence.media_type"),
            role=_require_string(value["role"], "evidence.role"),
            provider_id=_require_string(value["provider_id"], "evidence.provider_id"),
            evidence_class=EvidenceClass(
                _require_string(value["evidence_class"], "evidence.evidence_class")
            ),
        )


@dataclass(frozen=True)
class ActionRequest:
    action_id: str
    kind: str
    actor_id: str
    provider_id: str
    evidence_class: EvidenceClass
    parameters: Mapping[str, Any] = field(default_factory=dict)
    sample_id: str | None = None
    sample_lineage: list[str] = field(default_factory=list)
    station_id: str | None = None

    def __post_init__(self) -> None:
        if not self.action_id or not self.actor_id or not self.provider_id:
            raise CampaignValidationError("action id, actor id, and provider id are required")
        _validate_action_kind(self.kind, "action.kind")
        if self.sample_id is not None and not self.sample_id:
            raise CampaignValidationError("action sample_id must not be empty when present")
        if any(not item for item in self.sample_lineage):
            raise CampaignValidationError("action sample_lineage entries must not be empty")
        if self.station_id is not None and not self.station_id:
            raise CampaignValidationError("action station_id must not be empty when present")
        canonical_json(self.parameters)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "kind": self.kind,
            "actor_id": self.actor_id,
            "provider_id": self.provider_id,
            "evidence_class": self.evidence_class.value,
            "parameters": dict(self.parameters),
            "sample_id": self.sample_id,
            "sample_lineage": list(self.sample_lineage),
            "station_id": self.station_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ActionRequest:
        required_keys = {
            "action_id",
            "kind",
            "actor_id",
            "provider_id",
            "evidence_class",
            "parameters",
        }
        optional_keys = {"sample_id", "sample_lineage", "station_id"}
        _strict_mapping(value, required_keys | optional_keys, "action")
        if not required_keys.issubset(value) or not isinstance(value["parameters"], Mapping):
            raise CampaignValidationError("action record is incomplete or invalid")
        sample_id_raw = value.get("sample_id")
        if sample_id_raw is not None:
            sample_id_raw = _require_string(sample_id_raw, "action.sample_id")
        sample_lineage_raw = value.get("sample_lineage", [])
        if not isinstance(sample_lineage_raw, list):
            raise CampaignValidationError("action.sample_lineage must be an array")
        station_id_raw = value.get("station_id")
        if station_id_raw is not None:
            station_id_raw = _require_string(station_id_raw, "action.station_id")
        return cls(
            action_id=_require_string(value["action_id"], "action.action_id"),
            kind=_require_string(value["kind"], "action.kind"),
            actor_id=_require_string(value["actor_id"], "action.actor_id"),
            provider_id=_require_string(value["provider_id"], "action.provider_id"),
            evidence_class=EvidenceClass(
                _require_string(value["evidence_class"], "action.evidence_class")
            ),
            parameters=dict(value["parameters"]),
            sample_id=sample_id_raw,
            sample_lineage=[
                _require_string(item, "action.sample_lineage item") for item in sample_lineage_raw
            ],
            station_id=station_id_raw,
        )


UNCERTAINTY_KINDS = {"declared", "propagated", "measured"}


@dataclass(frozen=True)
class ObservationChannel:
    name: str
    value: float | int | str | bool | None
    unit: str
    quality: str
    origin: ObservationOrigin
    provider_id: str
    evidence_class: EvidenceClass
    uncertainty: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.name or not self.unit or not self.quality or not self.provider_id:
            raise CampaignValidationError(
                "observation name, unit, quality, and provider are required"
            )
        if self.quality not in {"valid", "estimated", "degraded", "unavailable"}:
            raise CampaignValidationError("observation quality is invalid")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise CampaignValidationError("numeric observations must be finite")
        if set(self.uncertainty) != {"value", "kind", "origin"}:
            raise CampaignValidationError(
                "observation uncertainty must have exactly value, kind, and origin"
            )
        uncertainty_value = self.uncertainty["value"]
        # An unavailable or unreported measurement's uncertainty is genuinely
        # unknown, not zero -- zero is an exact-measurement claim no provider
        # that reports nothing has actually made. ``None`` is the honest value;
        # when a number is reported instead, it must still be finite and
        # non-negative.
        if uncertainty_value is not None:
            if isinstance(uncertainty_value, bool) or not isinstance(
                uncertainty_value, (int, float)
            ):
                raise CampaignValidationError(
                    "observation uncertainty value must be a number or null"
                )
            if not math.isfinite(float(uncertainty_value)) or float(uncertainty_value) < 0:
                raise CampaignValidationError(
                    "observation uncertainty value must be finite and non-negative"
                )
        if self.uncertainty["kind"] not in UNCERTAINTY_KINDS:
            raise CampaignValidationError("observation uncertainty kind is invalid")
        if not self.uncertainty["origin"]:
            raise CampaignValidationError("observation uncertainty origin is required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "quality": self.quality,
            "origin": self.origin.value,
            "provider_id": self.provider_id,
            "evidence_class": self.evidence_class.value,
            "uncertainty": dict(self.uncertainty),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ObservationChannel:
        keys = {
            "name",
            "value",
            "unit",
            "quality",
            "origin",
            "provider_id",
            "evidence_class",
            "uncertainty",
        }
        _strict_mapping(value, keys, "observation channel")
        if set(value) != keys:
            raise CampaignValidationError("observation channel is incomplete")
        raw = value["value"]
        if raw is not None and not isinstance(raw, (float, int, str, bool)):
            raise CampaignValidationError("observation value has an unsupported type")
        return cls(
            name=_require_string(value["name"], "observation channel.name"),
            value=raw,
            unit=_require_string(value["unit"], "observation channel.unit"),
            quality=_require_string(value["quality"], "observation channel.quality"),
            origin=ObservationOrigin(
                _require_string(value["origin"], "observation channel.origin")
            ),
            provider_id=_require_string(value["provider_id"], "observation channel.provider_id"),
            evidence_class=EvidenceClass(
                _require_string(value["evidence_class"], "observation channel.evidence_class")
            ),
            uncertainty=dict(
                _require_mapping(value["uncertainty"], "observation channel.uncertainty")
            ),
        )


@dataclass(frozen=True)
class ObservationFrame:
    frame_id: str
    logical_time_s: float
    provider_id: str
    evidence_class: EvidenceClass
    channels: tuple[ObservationChannel, ...]
    evidence_ids: tuple[str, ...] = ()
    sample_id: str | None = None
    sample_lineage: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.frame_id or not self.provider_id or not self.channels:
            raise CampaignValidationError(
                "observation frame id, provider id, and channels are required"
            )
        _finite_nonnegative(self.logical_time_s, "observation logical_time_s")
        names = [channel.name for channel in self.channels]
        if len(names) != len(set(names)):
            raise CampaignValidationError("observation channel names must be unique")
        if any(channel.provider_id != self.provider_id for channel in self.channels):
            raise CampaignValidationError("observation channels must belong to the frame provider")
        if any(channel.evidence_class is not self.evidence_class for channel in self.channels):
            raise CampaignValidationError("observation channels must use the frame evidence class")
        if self.sample_id is not None and not self.sample_id:
            raise CampaignValidationError("observation sample_id must not be empty when present")
        if any(not item for item in self.sample_lineage):
            raise CampaignValidationError("observation sample_lineage entries must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "logical_time_s": self.logical_time_s,
            "provider_id": self.provider_id,
            "evidence_class": self.evidence_class.value,
            "channels": [channel.to_dict() for channel in self.channels],
            "evidence_ids": list(self.evidence_ids),
            "sample_id": self.sample_id,
            "sample_lineage": list(self.sample_lineage),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ObservationFrame:
        required_keys = {
            "frame_id",
            "logical_time_s",
            "provider_id",
            "evidence_class",
            "channels",
            "evidence_ids",
        }
        optional_keys = {"sample_id", "sample_lineage"}
        _strict_mapping(value, required_keys | optional_keys, "observation")
        if not required_keys.issubset(value) or not isinstance(value["channels"], list):
            raise CampaignValidationError("observation frame is incomplete or invalid")
        if not isinstance(value["evidence_ids"], list):
            raise CampaignValidationError("observation evidence_ids must be an array")
        sample_id_raw = value.get("sample_id")
        if sample_id_raw is not None:
            sample_id_raw = _require_string(sample_id_raw, "observation.sample_id")
        sample_lineage_raw = value.get("sample_lineage", [])
        if not isinstance(sample_lineage_raw, list):
            raise CampaignValidationError("observation.sample_lineage must be an array")
        return cls(
            frame_id=_require_string(value["frame_id"], "observation.frame_id"),
            logical_time_s=_require_number(value["logical_time_s"], "observation.logical_time_s"),
            provider_id=_require_string(value["provider_id"], "observation.provider_id"),
            evidence_class=EvidenceClass(
                _require_string(value["evidence_class"], "observation.evidence_class")
            ),
            channels=tuple(
                ObservationChannel.from_dict(_require_mapping(item, "observation channel"))
                for item in value["channels"]
            ),
            evidence_ids=tuple(
                _require_string(item, "observation.evidence_ids item")
                for item in value["evidence_ids"]
            ),
            sample_id=sample_id_raw,
            sample_lineage=[
                _require_string(item, "observation.sample_lineage item")
                for item in sample_lineage_raw
            ],
        )


CONSTRAINT_OUTCOMES = {"passed", "violated", "unavailable"}


@dataclass(frozen=True)
class ConstraintEvaluation:
    constraint_id: str
    phase: str
    passed: bool
    outcome: str
    measured_value: float | int | str | bool | None
    limit: Mapping[str, Any]
    verifier: str
    margin: float | None = None

    def __post_init__(self) -> None:
        if self.phase not in {"pre_action", "runtime", "post_action"}:
            raise CampaignValidationError("constraint phase is invalid")
        if not self.constraint_id or not self.verifier:
            raise CampaignValidationError("constraint id and verifier are required")
        if self.outcome not in CONSTRAINT_OUTCOMES:
            raise CampaignValidationError("constraint outcome is invalid")
        if self.passed is not (self.outcome == "passed"):
            raise CampaignValidationError("constraint passed flag disagrees with its outcome")
        if self.margin is not None:
            if isinstance(self.margin, bool) or not isinstance(self.margin, (int, float)):
                raise CampaignValidationError("constraint margin must be a number or null")
            if not math.isfinite(float(self.margin)):
                raise CampaignValidationError("constraint margin must be finite")

    def to_dict(self) -> dict[str, Any]:
        return {
            "constraint_id": self.constraint_id,
            "phase": self.phase,
            "passed": self.passed,
            "outcome": self.outcome,
            "measured_value": self.measured_value,
            "margin": self.margin,
            "limit": dict(self.limit),
            "verifier": self.verifier,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ConstraintEvaluation:
        keys = {
            "constraint_id",
            "phase",
            "passed",
            "outcome",
            "measured_value",
            "margin",
            "limit",
            "verifier",
        }
        _strict_mapping(value, keys, "constraint")
        if set(value) != keys or not isinstance(value["limit"], Mapping):
            raise CampaignValidationError("constraint evaluation is incomplete or invalid")
        margin_raw = value["margin"]
        if margin_raw is not None:
            margin_raw = _require_number(margin_raw, "constraint.margin")
        return cls(
            constraint_id=_require_string(value["constraint_id"], "constraint.constraint_id"),
            phase=_require_string(value["phase"], "constraint.phase"),
            passed=_require_boolean(value["passed"], "constraint.passed"),
            outcome=_require_string(value["outcome"], "constraint.outcome"),
            measured_value=value["measured_value"],
            margin=margin_raw,
            limit=dict(value["limit"]),
            verifier=_require_string(value["verifier"], "constraint.verifier"),
        )


@dataclass(frozen=True)
class CampaignIdentity:
    campaign_id: str
    run_id: str
    seed: int
    backend_revision: str
    ir_hash: str
    world_hash: str
    campaign_hash: str
    provenance: Mapping[str, Any]
    source_trace_sha256: str | None = None

    def __post_init__(self) -> None:
        if not self.campaign_id or not self.run_id or self.seed < 0 or not self.backend_revision:
            raise CampaignValidationError("campaign identity is incomplete")
        for name in ("ir_hash", "world_hash", "campaign_hash"):
            value = getattr(self, name)
            if not _is_sha256(value):
                raise CampaignValidationError(f"{name} must be a SHA-256 value")
        if self.source_trace_sha256 is not None and not _is_sha256(self.source_trace_sha256):
            raise CampaignValidationError("source_trace_sha256 must be a SHA-256 value")


@dataclass(frozen=True)
class TraceEvent:
    event_type: str
    event_id: str
    campaign_id: str
    run_id: str
    sequence: int
    logical_time_s: float
    mode: RunMode
    seed: int
    backend_revision: str
    ir_hash: str
    world_hash: str
    campaign_hash: str
    provenance: Mapping[str, Any]
    source_trace_sha256: str | None = None
    action: ActionRequest | None = None
    observation: ObservationFrame | None = None
    constraints: tuple[ConstraintEvaluation, ...] = ()
    evidence: tuple[EvidenceReference, ...] = ()
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise CampaignValidationError("unsupported campaign schema version")
        if self.event_type not in {"campaign_start", "action", "observation", "campaign_end"}:
            raise CampaignValidationError("unsupported event type")
        if not self.event_id or not self.run_id:
            raise CampaignValidationError("event_id and run_id are required")
        if self.sequence < 0 or self.seed < 0:
            raise CampaignValidationError("sequence and seed must be non-negative")
        _finite_nonnegative(self.logical_time_s, "event logical_time_s")
        if self.mode is RunMode.REPLAY and not self.source_trace_sha256:
            raise CampaignValidationError("replay events require source_trace_sha256")
        if self.source_trace_sha256 is not None and not _is_sha256(self.source_trace_sha256):
            raise CampaignValidationError("source_trace_sha256 must be a SHA-256 value")
        for name in ("ir_hash", "world_hash", "campaign_hash"):
            if not _is_sha256(getattr(self, name)):
                raise CampaignValidationError(f"{name} must be a SHA-256 value")
        if self.event_type == "action" and self.action is None:
            raise CampaignValidationError("action event requires an action")
        if self.event_type == "observation" and self.observation is None:
            raise CampaignValidationError("observation event requires an observation")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_type": self.event_type,
            "event_id": self.event_id,
            "campaign_id": self.campaign_id,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "logical_time_s": self.logical_time_s,
            "mode": self.mode.value,
            "seed": self.seed,
            "backend_revision": self.backend_revision,
            "ir_hash": self.ir_hash,
            "world_hash": self.world_hash,
            "campaign_hash": self.campaign_hash,
            "provenance": dict(self.provenance),
            "source_trace_sha256": self.source_trace_sha256,
            "action": self.action.to_dict() if self.action else None,
            "observation": self.observation.to_dict() if self.observation else None,
            "constraints": [item.to_dict() for item in self.constraints],
            "evidence": [item.to_dict() for item in self.evidence],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TraceEvent:
        _strict_mapping(value, EVENT_KEYS, "trace event")
        missing = EVENT_KEYS - set(value)
        if missing:
            raise CampaignValidationError(f"missing trace event fields: {sorted(missing)}")
        if not isinstance(value["provenance"], Mapping):
            raise CampaignValidationError("provenance must be an object")
        if not isinstance(value["constraints"], list) or not isinstance(value["evidence"], list):
            raise CampaignValidationError("constraints and evidence must be arrays")
        action_raw = value["action"]
        observation_raw = value["observation"]
        if action_raw is not None:
            action_raw = _require_mapping(action_raw, "trace event.action")
        if observation_raw is not None:
            observation_raw = _require_mapping(observation_raw, "trace event.observation")
        source_trace_raw = value["source_trace_sha256"]
        if source_trace_raw is not None:
            source_trace_raw = _require_string(source_trace_raw, "trace event.source_trace_sha256")
        return cls(
            schema_version=_require_string(value["schema_version"], "trace event.schema_version"),
            event_type=_require_string(value["event_type"], "trace event.event_type"),
            event_id=_require_string(value["event_id"], "trace event.event_id"),
            campaign_id=_require_string(value["campaign_id"], "trace event.campaign_id"),
            run_id=_require_string(value["run_id"], "trace event.run_id"),
            sequence=_require_integer(value["sequence"], "trace event.sequence"),
            logical_time_s=_require_number(value["logical_time_s"], "trace event.logical_time_s"),
            mode=RunMode(_require_string(value["mode"], "trace event.mode")),
            seed=_require_integer(value["seed"], "trace event.seed"),
            backend_revision=_require_string(
                value["backend_revision"], "trace event.backend_revision"
            ),
            ir_hash=_require_string(value["ir_hash"], "trace event.ir_hash"),
            world_hash=_require_string(value["world_hash"], "trace event.world_hash"),
            campaign_hash=_require_string(value["campaign_hash"], "trace event.campaign_hash"),
            provenance=dict(value["provenance"]),
            source_trace_sha256=source_trace_raw,
            action=ActionRequest.from_dict(action_raw) if action_raw is not None else None,
            observation=(
                ObservationFrame.from_dict(observation_raw) if observation_raw is not None else None
            ),
            constraints=tuple(
                ConstraintEvaluation.from_dict(_require_mapping(item, "constraint"))
                for item in value["constraints"]
            ),
            evidence=tuple(
                EvidenceReference.from_dict(_require_mapping(item, "evidence"))
                for item in value["evidence"]
            ),
        )


def _validated_restore(events: Sequence[TraceEvent]) -> tuple[tuple[Sample, ...], list[str]]:
    start = events[0]
    restore_raw, binding_raw = (
        start.provenance.get("restore"),
        start.provenance.get("restore_binding_sha256"),
    )
    if restore_raw is None and binding_raw is None:
        if any(
            "restore" in event.provenance or "restore_binding_sha256" in event.provenance
            for event in events[1:]
        ):
            raise CampaignValidationError("restore metadata is allowed only on a restored trace")
        return (), []
    if restore_raw is None or binding_raw is None:
        raise CampaignValidationError("restored trace requires restore metadata and binding")
    if start.mode is not RunMode.SIMULATE or not start.source_trace_sha256:
        raise CampaignValidationError("restore metadata is valid only on a bound simulate trace")
    restore = _require_mapping(restore_raw, "restore")
    _strict_mapping(restore, RESTORE_KEYS, "restore")
    if set(restore) != RESTORE_KEYS:
        raise CampaignValidationError("restore metadata is incomplete")
    binding = _require_string(binding_raw, "restore binding")
    if not _is_sha256(binding) or stable_hash(restore) != binding:
        raise CampaignValidationError("restore binding differs from restore metadata")
    if any(event.provenance.get("restore_binding_sha256") != binding for event in events):
        raise CampaignValidationError("restore binding changed within the trace")
    if any("restore" in event.provenance for event in events[1:]):
        raise CampaignValidationError("full restore metadata is allowed only on campaign_start")
    hashes = (
        "source_prefix_sha256",
        "source_world_sha256",
        "source_registry_sha256",
        "source_facility_sha256",
        "restored_state_sha256",
    )
    for name in hashes:
        value = _require_string(restore[name], f"restore.{name}")
        if not _is_sha256(value):
            raise CampaignValidationError(f"restore.{name} must be a SHA-256 value")
    _require_string(restore["at_event_id"], "restore.at_event_id")
    logical_time = _require_number(restore["source_logical_time_s"], "restore logical time")
    _finite_nonnegative(logical_time, "restore logical time")
    raw_samples = _require_list(restore["initial_samples"], "restore initial samples")
    try:
        samples = tuple(Sample.model_validate(item) for item in raw_samples)
    except (TypeError, ValueError) as exc:
        raise CampaignValidationError(f"restore initial samples are invalid: {exc}") from exc
    ids = [sample.id for sample in samples]
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise CampaignValidationError("restore initial samples must be unique and sorted by ID")
    canonical_samples = [sample.model_dump(mode="json") for sample in samples]
    if canonical_samples != raw_samples:
        raise CampaignValidationError("restore initial samples are not complete canonical objects")
    if stable_hash(canonical_samples) != restore["restored_state_sha256"]:
        raise CampaignValidationError("restored state hash differs from initial samples")
    raw_classes = _require_list(restore["source_evidence_classes"], "source evidence classes")
    source_classes = [
        EvidenceClass(_require_string(item, "source evidence class")).value for item in raw_classes
    ]
    if source_classes != sorted(set(source_classes)) or "physical" in source_classes:
        raise CampaignValidationError("restore source evidence classes are invalid")
    compiled_pack = _require_mapping(start.provenance.get("compiled_pack"), "compiled pack")
    if (
        compiled_pack.get("target") != "openusd"
        or start.provenance.get("embodied_backend")
        or start.provenance.get("embodied_evidence_bound")
        or any(
            (event.action is not None and event.action.evidence_class is EvidenceClass.PHYSICAL)
            or (
                event.observation is not None
                and event.observation.evidence_class is EvidenceClass.PHYSICAL
            )
            or any(item.evidence_class is EvidenceClass.PHYSICAL for item in event.evidence)
            for event in events
        )
    ):
        raise CampaignValidationError("restored child trace must remain virtual and non-embodied")
    digest = stable_hash(
        {
            "composition_sha256": compiled_pack.get("composition_sha256"),
            "steps": start.provenance.get("declared_step_ids"),
            "seed": start.seed,
            "restore_binding_sha256": binding,
            "source_trace_sha256": start.source_trace_sha256,
        }
    )
    if (
        start.campaign_hash != digest
        or start.campaign_id != f"composed-{digest[:12]}"
        or start.run_id != f"simulate-{digest[12:24]}"
    ):
        raise CampaignValidationError("child campaign identity omits or mismatches restore binding")
    return samples, source_classes


def validate_events(events: Sequence[TraceEvent]) -> dict[str, Any]:
    if not events:
        raise CampaignValidationError("campaign trace is empty")
    if events[0].event_type != "campaign_start" or events[-1].event_type != "campaign_end":
        raise CampaignValidationError("campaign trace requires start and end events")
    identity = (
        events[0].campaign_id,
        events[0].run_id,
        events[0].seed,
        events[0].backend_revision,
        events[0].ir_hash,
        events[0].world_hash,
        events[0].campaign_hash,
        events[0].mode,
        events[0].source_trace_sha256,
    )
    last_time = -1.0
    action_count = 0
    observation_count = 0
    event_ids: set[str] = set()
    actions: list[ActionRequest] = []
    failed_constraint = False
    observation_channels_by_action: dict[str, tuple[ObservationChannel, ...]] = {}
    for expected_sequence, event in enumerate(events):
        if event.sequence != expected_sequence:
            raise CampaignValidationError("trace sequence must be contiguous from zero")
        if event.logical_time_s < last_time:
            raise CampaignValidationError("trace logical time must be monotonic")
        last_time = event.logical_time_s
        if event.event_id in event_ids:
            raise CampaignValidationError("trace event_id values must be unique")
        event_ids.add(event.event_id)
        for evaluation in event.constraints:
            limit = _require_mapping(
                evaluation.limit, f"constraint limit {evaluation.constraint_id}"
            )
            operator = _require_string(
                limit.get("operator"), f"constraint operator {evaluation.constraint_id}"
            )
            expected_outcome = _constraint_outcome(
                operator,
                limit.get("bound"),
                evaluation.measured_value,
            )
            if evaluation.outcome != expected_outcome:
                raise CampaignValidationError(
                    f"constraint result differs from measured value: {evaluation.constraint_id}"
                )
        if (
            event.campaign_id,
            event.run_id,
            event.seed,
            event.backend_revision,
            event.ir_hash,
            event.world_hash,
            event.campaign_hash,
            event.mode,
            event.source_trace_sha256,
        ) != identity:
            raise CampaignValidationError("trace identity fields changed within a campaign")
        if event.event_type == "action":
            if event.observation is not None or event.evidence:
                raise CampaignValidationError(
                    "action events cannot carry observations or evidence records"
                )
            action_count += 1
            if event.action is None:
                raise CampaignValidationError("action event has no action")
            actions.append(event.action)
            failed_constraint = failed_constraint or any(
                not item.passed for item in event.constraints
            )
            if expected_sequence + 1 >= len(events) or events[expected_sequence + 1].event_type != (
                "observation"
            ):
                raise CampaignValidationError("each action must be followed by an observation")
        if event.event_type == "observation":
            if event.action is not None:
                raise CampaignValidationError("observation events cannot carry action records")
            observation_count += 1
            previous = events[expected_sequence - 1].action if expected_sequence else None
            if previous is None or event.observation is None:
                raise CampaignValidationError("observation has no preceding action")
            observation_channels_by_action[previous.action_id] = event.observation.channels
            if event.observation.provider_id != previous.provider_id:
                raise CampaignValidationError("observation provider differs from its action")
            if event.observation.evidence_class is not previous.evidence_class:
                raise CampaignValidationError("observation evidence class differs from its action")
            available_evidence = {item.evidence_id for item in event.evidence}
            if event.observation and not set(event.observation.evidence_ids).issubset(
                available_evidence
            ):
                raise CampaignValidationError("observation has an unresolved evidence reference")
            failed_constraint = failed_constraint or any(
                not item.passed for item in event.constraints
            )
        if event.event_type in {"campaign_start", "campaign_end"} and (
            event.action is not None or event.observation is not None or event.constraints
        ):
            raise CampaignValidationError(
                f"{event.event_type} cannot carry action, observation, or constraint records"
            )
    if not 1 <= action_count <= 32:
        raise CampaignValidationError("campaign must contain 1 to 32 action requests")
    provider_contract = events[0].provenance.get("provider_contract")
    if provider_contract is not None:
        providers = _require_mapping(provider_contract, "provider contract")
        for action in actions:
            expected_provider = _require_mapping(
                providers.get(action.action_id), f"provider contract for {action.action_id}"
            )
            if (
                expected_provider.get("provider_id") != action.provider_id
                or expected_provider.get("evidence_class") != action.evidence_class.value
            ):
                raise CampaignValidationError(
                    f"action provider differs from admitted binding: {action.action_id}"
                )

    constraint_contract = events[0].provenance.get("constraint_contract")
    constrained_trace = any(event.constraints for event in events)
    if constrained_trace and constraint_contract is None:
        raise CampaignValidationError(
            "a constrained trace requires its declaration and applicability contract"
        )
    if constraint_contract is not None:
        contract = _require_mapping(constraint_contract, "constraint contract")
        contract_hash = _require_string(
            events[0].provenance.get("constraint_contract_sha256"),
            "constraint contract hash",
        )
        if stable_hash(contract) != contract_hash:
            raise CampaignValidationError("constraint contract hash differs from its content")
        for event in events:
            if (
                event.provenance.get("constraint_contract") != contract
                or event.provenance.get("constraint_contract_sha256") != contract_hash
            ):
                raise CampaignValidationError("constraint contract changed within the trace")
        applicability = _require_mapping(contract.get("applicability"), "constraint applicability")
        declarations = _require_mapping(contract.get("declarations"), "constraint declarations")
        for action_event in (event for event in events if event.event_type == "action"):
            action = action_event.action
            assert action is not None
            expected = _require_mapping(
                applicability.get(action.action_id),
                f"constraint applicability for {action.action_id}",
            )
            observation_event = events[action_event.sequence + 1]
            for phase, evaluations in (
                ("pre_action", action_event.constraints),
                ("observation", observation_event.constraints),
            ):
                expected_ids = {
                    _require_string(item, f"constraint applicability {phase} item")
                    for item in _require_list(
                        expected.get(phase), f"constraint applicability {phase}"
                    )
                }
                actual_ids = [item.constraint_id for item in evaluations]
                if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != expected_ids:
                    raise CampaignValidationError(
                        f"trace constraint coverage differs for {action.action_id} {phase}"
                    )
                if not expected_ids.issubset(declarations):
                    raise CampaignValidationError(
                        "constraint applicability refers to undeclared IDs"
                    )
                for evaluation in evaluations:
                    declaration = _require_mapping(
                        declarations[evaluation.constraint_id],
                        f"constraint declaration {evaluation.constraint_id}",
                    )
                    if evaluation.verifier != declaration.get("verifier_binding_id"):
                        raise CampaignValidationError(
                            f"constraint verifier differs from declaration: "
                            f"{evaluation.constraint_id}"
                        )
                    if evaluation.phase != declaration.get("phase"):
                        raise CampaignValidationError(
                            f"constraint phase differs from declaration: {evaluation.constraint_id}"
                        )
                    expected_limit = {
                        "operator": declaration.get("operator"),
                        "bound": declaration.get("bound"),
                        "unit": declaration.get("unit"),
                        "enforcement": declaration.get("enforcement"),
                    }
                    if dict(evaluation.limit) != expected_limit:
                        raise CampaignValidationError(
                            f"constraint limit differs from declaration: {evaluation.constraint_id}"
                        )
                    expected_outcome = _constraint_outcome(
                        _require_string(
                            declaration.get("operator"), "constraint declaration operator"
                        ),
                        declaration.get("bound"),
                        evaluation.measured_value,
                    )
                    if evaluation.outcome != expected_outcome:
                        raise CampaignValidationError(
                            f"constraint result differs from measured value: "
                            f"{evaluation.constraint_id}"
                        )
    # The compiled runtime's own copy of this contract (compiled_runtime.py's
    # validate_trace) already refuses a missing/unrecognized terminal status;
    # this is the one public validator every trace passes through (a local run's
    # own self-check, `dynamical validate`, and replay's read of a raw backend
    # trace), so it enforces the identical rule rather than defaulting a missing
    # status to a false "completed" success.
    terminal_status = events[-1].provenance.get("execution_status")
    if terminal_status not in {"passed", "failed"}:
        raise CampaignValidationError("campaign_end needs an explicit passed or failed status")
    if failed_constraint and terminal_status != "failed":
        raise CampaignValidationError("a failed constraint needs failed campaign status")
    execution_status = terminal_status
    reasons: list[dict[str, object]] = [
        dict(item) for item in (events[-1].provenance.get("reasons") or [])
    ]
    declared_steps = {str(step) for step in (events[0].provenance.get("declared_step_ids") or [])}
    covered_steps = {event.action.action_id for event in events if event.action is not None}
    missing = sorted(declared_steps - covered_steps)
    if missing:
        reasons.append(
            {
                "code": "STEP_COVERAGE_INCOMPLETE",
                "detail": f"composition declares steps not present in the trace: {missing}",
                "step_id": None,
                "channel_id": None,
                "recoverable": False,
            }
        )
        execution_status = "failed"
    # The declared proof block itself must be tamper-evident before its
    # contents are worth checking: every event carries proof_contract_sha256,
    # the hash of exactly the campaign_start-declared step ids and proof
    # requirements. A trace that drops, edits, or never carried that binding
    # fails here -- required proof outputs come from the compiled campaign,
    # and a trace cannot remove or redefine them. (Pack-anchored validation in
    # the compiled pack's validate_trace additionally requires the block to
    # equal the verified pack's own record.)
    expected_proof_contract = stable_hash(
        {
            "declared_step_ids": events[0].provenance.get("declared_step_ids") or [],
            "proof_requirements": events[0].provenance.get("proof_requirements") or [],
        }
    )
    unbound = [
        event.sequence
        for event in events
        if event.provenance.get("proof_contract_sha256") != expected_proof_contract
    ]
    if unbound:
        reasons.append(
            {
                "code": "PROOF_CONTRACT_MISMATCH",
                "detail": (
                    "the trace's declared proof requirements are not bound by a matching "
                    f"proof_contract_sha256 on every event (first unbound sequence: {unbound[0]}); "
                    "a trace cannot remove or redefine its required proof outputs"
                ),
                "step_id": None,
                "channel_id": None,
                "recoverable": False,
            }
        )
        execution_status = "failed"
    # A required proof output's own runtime completion is not proof completion:
    # a backend can finish every action and pass every constraint while a proof
    # requirement's declared output_port_ids stay unavailable (an embodied
    # backend with no domain model for the operation, for example). The proof
    # requirements are declared on the requirement and carried into the
    # composition (VirtualSDL.proof_requirements) and from there into every
    # compiled campaign's provenance -- checked here, once, for every backend
    # and every replay, rather than trusted from each backend's own terminal
    # status claim.
    for requirement in events[0].provenance.get("proof_requirements") or []:
        if not isinstance(requirement, Mapping):
            continue
        output_port_ids = [str(item) for item in (requirement.get("output_port_ids") or [])]
        # channel_ids is each output_port_id resolved to the concrete observation
        # channel name this backend actually reports it on (see
        # _resolved_proof_requirements in campaign.py and _runtime_pack.py) --
        # a different, device-namespaced vocabulary for an embodied backend, so
        # availability is checked against channel_ids, never output_port_ids
        # directly.
        raw_channel_ids = requirement.get("channel_ids")
        channel_ids = (
            [str(item) for item in raw_channel_ids] if raw_channel_ids else output_port_ids
        )
        for action_id in (str(item) for item in (requirement.get("action_ids") or [])):
            channels = observation_channels_by_action.get(action_id, ())
            available = {
                channel.name
                for channel in channels
                if channel.quality != "unavailable" and channel.value is not None
            }
            unmet = sorted(
                (port_id, channel_id)
                for port_id, channel_id in zip(output_port_ids, channel_ids, strict=True)
                if channel_id not in available
            )
            if unmet:
                unmet_ports = [port_id for port_id, _ in unmet]
                reasons.append(
                    {
                        "code": "PROOF_OUTPUT_UNAVAILABLE",
                        "detail": (
                            f"proof requirement {requirement.get('id')!r} needs {unmet_ports} "
                            f"from {action_id!r}, which the trace reports as unavailable"
                        ),
                        "step_id": action_id,
                        "channel_id": unmet[0][1],
                        "recoverable": False,
                    }
                )
    initial_samples, source_evidence_classes = _validated_restore(events)
    reasons.extend(item.model_dump() for item in check_invariants(events, initial_samples))
    if reasons:
        execution_status = "failed"
    result = {
        "valid": execution_status != "failed",
        "execution_status": execution_status,
        "validation_reasons": reasons,
        "schema_version": SCHEMA_VERSION,
        "mode": events[0].mode.value,
        "event_count": len(events),
        "action_count": action_count,
        "observation_count": observation_count,
        "campaign_id": events[0].campaign_id,
        "run_id": events[0].run_id,
        "provider_ids": sorted(
            {
                item.provider_id
                for event in events
                for item in ([event.action] if event.action is not None else [])
            }
            | {evidence.provider_id for event in events for evidence in event.evidence}
        ),
        "evidence_classes": sorted(
            {
                item.evidence_class.value
                for event in events
                for item in ([event.action] if event.action is not None else [])
            }
            | {evidence.evidence_class.value for event in events for evidence in event.evidence}
        ),
        "embodied_evidence_bound": False,
        "claim_boundary": events[0].provenance.get(
            "claim_boundary",
            "Trace validation only; no embodied or physical evidence binding is established.",
        ),
        "authority_anchor": events[0].provenance.get("authority_anchor", "installed_bundle"),
    }
    if source_evidence_classes:
        result["source_evidence_classes"] = source_evidence_classes
    return result


def read_trace(path: str | Path) -> list[TraceEvent]:
    trace_path = Path(path)
    events: list[TraceEvent] = []
    with trace_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CampaignValidationError(f"invalid JSON at line {line_number}: {exc}") from exc
            if not isinstance(raw, Mapping):
                raise CampaignValidationError(f"line {line_number} is not an object")
            events.append(TraceEvent.from_dict(raw))
    validate_events(events)
    return events


def write_trace(path: str | Path, events: Sequence[TraceEvent]) -> str:
    validate_events(events)
    trace_path = Path(path)
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(canonical_json(event.to_dict()) + "\n" for event in events)
    trace_path.write_text(content, encoding="utf-8")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _constraint_outcome(operator: str, bound: Any, measured: Any) -> str:
    """Classify a declared constraint result as passed, violated, or unavailable.

    A missing measurement (``measured is None``, produced when a channel has no
    recorded value) is ``unavailable`` rather than a silent violation: it is a
    different failure mode and must be reportable as such.
    """

    if measured is None:
        return "unavailable"
    if isinstance(measured, bool) or not isinstance(measured, (int, float)):
        return "violated"
    value = float(measured)
    if not math.isfinite(value):
        return "violated"
    if operator == "between":
        bounds = _require_mapping(bound, "constraint bound")
        ok = float(bounds["minimum"]) <= value <= float(bounds["maximum"])
    elif operator == "gt":
        ok = value > float(bound)
    elif operator in {"ge", "gte"}:
        ok = value >= float(bound)
    elif operator == "lt":
        ok = value < float(bound)
    elif operator in {"le", "lte"}:
        ok = value <= float(bound)
    elif operator == "eq":
        ok = value == float(bound)
    else:
        raise CampaignValidationError(f"unsupported declared constraint operator: {operator}")
    return "passed" if ok else "violated"


def _constraint_margin(operator: str, bound: Any, measured: Any) -> float | None:
    """Signed distance from a measured value to its declared bound, in the constraint's unit.

    Positive means the bound is satisfied by that much; zero means exactly at
    the bound; negative means it is violated by that much. ``None`` when the
    measurement is missing or non-numeric -- the same case ``_constraint_outcome``
    reports as ``unavailable``, not a fabricated distance.
    """

    if measured is None or isinstance(measured, bool) or not isinstance(measured, (int, float)):
        return None
    value = float(measured)
    if not math.isfinite(value):
        return None
    if operator == "between":
        bounds = _require_mapping(bound, "constraint bound")
        minimum = float(bounds["minimum"])
        maximum = float(bounds["maximum"])
        return min(value - minimum, maximum - value)
    if operator == "gt":
        return value - float(bound)
    if operator in {"ge", "gte"}:
        return value - float(bound)
    if operator == "lt":
        return float(bound) - value
    if operator in {"le", "lte"}:
        return float(bound) - value
    if operator == "eq":
        return -abs(value - float(bound))
    raise CampaignValidationError(f"unsupported declared constraint operator: {operator}")


def evaluate_declared_constraints(
    declared_constraints: Mapping[str, Mapping[str, Any]],
    constraint_ids: Iterable[str],
    measured_channels: Mapping[str, tuple[Any, str]],
) -> tuple[ConstraintEvaluation, ...]:
    """Evaluate only declared constraints against unit-checked channel values."""

    checks: list[ConstraintEvaluation] = []
    for constraint_id in constraint_ids:
        if constraint_id not in declared_constraints:
            raise CampaignValidationError(
                f"constraint is not declared by the active contract: {constraint_id}"
            )
        constraint = declared_constraints[constraint_id]
        channel_id = _require_string(constraint.get("channel_id"), "constraint.channel_id")
        expected_unit = _require_string(constraint.get("unit"), "constraint.unit")
        if channel_id not in measured_channels:
            measured_value: Any = None
            measured_unit = expected_unit
        else:
            measured_value, measured_unit = measured_channels[channel_id]
        if measured_unit != expected_unit:
            raise CampaignValidationError(
                f"constraint unit mismatch for {constraint_id}: {measured_unit} != {expected_unit}"
            )
        operator = _require_string(constraint.get("operator"), "constraint.operator")
        bound = constraint.get("bound")
        outcome = _constraint_outcome(operator, bound, measured_value)
        margin = _constraint_margin(operator, bound, measured_value)
        checks.append(
            ConstraintEvaluation(
                constraint_id=constraint_id,
                phase=_require_string(constraint.get("phase"), "constraint.phase"),
                passed=outcome == "passed",
                outcome=outcome,
                measured_value=measured_value,
                margin=margin,
                limit={
                    "operator": operator,
                    "bound": bound,
                    "unit": expected_unit,
                    "enforcement": constraint.get("enforcement"),
                },
                verifier=_require_string(
                    constraint.get("verifier_binding_id"), "constraint.verifier_binding_id"
                ),
            )
        )
    return tuple(checks)


def evaluate_action_constraints(
    action: ActionRequest,
    *,
    declared_constraints: Mapping[str, Mapping[str, Any]] | None = None,
    constraint_ids: Iterable[str] = (),
    measured_channels: Mapping[str, tuple[Any, str]] | None = None,
) -> tuple[ConstraintEvaluation, ...]:
    """Evaluate the active declared pre-action contract; no hidden checks are added."""

    if declared_constraints is None:
        raise CampaignValidationError("action constraint evaluation requires declarations")
    return evaluate_declared_constraints(
        declared_constraints,
        constraint_ids,
        measured_channels or {},
    )


def post_action_constraints(
    observation: ObservationFrame,
    *,
    declared_constraints: Mapping[str, Mapping[str, Any]],
    constraint_ids: Iterable[str],
) -> tuple[ConstraintEvaluation, ...]:
    values = {channel.name: (channel.value, channel.unit) for channel in observation.channels}
    return evaluate_declared_constraints(declared_constraints, constraint_ids, values)


@dataclass(frozen=True)
class CompiledCampaignContract:
    """Validated campaign surface from one compiled facility pack."""

    target: str
    manifest_sha256: str
    core_ir_sha256: str
    world_sha256: str
    adapter_pack_sha256: str
    facility_ir_sha256: str
    action_schema_sha256: str
    observation_schema_sha256: str
    action_kinds: frozenset[str]
    observation_channels: frozenset[str]
    channel_units: Mapping[str, str]
    capability_by_action: Mapping[str, Mapping[str, Any]]
    constraint_by_id: Mapping[str, Mapping[str, Any]]
    composition_sha256: str | None = None
    operation_bindings: tuple[Mapping[str, Any], ...] = ()
    # Keyed by facility model_binding id, which is exactly the composition's
    # endpoint_id/action.actor_id when that endpoint is a Python instrument model
    # (see FacilityProviderBinding.endpoint_id / CapabilityProvider.endpoint_id).
    # Not every endpoint has one -- a device- or agent-backed provider with no
    # model_binding entry has nothing declared to verify against.
    model_binding_by_id: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    # The objective's proof requirements, each resolved to the concrete compiled
    # action_ids that embody its declared operation_id (see composition.py's
    # VirtualSDL.proof_requirements). Empty when the compiled pack was produced
    # without a composition_result.json (e.g. directly from a facility document).
    proof_requirements: tuple[Mapping[str, Any], ...] = ()

    def provenance_binding(self) -> dict[str, Any]:
        result = {
            "target": self.target,
            "manifest_sha256": self.manifest_sha256,
            "core_ir_sha256": self.core_ir_sha256,
            "world_sha256": self.world_sha256,
            "adapter_pack_sha256": self.adapter_pack_sha256,
            "facility_ir_sha256": self.facility_ir_sha256,
            "action_schema_sha256": self.action_schema_sha256,
            "observation_schema_sha256": self.observation_schema_sha256,
            "declared_action_kinds": sorted(self.action_kinds),
            "declared_observation_channels": sorted(self.observation_channels),
            "capability_by_action": {
                kind: {
                    "capability_id": capability["id"],
                    "provider_id": capability["provider_id"],
                }
                for kind, capability in sorted(self.capability_by_action.items())
            },
            "constraint_by_id": {
                constraint_id: {
                    "phase": constraint["phase"],
                    "channel_id": constraint["channel_id"],
                    "unit": constraint["unit"],
                    "verifier_binding_id": constraint["verifier_binding_id"],
                }
                for constraint_id, constraint in sorted(self.constraint_by_id.items())
            },
        }
        if self.composition_sha256 is not None:
            result["composition_sha256"] = self.composition_sha256
            result["operation_bindings"] = [
                {
                    "step_id": binding["step_id"],
                    "operation_id": binding["operation_id"],
                    "provider_id": binding["provider_id"],
                    "evidence_class": binding["evidence_class"],
                }
                for binding in self.operation_bindings
            ]
        return result


REQUIRED_CAMPAIGN_PACK_FILES = {
    "action_schema.json",
    "compile_manifest.json",
    "core_ir.json",
    "facility_ir.json",
    "observation_schema.json",
}


def _manifest_artifact_hashes(manifest: Mapping[str, Any]) -> dict[str, str]:
    artifacts = _require_list(manifest.get("artifacts"), "compile manifest.artifacts")
    result: dict[str, str] = {}
    for index, raw_artifact in enumerate(artifacts):
        artifact = _require_mapping(raw_artifact, f"compile manifest.artifacts[{index}]")
        if set(artifact) != {"path", "sha256"}:
            raise CampaignValidationError("compiled artifact records require path and sha256")
        relative = _require_string(artifact["path"], "compiled artifact.path")
        digest = _require_string(artifact["sha256"], "compiled artifact.sha256")
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts or relative in result:
            raise CampaignValidationError(f"unsafe or duplicate compiled artifact path: {relative}")
        if not _is_sha256(digest):
            raise CampaignValidationError(f"invalid compiled artifact hash: {relative}")
        result[relative] = digest
    return result


def _facility_contract(
    facility_ir: Mapping[str, Any],
) -> tuple[
    dict[str, Mapping[str, Any]],
    set[str],
    dict[str, str],
    dict[str, Mapping[str, Any]],
]:
    capabilities: dict[str, Mapping[str, Any]] = {}
    provider_ids: set[str] = set()
    channels: set[str] = set()
    channel_units: dict[str, str] = {}
    constraints: dict[str, Mapping[str, Any]] = {}
    for group in ("devices", "agents"):
        for raw_provider in _require_list(facility_ir.get(group), f"facility IR.{group}"):
            provider = _require_mapping(raw_provider, f"facility IR.{group} item")
            provider_ids.add(_require_string(provider.get("id"), f"facility IR.{group}.id"))
            for raw_channel in _require_list(
                provider.get("state_channels"), f"facility IR.{group}.state_channels"
            ):
                channel = _require_mapping(raw_channel, "facility IR state channel")
                channel_id = _require_string(channel.get("id"), "state channel.id")
                unit = _require_string(channel.get("unit"), "state channel.unit")
                channels.add(channel_id)
                channel_units[channel_id] = unit
    for raw_material in _require_list(
        facility_ir.get("material_states"), "facility IR.material_states"
    ):
        material = _require_mapping(raw_material, "facility IR material state")
        for raw_channel in _require_list(
            material.get("initial_channels"), "facility IR material.initial_channels"
        ):
            channel = _require_mapping(raw_channel, "facility IR material channel")
            channel_id = _require_string(channel.get("channel_id"), "material channel.channel_id")
            unit = _require_string(channel.get("unit"), "material channel.unit")
            channels.add(channel_id)
            channel_units[channel_id] = unit
    for raw_capability in _require_list(
        facility_ir.get("capabilities"), "facility IR.capabilities"
    ):
        capability = _require_mapping(raw_capability, "facility IR capability")
        action_type = _require_string(
            capability.get("action_type"), "facility capability.action_type"
        )
        kind = _validate_action_kind(action_type, "facility capability.action_type")
        if kind in capabilities:
            raise CampaignValidationError(f"reference action kind is ambiguous: {kind}")
        provider_id = _require_string(
            capability.get("provider_id"), "facility capability.provider_id"
        )
        if provider_id not in provider_ids:
            raise CampaignValidationError(
                f"facility capability has unknown provider: {provider_id}"
            )
        _require_string(capability.get("id"), "facility capability.id")
        _require_list(capability.get("parameters"), "facility capability.parameters")
        capabilities[kind] = capability
    for raw_constraint in _require_list(facility_ir.get("constraints"), "facility IR.constraints"):
        constraint = _require_mapping(raw_constraint, "facility IR constraint")
        constraint_id = _require_string(constraint.get("id"), "facility constraint.id")
        if constraint_id in constraints:
            raise CampaignValidationError(f"duplicate facility constraint: {constraint_id}")
        channel_id = _require_string(constraint.get("channel_id"), "facility constraint.channel_id")
        unit = _require_string(constraint.get("unit"), "facility constraint.unit")
        if channel_id not in channels:
            raise CampaignValidationError(
                f"facility constraint refers to an unknown channel: {channel_id}"
            )
        if channel_units[channel_id] != unit:
            raise CampaignValidationError(
                f"facility constraint unit mismatch for {channel_id}: "
                f"{unit} != {channel_units[channel_id]}"
            )
        constraints[constraint_id] = constraint
    for capability in capabilities.values():
        for field_name, expected_phases in (
            ("precondition_constraint_ids", {"pre_action"}),
            ("postcondition_constraint_ids", {"runtime", "post_action"}),
        ):
            references = _require_list(capability.get(field_name), f"capability.{field_name}")
            for reference in references:
                constraint_id = _require_string(reference, f"capability.{field_name} item")
                if constraint_id not in constraints:
                    raise CampaignValidationError(
                        f"capability refers to an unknown constraint: {constraint_id}"
                    )
                if constraints[constraint_id].get("phase") not in expected_phases:
                    raise CampaignValidationError(
                        f"capability constraint has an invalid phase: {constraint_id}"
                    )
    return capabilities, channels, channel_units, constraints


def _model_binding_index(facility_ir: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Map each facility model binding id to its declared implementation identity."""

    index: dict[str, Mapping[str, Any]] = {}
    for raw_model in _require_list(facility_ir.get("model_bindings"), "facility IR.model_bindings"):
        model = _require_mapping(raw_model, "facility IR model binding")
        model_id = _require_string(model.get("id"), "facility model binding.id")
        index[model_id] = model
    return index


def _resolved_proof_requirements(
    proof_requirements: Sequence[Any],
    operation_bindings: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    """Bind each proof requirement's declared ``operation_id`` to this campaign's own action_ids.

    Resolved once, here, from the same ``operation_bindings`` this composition
    already selected -- an operation_id can map to more than one compiled
    action (a retried or repeated step), so every matching action_id is kept,
    not just the first. This is the local composed runtime's own trace, whose
    observation channels are named directly from the registry Capability's own
    output_ports (see ``run_composed_campaign``'s channel construction), so
    ``channel_ids`` is ``output_port_ids`` verbatim -- unlike an embodied
    backend's compiled campaign (``_runtime_pack.py``'s
    ``_resolved_proof_requirements``), which must bridge to a separate,
    device-namespaced channel vocabulary.
    """

    action_ids_by_operation: dict[str, list[str]] = {}
    for binding in operation_bindings:
        operation_id = str(binding.get("operation_id") or "")
        step_id = str(binding.get("step_id") or "")
        if operation_id and step_id:
            action_ids_by_operation.setdefault(operation_id, []).append(step_id)
    resolved: list[Mapping[str, Any]] = []
    for item in proof_requirements:
        payload = item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
        operation_id = str(payload.get("operation_id") or "")
        output_port_ids = [str(x) for x in (payload.get("output_port_ids") or [])]
        resolved.append(
            {
                "id": str(payload.get("id") or ""),
                "operation_id": operation_id,
                "output_port_ids": output_port_ids,
                "channel_ids": output_port_ids,
                "action_ids": sorted(action_ids_by_operation.get(operation_id, [])),
            }
        )
    return tuple(resolved)


def load_compiled_campaign_contract(path: str | Path) -> CompiledCampaignContract:
    """Load a complete compiled pack and fail closed on any invalid receipt or contract."""

    destination = Path(path)
    if not destination.exists():
        raise CampaignValidationError(f"compiled world path is absent: {destination}")
    if not destination.is_dir():
        raise CampaignValidationError(
            f"simulate input must be a compiled world directory: {destination}"
        )
    missing = sorted(
        name for name in REQUIRED_CAMPAIGN_PACK_FILES if not (destination / name).is_file()
    )
    if missing:
        raise CampaignValidationError(f"compiled pack is missing required files: {missing}")

    from .compiler import validate_compiled_world

    try:
        validation = validate_compiled_world(destination)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CampaignValidationError(f"compiled pack validation failed: {exc}") from exc
    if not validation.get("valid"):
        raise CampaignValidationError(
            f"compiled pack validation failed: {validation.get('failures', [])}"
        )
    if validation.get("execution_status") != "ready":
        raise CampaignValidationError(
            "compiled world is validation-only; compose a campaign before running it"
        )
    if validation.get("authority_anchor") != "installed_bundle":
        raise CampaignValidationError("compiled world is outside the installed authority bundle")

    manifest_path = destination / "compile_manifest.json"
    manifest = _read_json_object(manifest_path, "compile_manifest.json")
    facility_ir = _read_json_object(destination / "facility_ir.json", "facility_ir.json")
    core_ir = _read_json_object(destination / "core_ir.json", "core_ir.json")
    action_schema = _read_json_object(destination / "action_schema.json", "action_schema.json")
    observation_schema = _read_json_object(
        destination / "observation_schema.json", "observation_schema.json"
    )
    artifact_hashes = _manifest_artifact_hashes(manifest)
    if manifest.get("schema_version") != "dynamical.compile-manifest.v1":
        raise CampaignValidationError("compiled manifest schema version is unsupported")
    artifact_count = _require_integer(
        manifest.get("artifact_count"), "compile manifest.artifact_count"
    )
    if artifact_count != len(artifact_hashes):
        raise CampaignValidationError("compiled manifest artifact count is invalid")
    declared_artifacts = set(artifact_hashes) | {"compile_manifest.json"}
    if not REQUIRED_CAMPAIGN_PACK_FILES.issubset(declared_artifacts):
        raise CampaignValidationError(
            "required campaign files are not declared by the compile manifest"
        )
    for name, document in (("facility IR", facility_ir), ("core IR", core_ir)):
        if (
            document.get("document_type") != "dynamical.facility"
            or document.get("schema_version") != "0.1.0"
        ):
            raise CampaignValidationError(f"{name} identity is invalid")
    core_projection = dict(facility_ir)
    core_projection.pop("adapter_bindings", None)
    if core_projection != core_ir:
        raise CampaignValidationError("facility_ir.json does not project to core_ir.json")

    (
        capability_by_action,
        facility_channels,
        channel_units,
        constraint_by_id,
    ) = _facility_contract(facility_ir)
    model_binding_by_id = _model_binding_index(facility_ir)
    raw_declared_actions = _require_list(
        action_schema.get("x-dynamical-declared-capability-action-types"),
        "action schema declared action types",
    )
    declared_actions = {
        _validate_action_kind(_require_string(item, "declared action type"), "declared action type")
        for item in raw_declared_actions
    }
    if len(raw_declared_actions) != len(declared_actions):
        raise CampaignValidationError("action schema has duplicate action declarations")
    if declared_actions != set(capability_by_action):
        raise CampaignValidationError(
            "action schema declarations differ from facility capabilities"
        )
    action_properties = _require_mapping(
        action_schema.get("properties"), "action schema.properties"
    )
    kind_schema = _require_mapping(action_properties.get("kind"), "action schema.kind")
    schema_action_enum = {
        _validate_action_kind(_require_string(item, "action schema kind"), "action schema kind")
        for item in _require_list(kind_schema.get("enum"), "action schema kind.enum")
    }
    if not declared_actions.issubset(schema_action_enum):
        raise CampaignValidationError("declared facility actions are absent from action schema")

    raw_declared_channels = _require_list(
        observation_schema.get("x-dynamical-declared-channel-ids"),
        "observation schema declared channels",
    )
    declared_channels = {
        _require_string(item, "declared observation channel") for item in raw_declared_channels
    }
    if len(raw_declared_channels) != len(declared_channels):
        raise CampaignValidationError("observation schema has duplicate channel declarations")
    if declared_channels != facility_channels:
        raise CampaignValidationError(
            "observation schema declarations differ from facility state channels"
        )

    target = _require_string(manifest.get("target"), "compile manifest.target")
    if target not in {"isaac", "openusd"}:
        raise CampaignValidationError(f"compile manifest target is unsupported: {target}")
    core_hash = _require_string(manifest.get("core_ir_sha256"), "compile manifest.core hash")
    world_hash = _require_string(manifest.get("world_sha256"), "compile manifest.world hash")
    adapter_hash = _require_string(
        manifest.get("adapter_pack_sha256"), "compile manifest.adapter hash"
    )
    for name, digest in (
        ("core IR", core_hash),
        ("world", world_hash),
        ("adapter pack", adapter_hash),
    ):
        if not _is_sha256(digest):
            raise CampaignValidationError(f"compile manifest {name} hash is invalid")

    composition_sha256: str | None = None
    operation_bindings: tuple[Mapping[str, Any], ...] = ()
    proof_requirements: tuple[Mapping[str, Any], ...] = ()
    composition_path = destination / "composition_result.json"
    if composition_path.is_file():
        from .composition import validate_composition_result

        raw_composition = _read_json_object(composition_path, "composition_result.json")
        composition = validate_composition_result(raw_composition)
        if composition.status != "COMPILED" or composition.virtual_sdl is None:
            raise CampaignValidationError("campaign runner requires a COMPILED composition")
        composition_sha256 = composition.composition_sha256
        operation_bindings = tuple(
            binding.model_dump(mode="json", exclude_none=True)
            for binding in composition.virtual_sdl.operation_bindings
        )
        proof_requirements = _resolved_proof_requirements(
            composition.virtual_sdl.proof_requirements, operation_bindings
        )
    return CompiledCampaignContract(
        target=target,
        manifest_sha256=file_sha256(manifest_path),
        core_ir_sha256=core_hash,
        world_sha256=world_hash,
        adapter_pack_sha256=adapter_hash,
        facility_ir_sha256=artifact_hashes["facility_ir.json"],
        action_schema_sha256=artifact_hashes["action_schema.json"],
        observation_schema_sha256=artifact_hashes["observation_schema.json"],
        action_kinds=frozenset(declared_actions),
        observation_channels=frozenset(declared_channels),
        channel_units=channel_units,
        capability_by_action=capability_by_action,
        constraint_by_id=constraint_by_id,
        composition_sha256=composition_sha256,
        operation_bindings=operation_bindings,
        model_binding_by_id=model_binding_by_id,
        proof_requirements=proof_requirements,
    )


def _validate_capability_parameters(action: ActionRequest, capability: Mapping[str, Any]) -> None:
    specs: dict[str, Mapping[str, Any]] = {}
    for raw_spec in _require_list(capability.get("parameters"), "capability.parameters"):
        spec = _require_mapping(raw_spec, "capability parameter")
        name = _require_string(spec.get("name"), "capability parameter.name")
        specs[name] = spec
    unknown = set(action.parameters) - set(specs)
    if unknown:
        raise CampaignValidationError(
            f"action {action.action_id} has parameters outside the compiled capability: "
            f"{sorted(unknown)}"
        )
    missing = {
        name
        for name, spec in specs.items()
        if spec.get("required") is True and name not in action.parameters
    }
    if missing:
        raise CampaignValidationError(
            f"action {action.action_id} is missing compiled capability parameters: "
            f"{sorted(missing)}"
        )
    for name, value in action.parameters.items():
        spec = specs[name]
        value_type = _require_string(spec.get("value_type"), "capability parameter.value_type")
        if value_type == "boolean":
            _require_boolean(value, f"action parameter {name}")
        elif value_type in {"number", "duration"}:
            number = _require_number(value, f"action parameter {name}")
            if spec.get("minimum") is not None and number < float(spec["minimum"]):
                raise CampaignValidationError(f"action parameter {name} is below its minimum")
            if spec.get("maximum") is not None and number > float(spec["maximum"]):
                raise CampaignValidationError(f"action parameter {name} is above its maximum")
        elif value_type in {"string", "asset_id"}:
            text_value = _require_string(value, f"action parameter {name}")
            if spec.get("enum") is not None and text_value not in spec["enum"]:
                raise CampaignValidationError(f"action parameter {name} is outside its enum")
        else:
            raise CampaignValidationError(
                f"compiled capability has unsupported parameter type: {value_type}"
            )


def _event(
    identity: CampaignIdentity,
    sequence: int,
    logical_time_s: float,
    event_type: str,
    *,
    provenance: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> TraceEvent:
    return TraceEvent(
        event_type=event_type,
        event_id=f"{identity.run_id}:event:{sequence:06d}",
        campaign_id=identity.campaign_id,
        run_id=identity.run_id,
        sequence=sequence,
        logical_time_s=logical_time_s,
        mode=RunMode.SIMULATE,
        seed=identity.seed,
        backend_revision=identity.backend_revision,
        ir_hash=identity.ir_hash,
        world_hash=identity.world_hash,
        campaign_hash=identity.campaign_hash,
        provenance=provenance if provenance is not None else identity.provenance,
        source_trace_sha256=identity.source_trace_sha256,
        **kwargs,
    )


def _composed_actions(contract: CompiledCampaignContract) -> tuple[ActionRequest, ...]:
    actions: list[ActionRequest] = []
    for binding in contract.operation_bindings:
        operation_id = _require_string(
            binding.get("operation_id"), "operation binding.operation_id"
        )
        sample_id_raw = binding.get("sample_id")
        station_id_raw = binding.get("selected_facility_id")
        action = ActionRequest(
            action_id=_require_string(binding.get("step_id"), "operation binding.step_id"),
            kind=operation_id,
            actor_id=_require_string(binding.get("endpoint_id"), "operation binding.endpoint_id"),
            provider_id=_require_string(
                binding.get("provider_id"), "operation binding.provider_id"
            ),
            evidence_class=EvidenceClass(
                _require_string(binding.get("evidence_class"), "operation binding.evidence_class")
            ),
            parameters={
                _require_string(item.get("name"), "operation parameter.name"): item.get("value")
                for raw_item in _require_list(
                    binding.get("parameters"), "operation binding.parameters"
                )
                for item in [_require_mapping(raw_item, "operation parameter")]
            },
            sample_id=(
                _require_string(sample_id_raw, "operation binding.sample_id")
                if sample_id_raw is not None
                else None
            ),
            # The *workstation* this step actually executes at -- not
            # actor_id/endpoint_id, which names the acting instrument's own
            # endpoint (e.g. a registered simulator model id) and lives in a
            # different vocabulary that a Sample's station_id never matches.
            # Composition already resolves exactly one facility per step
            # (selected_facility_id); dynamical.samples.check_invariants
            # compares like with like using this field, not actor_id.
            station_id=(
                _require_string(station_id_raw, "operation binding.selected_facility_id")
                if station_id_raw is not None
                else None
            ),
        )
        capability = _require_mapping(
            binding.get("capability_contract"), "operation binding.capability_contract"
        )
        _validate_capability_parameters(action, capability)
        actions.append(action)
    return tuple(actions)


def _resolved_step_inputs(
    binding: Mapping[str, Any],
    outputs: Mapping[tuple[str, str], Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    values: dict[str, Any] = {}
    units: dict[str, str] = {}
    for raw_input in _require_list(binding.get("inputs"), "operation binding.inputs"):
        item = _require_mapping(raw_input, "operation input")
        target_port_id = _require_string(
            item.get("target_port_id"), "operation input.target_port_id"
        )
        units[target_port_id] = _require_string(
            item.get("target_unit"), "operation input.target_unit"
        )
        source_kind = _require_string(item.get("source_kind"), "operation input.source_kind")
        if source_kind == "campaign_input":
            if "value" not in item:
                raise CampaignValidationError(
                    f"campaign input has no executable value: {target_port_id}"
                )
            values[target_port_id] = item["value"]
            continue
        if source_kind != "step_output":
            raise CampaignValidationError(f"unsupported operation input source: {source_kind}")
        key = (
            _require_string(item.get("source_id"), "operation input.source_id"),
            _require_string(item.get("source_port_id"), "operation input.source_port_id"),
        )
        if key not in outputs:
            raise CampaignValidationError(
                f"operation input refers to an unavailable prior output: {key[0]}.{key[1]}"
            )
        values[target_port_id] = outputs[key]
    return values, units


def _composed_constraint_ids(
    binding: Mapping[str, Any],
    contract: CompiledCampaignContract,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    policy = _require_mapping(binding.get("policy"), "operation binding.policy")
    pre_action: list[str] = []
    observation: list[str] = []
    for raw_constraint_id in _require_list(
        policy.get("safety_limit_ids"), "operation binding.policy.safety_limit_ids"
    ):
        constraint_id = _require_string(raw_constraint_id, "provider safety limit")
        declaration = contract.constraint_by_id.get(constraint_id)
        if declaration is None:
            raise CampaignValidationError(
                f"selected provider refers to an unknown safety limit: {constraint_id}"
            )
        phase = declaration.get("phase")
        if phase == "pre_action":
            pre_action.append(constraint_id)
        elif phase in {"runtime", "post_action"}:
            observation.append(constraint_id)
        else:
            raise CampaignValidationError(
                f"selected provider safety limit has an invalid phase: {constraint_id}"
            )
    return tuple(sorted(pre_action)), tuple(sorted(observation))


def _dataflow_edges(contract: CompiledCampaignContract) -> list[dict[str, str]]:
    """Serialize step-to-step wiring from each binding's declared inputs.

    This is exactly the ``(step_id, port_id)`` lookup ``_resolved_step_inputs``
    performs against the runtime's ``prior_outputs`` dict -- computed here
    from the same declared source (``operation_bindings[*].inputs``) so the
    causal graph survives into the trace instead of dying with that
    in-process dict at exit.
    """

    edges: list[dict[str, str]] = []
    for binding in contract.operation_bindings:
        to_step = _require_string(binding.get("step_id"), "operation binding.step_id")
        for raw_input in _require_list(binding.get("inputs"), "operation binding.inputs"):
            item = _require_mapping(raw_input, "operation input")
            if item.get("source_kind") != "step_output":
                continue
            edges.append(
                {
                    "from_step": _require_string(
                        item.get("source_id"), "operation input.source_id"
                    ),
                    "from_port": _require_string(
                        item.get("source_port_id"), "operation input.source_port_id"
                    ),
                    "to_step": to_step,
                    "to_port": _require_string(
                        item.get("target_port_id"), "operation input.target_port_id"
                    ),
                }
            )
    return edges


def _capability_parameter_units(
    capability: Mapping[str, Any], *, numeric_only: bool
) -> dict[str, str]:
    units: dict[str, str] = {}
    for raw_spec in _require_list(capability.get("parameters"), "capability.parameters"):
        spec = _require_mapping(raw_spec, "capability parameter")
        name = _require_string(spec.get("name"), "capability parameter.name")
        unit = spec.get("unit")
        if not isinstance(unit, str) or not unit:
            continue
        if numeric_only and spec.get("value_type") not in {"number", "duration"}:
            continue
        units[name] = unit
    return units


def _envelope_in_force(capability: Mapping[str, Any]) -> dict[str, Any]:
    """Each declared parameter's admitted bounds for this action.

    Makes unexplored operating space legible in the trace itself, so an
    agent can reason about counterfactuals rather than rediscover bounds by
    hitting them.
    """

    envelope: dict[str, Any] = {}
    for raw_spec in _require_list(capability.get("parameters"), "capability.parameters"):
        spec = _require_mapping(raw_spec, "capability parameter")
        name = _require_string(spec.get("name"), "capability parameter.name")
        envelope[name] = {
            "unit": spec.get("unit"),
            "minimum": spec.get("minimum"),
            "maximum": spec.get("maximum"),
        }
    return envelope


def _parameter_channel_values(
    action: ActionRequest,
    capability: Mapping[str, Any],
    constraint_ids: Iterable[str],
    contract: CompiledCampaignContract,
) -> dict[str, tuple[Any, str]]:
    """Generic parameter -> channel mapping for pre-action constraint evaluation.

    Replaces the heater-era special case that injected
    ``action.parameters['target-temperature']`` as a hardcoded
    ``heater.target_temperature_K`` channel, and the later unit-matching
    inference that replaced it: matching a constraint to "the one action
    parameter that shares its declared unit" silently picks the wrong
    parameter the moment an operation declares two parameters in the same
    unit (e.g. two durations). The declared contract is the source of truth
    instead -- each constraint names the exact parameter it constrains via
    its own ``constrained_parameter_name`` (schema.py's ``Constraint``). A
    constraint that declares no such name, names one this operation does not
    have, or whose named parameter this action did not supply, is left out,
    so ``evaluate_declared_constraints`` reports it honestly as
    ``MEASUREMENT_UNAVAILABLE`` rather than guessing.
    """

    capability_parameter_names = {
        _require_string(item.get("name"), "capability parameter.name")
        for raw_item in _require_list(capability.get("parameters"), "capability.parameters")
        for item in [_require_mapping(raw_item, "capability parameter")]
    }
    measured: dict[str, tuple[Any, str]] = {}
    for constraint_id in constraint_ids:
        declaration = contract.constraint_by_id.get(constraint_id)
        if declaration is None:
            continue
        channel_id = declaration.get("channel_id")
        unit = declaration.get("unit")
        name = declaration.get("constrained_parameter_name")
        if (
            not isinstance(channel_id, str)
            or not isinstance(unit, str)
            or not isinstance(name, str)
        ):
            continue
        if name not in capability_parameter_names:
            continue
        if name in action.parameters:
            measured[channel_id] = (action.parameters[name], unit)
    return measured


def _applied_parameter_values(
    capability: Mapping[str, Any],
    output_ports: Mapping[str, str],
    result: Mapping[str, Any],
    instrument_result: InstrumentResult,
) -> dict[str, Any]:
    """Best-effort applied value per numeric declared parameter.

    Derived from the instrument's own declared output units, not a
    per-operation name list: a numeric parameter's applied value is the
    instrument's own output when exactly one declared output port shares
    that parameter's unit and carries a genuine numeric reading. Clamping,
    like the OT-2 pump's quantized time step, shows up here as requested !=
    applied. When a unit collision leaves more than one numeric candidate,
    the one carrying a declared measurement uncertainty is preferred -- an
    echoed request typically has none. Anything still ambiguous, or with no
    matching output at all, is left equal to the requested value: an honest
    "the instrument did not report a distinct applied value", not an
    invented one.
    """

    param_units = _capability_parameter_units(capability, numeric_only=True)
    applied: dict[str, Any] = {}
    for name, unit in param_units.items():
        candidates = [
            port_id
            for port_id, port_unit in output_ports.items()
            if port_unit == unit
            and isinstance(result.get(port_id), (int, float))
            and not isinstance(result.get(port_id), bool)
        ]
        if len(candidates) > 1:
            with_uncertainty = [pid for pid in candidates if pid in instrument_result.uncertainty]
            if len(with_uncertainty) == 1:
                candidates = with_uncertainty
        if len(candidates) == 1:
            applied[name] = result[candidates[0]]
    return applied


def _channel_uncertainty(
    name: str,
    *,
    evidence_class: EvidenceClass,
    provider_id: str,
    instrument_result: InstrumentResult | None,
) -> dict[str, Any]:
    """Typed uncertainty for one observation channel.

    Driven by the instrument model's own declared ``uncertainty`` dict
    (keyed by output port name), not a naming convention: every model in
    ``dynamical.instruments`` reports uncertainty it declares as an
    engineering assumption, so a channel it covers is ``declared`` (or
    ``measured`` for a physical provider). A channel with no reported figure
    -- including when the step never executed because a precondition
    constraint failed -- has genuinely unknown uncertainty, not zero: zero
    claims an exact measurement no provider that reports nothing has
    actually made. ``value`` is left ``None`` with an origin saying so.
    """

    kind = "measured" if evidence_class is EvidenceClass.PHYSICAL else "declared"
    if instrument_result is not None and name in instrument_result.uncertainty:
        return {
            "value": float(instrument_result.uncertainty[name]),
            "kind": kind,
            "origin": f"{provider_id} instrument model",
        }
    return {
        "value": None,
        "kind": kind,
        "origin": f"{provider_id} reports no uncertainty for {name}",
    }


def _composed_identity(
    contract: CompiledCampaignContract,
    actions: Sequence[ActionRequest],
    seed: int,
    restore_binding_sha256: str | None = None,
    source_trace_sha256: str | None = None,
) -> CampaignIdentity:
    bindings_by_step = {
        _require_string(binding.get("step_id"), "operation binding.step_id"): binding
        for binding in contract.operation_bindings
    }
    constraint_contract = {
        "declarations": {
            key: dict(value) for key, value in sorted(contract.constraint_by_id.items())
        },
        "applicability": {
            action.action_id: {
                "pre_action": list(
                    _composed_constraint_ids(bindings_by_step[action.action_id], contract)[0]
                ),
                "observation": list(
                    _composed_constraint_ids(bindings_by_step[action.action_id], contract)[1]
                ),
            }
            for action in actions
        },
    }
    campaign_spec = {
        "composition_sha256": contract.composition_sha256,
        "steps": [action.action_id for action in actions],
        "seed": seed,
    }
    if restore_binding_sha256 is not None:
        campaign_spec["restore_binding_sha256"] = restore_binding_sha256
        campaign_spec["source_trace_sha256"] = source_trace_sha256
    digest = stable_hash(campaign_spec)
    restore_provenance = (
        {"restore_binding_sha256": restore_binding_sha256}
        if restore_binding_sha256 is not None
        else {}
    )
    return CampaignIdentity(
        campaign_id=f"composed-{digest[:12]}",
        run_id=f"simulate-{digest[12:24]}",
        seed=seed,
        backend_revision=(
            f"compiled_target={contract.target};adapter={contract.adapter_pack_sha256};"
            "composed_virtual_sdl:not_embodied"
        ),
        ir_hash=contract.core_ir_sha256,
        world_hash=contract.world_sha256,
        campaign_hash=digest,
        provenance={
            "runner": "dynamical.campaign.run_composed_campaign",
            "embodied_backend": False,
            "embodied_evidence_bound": False,
            "claim_boundary": (
                "Bounded virtual provider execution only; no physical backend was called."
            ),
            "authority_anchor": "installed_bundle",
            "compiled_contract_bound": True,
            "compiled_pack": contract.provenance_binding(),
            "constraint_contract": constraint_contract,
            "constraint_contract_sha256": stable_hash(constraint_contract),
            # Binds the campaign_start proof block on every event, so removing
            # or rewriting the trace's declared proof requirements is detectable
            # without the compiled pack in hand (the pack-anchored check lives
            # in the pack's own validate_trace).
            "proof_contract_sha256": stable_hash(
                {
                    "declared_step_ids": [action.action_id for action in actions],
                    "proof_requirements": [dict(item) for item in contract.proof_requirements],
                }
            ),
            "provider_contract": {
                action.action_id: {
                    "provider_id": action.provider_id,
                    "evidence_class": action.evidence_class.value,
                }
                for action in actions
            },
            **restore_provenance,
        },
        source_trace_sha256=source_trace_sha256,
    )


def _model_implementation_mismatch(
    contract: CompiledCampaignContract,
    action: ActionRequest,
    model: instruments.InstrumentModel,
) -> RuntimeReason | None:
    """Fail closed when the module that will execute differs from what was declared.

    ``action.actor_id`` is the composition's ``endpoint_id`` -- the same identifier
    a facility's ``model_bindings`` entry is keyed on when that endpoint is a Python
    instrument model. This hashes the bytes backing the already-resolved callable
    -- what Python actually loaded, not a declared path -- so it catches drift
    between a manifest's ``implementation_sha256`` and the installed module however
    the two came to differ. An endpoint with no declared model binding, or one that
    declares no hash, has nothing to verify and is left alone.
    """

    declared = contract.model_binding_by_id.get(action.actor_id)
    if declared is None:
        return None
    declared_hash = declared.get("implementation_sha256")
    if not declared_hash:
        return None
    module = sys.modules.get(getattr(model, "__module__", ""))
    module_file = getattr(module, "__file__", None) if module is not None else None
    if not module_file:
        return RuntimeReason(
            code="MODEL_IMPLEMENTATION_UNRESOLVABLE",
            detail=(
                f"instrument model for endpoint {action.actor_id!r} has no resolvable module "
                "file to verify against its declared implementation_sha256"
            ),
            step_id=action.action_id,
            recoverable=False,
        )
    actual_hash = file_sha256(Path(module_file))
    if actual_hash == declared_hash:
        return None
    return RuntimeReason(
        code="MODEL_IMPLEMENTATION_MISMATCH",
        detail=(
            f"executed module for endpoint {action.actor_id!r} hashes to {actual_hash!r}, "
            f"declared implementation_sha256 is {declared_hash!r}"
        ),
        step_id=action.action_id,
        recoverable=False,
    )


def _execute_composed_campaign(
    contract: CompiledCampaignContract,
    *,
    seed: int,
    initial_samples: Sequence[Sample] = (),
    stop_at_event_id: str | None = None,
    restore: Mapping[str, Any] | None = None,
    event_sink: Any | None = None,
) -> tuple[list[TraceEvent], dict[str, Sample]]:
    """Execute only the ordered providers selected by a compiled composition.

    Each step's operation is dispatched through ``dynamical.instruments.resolve``;
    an operation with no admitted instrument model fails closed rather than
    silently no-op-ing.

    A ``dict[str, Sample]`` ledger is threaded across steps: each action's
    known sample (looked up by ``action.sample_id``, itself read from the
    compiled binding's optional ``sample_id`` key) is passed as
    ``InstrumentRequest.sample``. When a model returns a moved sample on
    ``InstrumentResult.sample``, the resulting ``SampleTransition`` is folded
    into the ledger and embedded on that same action event, so
    ``dynamical.samples.check_invariants`` can enforce lineage across the run.
    """

    actions = _composed_actions(contract)
    if restore is not None and (
        contract.target != "openusd"
        or any(action.evidence_class is EvidenceClass.PHYSICAL for action in actions)
    ):
        raise CampaignValidationError(
            "restored child campaign must remain virtual and non-embodied"
        )
    restore_binding_sha256 = (
        _require_string(restore.get("restore_binding_sha256"), "restore binding")
        if restore is not None
        else None
    )
    source_trace_sha256 = (
        _require_string(restore.get("source_trace_sha256"), "restore source trace hash")
        if restore is not None
        else None
    )
    identity = _composed_identity(
        contract, actions, seed, restore_binding_sha256, source_trace_sha256
    )
    events: list[TraceEvent] = []

    def append(event: TraceEvent) -> None:
        events.append(event)
        if event_sink is not None:
            event_sink(event)

    start_identity = CampaignIdentity(
        campaign_id=identity.campaign_id,
        run_id=identity.run_id,
        seed=identity.seed,
        backend_revision=identity.backend_revision,
        ir_hash=identity.ir_hash,
        world_hash=identity.world_hash,
        campaign_hash=identity.campaign_hash,
        provenance={
            **identity.provenance,
            "declared_step_ids": [action.action_id for action in actions],
            "dataflow_edges": _dataflow_edges(contract),
            "proof_requirements": [dict(item) for item in contract.proof_requirements],
            **({"restore": dict(restore["restore"])} if restore is not None else {}),
        },
        source_trace_sha256=identity.source_trace_sha256,
    )
    append(_event(start_identity, 0, 0.0, "campaign_start"))
    prior_outputs: dict[tuple[str, str], Any] = {}
    samples = {sample.id: sample for sample in initial_samples}
    last_known_station = {sample.id: sample.station_id for sample in initial_samples}
    logical_time = 0.0
    failed = False
    reasons: list[RuntimeReason] = []
    consumed_cost_usd = 0.0
    consumed_duration_s = 0.0

    def constraint_reasons(
        evaluations: Iterable[ConstraintEvaluation], step_id: str
    ) -> list[RuntimeReason]:
        found: list[RuntimeReason] = []
        for evaluation in evaluations:
            declaration = contract.constraint_by_id.get(evaluation.constraint_id, {})
            channel_id = declaration.get("channel_id")
            if evaluation.outcome == "unavailable":
                found.append(
                    RuntimeReason(
                        code="MEASUREMENT_UNAVAILABLE",
                        detail=(
                            f"constraint {evaluation.constraint_id} has no measured value "
                            f"for channel {channel_id}"
                        ),
                        step_id=step_id,
                        channel_id=channel_id,
                        recoverable=True,
                    )
                )
            elif evaluation.outcome == "violated":
                found.append(
                    RuntimeReason(
                        code="CONSTRAINT_VIOLATED",
                        detail=f"constraint {evaluation.constraint_id} failed its declared limit",
                        step_id=step_id,
                        channel_id=channel_id,
                        recoverable=False,
                    )
                )
        return found

    for binding, action in zip(contract.operation_bindings, actions, strict=True):
        capability = _require_mapping(
            binding.get("capability_contract"), "operation binding.capability_contract"
        )
        inputs, input_units = _resolved_step_inputs(binding, prior_outputs)
        measured = {name: (value, input_units[name]) for name, value in inputs.items()}
        pre_ids, post_ids = _composed_constraint_ids(binding, contract)
        measured.update(_parameter_channel_values(action, capability, pre_ids, contract))
        pre_constraints = evaluate_action_constraints(
            action,
            declared_constraints=contract.constraint_by_id,
            constraint_ids=pre_ids,
            measured_channels=measured,
        )
        output_ports = {
            _require_string(port.get("id"), "operation output port.id"): _require_string(
                port.get("unit"), "operation output port.unit"
            )
            for raw_port in _require_list(capability.get("output_ports"), "operation outputs")
            for port in [_require_mapping(raw_port, "operation output port")]
        }
        reasons.extend(constraint_reasons(pre_constraints, action.action_id))
        current_sample = samples.get(action.sample_id) if action.sample_id else None
        instrument_result: InstrumentResult | None = None
        if any(not item.passed for item in pre_constraints):
            result = {name: None for name in output_ports}
            failed = True
        else:
            model = instruments.resolve(action.kind, action.provider_id)
            if model is None:
                raise CampaignValidationError(
                    f"no admitted instrument model for operation {action.kind!r} "
                    f"and provider {action.provider_id!r}"
                )
            mismatch = _model_implementation_mismatch(contract, action, model)
            if mismatch is not None:
                # Fail closed exactly like a failed pre-condition: the declared
                # implementation hash does not bind the module that would actually
                # run, so nothing is executed and no output is produced.
                reasons.append(mismatch)
                result = {name: None for name in output_ports}
                failed = True
            else:
                instrument_result = model(
                    InstrumentRequest(
                        parameters=dict(action.parameters),
                        inputs=dict(inputs),
                        sample=current_sample,
                    )
                )
                result = instrument_result.outputs
                if set(result) != set(output_ports):
                    raise CampaignValidationError(
                        f"operation {action.action_id} outputs differ from its compiled "
                        f"contract: expected={sorted(output_ports)}, found={sorted(result)}"
                    )
                for port_id, value in result.items():
                    prior_outputs[(action.action_id, port_id)] = value
                reasons.extend(instrument_result.reasons)
                consumed_cost_usd += instrument_result.cost_usd
                consumed_duration_s += instrument_result.duration_s
                # A sample-moving model (a transfer, an aliquot, or a consume)
                # returns the sample's new state on InstrumentResult.sample. Fold
                # it into the ledger and embed the resulting SampleTransition on
                # this action, per the convention samples.py enforces: an action
                # that moves a sample carries the transition at
                # action.parameters["sample_transition"], not a bare sample_id.
                if (
                    instrument_result.sample is not None
                    and current_sample is not None
                    and instrument_result.sample.station_id == current_sample.station_id
                    and instrument_result.sample.state != current_sample.state
                ):
                    # A processing model wrote process state onto the sample in
                    # place -- a deposit, not a move. Fold the new state into the
                    # ledger so the next instrument measures this film, but do
                    # not fabricate a SampleTransition: nothing changed custody,
                    # and a transition whose from_station equals its to_station
                    # would be a false custody record.
                    samples = {**samples, instrument_result.sample.id: instrument_result.sample}
                    if action.sample_id is not None and action.station_id is not None:
                        last_known_station[action.sample_id] = action.station_id
                elif instrument_result.sample is not None:
                    moved = instrument_result.sample
                    from_station_hint = str(
                        action.parameters.get("from_station")
                        or (action.sample_id and last_known_station.get(action.sample_id))
                        or action.station_id
                    )
                    transition = build_transition(
                        moved,
                        current_sample=current_sample,
                        from_station_hint=from_station_hint,
                        timestamp_s=logical_time,
                        step_id=action.action_id,
                    )
                    samples = apply_transition(samples, transition)
                    action = replace(
                        action,
                        parameters={
                            **action.parameters,
                            "sample_transition": transition.model_dump(mode="json"),
                        },
                        sample_id=transition.sample_id,
                    )
                    last_known_station[transition.sample_id] = transition.to_station
                elif action.sample_id is not None and action.station_id is not None:
                    # A plain (non-moving) action honestly reporting the sample it
                    # acted on, at the workstation it acted at. Recorded *only* in
                    # this from_station lookup, deliberately kept separate from
                    # the `samples` ledger above: `samples` feeds
                    # InstrumentRequest.sample into the next real instrument
                    # model (transfer_sample copies an existing sample's own
                    # quantity/unit forward when one is passed in), so seeding it
                    # from a plain action with no real measured quantity would
                    # silently corrupt that model's physics. This dict only ever
                    # supplies a cold-start from_station guess for a sample's
                    # first-ever transfer -- e.g. dispense has no upstream
                    # "create sample" operation, so its station is the closest
                    # thing to a declared origin. check_invariants performs the
                    # equivalent origin bookkeeping on its own independent replay
                    # of the trace (dynamical.samples.establish_origin), so what
                    # this runner implies here and what the validator reconstructs
                    # from the written trace agree without this dict being
                    # written into the trace itself.
                    last_known_station[action.sample_id] = action.station_id
        # The trace records both what was commanded and what the instrument
        # actually did with it: declared parameters are widened to
        # {"requested", "applied"} for the emitted event only -- the
        # internal `action` used above for execution (instrument dispatch,
        # the "dwell-time"/"from_station" fallbacks below) stays flat, and
        # the "sample_transition" key Task 8's check_invariants reads is
        # left untouched rather than wrapped.
        applied_values = (
            _applied_parameter_values(capability, output_ports, result, instrument_result)
            if instrument_result is not None
            else {}
        )
        trace_action = replace(
            action,
            parameters={
                name: (
                    value
                    if name == "sample_transition"
                    else {"requested": value, "applied": applied_values.get(name, value)}
                )
                for name, value in action.parameters.items()
            },
        )
        append(
            _event(
                identity,
                len(events),
                logical_time,
                "action",
                action=trace_action,
                constraints=pre_constraints,
                provenance={
                    **identity.provenance,
                    "envelope_in_force": _envelope_in_force(capability),
                },
            )
        )
        duration = action.parameters.get("dwell-time")
        if duration is None:
            duration = _require_mapping(binding.get("duration"), "operation binding.duration").get(
                "typical_s", 0.0
            )
        logical_time += _require_number(duration, "operation duration")
        channels = tuple(
            ObservationChannel(
                name=name,
                value=result[name],
                unit=unit,
                quality="unavailable" if result[name] is None else "estimated",
                origin=ObservationOrigin.SOURCE_MODEL,
                provider_id=action.provider_id,
                evidence_class=action.evidence_class,
                uncertainty=_channel_uncertainty(
                    name,
                    evidence_class=action.evidence_class,
                    provider_id=action.provider_id,
                    instrument_result=instrument_result,
                ),
            )
            for name, unit in sorted(output_ports.items())
        )
        observation = ObservationFrame(
            frame_id=f"frame-after-{action.action_id}",
            logical_time_s=logical_time,
            provider_id=action.provider_id,
            evidence_class=action.evidence_class,
            channels=channels,
        )
        post_constraints = post_action_constraints(
            observation,
            declared_constraints=contract.constraint_by_id,
            constraint_ids=post_ids,
        )
        reasons.extend(constraint_reasons(post_constraints, action.action_id))
        failed = failed or any(not item.passed for item in post_constraints)
        # Scientific-state continuity evidence: the observation records which
        # sample the action touched, the digest of that sample's state after
        # the action, and whether this action wrote state. check_invariants
        # verifies every pure read saw exactly the last written state.
        observed_sample = samples.get(action.sample_id) if action.sample_id else None
        observation_provenance = None
        if action.sample_id is not None and observed_sample is not None:
            observation_provenance = {
                **identity.provenance,
                "sample_id": action.sample_id,
                "sample_state_sha256": state_digest(observed_sample.state),
                "sample_state_written": bool(
                    instrument_result is not None and instrument_result.sample is not None
                ),
            }
        observation_event = _event(
            identity,
            len(events),
            logical_time,
            "observation",
            observation=observation,
            constraints=post_constraints,
            provenance=observation_provenance,
        )
        append(observation_event)
        if stop_at_event_id == observation_event.event_id:
            return events, samples
        if failed:
            break
    if stop_at_event_id is not None:
        raise CampaignValidationError(
            f"source re-execution did not reach observation event: {stop_at_event_id}"
        )
    terminal_identity = CampaignIdentity(
        campaign_id=identity.campaign_id,
        run_id=identity.run_id,
        seed=identity.seed,
        backend_revision=identity.backend_revision,
        ir_hash=identity.ir_hash,
        world_hash=identity.world_hash,
        campaign_hash=identity.campaign_hash,
        provenance={
            **identity.provenance,
            "execution_status": "failed" if failed else "passed",
            "reasons": [item.model_dump(mode="json") for item in reasons],
            "cost_consumed_usd": consumed_cost_usd,
            "duration_consumed_s": consumed_duration_s,
        },
        source_trace_sha256=identity.source_trace_sha256,
    )
    append(_event(terminal_identity, len(events), logical_time, "campaign_end"))
    return events, samples


def run_composed_campaign(
    contract: CompiledCampaignContract,
    output_path: Path,
    *,
    seed: int,
    initial_samples: Sequence[Sample] = (),
    restore: Mapping[str, Any] | None = None,
) -> tuple[list[TraceEvent], str]:
    """Execute a composed campaign and incrementally write its validated trace."""

    if restore is None:
        if initial_samples:
            raise CampaignValidationError("initial samples require restore metadata")
    else:
        restore_metadata = _require_mapping(restore.get("restore"), "restore metadata")
        expected_samples = _require_list(
            restore_metadata.get("initial_samples"), "restore initial samples"
        )
        canonical_samples = [sample.model_dump(mode="json") for sample in initial_samples]
        if canonical_samples != expected_samples:
            raise CampaignValidationError(
                "executed initial samples differ from bound restore metadata"
            )

    stream: Any | None = None

    def write_event(event: TraceEvent) -> None:
        nonlocal stream
        if stream is None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                stream = output_path.open("x" if restore is not None else "w", encoding="utf-8")
            except FileExistsError as exc:
                raise CampaignValidationError(
                    f"restore output appeared after preflight; select a new path: {output_path}"
                ) from exc
        stream.write(canonical_json(event.to_dict()) + "\n")
        stream.flush()

    try:
        events, _ = _execute_composed_campaign(
            contract,
            seed=seed,
            initial_samples=initial_samples,
            restore=restore,
            event_sink=write_event,
        )
    finally:
        if stream is not None:
            stream.close()
    validate_events(events)
    return events, file_sha256(output_path)


def validate_path(path: str | Path) -> dict[str, Any]:
    candidate = Path(path)
    if candidate.suffix == ".ndjson":
        events = read_trace(candidate)
        result = validate_events(events)
        result["trace_sha256"] = file_sha256(candidate)
        result["embodied_evidence_bound"] = False
        result["claim_boundary"] = (
            "Trace validation only; embodied evidence requires a verified compiled pack, "
            "runtime receipt, source trace hash, and replay binding."
        )
        result["authority_anchor"] = "installed_bundle"
        return result
    raise CampaignValidationError(f"not a campaign trace: {candidate}")


def run_cli(args: argparse.Namespace) -> int:
    input_value = (
        getattr(args, "input", None)
        or getattr(args, "compiled_world", None)
        or getattr(args, "campaign", None)
    )
    if input_value is None:
        raise CampaignValidationError("run requires an input path")
    input_path = Path(input_value)
    mode = RunMode(str(getattr(args, "mode", RunMode.SIMULATE.value)))
    output_value = getattr(args, "output", None)
    output_path = Path(output_value) if output_value else Path.cwd() / f"run.{mode.value}.ndjson"
    restore_from = getattr(args, "restore_from", None)
    if restore_from is not None:
        from .restore import _prepare_restore

        context = _prepare_restore(
            source_trace=restore_from,
            source_world=args.restore_world,
            child_world=input_path,
            at_event_id=args.restore_at_event,
            output=output_value,
            seed=int(getattr(args, "seed", 0) or 0),
        )
        if context.output is not None:
            output_path = context.output
        if getattr(args, "dry_run", False):
            ready = {
                "status": "ready",
                "execution_status": "not_executed",
                "mode": "simulate",
                "source_trace_sha256": context.source_trace_sha256,
                "source_prefix_sha256": context.restore["source_prefix_sha256"],
                "restored_state_sha256": context.restore["restored_state_sha256"],
                "restored_sample_count": len(context.initial_samples),
                "expected_run_id": context.expected_run_id,
                "validation_reasons": [],
            }
            print(json.dumps(ready, sort_keys=True, separators=(",", ":")))
            return 0
        binding = stable_hash(context.restore)
        if context.reused is not None:
            events, trace_hash = context.reused
        else:
            execution_binding = {
                "source_trace_sha256": context.source_trace_sha256,
                "restore_binding_sha256": binding,
                "restore": context.restore,
            }
            events, trace_hash = run_composed_campaign(
                context.child_contract,
                output_path,
                seed=int(getattr(args, "seed", 0) or 0),
                initial_samples=context.initial_samples,
                restore=execution_binding,
            )
        result = {"trace_sha256": trace_hash, **validate_events(events)}
        result.update(
            {
                "source_trace_sha256": context.source_trace_sha256,
                "restored_at_event": context.restore["at_event_id"],
                "source_prefix_sha256": context.restore["source_prefix_sha256"],
                "restored_state_sha256": context.restore["restored_state_sha256"],
                "source_evidence_classes": context.restore["source_evidence_classes"],
                "restore_binding_sha256": binding,
                "reused": context.reused is not None,
            }
        )
    elif mode is RunMode.REPLAY:
        from .replay import replay_trace

        result = replay_trace(
            input_path,
            output_path,
            compiled_world=getattr(args, "compiled_world", None),
            runtime_receipt=getattr(args, "runtime_receipt", None),
        )
    else:
        contract = load_compiled_campaign_contract(input_path)
        seed = int(getattr(args, "seed", 0) or 0)
        if contract.operation_bindings:
            events, trace_hash = run_composed_campaign(
                contract,
                output_path,
                seed=seed,
            )
        else:
            raise CampaignValidationError(
                "local composed runtime has no complete executable route: the compiled pack "
                "has no composed operation bindings; compose a campaign before running it, or "
                "use the exact external provider runtime artifacts"
            )
        result = {"trace_sha256": trace_hash, **validate_events(events)}
        result["embodied_evidence_bound"] = False
        result["claim_boundary"] = (
            "bounded virtual provider execution only; no physical backend was called"
        )
        result["authority_anchor"] = "installed_bundle"
    receipt = {"output": str(output_path), **result}
    if output_value:
        compact_receipt = {
            key: receipt[key]
            for key in (
                "mode",
                "output",
                "trace_sha256",
                "source_trace_sha256",
                "restored_at_event",
                "source_prefix_sha256",
                "restored_state_sha256",
                "source_evidence_classes",
                "restore_binding_sha256",
                "reused",
                "event_count",
                "valid",
                "execution_status",
                "validation_reasons",
                "provider_ids",
                "evidence_classes",
                "embodied_evidence_bound",
                "claim_boundary",
                "authority_anchor",
            )
            if key in receipt
        }
        compact_receipt["next_command"] = f"dynamical validate {output_path} --json"
        print(json.dumps(compact_receipt, sort_keys=True, separators=(",", ":")))
    else:
        print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt.get("valid", True) else 1
