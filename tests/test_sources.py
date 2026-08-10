"""An admitted source must carry a resolvable license and a real digest."""

import pytest
from pydantic import ValidationError

from dynamical.sources import AssetSource

BASE = {
    "id": "ac-well-cartridge-v44",
    "retrieval_uri": "https://zenodo.org/records/15575908",
    "sha256": "0d646ba7e37d1a441472ac165b40a2902be24b7fd2f5d5720133615a47432e76",
    "authority_id": "dynamical-release-authority",
}


def test_admitted_source_requires_a_license():
    with pytest.raises(ValidationError, match="admitted source requires a license"):
        AssetSource(**BASE, admission="admitted")


def test_admitted_source_with_spdx_id_is_valid():
    source = AssetSource(**BASE, admission="admitted", spdx_id="CC-BY-4.0")
    assert source.spdx_id == "CC-BY-4.0"


def test_license_ref_requires_evidence():
    with pytest.raises(ValidationError, match="license_ref requires license_evidence"):
        AssetSource(**BASE, admission="admitted", license_ref="zenodo-record-metadata")


def test_conflict_notes_survive_admission():
    source = AssetSource(
        **BASE,
        admission="admitted",
        license_ref="zenodo-record-metadata",
        license_evidence="Zenodo API metadata.license = {'id': 'cc-by-4.0'}",
        conflict_notes=[
            "archive contains no license text (grep over all 174 files, exit 1)",
            "github main carries MIT (DTU 2025), absent at tag 0.0.2",
        ],
    )
    assert len(source.conflict_notes) == 2


def test_unlicensed_source_cannot_be_admitted():
    with pytest.raises(ValidationError, match="unlicensed"):
        AssetSource(**BASE, admission="admitted", spdx_id="NOASSERTION")
