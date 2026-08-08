"""MATTERIX target adapter.

This adapter emits an auditable run contract.  It does not import Isaac Sim at
compile time and it never substitutes a Python process model for an embodied
runtime.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from ._extract import binding_payload, metadata, record_id, records, target_bindings
from ._runtime_pack import (
    emit_runtime_contract_files,
    runtime_campaign,
    selected_operation_bindings,
)
from .base import (
    MATTERIX_ASSETS_COMMIT,
    MATTERIX_COMMIT,
    BackendArtifact,
    artifact_manifest,
    stage_reference,
    write_json_artifact,
    write_text_artifact,
)

_PUBLIC_ASSETS = {
    "table": {
        "config_class": "matterix_assets.infrastructure.tables.TABLE_SEATTLE_INST_Cfg",
        "vendor_path": "isaac-nucleus://Props/Mounts/SeattleLabTable/table_instanceable.usd",
        "runtime_asset_name": "table",
    },
    "beaker": {
        "config_class": "matterix_assets.labware.beakers.BEAKER_500ML_INST_CFG",
        "vendor_path": "labware/beaker500ml/beaker-500ml-inst.usda",
        "runtime_asset_name": "beaker",
    },
    "heater": {
        "config_class": "matterix_assets.equipment.ika_plate.IKA_PLATE_INST_CFG",
        "vendor_path": "equipment/balance-heater-stirrer/IKA-plate-inst.usda",
        "runtime_asset_name": "ika_plate",
    },
    "franka": {
        "config_class": "matterix_assets.robots.FRANKA_PANDA_HIGH_PD_IK_CFG",
        "vendor_path": "isaac-nucleus://Robots/FrankaEmika/panda_instanceable.usd",
        "runtime_asset_name": "robot",
    },
}

_ACTION_BINDINGS = {
    "set_heater": "matterix_sm.TurnOnHeaterCfg",
    "pick": "matterix_sm.PickObjectCfg",
    "place": "matterix_sm.PlaceObjectCfg",
    "wait": "matterix_sm.WaitCfg",
    "observe": "matterix.envs.mdp observation terms",
}

_OBSERVATION_BINDINGS = {
    "heater.on": {
        "backend_symbol": "matterix.envs.mdp.object_is_heater_on(ika_plate)",
        "source_path": ["policy", "ika_plate_is_heater_on"],
        "unit": "1",
    },
    "heater.temperature_K": {
        "backend_symbol": "matterix.envs.mdp.object_temperature(ika_plate)",
        "source_path": ["policy", "ika_plate_temperature"],
        "unit": "K",
    },
    "beaker.temperature_K": {
        "backend_symbol": "matterix.envs.mdp.object_temperature(beaker)",
        "source_path": ["policy", "beaker_temperature"],
        "unit": "K",
    },
    "beaker.in_contact": {
        "backend_symbol": "matterix.envs.mdp.object_is_in_contact(beaker)",
        "source_path": ["policy", "beaker_is_in_contact"],
        "unit": "1",
    },
    "material.temperature_K": {
        "backend_symbol": "matterix.envs.mdp.object_temperature(beaker)",
        "source_path": ["policy", "beaker_temperature"],
        "unit": "K",
    },
}


def _asset_kind(asset: dict[str, Any]) -> str:
    values = [
        str(asset.get("asset_kind", "")),
        str(asset.get("kind", "")),
        str(asset.get("category", "")),
        record_id(asset),
        *[str(item) for item in asset.get("tags", []) if isinstance(asset.get("tags"), list)],
    ]
    text = " ".join(values).lower()
    if "beaker" in text:
        return "beaker"
    if "table" in text or "bench" in text:
        return "table"
    if "heater" in text or "ika" in text or "hotplate" in text:
        return "heater"
    if "franka" in text:
        return "franka"
    return "unresolved"


def named_quaternion_to_matterix_xyzw(quaternion: dict[str, Any]) -> list[float]:
    """Convert a named scalar-first IR quaternion to the documented MATTERIX order."""

    values = [
        float(quaternion.get("x", 0.0)),
        float(quaternion.get("y", 0.0)),
        float(quaternion.get("z", 0.0)),
        float(quaternion.get("w", 1.0)),
    ]
    if not math.isclose(sum(value * value for value in values), 1.0, abs_tol=1e-6):
        raise ValueError("quaternion must have unit norm")
    return values


def matterix_xyzw_to_named_quaternion(values: list[float]) -> dict[str, float]:
    """Convert the documented MATTERIX order back to named IR components."""

    if len(values) != 4:
        raise ValueError("MATTERIX quaternion must have four components")
    x, y, z, w = (float(value) for value in values)
    if not math.isclose(x * x + y * y + z * z + w * w, 1.0, abs_tol=1e-6):
        raise ValueError("quaternion must have unit norm")
    return {"w": w, "x": x, "y": y, "z": z}


def _pose(asset: dict[str, Any]) -> dict[str, Any]:
    pose = asset.get("pose", {}) if isinstance(asset.get("pose"), dict) else {}
    position_value = pose.get("position_m", pose.get("position", {}))
    position = position_value if isinstance(position_value, dict) else {}
    quaternion = pose.get("orientation", pose.get("quaternion", {}))
    if not isinstance(quaternion, dict):
        quaternion = {}
    w = float(quaternion.get("w", 1.0))
    x = float(quaternion.get("x", 0.0))
    y = float(quaternion.get("y", 0.0))
    z = float(quaternion.get("z", 0.0))
    return {
        "position_xyz_m": [
            float(position.get("x", 0.0)),
            float(position.get("y", 0.0)),
            float(position.get("z", 0.0)),
        ],
        "ir_quaternion_wxyz": {"w": w, "x": x, "y": y, "z": z},
        "matterix_rot_xyzw": named_quaternion_to_matterix_xyzw({"w": w, "x": x, "y": y, "z": z}),
        "rotation_contract": (
            "IR uses named components. Matterix public documentation uses xyzw. "
            "A live runtime pose probe is required before W1 admission."
        ),
    }


def _facility_info(document: Any) -> tuple[str, str]:
    raw = (
        document.model_dump(mode="json", exclude_none=True)
        if hasattr(document, "model_dump")
        else dict(document)
    )
    facility = raw.get("facility", {}) if isinstance(raw.get("facility"), dict) else {}
    basis = str(
        facility.get("authoring_basis")
        or metadata(facility).get("authoring_basis")
        or "unspecified"
    )
    return str(facility.get("id", "facility")), basis


def _capability_bindings(document: Any) -> list[dict[str, Any]]:
    result = []
    for capability in sorted(records(document, "capabilities"), key=record_id):
        action_type = str(capability.get("action_type", ""))
        symbol = _ACTION_BINDINGS.get(action_type)
        result.append(
            {
                "capability_id": record_id(capability),
                "action_type": action_type,
                "backend_symbol": symbol,
                "status": "bound" if symbol else "not_exposed_by_current_public_task",
            }
        )
    return result


def _observation_bindings(document: Any) -> list[dict[str, Any]]:
    channel_units: dict[str, str] = {}

    def declare_unit(channel_id: str, unit: str) -> None:
        previous = channel_units.get(channel_id)
        if previous is not None and previous != unit:
            raise ValueError(
                f"observation channel {channel_id!r} has conflicting units: "
                f"{previous!r} and {unit!r}"
            )
        channel_units[channel_id] = unit

    for device in records(document, "devices"):
        for channel in device.get("state_channels", []):
            if isinstance(channel, dict) and channel.get("id"):
                declare_unit(str(channel["id"]), str(channel["unit"]))
    for material in records(document, "material_states"):
        for channel in material.get("initial_channels", []):
            if isinstance(channel, dict) and channel.get("channel_id"):
                declare_unit(str(channel["channel_id"]), str(channel["unit"]))

    result = []
    for channel_id in sorted(channel_units):
        mapping = _OBSERVATION_BINDINGS.get(channel_id)
        declared_unit = channel_units[channel_id]
        if mapping and mapping.get("unit") != declared_unit:
            raise ValueError(
                f"MATTERIX observation mapping for {channel_id!r} uses "
                f"{mapping.get('unit')!r}, but the manifest declares {declared_unit!r}"
            )
        if mapping:
            status = "bound"
        elif channel_id in {"balance.mass_kg", "camera.average_v"}:
            status = "calibration_evidence_only"
        elif channel_id == "fluidA/CCCl":
            status = "not_exposed_by_current_public_task"
        else:
            status = "manifest_state_only"
        result.append(
            {
                "channel_id": channel_id,
                "backend_symbol": mapping.get("backend_symbol") if mapping else None,
                "source_path": mapping.get("source_path") if mapping else None,
                "unit": declared_unit,
                "status": status,
            }
        )
    return result


def _launcher_text() -> str:
    return Path(__file__).with_name("matterix_runtime.py").read_text(encoding="utf-8")


def _gate_launcher_text() -> str:
    return Path(__file__).with_name("matterix_gate.py").read_text(encoding="utf-8")


def emit_matterix(
    document: Any,
    output_dir: Path,
    *,
    ir_hash: str,
    stage_path: str | Path | None = None,
) -> list[BackendArtifact]:
    """Emit MATTERIX configuration and a guarded real-runtime launcher."""

    facility_id, authoring_basis = _facility_info(document)
    bindings = target_bindings(document, "matterix")
    explicit = binding_payload(bindings[0]) if bindings else {}
    explicit_paths = explicit.get("vendor_asset_paths", {})
    if not isinstance(explicit_paths, dict):
        explicit_paths = {}
    assets = sorted(records(document, "assets"), key=record_id)
    mapped_assets = []
    unresolved = []
    for asset in assets:
        kind = _asset_kind(asset)
        public = _PUBLIC_ASSETS.get(kind)
        item = {
            "asset_id": record_id(asset),
            "mapping_kind": kind,
            "pose": _pose(asset),
            "collision_enabled": bool(asset.get("collision", {}).get("enabled", True))
            if isinstance(asset.get("collision"), dict)
            else bool(asset.get("collision_enabled", True)),
            "manipulation_frames": asset.get("manipulation_frames", asset.get("frames", {})),
        }
        if public:
            item.update(public)
            if record_id(asset) in explicit_paths:
                item["vendor_path"] = str(explicit_paths[record_id(asset)])
            item["mapping_status"] = "public_vendor_asset"
        else:
            item["mapping_status"] = "representative_primitive_only"
            unresolved.append(record_id(asset))
        mapped_assets.append(item)

    campaign = runtime_campaign(
        document,
        target="matterix",
        ir_hash=ir_hash,
        operation_bindings=selected_operation_bindings(output_dir),
    )
    task_id = str(explicit.get("task_id") or "Matterix-Test-Semantics-Heat-Transfer-Franka-v1")
    workflow = str(explicit.get("workflow") or "pickup_and_place_beaker")
    runtime_ready = campaign["execution_status"] == "requires_external_runtime_gate"
    # MATTERIX runs the selected upstream provider task. It does not load the
    # composed Dynamical stage as a complete facility task, even when every bounded
    # workstation asset has a public mapping.
    full_facility_executable = False
    config = {
        "schema_version": "dynamical.matterix-target.v1",
        "facility_id": facility_id,
        "authoring_basis": authoring_basis,
        "core_ir_sha256": ir_hash,
        "target": "matterix",
        "target_revision": MATTERIX_COMMIT,
        "asset_revision": MATTERIX_ASSETS_COMMIT,
        "source_stage": stage_reference(output_dir, stage_path),
        "task_id": task_id,
        "workflow": workflow,
        "upstream_workflow_action_classes": [
            "WaitCfg",
            "TurnOnHeaterCfg",
            "PickObjectCfg",
            "PlaceObjectCfg",
            "WaitCfg",
            "TurnOnHeaterCfg",
            "WaitCfg",
        ]
        if runtime_ready
        else [],
        "workflow_parameter_overrides": {
            "TurnOnHeaterCfg.target_temperature": {
                "upstream_value_k": 373.15,
                "compiled_value_k": 343.15,
                "reason": "the facility manifest maximum is 343.15 K",
            }
        }
        if runtime_ready
        else {},
        "compiled_stage": stage_reference(output_dir, stage_path),
        "compiled_world_loading": {
            "contract": "verify all compile-manifest hashes before AppLauncher starts",
            "execution_world": "selected upstream MATTERIX task",
            "stage_role": (
                "The composed Dynamical stage is the layout and identity contract. MATTERIX "
                "executes the mapped upstream task because it does not load this stage as "
                "a task environment."
            ),
        },
        "runtime_campaign": "runtime_campaign.json",
        "runtime_contract_module": "dynamical_runtime_contract.py",
        "runtime_child_launcher": "run_matterix.py",
        "runtime_gate_launcher": "run_matterix_gate.py",
        "asset_mappings": mapped_assets,
        "capability_bindings": _capability_bindings(document),
        "observation_bindings": _observation_bindings(document),
        "constraint_bindings": [
            {
                "constraint_id": record_id(item),
                "verifier_binding_id": item.get("verifier_binding_id"),
                "status": "independent_dynamical_runtime",
            }
            for item in sorted(records(document, "constraints"), key=record_id)
        ],
        "unresolved_vendor_assets": unresolved,
        "module_status": {
            "selected_bounded_provider": (
                "ready_for_external_runtime_gate" if runtime_ready else "blocked"
            ),
            "representative_full_facility": (
                "ready_for_external_runtime_gate" if full_facility_executable else "blocked"
            ),
        },
        "runtime_scope": (
            "full_facility" if full_facility_executable else "selected_bounded_provider_only"
        ),
        "runtime_status": "ready_for_external_runtime_gate" if runtime_ready else "blocked",
        "runtime_blocker": None
        if runtime_ready
        else (
            campaign.get("blocker")
            or "The selected public MATTERIX capability provider cannot execute."
        ),
        "full_facility_runtime_blocker": None
        if full_facility_executable
        else (
            "The public MATTERIX asset commit and task registry do not execute the "
            "complete representative facility. Any run is limited to the selected "
            "bounded public capability provider."
        ),
        "claim_boundary": (
            "This target configuration proves deterministic backend translation only. "
            "W1 requires a separately preserved MATTERIX render, workflow trace, "
            "replay, and inspection."
        ),
    }
    runtime_contract = {
        "schema_version": "dynamical.runtime-contract.v1",
        "matterix_commit": MATTERIX_COMMIT,
        "matterix_assets_commit": MATTERIX_ASSETS_COMMIT,
        "python": "3.11",
        "isaac_sim": "5.1.0.0",
        "isaac_lab": "2.3.0",
        "torch": "2.9.0",
        "torchvision": "0.24.0",
        "pytorch_index": "https://download.pytorch.org/whl/cu130",
        "nvidia_index": "https://pypi.nvidia.com",
        "platform": "linux-aarch64-nvidia",
        "minimum_available_memory_gib": 32,
        "stock_gate": {
            "runner": "runtime/matterix_stock_runner.py",
            "task_id": "Matterix-Test-Beaker-Lift-Franka-v1",
            "workflow": "pickup_beaker",
            "render": "balanced",
            "headless": True,
            "cameras": True,
        },
    }
    artifacts = [
        write_json_artifact(output_dir, "backend_config", "backend_config.json", config),
        write_json_artifact(
            output_dir, "runtime_contract", "runtime_contract.json", runtime_contract
        ),
        write_text_artifact(output_dir, "runtime_launcher", "run_matterix.py", _launcher_text()),
        write_text_artifact(
            output_dir,
            "runtime_gate_launcher",
            "run_matterix_gate.py",
            _gate_launcher_text(),
        ),
    ]
    runtime_artifacts, _ = emit_runtime_contract_files(
        document,
        output_dir,
        target="matterix",
        ir_hash=ir_hash,
    )
    artifacts.extend(runtime_artifacts)
    artifacts.append(
        write_json_artifact(
            output_dir,
            "backend_receipt",
            "backend_receipt.json",
            artifact_manifest("matterix", ir_hash, artifacts),
        )
    )
    return artifacts
