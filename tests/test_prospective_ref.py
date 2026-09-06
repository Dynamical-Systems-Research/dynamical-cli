"""``CampaignRequirement.prospective_ref``: an optional pointer to the agent's
prospective record (the prediction an arm tests, the result that would change
the decision). Dynamical carries it unchanged in the saved requirement and the
compose receipt, and never evaluates it. Requirements without it compose to
the same artifact, hashes and receipt as before."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml
from _fixtures import write_reference_requirement
from pydantic import ValidationError
from test_composition import _request

from dynamical.cli import main
from dynamical.schema import CampaignRequirement

REF = {
    "sha256": "a" * 64,
    "label": "arm-03 prospective record",
    "path": "records/arm-03/prospective.json",
}


def _compose(tmp_path: Path, capsys, ref: dict[str, object] | None) -> tuple[dict, dict]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    requirement = write_reference_requirement(tmp_path / "requirement.yaml")
    if ref is not None:
        document = yaml.safe_load(requirement.read_text(encoding="utf-8"))
        document["prospective_ref"] = ref
        requirement.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    composition = tmp_path / "composition.json"
    assert main(["compose", str(requirement), "-o", str(composition)]) == 0
    receipt = json.loads(capsys.readouterr().out)
    saved = json.loads(composition.read_text(encoding="utf-8"))
    return receipt, saved


def test_requirement_without_ref_composes_as_before(tmp_path: Path, capsys) -> None:
    receipt, saved = _compose(tmp_path, capsys, None)

    assert receipt["status"] == "COMPILED"
    assert "prospective_ref" not in receipt
    assert "prospective_ref" not in saved["sources"]["requirement"]
    # the resolution hash covers source paths and differs per directory; the request
    # and composition hashes are content hashes and must not move
    again, saved_again = _compose(tmp_path / "again", capsys, None)
    assert again["composition_sha256"] == receipt["composition_sha256"]
    assert saved_again["request_sha256"] == saved["request_sha256"]


def test_ref_is_carried_unchanged_into_receipt_and_saved_requirement(
    tmp_path: Path, capsys
) -> None:
    receipt, saved = _compose(tmp_path, capsys, REF)
    plain, plain_saved = _compose(tmp_path / "plain", capsys, None)

    assert receipt["status"] == "COMPILED"
    assert receipt["prospective_ref"] == REF
    assert saved["sources"]["requirement"]["prospective_ref"] == REF
    # the reference is part of the requirement's identity, not of the composed laboratory
    assert saved["request_sha256"] != plain_saved["request_sha256"]
    assert (
        saved["virtual_sdl"]["virtual_sdl_sha256"]
        == plain_saved["virtual_sdl"]["virtual_sdl_sha256"]
    )
    assert main(["validate", str(tmp_path / "composition.json"), "--json"]) == 0
    capsys.readouterr()


def test_ref_roundtrips_through_the_schema() -> None:
    requirement = CampaignRequirement.model_validate(
        {**copy.deepcopy(_request()), "prospective_ref": REF}
    )

    assert requirement.prospective_ref is not None
    assert requirement.prospective_ref.model_dump(mode="json") == REF
    assert "prospective_ref" in requirement.model_dump(mode="json")
    plain = CampaignRequirement.model_validate(copy.deepcopy(_request()))
    assert plain.prospective_ref is None
    assert "prospective_ref" not in plain.model_dump(mode="json")


@pytest.mark.parametrize(
    "bad_ref",
    [
        {**REF, "sha256": "not-a-digest"},
        {**REF, "sha256": "A" * 64},
        {**REF, "label": ""},
        {**REF, "unexpected": "field"},
        {"label": "digest missing"},
    ],
)
def test_malformed_ref_is_rejected(bad_ref: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        CampaignRequirement.model_validate(
            {**copy.deepcopy(_request()), "prospective_ref": bad_ref}
        )
