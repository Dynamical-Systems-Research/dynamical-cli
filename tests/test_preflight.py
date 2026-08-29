from __future__ import annotations

import copy
import importlib.util
import json
from argparse import Namespace
from pathlib import Path

import pytest
import yaml

from dynamical.cli import DEFAULT_FACILITY, DEFAULT_REGISTRY, main
from dynamical.composition import (
    compose_files,
    load_preflight_binding,
    preflight_state_sha256,
    validate_composition_result,
    write_composition_result,
)
from dynamical.schema import load_capability_registry

REPOSITORY = Path(__file__).resolve().parents[1]
REQUIREMENT = REPOSITORY / "examples/quickstart/requirement.yaml"
FINALIZER_PATH = REPOSITORY / "skills/dynamical-preflight/scripts/validate_receipt.py"
SPEC = importlib.util.spec_from_file_location("dynamical_preflight_finalizer", FINALIZER_PATH)
assert SPEC is not None and SPEC.loader is not None
FINALIZER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FINALIZER)


def _mapping(source: Path, *, value: float = 1.0) -> dict[str, object]:
    return {
        "created_at_utc": "2026-08-29T12:00:00Z",
        "discovery_roots": ["records"],
        "sources": [
            {
                "ref": "records",
                "path": str(source),
                "available_at": "2026-08-29T11:00:00Z",
                "disposition": "state",
                "owner": "example-lab",
                "license": "CC-BY-4.0",
                "reduction_level": "raw-enough detector record",
            }
        ],
        "entities": [
            {
                "ref": "sample",
                "kind": "sample",
                "source_native_ids": {"sample_id": "sample-1", "lot_id": "lot-1"},
                "evidence_refs": [{"source_ref": "records", "locator": "/sample"}],
            },
            {
                "ref": "instrument",
                "kind": "instrument",
                "source_native_ids": {"instrument_id": "balance-1"},
                "evidence_refs": [{"source_ref": "records", "locator": "/instrument"}],
            },
        ],
        "facts": [
            {
                "ref": "mass",
                "subject_ref": "sample",
                "field": "mass",
                "value": value,
                "kind": "readback",
                "unit": "g",
                "uncertainty": {"standard": 0.01, "unit": "g"},
                "observed_at": "2026-08-29T10:59:00Z",
                "available_at": "2026-08-29T11:00:00Z",
                "state_path": "/sample/mass",
                "material_effects": ["decision", "reconstruction"],
                "evidence_refs": [{"source_ref": "records", "locator": "/mass"}],
            }
        ],
        "relations": [
            {
                "ref": "measured-by",
                "subject_ref": "sample",
                "predicate": "measured_by",
                "object_ref": "instrument",
                "status": "verified",
                "available_at": "2026-08-29T11:00:00Z",
                "state_defining": True,
                "evidence_refs": [{"source_ref": "records", "locator": "/run"}],
            }
        ],
        "gaps": [],
    }


def _finalize(mapping: dict[str, object], mapping_path: Path) -> dict[str, object]:
    args = Namespace(
        requirement=REQUIREMENT,
        registry=DEFAULT_REGISTRY,
        facility=DEFAULT_FACILITY,
    )
    return FINALIZER.finalize(mapping, mapping_path, args)


def _write_case(tmp_path: Path, *, value: float = 1.0) -> tuple[Path, Path]:
    source = tmp_path / "records.json"
    source.write_text(json.dumps({"mass": value, "sample": "sample-1"}), encoding="utf-8")
    mapping_path = tmp_path / "mapping.json"
    receipt = _finalize(_mapping(source, value=value), mapping_path)
    receipt_path = tmp_path / "preflight.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    return source, receipt_path


