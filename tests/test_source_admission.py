"""Admission opens the artifact and digests it. A declared hash is not evidence."""

import hashlib
import json
from pathlib import Path

import pytest

from dynamical.source_admission import SourceAdmissionError, admit_sources
from dynamical.sources import AssetSource


def _write(root: Path, name: str, payload: bytes) -> str:
    (root / name).write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _source(name: str, digest: str, **kw) -> AssetSource:
    base = {
        "id": name,
        "retrieval_uri": f"https://example.invalid/{name}",
        "sha256": digest,
        "admission": "admitted",
        "authority_id": "test-authority",
        "spdx_id": "MIT",
    }
    base.update(kw)
    return AssetSource(**base)


def test_matching_digest_is_admitted(tmp_path):
    digest = _write(tmp_path, "part.stl", b"solid part\n")
    result = admit_sources([_source("part.stl", digest)], tmp_path)
    assert result["admitted"] == ["part.stl"]


def test_tampered_artifact_fails_closed(tmp_path):
    digest = _write(tmp_path, "part.stl", b"solid part\n")
    (tmp_path / "part.stl").write_bytes(b"solid tampered\n")
    with pytest.raises(SourceAdmissionError, match="digest mismatch"):
        admit_sources([_source("part.stl", digest)], tmp_path)


def test_missing_artifact_fails_closed(tmp_path):
    with pytest.raises(SourceAdmissionError, match="artifact is absent"):
        admit_sources([_source("gone.stl", "0" * 64)], tmp_path)


def test_unadmitted_source_is_refused(tmp_path):
    digest = _write(tmp_path, "ot2.step", b"deck\n")
    source = AssetSource(
        id="ot2.step",
        retrieval_uri="https://github.com/Opentrons/ot2",
        sha256=digest,
        admission="unlicensed",
        authority_id="test-authority",
        conflict_notes=["repo has no LICENSE; api.github.com/.../license returns 404"],
    )
    with pytest.raises(SourceAdmissionError, match="not admitted"):
        admit_sources([source], tmp_path)


def test_public_calibration_report_resolves_its_protocol_digest():
    root = Path("dynamical/bundle/calibration/ampere2-oer")
    report = json.loads((root / "calibration_report.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256((root / "frozen_protocol.json").read_bytes()).hexdigest()

    assert report["public_protocol_sha256"] == digest
