"""Draft/validate field evidence sidecars; does not certify factual correctness."""

from __future__ import annotations

import hashlib
import json
import re

import yaml

DOCUMENTS = ("facility.yaml", "registry.yaml")
CLASSIFICATIONS = {
    "upstream_protocol",
    "recorded_measurement",
    "recorded_provenance",
    "dynamical_convention",
    "unknown_physical_property",
}
SOURCE_KINDS = {"upstream_file", "measurement_record", "provenance_record", "implementation_file"}
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def unique_mapping(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate object key: {key}")
        result[key] = value
    return result


class UniqueLoader(yaml.SafeLoader):
    pass


def yaml_mapping(loader, node):
    loader.flatten_mapping(node)
    return unique_mapping(loader.construct_pairs(node, deep=True))


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, yaml_mapping)


def terminal_fields(value, pointer=""):
    """RFC 6901 pointers; empty arrays/objects themselves are terminal fields."""
    if isinstance(value, dict) and value:
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("document mapping keys must be strings")
            escaped = key.replace("~", "~0").replace("/", "~1")
            yield from terminal_fields(child, f"{pointer}/{escaped}")
    elif isinstance(value, list) and value:
        for index, child in enumerate(value):
            yield from terminal_fields(child, f"{pointer}/{index}")
    else:
        yield pointer, value


def value_sha256(value):
    encoded = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def inventory(bundle):
    result = {}
    for document in DOCUMENTS:
        value = yaml.load((bundle / document).read_text(), Loader=UniqueLoader)
        for pointer, leaf in terminal_fields(value):
            result[(document, pointer)] = value_sha256(leaf)
    return result


def draft(bundle):
    return {
        "document_type": "dynamical.field-traceability",
        "sources": {},
        "rows": [
            {
                "document": document,
                "pointer": pointer,
                "value_sha256": digest,
                "classification": None,
                "source_ids": [],
                "derivation": "",
                "claim_limit": "",
            }
            for (document, pointer), digest in inventory(bundle).items()
        ],
    }


def validate(bundle, table):
    """Return all structural/binding errors. Human source review is still required."""
    errors = []
    if not isinstance(table, dict):
        return ["sidecar must be an object"]
    if set(table) != {"document_type", "sources", "rows"}:
        errors.append("sidecar requires exactly document_type, sources, rows")
    if table.get("document_type") != "dynamical.field-traceability":
        errors.append("wrong document_type")
    expected = inventory(bundle)
    sources = table.get("sources")
    rows = table.get("rows")
    if not isinstance(sources, dict) or not isinstance(rows, list):
        return errors + ["sources must be an object; rows must be an array"]
    for source_id, source in sources.items():
        label = f"source {source_id}"
        if not isinstance(source, dict):
            errors.append(f"{label}: must be an object")
            continue
        if not isinstance(source.get("kind"), str) or source["kind"] not in SOURCE_KINDS:
            errors.append(f"{label}: unknown source kind")
        for field in ("path", "description"):
            if not isinstance(source.get(field), str) or not source[field].strip():
                errors.append(f"{label}: {field} required")
        if isinstance(source.get("path"), str) and any(
            token in source["path"] for token in ("*", "?")
        ):
            errors.append(f"{label}: wildcard paths prohibited")
        if not SHA256.fullmatch(str(source.get("sha256", ""))):
            errors.append(f"{label}: exact source file/record sha256 required")
        locator = source.get("locator", {})
        location_ok = False
        if isinstance(locator, dict):
            start, end = locator.get("line_start"), locator.get("line_end")
            location_ok = type(start) is int and type(end) is int and 0 < start <= end
            if locator.get("sheet") and isinstance(locator.get("cells"), list):
                location_ok |= bool(locator["cells"]) and all(
                    isinstance(cell, str) and re.fullmatch(r"[A-Z]+[1-9][0-9]*", cell)
                    for cell in locator["cells"]
                )
            page = locator.get("page")
            location_ok |= type(page) is int and page > 0 and bool(locator.get("region"))
        if not location_ok:
            errors.append(f"{label}: exact line interval, worksheet cells, or page+region required")
        if source.get("kind") == "upstream_file":
            revision = source.get("revision", "")
            uri = source.get("uri", "")
            if not re.fullmatch(r"[0-9a-f]{7,40}", str(revision)):
                errors.append(f"{label}: pinned git revision required")
            if (
                not isinstance(uri, str)
                or not uri.startswith("https://")
                or f"/{revision}/" not in uri
            ):
                errors.append(f"{label}: URL must embed pinned revision")
    seen = set()
    used_sources = set()
    required_fields = {
        "document",
        "pointer",
        "value_sha256",
        "classification",
        "source_ids",
        "derivation",
        "claim_limit",
    }
    for index, row in enumerate(rows):
        label = f"row {index}"
        if not isinstance(row, dict) or set(row) != required_fields:
            errors.append(f"{label}: exact row fields required")
            continue
        key = (row["document"], row["pointer"])
        if not all(isinstance(part, str) for part in key):
            errors.append(f"{label}: document/pointer must be strings")
            continue
        if key in seen:
            errors.append(f"{label}: duplicate field {key}")
        seen.add(key)
        if key not in expected:
            errors.append(f"{label}: stale/unknown field {key}")
        elif row["value_sha256"] != expected[key]:
            errors.append(f"{label}: stale value hash for {key}")
        classification = row["classification"]
        if not isinstance(classification, str) or classification not in CLASSIFICATIONS:
            errors.append(f"{label}: unassigned/unknown classification")
        for field in ("derivation", "claim_limit"):
            if not isinstance(row[field], str) or not row[field].strip():
                errors.append(f"{label}: field-specific {field} required")
        ids = row["source_ids"]
        if not isinstance(ids, list) or not ids or not all(isinstance(item, str) for item in ids):
            errors.append(f"{label}: nonempty explicit source_ids required")
            continue
        if len(ids) != len(set(ids)):
            errors.append(f"{label}: duplicate source IDs")
        used_sources.update(ids)
        for source_id in ids:
            if source_id not in sources:
                errors.append(f"{label}: unresolved source {source_id}")
        kinds = {
            sources[item].get("kind")
            for item in ids
            if isinstance(sources.get(item), dict) and isinstance(sources[item].get("kind"), str)
        }
        required_kind = {
            "upstream_protocol": "upstream_file",
            "recorded_measurement": "measurement_record",
            "recorded_provenance": "provenance_record",
        }.get(classification if isinstance(classification, str) else "")
        if required_kind and required_kind not in kinds:
            errors.append(
                f"{label}: {classification} requires {required_kind}; "
                "implementation alone is insufficient"
            )
    errors.extend(f"missing field {key}" for key in sorted(set(expected) - seen))
    errors.extend(f"unused source {key}" for key in sorted(set(sources) - used_sources))
    return errors
