"""Compiled campaign and standalone runtime-pack generation.

The runtime campaign is compiled from a composition's ``operation_bindings``
and ``dependency_edges``. Dynamical does not choose which operations run, in
what order, or under what stopping rule -- that is the agent's composition.
This module only walks the dependency graph the composition already declares
and emits one action per selected operation binding.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

from .. import instruments
from ..samples import Sample, apply_transition, build_transition
from ._extract import record_id, records
from .base import BackendArtifact, write_json_artifact, write_text_artifact

EVIDENCE_CLASSES = {"simulator", "calibrated_twin", "shadow", "physical"}


def _instrument_runtime_artifact(output_dir: Path) -> BackendArtifact:
    """Package the admitted instrument runtime used inside Isaac's Python."""

    instrument_root = Path(instruments.__file__).parent
    sources = {
        "dynamical_runtime/__init__.py": b"",
        "dynamical_runtime/reasons.py": (instrument_root.parent / "reasons.py").read_bytes(),
        "dynamical_runtime/samples.py": (instrument_root.parent / "samples.py").read_bytes(),
        **{
            f"dynamical_runtime/instruments/{source.name}": source.read_bytes()
            for source in sorted(instrument_root.glob("*.py"))
        },
    }
    path = output_dir / "dynamical_instrument_runtime.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in sorted(sources.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    return BackendArtifact(
        role="instrument_runtime",
        path=path,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


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


def selected_proof_requirements(output_dir: Path) -> list[dict[str, Any]] | None:
    """Load the composed objective's proof requirements when a composition artifact exists.

    Mirrors ``selected_operation_bindings`` exactly (same file, same shape checks)
    so the runtime campaign this module compiles can resolve each proof
    requirement's declared ``operation_id`` to the concrete compiled action_ids
    that embody it -- see ``_resolved_proof_requirements``.
    """

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
    proof_requirements = virtual_sdl.get("proof_requirements")
    if not isinstance(proof_requirements, list):
        return []
    return [item for item in proof_requirements if isinstance(item, dict)]


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


def _endpoint_candidates(
    operation_bindings: list[dict[str, Any]] | None,
) -> dict[str, list[dict[str, Any]]]:
    """Map each facility endpoint ref to every selected binding that names it.

    A device can offer more than one capability (e.g. ``ot2-device``: dispense
    + aliquot; ``squidstat-device``: electrodeposit + measure), and a
    composition can select more than one of them in the same campaign. The old
    single-binding-per-endpoint map silently let the last-selected operation's
    binding overwrite its sibling's, so a facility capability that shared a
    device with another selected capability picked up the wrong operation_id
    (see ``_select_capability_binding``, which resolves the ambiguity this
    creates).
    """

    candidates: dict[str, list[dict[str, Any]]] = {}
    for binding in operation_bindings or []:
        if not isinstance(binding, dict):
            continue
        endpoint_id = binding.get("endpoint_id")
        evidence_class = binding.get("evidence_class")
        provider_id = binding.get("provider_id")
        if not endpoint_id or not provider_id or evidence_class not in EVIDENCE_CLASSES:
            continue
        refs = {str(endpoint_id)}
        for adapter_link in binding.get("adapter_links", []):
            if isinstance(adapter_link, dict) and adapter_link.get("endpoint_ref"):
                refs.add(str(adapter_link["endpoint_ref"]))
        for ref in refs:
            candidates.setdefault(ref, []).append(binding)
    return candidates


def _binding_safety_limit_ids(binding: dict[str, Any]) -> set[str]:
    policy = binding.get("policy")
    ids = policy.get("safety_limit_ids", []) if isinstance(policy, dict) else []
    return {str(item) for item in ids}


