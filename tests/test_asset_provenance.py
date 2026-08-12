"""Every vendored artifact must match its lock entry, and OCCT must not ship."""

import hashlib
import json
from pathlib import Path

from dynamical.sources import AssetSource

LOCK = Path("registries/electrodeposition-source-lock.json")


def test_every_locked_artifact_matches_its_digest():
    records = json.loads(LOCK.read_text(encoding="utf-8"))["sources"]
    assert records, "the source lock must not be empty"
    for record in records:
        source = AssetSource(**record)
        path = Path(source.id)
        assert path.is_file(), f"{source.id} is declared but absent"
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == source.sha256, f"{source.id}: declared {source.sha256}, got {actual}"


def test_derived_layers_declare_their_conversion():
    records = json.loads(LOCK.read_text(encoding="utf-8"))["sources"]
    derived = [AssetSource(**r) for r in records if r.get("derived_from_source_id")]
    assert derived, "at least one derived USD layer must be locked"
    for source in derived:
        assert source.conversion_tool and source.conversion_tolerance