def test_finalizer_writes_one_fact_graph_and_ready_state(tmp_path: Path) -> None:
    _, receipt_path = _write_case(tmp_path)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    assert receipt["status"] == "READY"
    assert receipt["next_action"] == {"action": "compose"}
    assert receipt["facts"][0]["state_path"] == "/sample/mass"
    assert receipt["relations"][0]["predicate"] == "measured_by"
    assert receipt["state"]["state_sha256"] == preflight_state_sha256(receipt)
    assert not {
        "identity_bindings",
        "source_facts",
        "derived_values",
        "assumptions",
        "state_values",
        "initial_state",
        "state_value_bindings",
        "source_coverage",
        "provider_contexts",
        "questions",
    } & set(receipt)


def test_equivalent_source_layout_has_the_same_scientific_state(tmp_path: Path) -> None:
    first = tmp_path / "layout-a/records.json"
    second = tmp_path / "layout-b/data.json"
    first.parent.mkdir()
    second.parent.mkdir()
    payload = json.dumps({"mass": 1.0, "sample": "sample-1"})
    first.write_text(payload, encoding="utf-8")
    second.write_text(payload, encoding="utf-8")

    left = _finalize(_mapping(first), tmp_path / "left.json")
    right = _finalize(_mapping(second), tmp_path / "right.json")

    assert left["state"]["state_sha256"] == right["state"]["state_sha256"]
    assert left["sources"] != right["sources"]


def test_material_change_and_parent_change_state_identity(tmp_path: Path) -> None:
    source = tmp_path / "records.json"
    source.write_text("{}", encoding="utf-8")
    baseline = _finalize(_mapping(source), tmp_path / "mapping.json")
    changed = _finalize(_mapping(source, value=2.0), tmp_path / "mapping.json")
    branched_mapping = _mapping(source)
    branched_mapping["parent"] = {
        "state_id": baseline["state"]["state_id"],
        "state_sha256": baseline["state"]["state_sha256"],
        "receipt_sha256": "a" * 64,
    }
    branched = _finalize(branched_mapping, tmp_path / "mapping.json")

    assert baseline["state"]["state_sha256"] != changed["state"]["state_sha256"]
    assert baseline["state"]["state_sha256"] != branched["state"]["state_sha256"]
    assert baseline["state"]["parent"] is None


def test_material_gap_holds_and_preserves_known_state(tmp_path: Path) -> None:
    source = tmp_path / "records.json"
    source.write_text("{}", encoding="utf-8")
    mapping = _mapping(source)
    mapping["gaps"] = [
        {
            "ref": "calibration-gap",
            "subject_ref": "instrument",
            "reason": "current calibration record is missing",
            "material": True,
            "material_effects": ["evidence_class", "reconstruction"],
            "available_at": "2026-08-29T11:00:00Z",
            "release_condition": "supply the calibration record",
            "question": "Where is the current calibration record?",
            "next_route": "HOLD",
        }
    ]
    receipt = _finalize(mapping, tmp_path / "mapping.json")

    assert receipt["status"] == "HOLD"
    assert receipt["facts"]
    assert receipt["state"]["fact_ids"]
    assert receipt["gaps"][0]["question"].startswith("Where")
    assert receipt["next_action"] == {"action": "HOLD"}


def test_material_gap_without_route_returns_hold(tmp_path: Path) -> None:
    source = tmp_path / "records.json"
    source.write_text("{}", encoding="utf-8")
    mapping = _mapping(source)
    mapping["gaps"] = [
        {
            "ref": "calibration-gap",
            "material": True,
            "available_at": "2026-08-29T11:00:00Z",
            "release_condition": "supply the calibration record",
        }
    ]

    receipt = _finalize(mapping, tmp_path / "mapping.json")

    assert receipt["status"] == "HOLD"
    assert receipt["next_action"] == {"action": "HOLD"}


