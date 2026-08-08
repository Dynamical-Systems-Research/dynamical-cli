"""Compiled campaign and standalone runtime-pack generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ._extract import record_id, records
from .base import BackendArtifact, write_json_artifact, write_text_artifact

EVIDENCE_CLASSES = {"simulator", "calibrated_twin", "shadow", "physical"}
MATTERIX_HEATER_PROVIDER_ID = "matterix-heater-workstation-simulator"


def selected_operation_bindings(output_dir: Path) -> list[dict[str, Any]] | None:
    """Load compiler-selected providers when a composition artifact is present."""

    path = output_dir / "composition_result.json"
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != (
        "dynamical.composition-result.v1"
    ):
        raise ValueError("composition_result.json has an unsupported contract")
    if value.get("status") != "COMPILED":
        raise ValueError("a HOLD composition cannot emit an executable runtime campaign")
    virtual_sdl = value.get("virtual_sdl")
    if not isinstance(virtual_sdl, dict):
        raise ValueError("compiled composition has no virtual_sdl")
    bindings = virtual_sdl.get("operation_bindings")
    if not isinstance(bindings, list):
        raise ValueError("compiled virtual SDL has no operation bindings")
    return [binding for binding in bindings if isinstance(binding, dict)]


def _records_by_id(document: Any, collection: str) -> dict[str, dict[str, Any]]:
    return {record_id(item): item for item in records(document, collection)}


def _enum_parameter(capability: dict[str, Any], name: str) -> str | None:
    for parameter in capability.get("parameters", []):
        if not isinstance(parameter, dict) or parameter.get("name") != name:
            continue
        values = parameter.get("enum")
        if isinstance(values, list) and len(values) == 1:
            return str(values[0])
    return None


def _selection_by_endpoint(
    operation_bindings: list[dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for binding in operation_bindings or []:
        if not isinstance(binding, dict):
            continue
        endpoint_id = binding.get("endpoint_id")
        evidence_class = binding.get("evidence_class")
        provider_id = binding.get("provider_id")
        if not endpoint_id or not provider_id or evidence_class not in EVIDENCE_CLASSES:
            continue
        selected[str(endpoint_id)] = binding
        for adapter_link in binding.get("adapter_links", []):
            if isinstance(adapter_link, dict) and adapter_link.get("endpoint_ref"):
                selected[str(adapter_link["endpoint_ref"])] = binding
    return selected


def runtime_capability_bindings(
    document: Any,
    *,
    operation_bindings: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Resolve runtime roles from declared capability ports and selected providers.

    Asset names and asset-kind substrings do not select actions. The object and
    target roles come from the declared asset-id parameter ports. Provider roles
    come from the device or agent endpoint that owns each capability.
    """

    devices = _records_by_id(document, "devices")
    agents = _records_by_id(document, "agents")
    selected = _selection_by_endpoint(operation_bindings)
    result: dict[str, dict[str, Any]] = {}
    for capability in sorted(records(document, "capabilities"), key=record_id):
        action_type = str(capability.get("action_type", ""))
        if action_type not in {"set_heater", "wait", "observe", "pick", "place"}:
            continue
        endpoint_id = str(capability.get("provider_id", ""))
        if selected and endpoint_id not in selected:
            continue
        endpoint = devices.get(endpoint_id) or agents.get(endpoint_id)
        if not endpoint:
            continue
        selection = selected.get(endpoint_id, {})
        binding = {
            "capability_id": record_id(capability),
            "action_type": action_type,
            "endpoint_id": endpoint_id,
            "endpoint_asset_id": str(endpoint.get("asset_id", "")),
            "provider_id": str(selection.get("provider_id") or MATTERIX_HEATER_PROVIDER_ID),
            "operation_id": str(selection.get("operation_id") or record_id(capability)),
            "evidence_class": str(selection.get("evidence_class") or "simulator"),
            "object_asset_id": _enum_parameter(capability, "object"),
            "target_asset_id": _enum_parameter(capability, "target"),
        }
        if action_type in result:
            raise ValueError(f"runtime capability {action_type!r} is ambiguous")
        result[action_type] = binding
    return result


