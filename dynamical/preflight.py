"""Freeze one compact, model-authored preflight map into a campaign starting state.

The mapping is agent-authored. This module hashes its sources, assigns content
identities, resolves links, checks cutoff closure, selects the frozen state, and
derives READY or HOLD. It decides nothing scientific and admits nothing.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .composition import preflight_state_sha256
from .schema import (
    canonical_sha256,
    load_campaign_requirement,
    load_capability_registry,
    load_facility_manifest,
)

RECEIPT_DOCUMENT_TYPE = "dynamical.preflight-receipt"
RECEIPT_SCHEMA_VERSION = "0.1.0"
KINDS = {"declared", "setpoint", "readback", "observation", "derived", "assumption"}
ROOTS = {"sample", "instrument", "process", "model", "facility", "constraints"}
MAPPING_FIELDS = {
    "created_at_utc",
    "discovery_roots",
    "sources",
    "entities",
    "facts",
    "relations",
    "gaps",
    "requested_cutoff",
    "parent",
}


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _id(kind: str, value: Any) -> str:
    return f"{kind}-{hashlib.sha256(_json(value)).hexdigest()[:16]}"


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _time(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} needs a UTC timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError(f"{label} must use UTC")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _records(data: dict[str, Any], name: str) -> list[dict[str, Any]]:
    records = data.get(name, [])
    if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        raise ValueError(f"{name} must be a list of objects")
    refs = [item.get("ref") for item in records]
    if any(not isinstance(ref, str) or not ref for ref in refs) or len(refs) != len(set(refs)):
        raise ValueError(f"{name} needs unique refs")
    return records


def _evidence(items: Any, source_ids: dict[str, str]) -> list[dict[str, str]]:
    if not isinstance(items, list):
        raise ValueError("evidence_refs must be a list")
    output = []
    for item in items:
        if not isinstance(item, dict) or item.get("source_ref") not in source_ids:
            raise ValueError("unresolved source_ref")
        record = {"source_id": source_ids[item["source_ref"]]}
        if item.get("locator"):
            record["locator"] = str(item["locator"])
        output.append(record)
    return sorted(output, key=_json)


def _sources(data: dict[str, Any], base: Path) -> tuple[list[dict[str, Any]], dict[str, str]]:
    output: dict[str, dict[str, Any]] = {}
    refs: dict[str, str] = {}
    for item in _records(data, "sources"):
        if ("path" in item) == ("uri" in item):
            raise ValueError(f"source {item['ref']} needs one path or URI")
        record = {key: value for key, value in item.items() if key != "ref"}
        record["available_at"] = _time(record.get("available_at"), f"source {item['ref']}")
        if record.get("disposition") not in {"state", "context", "excluded"}:
            raise ValueError(f"source {item['ref']} has invalid disposition")
        if record["disposition"] == "excluded" and not record.get("exclusion_reason"):
            raise ValueError(f"excluded source {item['ref']} needs a reason")
        if "path" in record:
            path = Path(record["path"])
            path = (path if path.is_absolute() else base / path).resolve()
            if not path.is_file():
                raise ValueError(f"source does not exist: {path}")
            digest = _file_hash(path)
            if record.get("sha256") not in {None, digest}:
                raise ValueError(f"source {item['ref']} hash differs from its bytes")
            record.update(path=str(path), sha256=digest, size_bytes=path.stat().st_size)
            record.setdefault("format", path.suffix.lstrip(".") or "binary")
        elif not all(record.get(key) is not None for key in ("sha256", "size_bytes", "format")):
            raise ValueError(f"remote source {item['ref']} needs hash, size, and format")
        source_id = _id("source", record)
        output[source_id] = {"source_id": source_id, **record}
        refs[item["ref"]] = source_id
    return sorted(output.values(), key=lambda item: item["source_id"]), refs


def load_mapping(path: Path) -> dict[str, Any]:
    mapping = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(mapping, dict):
        raise ValueError("preflight mapping must contain one JSON object")
    return mapping


def finalize(
    data: dict[str, Any],
    mapping_path: Path,
    *,
    requirement: Path,
    registry: Path,
    facility: Path,
) -> dict[str, Any]:
    """Return the receipt for one mapping bound to exact compose inputs."""

    if extra := sorted(set(data) - MAPPING_FIELDS):
        raise ValueError(f"unknown mapping fields: {extra}")
    created = _time(data.get("created_at_utc"), "created_at_utc")
    sources, source_ids = _sources(data, mapping_path.parent)
    root_refs = data.get("discovery_roots")
    if not isinstance(root_refs, list) or any(ref not in source_ids for ref in root_refs):
        raise ValueError("discovery_roots must resolve to sources")

    entity_ids: dict[str, str] = {}
    entities: list[dict[str, Any]] = []
    for item in _records(data, "entities"):
        semantic = {
            key: value for key, value in item.items() if key not in {"ref", "evidence_refs"}
        }
        if not semantic.get("kind") or not isinstance(semantic.get("source_native_ids"), dict):
            raise ValueError(f"entity {item['ref']} needs kind and source_native_ids")
        entity_id = _id("entity", semantic)
        entity_ids[item["ref"]] = entity_id
        entities.append(
            {
                "entity_id": entity_id,
                **semantic,
                "evidence_refs": _evidence(item.get("evidence_refs", []), source_ids),
            }
        )

    raw_facts = _records(data, "facts")
    fact_ids: dict[str, str] = {}
    semantics: dict[str, dict[str, Any]] = {}
    for item in raw_facts:
        if item.get("subject_ref") not in entity_ids or item.get("kind") not in KINDS:
            raise ValueError(f"fact {item['ref']} has invalid subject or kind")
        field = item.get("field")
        if not isinstance(field, str) or not field.strip() or item.get("value") is None:
            raise ValueError(f"fact {item['ref']} needs field and value")
        semantic = {
            key: value
            for key, value in item.items()
            if key not in {"ref", "subject_ref", "evidence_refs", "input_refs"}
        }
        semantic.update(
            subject_id=entity_ids[item["subject_ref"]],
            available_at=_time(item.get("available_at"), f"fact {item['ref']}"),
        )
        path = semantic.get("state_path")
        if path and (not isinstance(path, str) or path.strip("/").split("/", 1)[0] not in ROOTS):
            raise ValueError(f"fact {item['ref']} has invalid state_path")
        fact_ids[item["ref"]] = _id("fact", semantic)
        semantics[item["ref"]] = semantic
    facts: list[dict[str, Any]] = []
    for item in raw_facts:
        inputs = item.get("input_refs", [])
        if not isinstance(inputs, list) or any(ref not in fact_ids for ref in inputs):
            raise ValueError(f"fact {item['ref']} has unresolved input_refs")
        facts.append(
            {
                "fact_id": fact_ids[item["ref"]],
                **semantics[item["ref"]],
                "evidence_refs": _evidence(item.get("evidence_refs", []), source_ids),
                "input_ids": sorted(fact_ids[ref] for ref in inputs),
            }
        )
    state_paths = [item["state_path"] for item in facts if item.get("state_path")]
    if len(state_paths) != len(set(state_paths)):
        raise ValueError("frozen state paths must be unique")

    relations: list[dict[str, Any]] = []
    for item in _records(data, "relations"):
        if item.get("subject_ref") not in entity_ids or item.get("object_ref") not in entity_ids:
            raise ValueError(f"relation {item['ref']} has unresolved entities")
        predicate = item.get("predicate")
        if not isinstance(predicate, str) or not predicate.strip():
            raise ValueError(f"relation {item['ref']} needs predicate")
        semantic = {
            key: value
            for key, value in item.items()
            if key not in {"ref", "subject_ref", "object_ref", "evidence_refs"}
        }
        semantic.update(
            subject_id=entity_ids[item["subject_ref"]],
            object_id=entity_ids[item["object_ref"]],
            available_at=_time(item.get("available_at"), f"relation {item['ref']}"),
        )
        relations.append(
            {
                "relation_id": _id("relation", semantic),
                **semantic,
                "evidence_refs": _evidence(item.get("evidence_refs", []), source_ids),
            }
        )

    gaps: list[dict[str, Any]] = []
    for item in _records(data, "gaps"):
        record = {key: value for key, value in item.items() if key != "ref"}
        record["available_at"] = _time(record.get("available_at"), f"gap {item['ref']}")
        if not isinstance(record.get("material"), bool) or not record.get("release_condition"):
            raise ValueError(f"gap {item['ref']} needs material and release_condition")
        if ref := record.pop("subject_ref", None):
            if ref not in entity_ids:
                raise ValueError(f"gap {item['ref']} has unresolved subject_ref")
            record["subject_id"] = entity_ids[ref]
        gaps.append({"gap_id": _id("gap", record), **record})

    state_facts = sorted(item["fact_id"] for item in facts if item.get("state_path"))
    state_relations = sorted(
        item["relation_id"] for item in relations if item.get("state_defining") is True
    )
    fact_index = {item["fact_id"]: item for item in facts}
    closure, pending = set(state_facts), list(state_facts)
    while pending:
        for input_id in fact_index[pending.pop()]["input_ids"]:
            if input_id not in closure:
                closure.add(input_id)
                pending.append(input_id)
    sourced = {item["fact_id"] for item in facts if item["evidence_refs"]}
    while unresolved := closure - sourced:
        resolved = {
            fact_id
            for fact_id in unresolved
            if fact_index[fact_id]["input_ids"] and set(fact_index[fact_id]["input_ids"]) <= sourced
        }
        if not resolved:
            raise ValueError(
                "frozen state records need evidence; derivation is cyclic or unsourced"
            )
        sourced.update(resolved)
    selected = [
        item
        for item in facts + relations
        if item.get("fact_id") in closure or item.get("relation_id") in state_relations
    ]
    if any(not item.get("evidence_refs") and not item.get("input_ids") for item in selected):
        raise ValueError("frozen state records need evidence or a sourced derivation")
    entity_index = {item["entity_id"]: item for item in entities}
    selected_entity_ids = {item["subject_id"] for item in selected}
    selected_entity_ids.update(item["object_id"] for item in selected if item.get("object_id"))
    selected_entities = [entity_index[entity_id] for entity_id in sorted(selected_entity_ids)]
    if any(not item["evidence_refs"] for item in selected_entities):
        raise ValueError("frozen state entities need evidence")
    times = [item["available_at"] for item in selected]
    cutoff = (
        _time(data["requested_cutoff"], "requested_cutoff")
        if data.get("requested_cutoff")
        else max(times, default=created, key=_instant)
    )
    if any(_instant(value) > _instant(cutoff) for value in times):
        raise ValueError("frozen state contains later evidence")
    source_index = {item["source_id"]: item for item in sources}
    for record in selected:
        for evidence in record["evidence_refs"]:
            source = source_index[evidence["source_id"]]
            if source["disposition"] != "state" or _instant(source["available_at"]) > _instant(
                cutoff
            ):
                raise ValueError("frozen state uses non-state or later evidence")
    for entity in selected_entities:
        for evidence in entity["evidence_refs"]:
            source = source_index[evidence["source_id"]]
            if source["disposition"] == "excluded" or _instant(source["available_at"]) > _instant(
                cutoff
            ):
                raise ValueError("frozen state entity uses excluded or later evidence")

    handoff_paths = {"requirement": requirement, "registry": registry, "facility": facility}
    documents = {
        "requirement": load_campaign_requirement(requirement),
        "registry": load_capability_registry(registry),
        "facility": load_facility_manifest(facility),
    }
    material_gaps = [item for item in gaps if item["material"]]
    assumed = any(item["fact_id"] in closure and item["kind"] == "assumption" for item in facts)
    receipt: dict[str, Any] = {
        "document_type": RECEIPT_DOCUMENT_TYPE,
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "created_at_utc": created,
        "status": "HOLD" if material_gaps or assumed else "READY",
        "discovery_roots": sorted(source_ids[ref] for ref in root_refs),
        "sources": sources,
        "entities": sorted(entities, key=lambda item: item["entity_id"]),
        "facts": sorted(facts, key=lambda item: item["fact_id"]),
        "relations": sorted(relations, key=lambda item: item["relation_id"]),
        "gaps": sorted(gaps, key=lambda item: item["gap_id"]),
        "state": {
            "fact_ids": state_facts,
            "relation_ids": state_relations,
            "evidence_cutoff": cutoff,
            "parent": data.get("parent"),
        },
        "handoff": {
            name: {
                "path": str(handoff_paths[name].resolve()),
                "canonical_sha256": canonical_sha256(document.model_dump(mode="json")),
            }
            for name, document in documents.items()
        },
        "authority": {
            "provider_admission_changed": False,
            "physical_authority_granted": False,
            "qualification_decision_made": False,
        },
    }
    digest = preflight_state_sha256(receipt)
    receipt["state"].update(state_sha256=digest, state_id=f"state-{digest[:16]}")
    route = material_gaps[0].get("next_route") if material_gaps else None
    receipt["next_action"] = {
        "action": "compose" if receipt["status"] == "READY" else (route or "HOLD")
    }
    return receipt


def material_gaps(receipt: dict[str, Any]) -> list[dict[str, Any]]:
    """Project the material gaps of a receipt to the fields an agent acts on."""

    projected = []
    for gap in receipt.get("gaps", []):
        if not gap.get("material"):
            continue
        record = {"gap_id": gap["gap_id"]}
        for key in ("reason", "question", "release_condition", "next_route"):
            if gap.get(key) is not None:
                record[key] = gap[key]
        projected.append(record)
    return projected


def write_receipt(receipt: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def self_test() -> dict[str, str]:
    """Check that the state identity moves with the frozen state's content."""

    receipt = {
        "facts": [
            {
                "fact_id": "f",
                "subject_id": "e",
                "field": "mass",
                "value": 1,
                "kind": "observation",
                "available_at": "2026-01-01T00:00:00Z",
                "state_path": "/sample/mass",
                "evidence_refs": [],
                "input_ids": [],
            }
        ],
        "relations": [],
        "state": {
            "fact_ids": ["f"],
            "relation_ids": [],
            "evidence_cutoff": "2026-01-01T00:00:00Z",
            "parent": None,
        },
    }
    first = preflight_state_sha256(receipt)
    receipt["facts"][0]["value"] = 2
    if preflight_state_sha256(receipt) == first:
        raise RuntimeError("preflight state identity did not change with its content")
    return {"check": "preflight-finalizer", "status": "passed"}