@pytest.mark.parametrize(
    ("section", "key", "message"),
    [
        ("facts", "field", "needs field and value"),
        ("facts", "value", "needs field and value"),
        ("relations", "predicate", "needs predicate"),
    ],
)
def test_incomplete_semantic_records_cannot_produce_ready(
    tmp_path: Path, section: str, key: str, message: str
) -> None:
    source = tmp_path / "records.json"
    source.write_text("{}", encoding="utf-8")
    mapping = _mapping(source)
    mapping[section][0].pop(key)

    with pytest.raises(ValueError, match=message):
        _finalize(mapping, tmp_path / "mapping.json")


def test_finalizer_rejects_ambiguous_or_unsourced_state(tmp_path: Path) -> None:
    source = tmp_path / "records.json"
    source.write_text("{}", encoding="utf-8")
    mapping = _mapping(source)
    mapping["facts"].append({**mapping["facts"][0], "ref": "duplicate-mass", "value": 2.0})
    with pytest.raises(ValueError, match="state paths must be unique"):
        _finalize(mapping, tmp_path / "mapping.json")

    mapping = _mapping(source)
    mapping["facts"][0]["evidence_refs"] = []
    with pytest.raises(ValueError, match="need evidence"):
        _finalize(mapping, tmp_path / "mapping.json")


def test_finalizer_rejects_unsourced_frozen_entity(tmp_path: Path) -> None:
    source = tmp_path / "records.json"
    source.write_text("{}", encoding="utf-8")
    mapping = _mapping(source)
    mapping["entities"][0]["evidence_refs"] = []

    with pytest.raises(ValueError, match="entities need evidence"):
        _finalize(mapping, tmp_path / "mapping.json")


def test_finalizer_rejects_unsourced_derivation_cycle(tmp_path: Path) -> None:
    source = tmp_path / "records.json"
    source.write_text("{}", encoding="utf-8")
    mapping = _mapping(source)
    mapping["facts"] = [
        {
            **mapping["facts"][0],
            "ref": "derived-a",
            "kind": "derived",
            "evidence_refs": [],
            "input_refs": ["derived-b"],
        },
        {
            **mapping["facts"][0],
            "ref": "derived-b",
            "field": "derived_b",
            "kind": "derived",
            "state_path": None,
            "evidence_refs": [],
            "input_refs": ["derived-a"],
        },
    ]

    with pytest.raises(ValueError, match="cyclic or unsourced"):
        _finalize(mapping, tmp_path / "mapping.json")


def test_finalizer_compares_cutoff_as_an_instant(tmp_path: Path) -> None:
    source = tmp_path / "records.json"
    source.write_text("{}", encoding="utf-8")
    mapping = _mapping(source)
    mapping["requested_cutoff"] = "2026-08-29T11:00:00Z"
    mapping["facts"][0]["available_at"] = "2026-08-29T11:00:00.500000Z"

    with pytest.raises(ValueError, match="later evidence"):
        _finalize(mapping, tmp_path / "mapping.json")


def test_loader_recomputes_ready_invariants(tmp_path: Path) -> None:
    source = tmp_path / "records.json"
    source.write_text("{}", encoding="utf-8")
    mapping = _mapping(source)
    mapping["gaps"] = [
        {
            "ref": "calibration-gap",
            "material": True,
            "available_at": "2026-08-29T11:00:00Z",
            "release_condition": "supply the calibration record",
        }
    ]
    receipt = _finalize(mapping, tmp_path / "mapping.json")
    receipt.update(status="READY", next_action={"action": "compose"})
    receipt_path = tmp_path / "preflight.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(ValueError, match="material gap"):
        load_preflight_binding(
            receipt_path,
            REQUIREMENT,
            DEFAULT_REGISTRY,
            DEFAULT_FACILITY,
        )

    assumption = _mapping(source)
    assumption["facts"][0]["kind"] = "assumption"
    receipt = _finalize(assumption, tmp_path / "mapping.json")
    receipt.update(status="READY", next_action={"action": "compose"})
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(ValueError, match="state assumption"):
        load_preflight_binding(
            receipt_path,
            REQUIREMENT,
            DEFAULT_REGISTRY,
            DEFAULT_FACILITY,
        )


