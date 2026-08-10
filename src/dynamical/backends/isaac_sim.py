"""Isaac Sim 5.1 target adapter for the supported Linux ARM64 route."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..schema import safe_usd_identifier
from ._extract import record_id, records
from ._runtime_pack import (
    emit_runtime_contract_files,
    runtime_campaign,
    runtime_role_assets,
    selected_operation_bindings,
    selected_proof_requirements,
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


_SUBLAYERS_BLOCK_RE = re.compile(r"(?P<prefix>subLayers\s*=\s*)\[(?P<body>.*?)\]", re.DOTALL)
_LAYER_TOKEN_RE = re.compile(r"@[^@]*@")


def _inject_physics_layer(root_path: Path) -> None:
    """Prepend the isaac physics overlay to root.usda's declared sublayer list.

    Parses the ``subLayers = [ ... ]`` array by its real tokens (each
    ``@path@`` asset-path reference), not by matching ``root_layer()``'s
    exact current whitespace/indentation, so a reformatting of that function
    cannot silently turn this into a no-op.
    """

    text = root_path.read_text(encoding="utf-8")
    match = _SUBLAYERS_BLOCK_RE.search(text)
    if match is None:
        raise ValueError("compiled root stage has no sublayer list")
    entry = "@./isaac_physics.usda@"
    layers = _LAYER_TOKEN_RE.findall(match.group("body"))
    if not layers:
        raise ValueError("compiled root stage sublayer list is empty")
    if entry in layers:
        return
    layers.insert(0, entry)
    rendered = ",\n".join(f"        {layer}" for layer in layers)
    new_block = f"{match.group('prefix')}[\n{rendered}\n    ]"
    root_path.write_text(text[: match.start()] + new_block + text[match.end() :], encoding="utf-8")


def _record_channel(
    bindings: dict[str, dict[str, str | None]],
    channel_id: str,
    unit: str,
    device_asset_id: str | None,
) -> None:
    existing = bindings.get(channel_id)
    if existing is None:
        bindings[channel_id] = {"unit": unit, "device_asset_id": device_asset_id}
        return
    if existing["unit"] != unit:
        # Fail closed rather than silently keep whichever declaration was seen
        # first.
        raise ValueError(
            f"channel {channel_id!r} is declared with conflicting units: "
            f"{existing['unit']!r} and {unit!r}"
        )
    if existing["device_asset_id"] is None and device_asset_id is not None:
        existing["device_asset_id"] = device_asset_id


def _channel_bindings(document: Any) -> dict[str, dict[str, str | None]]:
    """Map each declared channel id to its unit and the asset of the device that owns it.

    A material-state channel (no owning device) keeps ``device_asset_id`` as
    ``None``, so it can never be classified as backend-state-bound below.
    """

    bindings: dict[str, dict[str, str | None]] = {}
    for device in records(document, "devices"):
        asset_id = str(device.get("asset_id") or "") or None
        for channel in device.get("state_channels", []):
            if isinstance(channel, dict) and channel.get("id"):
                _record_channel(
                    bindings, str(channel["id"]), str(channel.get("unit", "1")), asset_id
                )
    for material in records(document, "material_states"):
        for channel in material.get("initial_channels", []):
            if isinstance(channel, dict) and channel.get("channel_id"):
                _record_channel(
                    bindings, str(channel["channel_id"]), str(channel.get("unit", "1")), None
                )
    return bindings


def _channel_owner_index(document: Any) -> dict[str, list[dict[str, Any]]]:
    """Map each declared channel id to every capability whose
    ``observation_channel_ids`` names it.

    A channel can be shared: e.g. ``squidstat.current_density_a_cm2`` is both
    an ``electrodeposit`` output (a late entry in its channel list) and the
    ``measure`` capability's own sole, primary requested value. A list (not
    "first one wins") lets ``_observation_binding`` find whichever owner, if
    any, actually treats this channel as its primary echoed value.
    """

    index: dict[str, list[dict[str, Any]]] = {}
    for capability in records(document, "capabilities"):
        for channel_id in capability.get("observation_channel_ids", []) or []:
            index.setdefault(str(channel_id), []).append(capability)
    return index


def _always_present_parameter_names(
    campaign_actions: list[dict[str, Any]], action_type: str
) -> set[str]:
    """Parameter names guaranteed present on every compiled action of ``action_type``.

    A compiled campaign's actions are already fully resolved -- fixed concrete
    parameter values, not a runtime-variable schema -- so this is knowable
    exactly, not merely hoped for: it is the intersection of parameter names
    (with non-null values) actually carried by every one of this specific
    compiled campaign's own instances of ``action_type`` (there can be more
    than one, e.g. two ``transfer`` steps with different parameter sets).
    """

    instances = [
        action["parameters"]
        for action in campaign_actions
        if action.get("kind") == action_type and isinstance(action.get("parameters"), dict)
    ]
    if not instances:
        return set()
    present = {name for name, value in instances[0].items() if value is not None}
    for parameters in instances[1:]:
        present &= {name for name, value in parameters.items() if value is not None}
    return present


def _paired_channels(
    capability: dict[str, Any], always_present_parameters: set[str]
) -> dict[str, str]:
    """Map a capability's declared observation channels to the requested
    parameter each one can honestly echo from Isaac's compiled world.

    Isaac has no domain model for this facility's chemistry (dispensed/applied
    volume, deposited mass, overpotential, ...), so it cannot honestly report
    most declared state_channels. What it does know, deterministically, every
    time an action executes, is the parameters the compiled campaign commanded
    it with. Which channel echoes which parameter is read straight from the
    capability's own declared ``echoed_parameter_bindings`` (schema.py's
    ``FacilityCapability``) -- not inferred by pairing ``observation_channel_ids``
    and ``parameters`` by list position, which silently binds the wrong value
    the moment either list is reordered or a capability gains a second
    same-typed parameter. Only a binding whose named parameter is in
    ``always_present_parameters`` (this specific compiled campaign's own
    instances of this action all supply it) is honoured: a schema-optional
    parameter this particular campaign happens not to always supply stays
    unavailable rather than promising a "bound" channel that could go missing
    on some action instance.
    """

    bindings = capability.get("echoed_parameter_bindings") or {}
    return {
        str(channel_id): str(name)
        for channel_id, name in bindings.items()
        if name in always_present_parameters
    }


def _observation_binding(
    channel_id: str,
    binding: dict[str, str | None],
    owners: list[dict[str, Any]],
    bound_action_types: set[str],
    campaign_actions: list[dict[str, Any]],
) -> dict[str, Any]:
    """One declared channel's compiled observation-binding record.

    A channel can be listed by more than one capability (see
    ``_channel_owner_index``); ``status`` is ``compiled_stage_state_binding``
    only when at least one of those capabilities pairs this channel with a
    parameter this compiled campaign always supplies for that action (see
    ``_paired_channels`` / ``_always_present_parameter_names``) *and* that
    capability's action_type is one this composition actually selected
    (``bound_action_types``, i.e. it appears in ``role_prim_paths``) -- the
    one case a live Isaac run can honestly promise a real value for, every
    time that capability's own action executes. A sibling capability that
    merely shares a device with a selected one (e.g. ``aliquot`` sharing
    ``ot2-device`` with a selected ``dispense``) is not itself selected, so
    its channel is not marked bound either. Every other channel stays
    ``runtime_unavailable_until_task_binding``.
    """

    paired_owner = None
    echoes_parameter = None
    for owner in owners:
        action_type = owner.get("action_type")
        if action_type not in bound_action_types:
            continue
        always_present = _always_present_parameter_names(campaign_actions, action_type)
        pairs = _paired_channels(owner, always_present)
        if channel_id in pairs:
            paired_owner = owner
            echoes_parameter = pairs[channel_id]
            break
    action_type = (
        paired_owner.get("action_type")
        if paired_owner is not None
        else (owners[0].get("action_type") if owners else None)
    )
    return {
        "channel_id": channel_id,
        "unit": binding["unit"],
        "action_type": action_type,
        "echoes_parameter": echoes_parameter,
        "status": (
            "compiled_stage_state_binding"
            if paired_owner is not None
            else "runtime_unavailable_until_task_binding"
        ),
    }


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
    channel_bindings = _channel_bindings(document)
    channels = sorted(channel_bindings)
    channel_owners = _channel_owner_index(document)
    operation_bindings = selected_operation_bindings(output_dir)
    campaign = runtime_campaign(
        document,
        target="isaac",
        ir_hash=ir_hash,
        operation_bindings=operation_bindings,
        proof_requirements=selected_proof_requirements(output_dir),
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
    bound_action_types = set(role_assets)
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
                ),
            }
            for capability in capabilities
        ],
        "observation_bindings": [
            _observation_binding(
                channel_id,
                channel_bindings[channel_id],
                channel_owners.get(channel_id, []),
                bound_action_types,
                campaign["actions"],
            )
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
