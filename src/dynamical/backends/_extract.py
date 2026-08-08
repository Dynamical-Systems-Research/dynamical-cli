"""Small compatibility layer for strict FacilityDocument revisions."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .base import document_mapping


def records(document: Any, field: str) -> list[dict[str, Any]]:
    """Read a top-level record list without weakening core validation."""

    value = document_mapping(document).get(field, [])
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def record_id(record: dict[str, Any]) -> str:
    return str(record.get("id") or record.get("asset_id") or record.get("device_id") or "")


def metadata(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("metadata", {})
    return value if isinstance(value, dict) else {}


def tags(record: dict[str, Any]) -> list[str]:
    value = record.get("tags", [])
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def binding_payload(binding: dict[str, Any]) -> dict[str, Any]:
    for key in ("configuration", "config", "parameters", "payload", "metadata"):
        value = binding.get(key)
        if isinstance(value, dict):
            return value
    return {}


def target_bindings(document: Any, target: str) -> list[dict[str, Any]]:
    aliases = {target, target.replace("_", "-"), target.replace("-", "_")}
    found: list[dict[str, Any]] = []
    for binding in records(document, "adapter_bindings"):
        candidate = str(
            binding.get("target") or binding.get("backend") or binding.get("adapter") or ""
        )
        if candidate.lower() in aliases:
            found.append(binding)
    return sorted(found, key=record_id)


def by_id(values: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {record_id(item): item for item in values if record_id(item)}
