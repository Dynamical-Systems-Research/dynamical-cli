from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from dynamical.schema import (
    FacilityDocument,
    canonical_json_bytes,
    load_facility_manifest,
)

REPOSITORY = Path(__file__).resolve().parents[1]
MANIFEST = REPOSITORY / "dynamical" / "bundle" / "facility.yaml"


def _raw_manifest(path: Path = MANIFEST) -> dict[str, object]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_unknown_root_field_is_rejected() -> None:
    raw = _raw_manifest()
    raw["unknown_release_claim"] = True

    with pytest.raises(ValidationError, match="extra"):
        FacilityDocument.model_validate(raw)


def test_unknown_graph_reference_is_rejected() -> None:
    raw = _raw_manifest()
    devices = raw["devices"]
    assert isinstance(devices, list)
    assert isinstance(devices[0], dict)
    devices[0]["asset_id"] = "missing-asset"

    with pytest.raises(ValidationError, match="unknown asset"):
        FacilityDocument.model_validate(raw)


def test_target_adapter_changes_do_not_change_core_ir_hash() -> None:
    original = load_facility_manifest(MANIFEST)
    raw = original.model_dump(mode="json")
    raw["adapter_bindings"][0]["adapter_version"] = "0.1.1-test"
    changed = FacilityDocument.model_validate(raw)

    assert changed.core_ir_sha256() == original.core_ir_sha256()
    assert changed.adapter_pack_sha256("isaac") != original.adapter_pack_sha256("isaac")


@pytest.mark.parametrize(
    ("field_path", "value"),
    [
        (("assets", 0, "pose", "position_m", "x"), math.nan),
        (("assets", 0, "geometry", "dimensions_m", "x"), math.inf),
        (("devices", 0, "state_channels", 1, "minimum"), -math.inf),
    ],
)
def test_non_finite_physical_values_are_rejected(
    field_path: tuple[object, ...], value: float
) -> None:
    raw = _raw_manifest()
    target: object = raw
    for component in field_path[:-1]:
        target = target[component]  # type: ignore[index]
    target[field_path[-1]] = value  # type: ignore[index]

    with pytest.raises(ValidationError, match="finite"):
        FacilityDocument.model_validate(raw)

    with pytest.raises(ValueError, match="JSON compliant"):
        canonical_json_bytes({"value": value})


def test_channel_ids_are_unique_across_facility_providers() -> None:
    raw = _raw_manifest()
    raw["devices"][1]["state_channels"][0]["id"] = raw["devices"][0]["state_channels"][0]["id"]
    raw["devices"][1]["state_channels"][0]["unit"] = raw["devices"][0]["state_channels"][0]["unit"]

    with pytest.raises(ValidationError, match="channel IDs must be unique"):
        FacilityDocument.model_validate(raw)


def test_model_maps_use_declared_facility_channels() -> None:
    raw = _raw_manifest()
    raw["model_bindings"][0]["input_channel_map"]["missing.input"] = "model.input"
    raw["model_bindings"][0]["output_channel_map"]["model.output"] = "missing.output"

    with pytest.raises(ValidationError, match="unknown facility-side channels"):
        FacilityDocument.model_validate(raw)


def test_endpoint_capability_ids_exactly_match_provider_ownership() -> None:
    raw = _raw_manifest()
    raw["devices"][0]["capability_ids"].remove("dispense-electrolyte-capability")

    with pytest.raises(ValidationError, match="must match provider ownership"):
        FacilityDocument.model_validate(raw)
