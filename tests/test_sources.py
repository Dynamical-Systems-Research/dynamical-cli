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


def test_license_ref_requires_evidence():
    with pytest.raises(ValidationError, match="license_ref requires license_evidence"):
        AssetSource(**BASE, admission="admitted", license_ref="zenodo-record-metadata")


def test_unlicensed_source_cannot_be_admitted():
    with pytest.raises(ValidationError, match="unlicensed"):
        AssetSource(**BASE, admission="admitted", spdx_id="NOASSERTION")