def test_preflight_binds_supplied_registry_before_trusted_demotion(tmp_path: Path, capsys) -> None:
    registry = yaml.safe_load(DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    proposal = copy.deepcopy(registry["providers"][0])
    proposal["provider_id"] = f"proposal-{proposal['provider_id']}"
    registry["providers"].append(proposal)
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")

    source = tmp_path / "records.json"
    source.write_text("{}", encoding="utf-8")
    args = Namespace(
        requirement=REQUIREMENT,
        registry=registry_path,
        facility=DEFAULT_FACILITY,
    )
    receipt = FINALIZER.finalize(_mapping(source), tmp_path / "mapping.json", args)
    receipt_path = tmp_path / "preflight.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    binding = load_preflight_binding(
        receipt_path,
        REQUIREMENT,
        registry_path,
        DEFAULT_FACILITY,
    )

    result = compose_files(
        REQUIREMENT,
        registry_path,
        DEFAULT_FACILITY,
        installed_registry=load_capability_registry(DEFAULT_REGISTRY),
        preflight_binding=binding,
    )

    assert result.status == "COMPILED"
    assert result.sources is not None
    assert result.sources.registry_sha256 != binding.registry_sha256
    assert result.sources.preflight is not None
    assert result.sources.preflight.supplied_registry_sha256 == binding.registry_sha256
    assert result.sources.preflight.registry_sha256 == result.sources.registry_sha256
    validate_composition_result(result)
    demoted = next(
        item
        for item in result.sources.registry.providers
        if item.provider_id.startswith("proposal-")
    )
    assert demoted.admission.status == "pending"

    composition_path = tmp_path / "composition.json"
    write_composition_result(composition_path, result)
    assert main(["compile", str(composition_path), "-o", str(tmp_path / "compiled")]) == 0
    capsys.readouterr()


def test_loader_rejects_receipt_without_evidence_sources(tmp_path: Path) -> None:
    _, receipt_path = _write_case(tmp_path)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["sources"] = []
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(ValueError, match="unresolved sources"):
        load_preflight_binding(
            receipt_path,
            REQUIREMENT,
            DEFAULT_REGISTRY,
            DEFAULT_FACILITY,
        )


def test_ready_receipt_binds_to_composition_and_stale_source_fails(tmp_path: Path) -> None:
    source, receipt_path = _write_case(tmp_path)
    binding = load_preflight_binding(
        receipt_path,
        REQUIREMENT,
        DEFAULT_REGISTRY,
        DEFAULT_FACILITY,
    )
    result = compose_files(
        REQUIREMENT,
        DEFAULT_REGISTRY,
        DEFAULT_FACILITY,
        installed_registry=load_capability_registry(DEFAULT_REGISTRY),
        preflight_binding=binding,
    )

    assert result.status == "COMPILED"
    assert result.sources is not None and result.sources.preflight == binding
    validate_composition_result(result)

    source.write_text('{"changed":true}', encoding="utf-8")
    with pytest.raises(ValueError, match="source changed"):
        load_preflight_binding(
            receipt_path,
            REQUIREMENT,
            DEFAULT_REGISTRY,
            DEFAULT_FACILITY,
        )


def test_cli_returns_compact_preflight_binding(tmp_path: Path, capsys) -> None:
    _, receipt_path = _write_case(tmp_path)
    output = tmp_path / "composition.json"

    assert (
        main(["compose", str(REQUIREMENT), "--preflight", str(receipt_path), "-o", str(output)])
        == 0
    )
    receipt = json.loads(capsys.readouterr().out)

    assert output.is_file()
    assert set(receipt["preflight"]) == {
        "receipt_sha256",
        "state_id",
        "state_sha256",
        "evidence_cutoff",
    }
    assert "facts" not in receipt
