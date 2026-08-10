from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from dynamical.campaign import trace_event_json_schema
from dynamical.composition import CompositionResult
from dynamical.schema import (
    CampaignRequirement,
    CapabilityRegistry,
    FacilityDocument,
    canonical_json_bytes,
    load_facility_manifest,
)

REPOSITORY = Path(__file__).resolve().parents[1]
MANIFEST = REPOSITORY / "manifests" / "ac-electrodeposition-cell.yaml"
SCHEMA_PATH = REPOSITORY / "schemas" / "facility.schema.json"
TRACE_SCHEMA_PATH = REPOSITORY / "schemas" / "campaign-trace.schema.json"
REGISTRY_SCHEMA_PATH = REPOSITORY / "schemas" / "capability-registry.schema.json"
REQUIREMENT_SCHEMA_PATH = REPOSITORY / "schemas" / "campaign-requirement.schema.json"
COMPOSITION_SCHEMA_PATH = REPOSITORY / "schemas" / "composition-result.schema.json"


def _raw_manifest(path: Path = MANIFEST) -> dict[str, object]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_required_facility_records_are_in_the_executable_schema() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert set(schema["$defs"]) >= {
        "Facility",
        "Workstation",
        "Asset",
        "Device",
        "Agent",
        "FacilityCapability",
        "MaterialState",
        "ModelBinding",
        "AdapterBinding",
        "Constraint",
        "CalibrationEvidence",
    }
    assert schema["additionalProperties"] is False


def test_published_schemas_equal_executable_schemas() -> None:
    facility_schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    trace_schema = json.loads(TRACE_SCHEMA_PATH.read_text(encoding="utf-8"))

    assert facility_schema == FacilityDocument.model_json_schema()
    assert trace_schema == trace_event_json_schema()


def test_published_capability_and_requirement_schemas_match_executable_models() -> None:
    registry_schema = json.loads(REGISTRY_SCHEMA_PATH.read_text(encoding="utf-8"))
    requirement_schema = json.loads(REQUIREMENT_SCHEMA_PATH.read_text(encoding="utf-8"))
    composition_schema = json.loads(COMPOSITION_SCHEMA_PATH.read_text(encoding="utf-8"))

    assert registry_schema == CapabilityRegistry.model_json_schema()
    assert requirement_schema == CampaignRequirement.model_json_schema()
    assert composition_schema == CompositionResult.model_json_schema()
    assert "provider_id" not in registry_schema["$defs"]["Capability"]["properties"]
    assert "providers" not in requirement_schema["properties"]


def test_electrodeposition_manifest_uses_one_z_up_meter_frame_and_named_quaternions() -> None:
    document = load_facility_manifest(MANIFEST)

    assert document.facility.frame.up_axis == "Z"
    assert document.facility.frame.length_unit == "m"
    assert document.facility.authoring_basis == "derived"
    for asset in document.assets:
        orientation = asset.pose.orientation
        assert orientation.model_dump() == {
            "w": orientation.w,
            "x": orientation.x,
            "y": orientation.y,
            "z": orientation.z,
        }


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


def test_devices_and_agents_reference_asset_identity_without_pose_duplication() -> None:
    document = load_facility_manifest(MANIFEST)

    for endpoint in [*document.devices, *document.agents]:
        dumped = endpoint.model_dump(mode="json")
        assert "asset_id" in dumped
        assert "geometry" not in dumped
        assert "pose" not in dumped


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


def test_ot2_and_arduino_proxies_do_not_intersect() -> None:
    document = load_facility_manifest(MANIFEST)
    assets = {asset.id: asset for asset in document.assets}
    workstations = {station.id: station for station in document.workstations}
    ot2 = assets["ot2-robot-body"]
    arduino = assets["arduino-controller-body"]
    ot2_station_x = workstations[ot2.workstation_id].local_frame.position_m.x
    arduino_station_x = workstations[arduino.workstation_id].local_frame.position_m.x

    ot2_max_x = ot2_station_x + ot2.pose.position_m.x + ot2.geometry.dimensions_m.x / 2
    arduino_min_x = (
        arduino_station_x + arduino.pose.position_m.x - arduino.geometry.dimensions_m.x / 2
    )

    assert ot2_max_x < arduino_min_x
