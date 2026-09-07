"""Exact sidecar bindings are necessary, but do not replace factual source review."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from _traceability import draft, terminal_fields, unique_mapping, validate

BUNDLE_ROOT = Path(__file__).resolve().parents[1] / "dynamical" / "bundle"


@pytest.mark.parametrize("name", ["reference-lab", "fastcat"])
def test_installed_bundle_field_traceability(name):
    bundle = BUNDLE_ROOT / name
    sidecar = bundle / "field-traceability.json"
    assert sidecar.is_file(), f"missing field evidence sidecar: {sidecar}"
    table = json.loads(sidecar.read_text(encoding="utf-8"), object_pairs_hook=unique_mapping)
    assert validate(bundle, table) == []
    # Current repository records are byte-bound as well as field-bound. Historical
    # revisions and external upstream records need their pinned source, not HEAD.
    repository = BUNDLE_ROOT.parents[1]
    for source in table["sources"].values():
        if source.get("revision") or not source["path"].startswith("dynamical/"):
            continue
        path = repository / source["path"]
        assert path.resolve().is_relative_to(repository.resolve())
        assert hashlib.sha256(path.read_bytes()).hexdigest() == source["sha256"], source
        if "line_end" in source["locator"]:
            assert source["locator"]["line_end"] <= len(path.read_text().splitlines()), source


@pytest.fixture
def traced_fixture(tmp_path):
    (tmp_path / "facility.yaml").write_text("facility:\n  id: fixture\n  empty: []\n")
    (tmp_path / "registry.yaml").write_text("providers: {}\n")
    table = draft(tmp_path)
    table["sources"] = {
        "fixture": {
            "kind": "implementation_file",
            "path": "fixture.py",
            "description": "Synthetic test policy only",
            "sha256": "a" * 64,
            "locator": {"line_start": 1, "line_end": 2},
        }
    }
    for row in table["rows"]:
        row.update(
            classification="dynamical_convention",
            source_ids=["fixture"],
            derivation="Synthetic test assignment.",
            claim_limit="Not physical evidence.",
        )
    return tmp_path, table


def test_exact_fixture_and_pointer_escaping(traced_fixture):
    bundle, table = traced_fixture
    assert validate(bundle, table) == []
    assert dict(terminal_fields({"a/b~": [None, [], {}]})) == {
        "/a~1b~0/0": None,
        "/a~1b~0/1": [],
        "/a~1b~0/2": {},
    }
    assert validate(bundle, draft(bundle))  # Unassigned drafts must fail closed.


@pytest.mark.parametrize(
    "mutation,message",
    [
        ("missing", "missing field"),
        ("duplicate", "duplicate field"),
        ("pointer", "stale/unknown field"),
        ("value", "stale value hash"),
        ("source", "unresolved source"),
        ("location", "exact line interval"),
        ("classification", "requires measurement_record"),
    ],
)
def test_invalid_bindings_fail_closed(traced_fixture, mutation, message):
    bundle, original = traced_fixture
    table = copy.deepcopy(original)
    if mutation == "missing":
        table["rows"].pop()
    elif mutation == "duplicate":
        table["rows"].append(table["rows"][0])
    elif mutation == "pointer":
        table["rows"][0]["pointer"] = "/invented"
    elif mutation == "value":
        # Mutate the document itself: the old receipt must cease to validate.
        (bundle / "facility.yaml").write_text("facility:\n  id: changed\n  empty: []\n")
    elif mutation == "source":
        table["rows"][0]["source_ids"] = ["missing"]
    elif mutation == "location":
        table["sources"]["fixture"]["locator"] = {}
    elif mutation == "classification":
        table["rows"][0]["classification"] = "recorded_measurement"
    assert any(message in error for error in validate(bundle, table))


def test_upstream_url_must_pin_revision(traced_fixture):
    bundle, table = traced_fixture
    source = table["sources"]["fixture"]
    source.update(
        kind="upstream_file",
        revision="2c5a911",
        uri="https://github.com/example/project/blob/main/fixture.py",
    )
    assert any("URL must embed" in error for error in validate(bundle, table))
    source["uri"] = "https://github.com/example/project/blob/2c5a911/fixture.py"
    assert validate(bundle, table) == []


def test_duplicate_source_keys_and_yaml_fields_rejected(traced_fixture):
    bundle, _ = traced_fixture
    with pytest.raises(ValueError, match="duplicate object key"):
        json.loads('{"sources":{},"sources":{}}', object_pairs_hook=unique_mapping)
    (bundle / "facility.yaml").write_text("id: one\nid: two\n")
    with pytest.raises(ValueError, match="duplicate object key"):
        draft(bundle)