def _select_capability_binding(
    capability: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    device_capability_count: int,
) -> dict[str, Any] | None:
    """Pick the one selected binding a facility capability's operation was.

    A device offering exactly one capability has no ambiguity: its single
    candidate (if any) is it. A device offering more than one capability must
    disambiguate among the candidates selected at that shared endpoint. A
    capability's own ``precondition``/``postcondition_constraint_ids`` and a
    selected binding's registry-provider ``policy.safety_limit_ids`` are drawn
    from the same facility-declared constraint vocabulary -- a provider
    admission binding must restate its registry provider's envelope exactly
    (see ``FacilityProviderBinding`` / the registry admission tests) -- so a
    unique, non-empty intersection identifies the one candidate this specific
    capability's operation was selected for. No match means this capability's
    own operation was not selected in this composition at all.
    """

    if not candidates:
        return None
    if device_capability_count <= 1:
        return candidates[-1]
    capability_constraint_ids = {
        str(item)
        for item in (
            *capability.get("precondition_constraint_ids", []),
            *capability.get("postcondition_constraint_ids", []),
        )
    }
    matches = [
        binding
        for binding in candidates
        if capability_constraint_ids & _binding_safety_limit_ids(binding)
    ]
    if not matches:
        return None
    identities = {
        (
            str(binding.get("operation_id") or ""),
            str(binding.get("provider_id") or ""),
            str(binding.get("endpoint_id") or ""),
            str(binding.get("evidence_class") or ""),
            tuple(sorted(_binding_safety_limit_ids(binding))),
        )
        for binding in matches
    }
    if len(identities) == 1:
        # A campaign can select the same admitted operation more than once with
        # different parameters. Those steps share one facility capability.
        return matches[0]
    raise ValueError(
        f"capability {capability.get('id')!r} shares a device with sibling capabilities "
        "and cannot be unambiguously matched to one selected operation binding"
    )


def _declared_contract(capability: dict[str, Any]) -> tuple[set[str], set[str]] | None:
    """The parameter names and reported output ports a capability declares."""

    parameters = {
        str(item.get("name"))
        for item in capability.get("parameters", [])
        if isinstance(item, dict) and item.get("name")
    }
    outputs = {str(value) for value in (capability.get("reported_output_port_ids") or {}).values()}
    if not parameters and not outputs:
        return None
    return parameters, outputs


def _contract_projects_into(contract: tuple[set[str], set[str]], binding: dict[str, Any]) -> bool:
    """Whether a capability's declared contract projects into a binding's operation contract."""

    operation = binding.get("capability_contract")
    if not isinstance(operation, dict):
        return False
    parameters = {
        str(item.get("name"))
        for item in operation.get("parameters", [])
        if isinstance(item, dict) and item.get("name")
    }
    outputs = {
        str(item.get("id"))
        for item in operation.get("output_ports", [])
        if isinstance(item, dict) and item.get("id")
    }
    return contract[0] <= parameters and contract[1] <= outputs