def runtime_role_assets(
    document: Any,
    *,
    operation_bindings: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    """Resolve bounded roles from the executable capability graph."""

    bindings = runtime_capability_bindings(
        document,
        operation_bindings=operation_bindings,
    )
    heater = bindings.get("set_heater", {})
    pick = bindings.get("pick", {})
    place = bindings.get("place", {})
    result: dict[str, str] = {}
    if heater.get("endpoint_asset_id"):
        result["heater"] = str(heater["endpoint_asset_id"])
    if pick.get("endpoint_asset_id"):
        result["robot"] = str(pick["endpoint_asset_id"])
    if pick.get("object_asset_id"):
        result["beaker"] = str(pick["object_asset_id"])
    if place.get("target_asset_id") and place.get("target_asset_id") != result.get("heater"):
        raise ValueError("place target does not match the selected heater capability")
    if place.get("object_asset_id") and place.get("object_asset_id") != result.get("beaker"):
        raise ValueError("place object does not match the selected pick capability")
    return result


def runtime_campaign(
    document: Any,
    *,
    target: str,
    ir_hash: str,
    operation_bindings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build one target-neutral action contract for the bounded workstation."""

    capability_bindings = runtime_capability_bindings(
        document,
        operation_bindings=operation_bindings,
    )
    roles = runtime_role_assets(document, operation_bindings=operation_bindings)
    required = {"heater", "beaker", "robot"}
    required_actions = {"set_heater", "wait", "pick", "place"}
    missing = sorted(required - set(roles))
    missing_actions = sorted(required_actions - set(capability_bindings))
    if missing or missing_actions:
        return {
            "schema_version": "dynamical.runtime-campaign.v1",
            "campaign_id": "compiled-facility-runtime-gate",
            "core_ir_sha256": ir_hash,
            "target": target,
            "execution_status": "blocked",
            "blocker": (
                f"bounded runtime roles are absent: {missing}; "
                f"capability bindings are absent: {missing_actions}"
            ),
            "asset_roles": roles,
            "provider_bindings": capability_bindings,
            "actions": [],
            "claim_boundary": "No runtime trace can be admitted from an empty action mapping.",
        }

    heater_actor = capability_bindings["set_heater"]["endpoint_id"]
    robot_actor = capability_bindings["pick"]["endpoint_id"]
    if not heater_actor or not robot_actor:
        return {
            "schema_version": "dynamical.runtime-campaign.v1",
            "campaign_id": "compiled-facility-runtime-gate",
            "core_ir_sha256": ir_hash,
            "target": target,
            "execution_status": "blocked",
            "blocker": "heater device or embodied robot endpoint is absent",
            "asset_roles": roles,
            "provider_bindings": capability_bindings,
            "actions": [],
            "claim_boundary": "No runtime trace can be admitted without embodied endpoints.",
        }

    def action_binding(kind: str) -> dict[str, str]:
        binding = capability_bindings[kind]
        return {
            "provider_id": str(binding["provider_id"]),
            "evidence_class": str(binding["evidence_class"]),
        }

    selected_programs = [
        item
        for item in operation_bindings or []
        if item.get("operation_id") == "apply-thermal-program"
    ]
    required_adapters = {
        "dynamical-matterix-heater-control",
        "dynamical-matterix-franka-control",
    }
    if len(selected_programs) != 1:
        return {
            "schema_version": "dynamical.runtime-campaign.v1",
            "campaign_id": "compiled-facility-runtime-gate",
            "core_ir_sha256": ir_hash,
            "target": target,
            "execution_status": "blocked",
            "blocker": "one selected apply-thermal-program binding is required",
            "asset_roles": roles,
            "provider_bindings": capability_bindings,
            "actions": [],
            "claim_boundary": "No MATTERIX task can run without one selected thermal program.",
        }
    selected_program = selected_programs[0]
    selected_adapters = {
        str(item.get("adapter_id"))
        for item in selected_program.get("adapter_links", [])
        if isinstance(item, dict)
    }
    if selected_program.get(
        "provider_id"
    ) != MATTERIX_HEATER_PROVIDER_ID or not required_adapters.issubset(selected_adapters):
        return {
            "schema_version": "dynamical.runtime-campaign.v1",
            "campaign_id": "compiled-facility-runtime-gate",
            "core_ir_sha256": ir_hash,
            "target": target,
            "execution_status": "blocked",
            "blocker": "selected thermal provider has no admitted MATTERIX heater task binding",
            "asset_roles": roles,
            "provider_bindings": capability_bindings,
            "actions": [],
            "claim_boundary": "No MATTERIX task can run from an unrelated provider binding.",
        }
    heater_parameters = selected_program.get("parameters", [])
    parameter_values = {
        str(item.get("name")): item.get("value")
        for item in heater_parameters
        if isinstance(item, dict) and item.get("name")
    }
    if set(parameter_values) != {"target-temperature", "dwell-time"}:
        raise ValueError("selected thermal program requires target-temperature and dwell-time")
    target_temperature = float(parameter_values["target-temperature"])
    dwell_time = float(parameter_values["dwell-time"])

    action_specs: list[tuple[dict[str, Any], list[str], list[str]]] = []
    action_specs.extend(
        [
            (
                {
                    "kind": "wait",
                    "actor_id": heater_actor,
                    **action_binding("wait"),
                    "parameters": {"duration": 2.0},
                },
                ["material-mass-positive"],
                [],
            ),
            (
                {
                    "kind": "set_heater",
                    "actor_id": heater_actor,
                    **action_binding("set_heater"),
                    "parameters": {"enabled": True, "target-temperature": target_temperature},
                },
                ["setpoint-range"],
                [],
            ),
            (
                {
                    "kind": "pick",
                    "actor_id": robot_actor,
                    **action_binding("pick"),
                    "parameters": {"object": roles["beaker"]},
                },
                ["material-mass-positive"],
                [],
            ),
            (
                {
                    "kind": "place",
                    "actor_id": robot_actor,
                    **action_binding("place"),
                    "parameters": {"object": roles["beaker"], "target": roles["heater"]},
                },
                ["material-mass-positive"],
                [],
            ),
            (
                {
                    "kind": "wait",
                    "actor_id": heater_actor,
                    **action_binding("wait"),
                    "parameters": {"duration": dwell_time},
                },
                ["material-mass-positive"],
                ["solution-temperature-safety-range"],
            ),
            (
                {
                    "kind": "set_heater",
                    "actor_id": heater_actor,
                    **action_binding("set_heater"),
                    "parameters": {"enabled": False, "target-temperature": target_temperature},
                },
                ["setpoint-range"],
                [],
            ),
            (
                {
                    "kind": "wait",
                    "actor_id": heater_actor,
                    **action_binding("wait"),
                    "parameters": {"duration": 5.0},
                },
                ["material-mass-positive"],
                [],
            ),
        ]
    )
    actions: list[dict[str, Any]] = []
    constraint_bindings: dict[str, dict[str, list[str]]] = {}
    for index, (action, pre_action, post_action) in enumerate(action_specs):
        action_id = f"runtime-{index:03d}"
        actions.append({"action_id": action_id, **action})
        constraint_bindings[action_id] = {
            "pre_action": pre_action,
            "post_action": post_action,
        }
    return {
        "schema_version": "dynamical.runtime-campaign.v1",
        "campaign_id": "compiled-heater-workstation-runtime-gate",
        "core_ir_sha256": ir_hash,
        "target": target,
        "execution_status": "requires_external_runtime_gate",
        "blocker": None,
        "asset_roles": roles,
        "provider_bindings": capability_bindings,
        "constraint_bindings": constraint_bindings,
        "selected_operation": {
            "operation_id": "apply-thermal-program",
            "provider_id": selected_program["provider_id"],
            "evidence_class": selected_program["evidence_class"],
            "adapter_ids": sorted(selected_adapters),
            "parameters": parameter_values,
        },
        "actions": actions,
        "constraints": {
            "heater_target_temperature_k": {"minimum": 303.15, "maximum": 343.15},
            "material_mass_kg": {"exclusive_minimum": 0.0},
        },
        "claim_boundary": (
            "This campaign maps the compiled Dynamical contract to target actions. "
            "W1 remains false "
            "until the target run, render, trace, replay, and visual inspection pass."
        ),
    }


def emit_runtime_contract_files(
    document: Any,
    output_dir: Path,
    *,
    target: str,
    ir_hash: str,
) -> tuple[list[BackendArtifact], dict[str, Any]]:
    """Emit the common campaign and standard-library trace runtime."""

    from . import compiled_runtime

    campaign = runtime_campaign(
        document,
        target=target,
        ir_hash=ir_hash,
        operation_bindings=selected_operation_bindings(output_dir),
    )
    source_path = Path(compiled_runtime.__file__)
    artifacts = [
        write_json_artifact(
            output_dir,
            "runtime_campaign",
            "runtime_campaign.json",
            campaign,
        ),
        write_text_artifact(
            output_dir,
            "runtime_contract_module",
            "dynamical_runtime_contract.py",
            source_path.read_text(encoding="utf-8"),
        ),
    ]
    return artifacts, campaign
