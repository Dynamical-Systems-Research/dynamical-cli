"""Stable campaign records and the deterministic composed thermal runner."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

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


class CampaignValidationError(ValueError):
    """A campaign record does not meet the v0.1 contract."""


class RunMode(StrEnum):
    SIMULATE = "simulate"
    REPLAY = "replay"


class ActionKind(StrEnum):
    APPLY_THERMAL_PROGRAM = "apply-thermal-program"
    AGITATE_SAMPLE = "agitate-sample"
    MEASURE_PLATE_TEMPERATURE = "measure-plate-temperature"
    MEASURE_SAMPLE_TEMPERATURE = "measure-sample-temperature"
    ESTIMATE_TEMPERATURE_GRADIENT = "estimate-temperature-gradient"
    MEASURE_SAMPLE_MASS = "measure-sample-mass"
    MEASURE_MIXING_LOAD = "measure-mixing-load"
    ESTIMATE_REACTION_PROGRESS = "estimate-reaction-progress"
    MEASURE_REACTION_PROGRESS = "measure-reaction-progress"
    DISPENSE_LIQUID = "dispense_liquid"
    DISPENSE_SOLID = "dispense_solid"
    MATERIAL_TRANSFER = "material_transfer"
    SET_AIRFLOW = "set_airflow"
    SET_GLOVEBOX_STATE = "set_glovebox_state"
    TRANSPORT = "transport"
    WASH_AND_DRY = "wash_and_dry"
    SET_HEATER = "set_heater"
    PICK = "pick"
    PLACE = "place"
    WAIT = "wait"
    OBSERVE = "observe"
    STOP = "stop"


THERMAL_OPERATION_IDS = {
    ActionKind.AGITATE_SAMPLE.value,
    ActionKind.APPLY_THERMAL_PROGRAM.value,
    ActionKind.MEASURE_PLATE_TEMPERATURE.value,
    ActionKind.MEASURE_SAMPLE_TEMPERATURE.value,
    ActionKind.ESTIMATE_TEMPERATURE_GRADIENT.value,
    ActionKind.MEASURE_SAMPLE_MASS.value,
    ActionKind.MEASURE_MIXING_LOAD.value,
    ActionKind.ESTIMATE_REACTION_PROGRESS.value,
}


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
                    "kind": {"enum": [item.value for item in ActionKind]},
                    "actor_id": {"type": "string", "minLength": 1},
                    "provider_id": {"type": "string", "minLength": 1},
                    "evidence_class": {"enum": [item.value for item in EvidenceClass]},
                    "parameters": {"type": "object"},
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
                ],
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "value": scalar_schema,
                    "unit": {"type": "string", "minLength": 1},
                    "quality": {"enum": ["valid", "estimated", "degraded", "unavailable"]},
                    "origin": {"enum": [item.value for item in ObservationOrigin]},
                    "provider_id": {"type": "string", "minLength": 1},
                    "evidence_class": {"enum": [item.value for item in EvidenceClass]},
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
                },
            },
            "constraint": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "constraint_id",
                    "phase",
                    "passed",
                    "measured_value",
                    "limit",
                    "verifier",
                ],
                "properties": {
                    "constraint_id": {"type": "string", "minLength": 1},
                    "phase": {"enum": ["pre_action", "runtime", "post_action"]},
                    "passed": {"type": "boolean"},
                    "measured_value": scalar_schema,
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
    kind: ActionKind
    actor_id: str
    provider_id: str
    evidence_class: EvidenceClass
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.action_id or not self.actor_id or not self.provider_id:
            raise CampaignValidationError("action id, actor id, and provider id are required")
        canonical_json(self.parameters)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "kind": self.kind.value,
            "actor_id": self.actor_id,
            "provider_id": self.provider_id,
            "evidence_class": self.evidence_class.value,
            "parameters": dict(self.parameters),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ActionRequest:
        keys = {
            "action_id",
            "kind",
            "actor_id",
            "provider_id",
            "evidence_class",
            "parameters",
        }
        _strict_mapping(value, keys, "action")
        if set(value) != keys or not isinstance(value["parameters"], Mapping):
            raise CampaignValidationError("action record is incomplete or invalid")
        return cls(
            action_id=_require_string(value["action_id"], "action.action_id"),
            kind=ActionKind(_require_string(value["kind"], "action.kind")),
            actor_id=_require_string(value["actor_id"], "action.actor_id"),
            provider_id=_require_string(value["provider_id"], "action.provider_id"),
            evidence_class=EvidenceClass(
                _require_string(value["evidence_class"], "action.evidence_class")
            ),
            parameters=dict(value["parameters"]),
        )


@dataclass(frozen=True)
class ObservationChannel:
    name: str
    value: float | int | str | bool | None
    unit: str
    quality: str
    origin: ObservationOrigin
    provider_id: str
    evidence_class: EvidenceClass

    def __post_init__(self) -> None:
        if not self.name or not self.unit or not self.quality or not self.provider_id:
            raise CampaignValidationError(
                "observation name, unit, quality, and provider are required"
            )
        if self.quality not in {"valid", "estimated", "degraded", "unavailable"}:
            raise CampaignValidationError("observation quality is invalid")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise CampaignValidationError("numeric observations must be finite")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "quality": self.quality,
            "origin": self.origin.value,
            "provider_id": self.provider_id,
            "evidence_class": self.evidence_class.value,
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
        )


@dataclass(frozen=True)
class ObservationFrame:
    frame_id: str
    logical_time_s: float
    provider_id: str
    evidence_class: EvidenceClass
    channels: tuple[ObservationChannel, ...]
    evidence_ids: tuple[str, ...] = ()

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "logical_time_s": self.logical_time_s,
            "provider_id": self.provider_id,
            "evidence_class": self.evidence_class.value,
            "channels": [channel.to_dict() for channel in self.channels],
            "evidence_ids": list(self.evidence_ids),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ObservationFrame:
        keys = {
            "frame_id",
            "logical_time_s",
            "provider_id",
            "evidence_class",
            "channels",
            "evidence_ids",
        }
        _strict_mapping(value, keys, "observation")
        if set(value) != keys or not isinstance(value["channels"], list):
            raise CampaignValidationError("observation frame is incomplete or invalid")
        if not isinstance(value["evidence_ids"], list):
            raise CampaignValidationError("observation evidence_ids must be an array")
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
        )


@dataclass(frozen=True)
class ConstraintEvaluation:
    constraint_id: str
    phase: str
    passed: bool
    measured_value: float | int | str | bool | None
    limit: Mapping[str, Any]
    verifier: str

    def __post_init__(self) -> None:
        if self.phase not in {"pre_action", "runtime", "post_action"}:
            raise CampaignValidationError("constraint phase is invalid")
        if not self.constraint_id or not self.verifier:
            raise CampaignValidationError("constraint id and verifier are required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "constraint_id": self.constraint_id,
            "phase": self.phase,
            "passed": self.passed,
            "measured_value": self.measured_value,
            "limit": dict(self.limit),
            "verifier": self.verifier,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ConstraintEvaluation:
        keys = {"constraint_id", "phase", "passed", "measured_value", "limit", "verifier"}
        _strict_mapping(value, keys, "constraint")
        if set(value) != keys or not isinstance(value["limit"], Mapping):
            raise CampaignValidationError("constraint evaluation is incomplete or invalid")
        return cls(
            constraint_id=_require_string(value["constraint_id"], "constraint.constraint_id"),
            phase=_require_string(value["phase"], "constraint.phase"),
            passed=_require_boolean(value["passed"], "constraint.passed"),
            measured_value=value["measured_value"],
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

    def __post_init__(self) -> None:
        if not self.campaign_id or not self.run_id or self.seed < 0 or not self.backend_revision:
            raise CampaignValidationError("campaign identity is incomplete")
        for name in ("ir_hash", "world_hash", "campaign_hash"):
            value = getattr(self, name)
            if not _is_sha256(value):
                raise CampaignValidationError(f"{name} must be a SHA-256 value")


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
            expected_passed = _constraint_passed(
                operator,
                limit.get("bound"),
                evaluation.measured_value,
            )
            if evaluation.passed is not expected_passed:
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
                    expected_passed = _constraint_passed(
                        _require_string(
                            declaration.get("operator"), "constraint declaration operator"
                        ),
                        declaration.get("bound"),
                        evaluation.measured_value,
                    )
                    if evaluation.passed is not expected_passed:
                        raise CampaignValidationError(
                            f"constraint result differs from measured value: "
                            f"{evaluation.constraint_id}"
                        )
    if failed_constraint and events[-1].provenance.get("execution_status") != "failed":
        raise CampaignValidationError("a failed constraint needs failed campaign status")
    return {
        "valid": True,
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
    }


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


def _constraint_passed(operator: str, bound: Any, measured: Any) -> bool:
    if isinstance(measured, bool) or not isinstance(measured, (int, float)):
        return False
    value = float(measured)
    if not math.isfinite(value):
        return False
    if operator == "between":
        bounds = _require_mapping(bound, "constraint bound")
        return float(bounds["minimum"]) <= value <= float(bounds["maximum"])
    if operator == "gt":
        return value > float(bound)
    if operator in {"ge", "gte"}:
        return value >= float(bound)
    if operator == "lt":
        return value < float(bound)
    if operator in {"le", "lte"}:
        return value <= float(bound)
    if operator == "eq":
        return value == float(bound)
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
        checks.append(
            ConstraintEvaluation(
                constraint_id=constraint_id,
                phase=_require_string(constraint.get("phase"), "constraint.phase"),
                passed=_constraint_passed(operator, bound, measured_value),
                measured_value=measured_value,
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
    action_kinds: frozenset[ActionKind]
    observation_channels: frozenset[str]
    channel_units: Mapping[str, str]
    capability_by_action: Mapping[ActionKind, Mapping[str, Any]]
    constraint_by_id: Mapping[str, Mapping[str, Any]]
    composition_sha256: str | None = None
    operation_bindings: tuple[Mapping[str, Any], ...] = ()

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
            "declared_action_kinds": sorted(item.value for item in self.action_kinds),
            "declared_observation_channels": sorted(self.observation_channels),
            "capability_by_action": {
                kind.value: {
                    "capability_id": capability["id"],
                    "provider_id": capability["provider_id"],
                }
                for kind, capability in sorted(
                    self.capability_by_action.items(), key=lambda item: item[0].value
                )
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
    dict[ActionKind, Mapping[str, Any]],
    set[str],
    dict[str, str],
    dict[str, Mapping[str, Any]],
]:
    capabilities: dict[ActionKind, Mapping[str, Any]] = {}
    provider_ids: set[str] = set()
    channels: set[str] = set()
    channel_units: dict[str, str] = {}
    constraints: dict[str, Mapping[str, Any]] = {}
    for group in ("devices", "agents"):
        for raw_provider in _require_list(facility_ir.get(group), f"facility IR.{group}"):
            provider = _require_mapping(raw_provider, f"facility IR.{group} item")
            provider_ids.add(_require_string(provider.get("id"), f"facility IR.{group}.id"))
            if group == "devices":
                for raw_channel in _require_list(
                    provider.get("state_channels"), "facility IR device.state_channels"
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
        try:
            kind = ActionKind(action_type)
        except ValueError as exc:
            raise CampaignValidationError(
                f"compiled pack action is not supported by the reference campaign: {action_type}"
            ) from exc
        if kind in capabilities:
            raise CampaignValidationError(f"reference action kind is ambiguous: {kind.value}")
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
    raw_declared_actions = _require_list(
        action_schema.get("x-dynamical-declared-capability-action-types"),
        "action schema declared action types",
    )
    declared_actions = {
        ActionKind(_require_string(item, "declared action type")) for item in raw_declared_actions
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
        ActionKind(_require_string(item, "action schema kind"))
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
    if target not in {"matterix", "isaac", "openusd"}:
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
    identity: CampaignIdentity, sequence: int, logical_time_s: float, event_type: str, **kwargs: Any
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
        provenance=identity.provenance,
        **kwargs,
    )


def _thermal_composition(contract: CompiledCampaignContract) -> bool:
    operation_ids = {
        _require_string(binding.get("operation_id"), "operation binding.operation_id")
        for binding in contract.operation_bindings
    }
    return bool(operation_ids) and operation_ids.issubset(THERMAL_OPERATION_IDS)


def _composed_actions(contract: CompiledCampaignContract) -> tuple[ActionRequest, ...]:
    actions: list[ActionRequest] = []
    for binding in contract.operation_bindings:
        operation_id = _require_string(
            binding.get("operation_id"), "operation binding.operation_id"
        )
        if operation_id not in THERMAL_OPERATION_IDS:
            raise CampaignValidationError(
                f"local composed runtime has no admitted handler for {operation_id!r}"
            )
        action = ActionRequest(
            action_id=_require_string(binding.get("step_id"), "operation binding.step_id"),
            kind=ActionKind(operation_id),
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


def _execute_thermal_operation(
    operation_id: str,
    inputs: Mapping[str, Any],
    parameters: Mapping[str, Any],
) -> dict[str, Any]:
    from .thermal import (
        agitate_sample,
        apply_thermal_program,
        estimate_reaction_progress,
        estimate_temperature_gradient,
        measure_mixing_load,
        measure_plate_temperature,
        measure_sample_mass,
        measure_sample_temperature,
    )

    if operation_id == ActionKind.APPLY_THERMAL_PROGRAM.value:
        return apply_thermal_program(inputs, parameters)
    if operation_id == ActionKind.AGITATE_SAMPLE.value:
        return agitate_sample(inputs, parameters)
    if operation_id == ActionKind.MEASURE_PLATE_TEMPERATURE.value:
        return measure_plate_temperature(inputs, parameters)
    if operation_id == ActionKind.MEASURE_SAMPLE_TEMPERATURE.value:
        return measure_sample_temperature(inputs, parameters)
    if operation_id == ActionKind.ESTIMATE_TEMPERATURE_GRADIENT.value:
        return estimate_temperature_gradient(inputs, parameters)
    if operation_id == ActionKind.MEASURE_SAMPLE_MASS.value:
        return measure_sample_mass(inputs, parameters)
    if operation_id == ActionKind.MEASURE_MIXING_LOAD.value:
        return measure_mixing_load(inputs, parameters)
    if operation_id == ActionKind.ESTIMATE_REACTION_PROGRESS.value:
        return estimate_reaction_progress(inputs, parameters)
    raise CampaignValidationError(f"bounded thermal operation is not executable: {operation_id}")


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


def _composed_identity(
    contract: CompiledCampaignContract,
    actions: Sequence[ActionRequest],
    seed: int,
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
    digest = stable_hash(campaign_spec)
    return CampaignIdentity(
        campaign_id=f"composed-{digest[:12]}",
        run_id=f"simulate-{digest[12:24]}",
        seed=seed,
        backend_revision=(
            f"compiled_target={contract.target};adapter={contract.adapter_pack_sha256};"
            "bounded_thermal_virtual_sdl:not_embodied"
        ),
        ir_hash=contract.core_ir_sha256,
        world_hash=contract.world_sha256,
        campaign_hash=digest,
        provenance={
            "runner": "dynamical.campaign.run_composed_campaign",
            "embodied_backend": False,
            "w1_evidence": False,
            "compiled_contract_bound": True,
            "compiled_pack": contract.provenance_binding(),
            "constraint_contract": constraint_contract,
            "constraint_contract_sha256": stable_hash(constraint_contract),
            "provider_contract": {
                action.action_id: {
                    "provider_id": action.provider_id,
                    "evidence_class": action.evidence_class.value,
                }
                for action in actions
            },
        },
    )


def run_composed_campaign(
    contract: CompiledCampaignContract,
    output_path: Path,
    *,
    seed: int,
) -> tuple[list[TraceEvent], str]:
    """Execute only the ordered providers selected by a compiled thermal composition."""

    actions = _composed_actions(contract)
    identity = _composed_identity(contract, actions, seed)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("", encoding="utf-8")
    events: list[TraceEvent] = []

    def append(event: TraceEvent) -> None:
        events.append(event)
        with output_path.open("a", encoding="utf-8") as stream:
            stream.write(canonical_json(event.to_dict()) + "\n")
            stream.flush()

    append(_event(identity, 0, 0.0, "campaign_start"))
    prior_outputs: dict[tuple[str, str], Any] = {}
    logical_time = 0.0
    failed = False
    for binding, action in zip(contract.operation_bindings, actions, strict=True):
        inputs, input_units = _resolved_step_inputs(binding, prior_outputs)
        measured = {name: (value, input_units[name]) for name, value in inputs.items()}
        if "target-temperature" in action.parameters:
            measured["heater.target_temperature_K"] = (
                action.parameters["target-temperature"],
                "K",
            )
        pre_ids, post_ids = _composed_constraint_ids(binding, contract)
        pre_constraints = evaluate_action_constraints(
            action,
            declared_constraints=contract.constraint_by_id,
            constraint_ids=pre_ids,
            measured_channels=measured,
        )
        append(
            _event(
                identity,
                len(events),
                logical_time,
                "action",
                action=action,
                constraints=pre_constraints,
            )
        )
        capability = _require_mapping(
            binding.get("capability_contract"), "operation binding.capability_contract"
        )
        output_ports = {
            _require_string(port.get("id"), "operation output port.id"): _require_string(
                port.get("unit"), "operation output port.unit"
            )
            for raw_port in _require_list(capability.get("output_ports"), "operation outputs")
            for port in [_require_mapping(raw_port, "operation output port")]
        }
        if any(not item.passed for item in pre_constraints):
            result = {name: None for name in output_ports}
            failed = True
        else:
            result = _execute_thermal_operation(
                action.kind.value,
                inputs,
                action.parameters,
            )
            if set(result) != set(output_ports):
                raise CampaignValidationError(
                    f"operation {action.action_id} outputs differ from its compiled contract: "
                    f"expected={sorted(output_ports)}, found={sorted(result)}"
                )
            for port_id, value in result.items():
                prior_outputs[(action.action_id, port_id)] = value
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
        failed = failed or any(not item.passed for item in post_constraints)
        append(
            _event(
                identity,
                len(events),
                logical_time,
                "observation",
                observation=observation,
                constraints=post_constraints,
            )
        )
        if failed:
            break
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
        },
    )
    append(_event(terminal_identity, len(events), logical_time, "campaign_end"))
    validate_events(events)
    return events, file_sha256(output_path)


def validate_path(path: str | Path) -> dict[str, Any]:
    candidate = Path(path)
    if candidate.suffix == ".ndjson":
        events = read_trace(candidate)
        result = validate_events(events)
        result["trace_sha256"] = file_sha256(candidate)
        result["w1_evidence"] = False
        result["w1_blocker"] = (
            "Trace syntax cannot admit W1 without a verified compiled pack, runtime receipt, "
            "source trace hash, and replay binding."
        )
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
    if mode is RunMode.REPLAY:
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
        if _thermal_composition(contract):
            events, trace_hash = run_composed_campaign(
                contract,
                output_path,
                seed=seed,
            )
        else:
            operation_ids = sorted(
                str(binding["operation_id"]) for binding in contract.operation_bindings
            )
            raise CampaignValidationError(
                "local composed runtime has no complete executable route for operations: "
                f"{operation_ids}; use a complete admitted thermal composition or the exact "
                "external provider runtime artifacts"
            )
        result = {"trace_sha256": trace_hash, **validate_events(events)}
        result["w1_evidence"] = False
        result["claim_boundary"] = (
            "bounded virtual provider execution only; no physical backend was called"
        )
    receipt = {"output": str(output_path), **result}
    if output_value:
        compact_receipt = {
            key: receipt[key]
            for key in (
                "mode",
                "output",
                "trace_sha256",
                "source_trace_sha256",
                "event_count",
                "valid",
                "w1_evidence",
                "provider_ids",
                "evidence_classes",
            )
            if key in receipt
        }
        compact_receipt["next_command"] = f"dynamical validate {output_path} --json"
        print(json.dumps(compact_receipt, sort_keys=True, separators=(",", ":")))
    else:
        print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0