def _select_by_declared_contract(
    capability: dict[str, Any],
    operation_bindings: list[dict[str, Any]] | None,
    *,
    sibling_capabilities: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Resolve a capability to its selected binding through the declared contracts.

    Endpoint refs alone cannot resolve a model-backed provider. A provider whose
    work is done by a model -- ``ac-bath-simulator``, ``ac-oer-twin`` -- declares
    no device-control adapter link, because no device drives it, so no selected
    binding names its capability's device endpoint. Such a capability resolved
    only by accident before: when a sibling capability on the same shared device
    happened to be selected too, and contributed the device endpoint key.

    The action's own selected capability is the authority. A facility capability
    declares the parameters it accepts and the output ports it reports; the
    binding carries the operation contract it was selected against. The
    capability selected for an operation is the one whose declared parameters and
    reported outputs both project into that operation's contract. This reads only
    declared contracts -- never physical device endpoints, safety-limit
    intersections, or workstation placement.

    A capability that declares neither parameters nor reported outputs carries no
    contract to match on, and a match that is not unique across operations is
    ambiguous. Both return ``None`` so the caller fails closed rather than
    guessing, exactly as a missing endpoint resolution does.
    """

    contract = _declared_contract(capability)
    if contract is None:
        return None

    matches = [
        binding
        for binding in operation_bindings or []
        if isinstance(binding, dict) and _contract_projects_into(contract, binding)
    ]
    if not matches:
        return None
    if len({str(binding.get("operation_id") or "") for binding in matches}) != 1:
        return None

    # The pairing must be unique in both directions. Two capabilities can declare
    # the same contract -- dispense-electrolyte and aliquot-to-well both take
    # (chemical, volume_ml) and report the same volumes -- and nothing in the
    # declared data separates them. Binding either one would attach an action
    # type the composition never selected, so an operation that more than one
    # capability could serve stays unresolved and fails closed upstream.
    rivals = [
        sibling
        for sibling in sibling_capabilities
        if record_id(sibling) != record_id(capability)
        and (sibling_contract := _declared_contract(sibling)) is not None
        and _contract_projects_into(sibling_contract, matches[0])
    ]
    if rivals:
        return None
    return matches[0]


def runtime_capability_bindings(
    document: Any,
    *,
    operation_bindings: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Resolve runtime instrument bindings from declared capability ports and providers.

    Asset names and asset-kind substrings do not select actions. The object and
    target roles come from the declared asset-id parameter ports. Provider roles
    come from the device or agent endpoint that owns each capability.

    Keyed by ``(instrument_id, action_type)``: two different instruments may
    declare the same ``action_type`` (for example, two independent observers)
    without one silently displacing the other.
    """

    devices = _records_by_id(document, "devices")
    agents = _records_by_id(document, "agents")
    candidates_by_endpoint = _endpoint_candidates(operation_bindings)
    capability_count_by_endpoint: dict[str, int] = {}
    for capability in records(document, "capabilities"):
        endpoint_id = str(capability.get("provider_id", ""))
        capability_count_by_endpoint[endpoint_id] = (
            capability_count_by_endpoint.get(endpoint_id, 0) + 1
        )
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    all_capabilities = sorted(records(document, "capabilities"), key=record_id)
    for capability in all_capabilities:
        action_type = str(capability.get("action_type", ""))
        if not action_type:
            continue
        endpoint_id = str(capability.get("provider_id", ""))
        endpoint_candidates = candidates_by_endpoint.get(endpoint_id, [])
        selection = _select_capability_binding(
            capability,
            endpoint_candidates,
            device_capability_count=capability_count_by_endpoint.get(endpoint_id, 0),
        )
        if selection is None:
            selection = _select_by_declared_contract(
                capability, operation_bindings, sibling_capabilities=all_capabilities
            )
        if selection is None:
            if candidates_by_endpoint:
                # A real (filtered) composition was given and this capability's
                # own operation was not selected in it.
                continue
            selection = {}
        endpoint = devices.get(endpoint_id) or agents.get(endpoint_id)
        if not endpoint:
            continue
        key = (endpoint_id, action_type)
        if key in seen:
            raise ValueError(f"runtime capability {key!r} is ambiguous")
        seen.add(key)
        result.append(
            {
                "instrument_id": endpoint_id,
                "action_type": action_type,
                "capability_id": record_id(capability),
                "endpoint_id": endpoint_id,
                "endpoint_asset_id": str(endpoint.get("asset_id", "")),
                "provider_id": str(selection.get("provider_id") or ""),
                "operation_id": str(selection.get("operation_id") or record_id(capability)),
                "evidence_class": str(selection.get("evidence_class") or "simulator"),
                "object_asset_id": _enum_parameter(capability, "object"),
                "target_asset_id": _enum_parameter(capability, "target"),
            }
        )
    return sorted(result, key=lambda item: (item["instrument_id"], item["action_type"]))


def runtime_role_assets(
    document: Any,
    *,
    operation_bindings: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    """Map each declared action type to the physical asset performing it.

    Data-driven: no role name is privileged. A facility that declares
    ``dispense``, ``electrodeposit`` and ``measure`` gets exactly those roles;
    nothing here assumes a heater, a robot or a beaker exists.
    """

    bindings = runtime_capability_bindings(document, operation_bindings=operation_bindings)
    return {
        binding["action_type"]: binding["endpoint_asset_id"]
        for binding in bindings
        if binding.get("endpoint_asset_id")
    }


def _ordered_bindings(
    bindings: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Topologically walk ``dependency_edges``, falling back to input order.

    The composition already emits ``operation_bindings`` in dependency order,
    but this performs an explicit Kahn's-algorithm walk rather than trusting
    that invariant. Ties, and any binding untouched by an edge, keep their
    original relative position. A cycle or a dangling edge is not this
    module's failure to diagnose -- composition already rejects it -- so this
    falls back to the given order rather than raising.
    """

    if not edges:
        return list(bindings)
    by_step: dict[str, dict[str, Any]] = {}
    for index, binding in enumerate(bindings):
        by_step[str(binding.get("step_id") or f"__unkeyed_{index}")] = binding
    order = {step_id: index for index, step_id in enumerate(by_step)}
    dependents: dict[str, list[str]] = {step_id: [] for step_id in by_step}
    indegree: dict[str, int] = dict.fromkeys(by_step, 0)
    for edge in edges:
        source = str(edge.get("source_step_id", ""))
        target = str(edge.get("target_step_id", ""))
        if source in by_step and target in by_step:
            dependents[source].append(target)
            indegree[target] += 1

    ready = sorted((step_id for step_id in by_step if indegree[step_id] == 0), key=order.get)
    resolved: list[str] = []
    while ready:
        step_id = ready.pop(0)
        resolved.append(step_id)
        newly_ready = []
        for dependent in dependents[step_id]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                newly_ready.append(dependent)
        ready = sorted([*ready, *newly_ready], key=order.get)
    if len(resolved) != len(by_step):
        return list(bindings)
    return [by_step[step_id] for step_id in resolved]


def _resolved_parameters(binding: dict[str, Any]) -> dict[str, Any]:
    return {
        str(item["name"]): item.get("value")
        for item in binding.get("parameters", [])
        if isinstance(item, dict) and item.get("name")
    }


def _constraint_phases(
    binding: dict[str, Any],
    constraint_by_id: dict[str, dict[str, Any]],
) -> tuple[list[str], list[str]]:
    pre_action: list[str] = []
    post_action: list[str] = []
    policy = binding.get("policy")
    safety_limit_ids = policy.get("safety_limit_ids", []) if isinstance(policy, dict) else []
    for raw_id in safety_limit_ids:
        constraint_id = str(raw_id)
        declaration = constraint_by_id.get(constraint_id)
        if declaration is None:
            continue
        if declaration.get("phase") == "pre_action":
            pre_action.append(constraint_id)
        elif declaration.get("phase") in {"runtime", "post_action"}:
            post_action.append(constraint_id)
    return sorted(pre_action), sorted(post_action)


def _resolve_action_kind(
    operation_id: str,
    action_types_by_operation: dict[str, set[str]],
) -> str:
    """Resolve a selected operation to the one facility-declared action type it embodies.

    The action vocabulary is data-driven, not an open string: emitting the raw
    operation_id would let an action kind exist that the compiled facility
    never declared, defeating the point of a per-pack action schema. A
    resolution that is missing or ambiguous is not silently guessed at -- both
    fail closed, naming the operation, rather than reintroducing a fallback
    shaped like the one this task removed.
    """

    action_types = action_types_by_operation.get(operation_id) or set()
    if not action_types:
        raise ValueError(f"operation {operation_id!r} has no facility-declared action type binding")
    if len(action_types) > 1:
        raise ValueError(
            f"operation {operation_id!r} resolves to multiple facility action types: "
            f"{sorted(action_types)}"
        )
    return next(iter(action_types))


def _output_channel_ids_by_operation(
    document: Any, facility_bindings: list[dict[str, Any]]
) -> dict[str, dict[str, str]]:
    """Map each operation_id to {registry output_port_id: facility channel_id}.

    A registry ``Capability``'s output ports (e.g. ``overpotential_v``) and a
    facility's own observation channels (e.g. ``squidstat.overpotential_v``)
    are different, device-namespaced vocabularies by design -- a proof
    requirement's declared ``output_port_ids`` names the former, but a compiled
    trace's observation channels are named in the latter. The bridge is each
    selected capability's own declared ``reported_output_port_ids`` (schema.py's
    ``FacilityCapability``); a channel this facility does not declare as
    reporting any output port is simply absent here, which is honest -- the
    proof check downstream will find no bound value for it, same as if the
    channel were genuinely unmeasured.
    """

    capabilities_by_id = _records_by_id(document, "capabilities")
    by_operation: dict[str, dict[str, str]] = {}
    for binding in facility_bindings:
        capability = capabilities_by_id.get(binding["capability_id"])
        if not isinstance(capability, dict):
            continue
        reported = capability.get("reported_output_port_ids")
        if not isinstance(reported, dict):
            continue
        by_port = by_operation.setdefault(binding["operation_id"], {})
        for channel_id, port_id in reported.items():
            by_port[str(port_id)] = str(channel_id)
    return by_operation


def _resolved_proof_requirements(
    proof_requirements: list[dict[str, Any]] | None,
    action_ids_by_operation: dict[str, list[str]],
    output_channel_ids_by_operation: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    """Bind each proof requirement's ``operation_id`` to this compiled campaign's own action_ids
    and each of its ``output_port_ids`` to the concrete channel this facility reports it on.

    Resolved once, here, at compile time, from the same ``operation_id`` this
    loop already assigns per compiled action -- not left for a runtime consumer
    to re-derive from a different vocabulary (a compiled action's own ``kind``
    is its facility action_type, e.g. ``measure``, not the operation_id
    ``measure-oer`` a proof requirement names). A requirement whose operation was
    not actually composed resolves to an empty ``action_ids`` list; composition
    itself already refuses to reach ``COMPILED`` when a proof's operation is
    missing (``PROOF_OPERATION_MISSING``), so this is a defensive echo, not the
    primary guard. An output port this facility declares no channel for resolves
    to itself, which the trace will honestly never find bound.
    """

    resolved = []
    for item in proof_requirements or []:
        operation_id = str(item.get("operation_id") or "")
        output_port_ids = [str(x) for x in (item.get("output_port_ids") or [])]
        channel_ids_by_port = output_channel_ids_by_operation.get(operation_id, {})
        resolved.append(
            {
                "id": str(item.get("id") or ""),
                "operation_id": operation_id,
                "output_port_ids": output_port_ids,
                "channel_ids": [
                    channel_ids_by_port.get(port_id, port_id) for port_id in output_port_ids
                ],
                "action_ids": sorted(action_ids_by_operation.get(operation_id, [])),
            }
        )
    return resolved


def _require_declared_implementation(
    document: Any, endpoint_id: str, model: Any, operation_id: str
) -> None:
    """Refuse a compile-time model bake whose executed bytes drifted from the
    facility's declared implementation hash -- the baked SampleTransitions
    become the custody record embodied runs are held to, so they must come
    from the declared code, exactly as the live runner enforces at run time."""

    import hashlib as _hashlib
    import sys as _sys

    declared = next(
        (
            item
            for item in getattr(document, "model_bindings", [])
            if getattr(item, "id", None) == endpoint_id
        ),
        None,
    )
    declared_hash = getattr(declared, "implementation_sha256", None) if declared else None
    if not declared_hash:
        return
    module = _sys.modules.get(getattr(model, "__module__", ""))
    module_file = getattr(module, "__file__", None) if module is not None else None
    if (
        not module_file
        or _hashlib.sha256(Path(module_file).read_bytes()).hexdigest() != declared_hash
    ):
        raise ValueError(
            f"transport model for operation {operation_id!r} does not match the facility's "
            f"declared implementation hash for endpoint {endpoint_id!r}; refusing to bake "
            "sample transitions from undeclared code"
        )


def runtime_campaign(
    document: Any,
    *,
    target: str = "compiled",
    ir_hash: str = "",
    operation_bindings: list[dict[str, Any]] | None = None,
    proof_requirements: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compile the runtime campaign from the composition's operation bindings.

    One action is emitted per selected ``operation_binding``, walked in
    dependency order. The experiment order lives entirely in the composition
    that produced ``operation_bindings`` and ``dependency_edges`` -- this
    function only compiles that graph into an executable action sequence. Each
    action's ``kind`` is the facility-declared action type bound to that
    operation (see ``_resolve_action_kind``), not the raw operation_id, so the
    per-pack action schema stays meaningful.
    """

    if operation_bindings is not None:
        bindings = operation_bindings
    else:
        bindings = records(document, "operation_bindings")
    edges = records(document, "dependency_edges")
    ordered = _ordered_bindings(bindings, edges)

    facility_bindings = runtime_capability_bindings(document, operation_bindings=bindings)
    # Keyed by action_id, not action_type: a campaign may repeat an action kind
    # across steps that select different providers or carry different parameter
    # contracts, and each action must validate against its own compiled binding
    # rather than whichever same-kind step was compiled last.
    provider_bindings: dict[str, dict[str, Any]] = {}
    action_types_by_operation: dict[str, set[str]] = {}
    for binding in facility_bindings:
        action_types_by_operation.setdefault(binding["operation_id"], set()).add(
            binding["action_type"]
        )
    constraint_by_id = _records_by_id(document, "constraints")
    model_bindings = _records_by_id(document, "model_bindings")
    output_channels_by_operation = _output_channel_ids_by_operation(document, facility_bindings)

    actions: list[dict[str, Any]] = []
    constraint_bindings: dict[str, dict[str, list[str]]] = {}
    action_ids_by_operation: dict[str, list[str]] = {}
    samples: dict[str, Sample] = {}
    last_known_station: dict[str, str] = {}
    for index, binding in enumerate(ordered):
        operation_id = str(binding.get("operation_id") or "")
        kind = _resolve_action_kind(operation_id, action_types_by_operation)
        action_id = str(binding.get("step_id") or f"runtime-{index:03d}")
        action_ids_by_operation.setdefault(operation_id, []).append(action_id)
        provider_id = str(binding.get("provider_id") or "")
        evidence_class = str(binding.get("evidence_class") or "simulator")
        endpoint_id = str(binding.get("endpoint_id") or "")
        action: dict[str, Any] = {
            "action_id": action_id,
            "kind": kind,
            "actor_id": endpoint_id,
            "provider_id": provider_id,
            "evidence_class": evidence_class,
            "parameters": _resolved_parameters(binding),
        }
        raw_sample_id = binding.get("sample_id")
        sample_id = str(raw_sample_id) if raw_sample_id is not None else None
        # The *workstation* this step actually executes at, not actor_id/endpoint_id
        # (the acting instrument's own endpoint, a different vocabulary a Sample's
        # station_id never matches) -- mirrors campaign.py's _composed_actions, whose
        # in-process openusd path already threads this field so
        # dynamical.samples.check_invariants can compare like with like.
        raw_station_id = binding.get("selected_facility_id")
        station_id = str(raw_station_id) if raw_station_id is not None else None
        # The action's actor_id is the operation binding's endpoint (a provider
        # or model identity), which is not guaranteed to equal the matched
        # FacilityCapability's device/agent provider_id -- so this is recorded
        # as a virtual-SDL scoped binding (compiled_runtime.py's validate_action
        # already has this path) rather than assumed to line up with the
        # embodied capability's own declared parameters.
        capability_contract = binding.get("capability_contract")
        is_transport = (
            isinstance(capability_contract, dict) and capability_contract.get("kind") == "transport"
        )
        model = None
        model_binding = model_bindings.get(endpoint_id)
        if target == "isaac" or is_transport:
            model = instruments.resolve(operation_id, provider_id)
            if model is None:
                raise ValueError(
                    f"no admitted instrument model for operation {operation_id!r} "
                    f"and provider {provider_id!r}"
                )
            if target == "isaac" and not isinstance(model_binding, dict):
                raise ValueError(
                    f"instrument endpoint {endpoint_id!r} has no declared model binding"
                )
            _require_declared_implementation(document, endpoint_id, model, operation_id)
        if is_transport:
            # A sample-moving operation's registered model (transfer.py's
            # transfer_sample) carries no live/measured dependency Isaac would
            # need to supply -- it is custody bookkeeping over already-resolved,
            # fixed parameters (see its own module docstring) -- so calling it
            # once here, exactly as campaign.py's run_composed_campaign calls it
            # live, and baking the resulting SampleTransition into the compiled
            # action is faithful, not a guess. samples.build_transition is the
            # one place that construction happens; a second, isaac-only
            # reimplementation would drift from it.
            current_sample = samples.get(sample_id) if sample_id else None
            assert model is not None
            result = model(
                instruments.InstrumentRequest(
                    parameters=dict(action["parameters"]),
                    inputs={},
                    sample=current_sample,
                )
            )
            if result.sample is not None:
                moved = result.sample
                from_station_hint = str(
                    action["parameters"].get("from_station")
                    or (sample_id and last_known_station.get(sample_id))
                    or station_id
                    or ""
                )
                transition = build_transition(
                    moved,
                    current_sample=current_sample,
                    from_station_hint=from_station_hint,
                    timestamp_s=float(index),
                    step_id=action_id,
                )
                samples = apply_transition(samples, transition)
                action["parameters"] = {
                    **action["parameters"],
                    "sample_transition": transition.model_dump(mode="json"),
                }
                sample_id = transition.sample_id
                last_known_station[transition.sample_id] = transition.to_station
        if sample_id is not None:
            action["sample_id"] = sample_id
        if station_id is not None:
            action["station_id"] = station_id
            if not is_transport and sample_id is not None:
                last_known_station[sample_id] = station_id
        actions.append(action)

        pre_action, post_action = _constraint_phases(binding, constraint_by_id)
        constraint_bindings[action_id] = {"pre_action": pre_action, "post_action": post_action}

        execution_parameters = (
            capability_contract.get("parameters", [])
            if isinstance(capability_contract, dict)
            else []
        )
        provider_binding = {
            "binding_scope": "virtual_sdl",
            "endpoint_id": endpoint_id,
            "provider_id": provider_id,
            "evidence_class": evidence_class,
            "operation_id": operation_id,
            "execution_parameters": execution_parameters,
            "inputs": binding.get("inputs", []),
            "output_port_ids": [
                str(port.get("id"))
                for port in (
                    capability_contract.get("output_ports", [])
                    if isinstance(capability_contract, dict)
                    else []
                )
                if isinstance(port, dict) and port.get("id")
            ],
            "output_channel_ids": output_channels_by_operation.get(operation_id, {}),
        }
        if target == "isaac":
            assert isinstance(model_binding, dict)
            provider_binding.update(
                {
                    "model_implementation_ref": model_binding.get("implementation_ref"),
                    "model_implementation_sha256": model_binding.get("implementation_sha256"),
                }
            )
        provider_bindings[action_id] = provider_binding

    roles = runtime_role_assets(document, operation_bindings=bindings)
    if actions:
        execution_status = "requires_external_runtime_gate"
        blocker = None
    else:
        execution_status = "blocked"
        blocker = "the composition selected no operation bindings"

    return {
        "schema_version": "dynamical.runtime-campaign.v1",
        "campaign_id": "compiled-facility-runtime-gate",
        "core_ir_sha256": ir_hash,
        "target": target,
        "execution_status": execution_status,
        "blocker": blocker,
        "asset_roles": roles,
        "provider_bindings": provider_bindings,
        "constraint_bindings": constraint_bindings,
        "actions": actions,
        "proof_requirements": _resolved_proof_requirements(
            proof_requirements,
            action_ids_by_operation,
            _output_channel_ids_by_operation(document, facility_bindings),
        ),
        "claim_boundary": (
            "This campaign maps the compiled Dynamical contract to target actions. "
            "Embodied evidence remains unbound until the target run, render, trace, replay, "
            "and visual inspection pass."
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
        proof_requirements=selected_proof_requirements(output_dir),
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
    if target == "isaac":
        artifacts.append(_instrument_runtime_artifact(output_dir))
    return artifacts, campaign
