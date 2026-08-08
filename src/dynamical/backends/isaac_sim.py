"""Isaac Sim 5.1 target adapter for the supported Linux ARM64 route."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..schema import safe_usd_identifier
from ._extract import record_id, records
from ._runtime_pack import (
    emit_runtime_contract_files,
    runtime_campaign,
    runtime_role_assets,
    selected_operation_bindings,
)
from .base import (
    BackendArtifact,
    artifact_manifest,
    stage_reference,
    write_json_artifact,
    write_text_artifact,
)


def _launcher_text() -> str:
    return Path(__file__).with_name("isaac_runtime.py").read_text(encoding="utf-8")


def _asset_prim_path(asset: dict[str, Any]) -> str:
    workstation = safe_usd_identifier(str(asset.get("workstation_id", "unassigned")))
    asset_name = safe_usd_identifier(record_id(asset))
    return f"/Facility/Workstations/{workstation}/Assets/{asset_name}"


def _isaac_physics_layer(document: Any) -> str:
    """Emit standard USD Physics APIs for the portable Isaac target."""

    assets = sorted(records(document, "assets"), key=record_id)
    agent_assets = {str(agent.get("asset_id")) for agent in records(document, "agents")}
    lines = ["#usda 1.0", "", 'over "Facility"', "{", '    over "Workstations"', "    {"]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for asset in assets:
        grouped.setdefault(str(asset.get("workstation_id", "unassigned")), []).append(asset)
    for workstation_id in sorted(grouped):
        workstation = safe_usd_identifier(workstation_id)
        lines.extend(
            [
                f'        over "{workstation}"',
                "        {",
                '            over "Assets"',
                "            {",
            ]
        )
        for asset in grouped[workstation_id]:
            name = safe_usd_identifier(record_id(asset))
            physical = asset.get("physical_properties", {})
            mass = physical.get("mass_kg") if isinstance(physical, dict) else None
            is_agent = record_id(asset) in agent_assets
            is_static = any(
                word in str(asset.get("asset_kind", "")).lower()
                for word in ("table", "floor", "hood", "station", "glovebox")
            )
            apis = []
            if is_agent:
                apis.extend(["PhysicsArticulationRootAPI", "PhysicsRigidBodyAPI"])
            elif mass is not None and not is_static:
                apis.append("PhysicsRigidBodyAPI")
            if mass is not None and not is_static:
                apis.append("PhysicsMassAPI")
            if apis:
                rendered = ", ".join(f'"{api}"' for api in apis)
                lines.extend(
                    [
                        f'                over "{name}" (',
                        f"                    prepend apiSchemas = [{rendered}]",
                        "                )",
                        "                {",
                        "                    bool physics:rigidBodyEnabled = true",
                        "                    bool physics:kinematicEnabled = false",
                    ]
                )
                if mass is not None:
                    lines.append(f"                    float physics:mass = {float(mass):.12g}")
            else:
                lines.extend([f'                over "{name}"', "                {"])
            collision = asset.get("collision", {})
            enabled = bool(collision.get("enabled", True)) if isinstance(collision, dict) else True
            lines.extend(
                [
                    '                    over "Geometry" (',
                    '                        prepend apiSchemas = ["PhysicsCollisionAPI"]',
                    "                    )",
                    "                    {",
                    "                        bool physics:collisionEnabled = "
                    f"{str(enabled).lower()}",
                    "                    }",
                    "                }",
                ]
            )
        lines.extend(["            }", "        }"])
    lines.extend(["    }", "}", ""])
    return "\n".join(lines)


def _inject_physics_layer(root_path: Path) -> None:
    text = root_path.read_text(encoding="utf-8")
    marker = "    subLayers = [\n"
    entry = "        @./isaac_physics.usda@,\n"
    if entry in text:
        return
    if marker not in text:
        raise ValueError("compiled root stage has no sublayer list")
    root_path.write_text(text.replace(marker, marker + entry, 1), encoding="utf-8")


def _channel_units(document: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for device in records(document, "devices"):
        for channel in device.get("state_channels", []):
            if isinstance(channel, dict) and channel.get("id"):
                result[str(channel["id"])] = str(channel.get("unit", "1"))
    for material in records(document, "material_states"):
        for channel in material.get("initial_channels", []):
            if isinstance(channel, dict) and channel.get("channel_id"):
                result[str(channel["channel_id"])] = str(channel.get("unit", "1"))
    return result


def emit_isaac_sim(
    document: Any,
    output_dir: Path,
    *,
    ir_hash: str,
    stage_path: str | Path | None = None,
) -> list[BackendArtifact]:
    """Emit a target lock and a real Isaac Sim stage launcher."""

    assets = sorted(records(document, "assets"), key=record_id)
    capabilities = sorted(records(document, "capabilities"), key=record_id)
    channel_units = _channel_units(document)
    channels = sorted(channel_units)
    operation_bindings = selected_operation_bindings(output_dir)
    campaign = runtime_campaign(
        document,
        target="isaac",
        ir_hash=ir_hash,
        operation_bindings=operation_bindings,
    )
    runtime_ready = campaign["execution_status"] == "requires_external_runtime_gate"
    raw_document = (
        document.model_dump(mode="json", exclude_none=True)
        if hasattr(document, "model_dump")
        else dict(document)
    )
    facility = raw_document.get("facility", {})
    representative_full_facility = (
        isinstance(facility, dict) and facility.get("authoring_basis") == "representative"
    )
    role_assets = runtime_role_assets(document, operation_bindings=operation_bindings)
    asset_paths = {record_id(asset): _asset_prim_path(asset) for asset in assets}
    config = {
        "schema_version": "dynamical.isaac-sim-target.v1",
        "core_ir_sha256": ir_hash,
        "target": "isaac_sim",
        "isaac_sim_version": "5.1.0.0",
        "isaac_lab_version": "2.3.0",
        "torch": "2.9.0",
        "torchvision": "0.24.0",
        "pytorch_index": "https://download.pytorch.org/whl/cu130",
        "nvidia_index": "https://pypi.nvidia.com",
        "python": "3.11",
        "architecture": "linux-aarch64",
        "stage": stage_reference(output_dir, stage_path),
        "compiled_world_loading": {
            "contract": "verify all compile-manifest hashes before SimulationApp starts",
            "execution_world": "the composed Dynamical root.usda stage",
            "stage_open_api": "omni.usd.get_context().open_stage",
        },
        "physics": {
            "enabled": True,
            "gravity_m_s2": [0.0, 0.0, -9.81],
            "physics_dt_s": 0.008333333333333333,
            "render_dt_s": 0.016666666666666666,
            "collision_source": "facility_ir",
        },
        "asset_prim_paths": asset_paths,
        "role_prim_paths": {
            role: asset_paths[asset_id] for role, asset_id in sorted(role_assets.items())
        },
        "physics_layer": "isaac_physics.usda",
        "runtime_campaign": "runtime_campaign.json",
        "runtime_contract_module": "dynamical_runtime_contract.py",
        "capability_bindings": [
            {
                "capability_id": record_id(capability),
                "action_type": capability.get("action_type"),
                "status": (
                    "portable_fixed_joint_binding"
                    if capability.get("action_type") in {"pick", "place"}
                    else "compiled_stage_state_binding"
                    if capability.get("action_type") in {"set_heater", "wait", "observe"}
                    else "not_executable_in_portable_adapter"
                ),
            }
            for capability in capabilities
        ],
        "observation_bindings": [
            {
                "channel_id": channel_id,
                "unit": channel_units[channel_id],
                "status": (
                    "compiled_stage_state_binding"
                    if channel_id in {"heater.on", "heater.target_temperature_K"}
                    else "runtime_unavailable_until_task_binding"
                ),
            }
            for channel_id in channels
        ],
        "runtime_status": "ready_for_external_runtime_gate" if runtime_ready else "blocked",
        "runtime_blocker": None if runtime_ready else campaign.get("blocker"),
        "runtime_scope": (
            "selected_bounded_provider_only"
            if representative_full_facility and runtime_ready
            else "compiled_facility"
        ),
        "module_status": {
            "selected_bounded_provider": (
                "ready_for_external_runtime_gate" if runtime_ready else "blocked"
            ),
            "representative_full_facility": (
                "blocked"
                if representative_full_facility
                else "ready_for_external_runtime_gate"
                if runtime_ready
                else "blocked"
            ),
        },
        "full_facility_runtime_blocker": (
            "The portable stage contains representative full-laboratory assets without "
            "validated articulation tasks. Only the selected bounded provider subgraph "
            "can enter the external runtime gate."
            if representative_full_facility
            else None
        ),
        "claim_boundary": (
            "The launcher executes the portable physics bindings and writes a Dynamical trace. "
            "W1 still requires a validated Isaac Lab articulation task, render, replay, "
            "and visual inspection."
        ),
    }
    physics_artifact = write_text_artifact(
        output_dir,
        "isaac_physics_layer",
        "isaac_physics.usda",
        _isaac_physics_layer(document),
    )
    _inject_physics_layer(output_dir / "root.usda")
    artifacts = [
        write_json_artifact(output_dir, "backend_config", "backend_config.json", config),
        write_text_artifact(output_dir, "runtime_launcher", "run_isaac_sim.py", _launcher_text()),
        physics_artifact,
    ]
    runtime_artifacts, _ = emit_runtime_contract_files(
        document,
        output_dir,
        target="isaac",
        ir_hash=ir_hash,
    )
    artifacts.extend(runtime_artifacts)
    artifacts.append(
        write_json_artifact(
            output_dir,
            "backend_receipt",
            "backend_receipt.json",
            artifact_manifest("isaac_sim", ir_hash, artifacts),
        )
    )
    return artifacts
