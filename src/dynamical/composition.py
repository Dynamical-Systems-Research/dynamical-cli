"""Deterministic capability composition for campaign-specific virtual SDLs."""

from __future__ import annotations

import itertools
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from .schema import (
    CampaignRequirement,
    CampaignStepRequirement,
    Capability,
    CapabilityProvider,
    CapabilityRegistry,
    EvidenceClass,
    FacilityDocument,
    FailureMode,
    ProofRequirement,
    ProviderAdapterLink,
    ProviderAdmission,
    ProviderAvailability,
    ProviderCost,
    ProviderDuration,
    ProviderPolicy,
    RequestedParameter,
    Scalar,
    StrictModel,
    ValidityEnvelopeEntry,
    canonical_sha256,
    load_campaign_requirement,
    load_capability_registry,
)

COMPOSITION_SCHEMA_VERSION = "dynamical.composition-result.v1"
VIRTUAL_SDL_SCHEMA_VERSION = "0.1.0"

EVIDENCE_RANK: dict[str, int] = {
    "simulator": 0,
    "calibrated_twin": 1,
    "shadow": 2,
    "physical": 3,
}

# itertools.product over per-step admitted-candidate lists is exponential in
# the step count. This ceiling is safe for any campaign this product composes
# today (a handful of steps, at most a few admitted providers each); a
# requirement that would need more combinations HOLDs with an explicit reason
# rather than silently searching only part of the space.
MAX_CANDIDATE_COMBINATIONS = 4096


class CompositionReason(StrictModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    detail: str = Field(min_length=1)
    step_id: str | None = None
    provider_id: str | None = None


class ResolvedInputBinding(StrictModel):
    target_port_id: str
    target_state_type: str
    target_unit: str
    source_kind: Literal["campaign_input", "step_output"]
    source_id: str
    source_port_id: str | None = None
    source_state_type: str
    source_unit: str
    value: Scalar | None = None
    source_facility_id: str | None = None
    transport_operation_id: str | None = None
    sample_id: str | None = None


class OperationBinding(StrictModel):
    step_id: str
    operation_id: str
    provider_id: str
    evidence_class: EvidenceClass
    selected_facility_id: str
    facility_ids: list[str]
    endpoint_id: str
    adapter_links: list[ProviderAdapterLink]
    capability_contract: Capability
    validity_envelope: list[ValidityEnvelopeEntry]
    cost: ProviderCost
    duration: ProviderDuration
    policy: ProviderPolicy
    availability: ProviderAvailability
    admission: ProviderAdmission
    failures: list[FailureMode]
    parameters: list[RequestedParameter]
    inputs: list[ResolvedInputBinding]
    sample_id: str | None = None


class TransportBinding(StrictModel):
    source_step_id: str | None = None
    target_step_id: str
    target_port_id: str
    operation_id: str
    provider_id: str
    evidence_class: EvidenceClass
    source_facility_id: str
    target_facility_id: str
    endpoint_id: str
    adapter_links: list[ProviderAdapterLink]
    capability_contract: Capability
    validity_envelope: list[ValidityEnvelopeEntry]
    cost: ProviderCost
    duration: ProviderDuration
    policy: ProviderPolicy
    availability: ProviderAvailability
    admission: ProviderAdmission
    failures: list[FailureMode]


class DependencyEdge(StrictModel):
    source_step_id: str
    target_step_id: str


class VirtualSDL(StrictModel):
    document_type: Literal["dynamical.virtual-sdl"] = "dynamical.virtual-sdl"
    schema_version: Literal["0.1.0"] = VIRTUAL_SDL_SCHEMA_VERSION
    requirement_id: str
    objective_id: str
    operation_bindings: list[OperationBinding]
    transport_bindings: list[TransportBinding] = Field(default_factory=list)
    dependency_edges: list[DependencyEdge] = Field(default_factory=list)
    # The objective's own proof requirements, carried forward from the requirement
    # so every downstream consumer (compiler, runtime, replay) can check proof
    # completeness from this one composed artifact rather than re-deriving or
    # re-trusting each backend's own claim of campaign success.
    proof_requirements: list[ProofRequirement] = Field(default_factory=list)
    total_cost_usd: float
    total_duration_s: float
    virtual_sdl_sha256: str


class CompositionSources(StrictModel):
    """Protected source snapshots needed to compile a saved composition."""

    requirement: CampaignRequirement
    registry: CapabilityRegistry
    facility: FacilityDocument
    requirement_source: str
    registry_source: str
    facility_source: str
    requirement_sha256: str
    registry_sha256: str
    facility_sha256: str
    # Not "isaac": openusd never calls a live embodied backend, so it is the target
    # that compiles fastest and needs nothing installed beyond this package -- the
    # right default for a save that mostly exists to record what was composed, not to
    # stage a run. isaac stays available via explicit --target isaac; T14 fixed
    # _runtime_pack.py's runtime_capability_bindings so a device offering more than one
    # capability (e.g. ot2-device: dispense-electrolyte + aliquot-to-well; squidstat-
    # device: electrodeposit-constant-current + measure-oer) no longer makes isaac
    # compilation raise "resolves to multiple facility action types" -- see
    # _select_capability_binding's constraint-id disambiguation.
    default_target: Literal["isaac", "openusd"] = "openusd"


class CompositionResult(StrictModel):
    schema_version: Literal["dynamical.composition-result.v1"] = COMPOSITION_SCHEMA_VERSION
    status: Literal["COMPILED", "HOLD"]
    request_sha256: str
    registry_sha256: str
    resolution_sha256: str
    composition_sha256: str | None = None
    reason_codes: list[str] = Field(default_factory=list)
    reasons: list[CompositionReason] = Field(default_factory=list)
    virtual_sdl: VirtualSDL | None = None
    sources: CompositionSources | None = None

    @model_validator(mode="after")
    def status_shape(self) -> CompositionResult:
        if self.status == "COMPILED":
            if self.virtual_sdl is None or self.composition_sha256 is None:
                raise ValueError("COMPILED result requires a virtual SDL and composition hash")
            if self.reason_codes or self.reasons:
                raise ValueError("COMPILED result cannot contain HOLD reasons")
        elif self.virtual_sdl is not None or self.composition_sha256 is not None:
            raise ValueError("HOLD result cannot contain a virtual SDL")
        return self


@dataclass(frozen=True)
class _Candidate:
    provider: CapabilityProvider
    facility_id: str


def _reason(
    code: str,
    detail: str,
    *,
    step_id: str | None = None,
    provider_id: str | None = None,
) -> CompositionReason:
    return CompositionReason(
        code=code,
        detail=detail,
        step_id=step_id,
        provider_id=provider_id,
    )


def _deduplicate_reasons(reasons: list[CompositionReason]) -> list[CompositionReason]:
    records = {
        (item.code, item.step_id or "", item.provider_id or "", item.detail): item
        for item in reasons
    }
    return [records[key] for key in sorted(records)]


def _hold(
    request: CampaignRequirement,
    registry: CapabilityRegistry,
    reasons: list[CompositionReason],
) -> CompositionResult:
    ordered = _deduplicate_reasons(reasons)
    payload = {
        "schema_version": COMPOSITION_SCHEMA_VERSION,
        "status": "HOLD",
        "request_sha256": canonical_sha256(request.model_dump(mode="json")),
        "registry_sha256": canonical_sha256(registry.model_dump(mode="json")),
        "reason_codes": sorted({item.code for item in ordered}),
        "reasons": [item.model_dump(mode="json", exclude_none=True) for item in ordered],
    }
    return CompositionResult(
        **payload,
        resolution_sha256=canonical_sha256(payload),
    )


def _value_matches(value: Scalar, expected: str) -> bool:
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    return False


def _topological_steps(
    request: CampaignRequirement,
) -> tuple[list[CampaignStepRequirement], list[CompositionReason]]:
    by_id = {item.step_id: item for item in request.steps}
    reasons: list[CompositionReason] = []
    for step in request.steps:
        for dependency in step.depends_on:
            if dependency not in by_id:
                reasons.append(
                    _reason(
                        "UNKNOWN_DEPENDENCY",
                        f"dependency {dependency!r} is not a campaign step",
                        step_id=step.step_id,
                    )
                )
        for binding in step.input_bindings:
            if binding.source_kind != "step_output":
                continue
            if binding.source_id not in by_id:
                reasons.append(
                    _reason(
                        "UNKNOWN_INPUT_SOURCE",
                        f"input source {binding.source_id!r} is not a campaign step",
                        step_id=step.step_id,
                    )
                )
            elif binding.source_id not in step.depends_on:
                reasons.append(
                    _reason(
                        "MISSING_DEPENDENCY_EDGE",
                        f"input source {binding.source_id!r} is not in depends_on",
                        step_id=step.step_id,
                    )
                )
    if reasons:
        return [], reasons

    remaining = {key: set(value.depends_on) for key, value in by_id.items()}
    ordered: list[CampaignStepRequirement] = []
    while remaining:
        ready = sorted(key for key, dependencies in remaining.items() if not dependencies)
        if not ready:
            return [], [_reason("DEPENDENCY_CYCLE", "campaign dependency graph is cyclic")]
        for step_id in ready:
            ordered.append(by_id[step_id])
            remaining.pop(step_id)
            for dependencies in remaining.values():
                dependencies.discard(step_id)
    return ordered, []


def _contract_reasons(
    request: CampaignRequirement,
    registry: CapabilityRegistry,
    ordered_steps: list[CampaignStepRequirement],
) -> list[CompositionReason]:
    capabilities = {item.operation_id: item for item in registry.capabilities}
    inputs = {item.id: item for item in request.inputs}
    steps = {item.step_id: item for item in ordered_steps}
    reasons: list[CompositionReason] = []

    for proof in request.objective.proof_requirements:
        capability = capabilities.get(proof.operation_id)
        if capability is None or not any(
            step.operation_id == proof.operation_id for step in ordered_steps
        ):
            reasons.append(
                _reason(
                    "PROOF_OPERATION_MISSING",
                    f"proof {proof.id!r} has no requested operation {proof.operation_id!r}",
                )
            )
            continue
        outputs = {port.id for port in capability.output_ports}
        missing = sorted(set(proof.output_port_ids) - outputs)
        if missing:
            reasons.append(
                _reason(
                    "PROOF_OUTPUT_MISMATCH",
                    f"proof {proof.id!r} requires unknown outputs {missing}",
                )
            )

    for step in ordered_steps:
        capability = capabilities.get(step.operation_id)
        if capability is None:
            reasons.append(
                _reason(
                    "MISSING_CAPABILITY",
                    f"operation {step.operation_id!r} is not in the registry",
                    step_id=step.step_id,
                )
            )
            continue
        expected_inputs = {port.id: port for port in capability.input_ports}
        actual_bindings = {item.target_port_id: item for item in step.input_bindings}
        missing_inputs = sorted(
            port_id
            for port_id, port in expected_inputs.items()
            if port.required and port_id not in actual_bindings
        )
        unknown_inputs = sorted(set(actual_bindings) - set(expected_inputs))
        if missing_inputs:
            reasons.append(
                _reason(
                    "MISSING_INPUT",
                    f"required input bindings are missing: {missing_inputs}",
                    step_id=step.step_id,
                )
            )
        if unknown_inputs:
            reasons.append(
                _reason(
                    "UNKNOWN_INPUT_PORT",
                    f"input bindings target unknown ports: {unknown_inputs}",
                    step_id=step.step_id,
                )
            )
        for target_id, binding in actual_bindings.items():
            target = expected_inputs.get(target_id)
            if target is None:
                continue
            if binding.source_kind == "campaign_input":
                source = inputs.get(binding.source_id)
                if source is None:
                    reasons.append(
                        _reason(
                            "UNKNOWN_INPUT_SOURCE",
                            f"campaign input {binding.source_id!r} is not declared",
                            step_id=step.step_id,
                        )
                    )
                    continue
                source_type, source_unit = source.state_type, source.unit
            else:
                source_step = steps.get(binding.source_id)
                source_capability = (
                    capabilities.get(source_step.operation_id) if source_step else None
                )
                source_port = (
                    next(
                        (
                            port
                            for port in source_capability.output_ports
                            if port.id == binding.source_port_id
                        ),
                        None,
                    )
                    if source_capability
                    else None
                )
                if source_port is None:
                    reasons.append(
                        _reason(
                            "UNKNOWN_OUTPUT_PORT",
                            f"source output {binding.source_port_id!r} is not declared",
                            step_id=step.step_id,
                        )
                    )
                    continue
                source_type, source_unit = source_port.state_type, source_port.unit
            if source_type != target.state_type:
                reasons.append(
                    _reason(
                        "STATE_TYPE_MISMATCH",
                        f"input {target_id!r} needs {target.state_type}, got {source_type}",
                        step_id=step.step_id,
                    )
                )
            if source_unit != target.unit:
                reasons.append(
                    _reason(
                        "UNIT_MISMATCH",
                        f"input {target_id!r} needs unit {target.unit!r}, got {source_unit!r}",
                        step_id=step.step_id,
                    )
                )
    return reasons


def _required_evidence(
    step: CampaignStepRequirement, request: CampaignRequirement
) -> EvidenceClass:
    rank = EVIDENCE_RANK[step.minimum_evidence_class]
    selected: EvidenceClass = step.minimum_evidence_class
    for proof in request.objective.proof_requirements:
        if proof.operation_id == step.operation_id:
            proof_rank = EVIDENCE_RANK[proof.minimum_evidence_class]
            if proof_rank > rank:
                rank = proof_rank
                selected = proof.minimum_evidence_class
    return selected


def _parameter_reasons(
    step: CampaignStepRequirement,
    capability: Capability,
    provider: CapabilityProvider,
) -> list[CompositionReason]:
    expected = {item.name: item for item in capability.parameters}
    supplied = {item.name: item for item in step.parameters}
    reasons: list[CompositionReason] = []
    for name in sorted(set(supplied) - set(expected)):
        reasons.append(
            _reason(
                "UNKNOWN_PARAMETER",
                f"parameter {name!r} is not in operation {capability.operation_id!r}",
                step_id=step.step_id,
                provider_id=provider.provider_id,
            )
        )
    for name, parameter in expected.items():
        value = supplied.get(name)
        if value is None:
            if parameter.required:
                reasons.append(
                    _reason(
                        "MISSING_PARAMETER",
                        f"required parameter {name!r} is absent",
                        step_id=step.step_id,
                        provider_id=provider.provider_id,
                    )
                )
            continue
        if value.value_type != parameter.value_type or not _value_matches(
            value.value, parameter.value_type
        ):
            reasons.append(
                _reason(
                    "PARAMETER_TYPE_MISMATCH",
                    f"parameter {name!r} needs type {parameter.value_type}",
                    step_id=step.step_id,
                    provider_id=provider.provider_id,
                )
            )
        if value.unit != parameter.unit:
            reasons.append(
                _reason(
                    "UNIT_MISMATCH",
                    f"parameter {name!r} needs unit {parameter.unit!r}",
                    step_id=step.step_id,
                    provider_id=provider.provider_id,
                )
            )
        numeric = isinstance(value.value, (int, float)) and not isinstance(value.value, bool)
        if numeric and parameter.minimum is not None and value.value < parameter.minimum:
            reasons.append(
                _reason(
                    "VALUE_OUT_OF_RANGE",
                    f"parameter {name!r} is below the capability minimum",
                    step_id=step.step_id,
                    provider_id=provider.provider_id,
                )
            )
        if numeric and parameter.maximum is not None and value.value > parameter.maximum:
            reasons.append(
                _reason(
                    "VALUE_OUT_OF_RANGE",
                    f"parameter {name!r} is above the capability maximum",
                    step_id=step.step_id,
                    provider_id=provider.provider_id,
                )
            )
        if parameter.enum is not None and value.value not in parameter.enum:
            reasons.append(
                _reason(
                    "VALUE_OUT_OF_RANGE",
                    f"parameter {name!r} is outside the allowed values",
                    step_id=step.step_id,
                    provider_id=provider.provider_id,
                )
            )
        envelope = next(
            (
                item
                for item in provider.validity_envelope
                if item.subject_kind == "parameter" and item.subject_id == name
            ),
            None,
        )
        if (
            provider.evidence_class == "physical"
            and parameter.value_type
            in {
                "number",
                "integer",
            }
            and envelope is None
        ):
            reasons.append(
                _reason(
                    "PHYSICAL_RANGE_UNKNOWN",
                    f"physical provider does not declare a range for parameter {name!r}",
                    step_id=step.step_id,
                    provider_id=provider.provider_id,
                )
            )
        if envelope is not None:
            if envelope.value_type != parameter.value_type or envelope.unit != parameter.unit:
                reasons.append(
                    _reason(
                        "PROVIDER_CONTRACT_MISMATCH",
                        f"provider validity contract differs for parameter {name!r}",
                        step_id=step.step_id,
                        provider_id=provider.provider_id,
                    )
                )
            if numeric and envelope.minimum is not None and value.value < envelope.minimum:
                reasons.append(
                    _reason(
                        "VALUE_OUT_OF_RANGE",
                        f"parameter {name!r} is below the provider validity range",
                        step_id=step.step_id,
                        provider_id=provider.provider_id,
                    )
                )
            if numeric and envelope.maximum is not None and value.value > envelope.maximum:
                reasons.append(
                    _reason(
                        "VALUE_OUT_OF_RANGE",
                        f"parameter {name!r} is above the provider validity range",
                        step_id=step.step_id,
                        provider_id=provider.provider_id,
                    )
                )
            if envelope.enum is not None and value.value not in envelope.enum:
                reasons.append(
                    _reason(
                        "VALUE_OUT_OF_RANGE",
                        f"parameter {name!r} is outside provider validity values",
                        step_id=step.step_id,
                        provider_id=provider.provider_id,
                    )
                )
    return reasons


def _provider_reasons(
    step: CampaignStepRequirement,
    capability: Capability,
    provider: CapabilityProvider,
    minimum_evidence: EvidenceClass,
    request: CampaignRequirement,
) -> list[CompositionReason]:
    reasons: list[CompositionReason] = []
    common = {"step_id": step.step_id, "provider_id": provider.provider_id}
    if provider.admission.status != "admitted":
        reasons.append(_reason("PROVIDER_NOT_ADMITTED", "provider is not admitted", **common))
    if not provider.availability.available:
        reasons.append(_reason("PROVIDER_UNAVAILABLE", "provider is not available", **common))
    if EVIDENCE_RANK[provider.evidence_class] < EVIDENCE_RANK[minimum_evidence]:
        reasons.append(
            _reason(
                "EVIDENCE_CLASS_INSUFFICIENT",
                "provider evidence class "
                f"{provider.evidence_class!r} is below {minimum_evidence!r}",
                **common,
            )
        )
    if not provider.policy.permitted:
        reasons.append(
            _reason(
                "SAFETY_POLICY_REJECTED",
                "provider policy forbids execution",
                **common,
            )
        )
    missing_tags = sorted(set(step.required_policy_tags) - set(provider.policy.policy_tags))
    if missing_tags:
        reasons.append(
            _reason(
                "POLICY_MISMATCH",
                f"provider lacks required policy tags {missing_tags}",
                **common,
            )
        )
    if provider.policy.required_approval_ids:
        reasons.append(
            _reason(
                "APPROVAL_REQUIRED",
                "provider needs an approval receipt outside the agent request",
                **common,
            )
        )
    inputs = {item.id: item for item in request.inputs}
    bindings = {item.target_port_id: item for item in step.input_bindings}
    for port in [*capability.input_ports, *capability.required_conditions]:
        subject_kind = "input" if port in capability.input_ports else "condition"
        envelope = next(
            (
                item
                for item in provider.validity_envelope
                if item.subject_kind == subject_kind and item.subject_id == port.id
            ),
            None,
        )
        physical_needs_envelope = (
            provider.evidence_class == "physical"
            and port.required
            and (
                subject_kind == "condition"
                or port.state_type in {"number", "integer", "material_state", "sample_state"}
            )
        )
        if physical_needs_envelope and envelope is None:
            reasons.append(
                _reason(
                    "PHYSICAL_RANGE_UNKNOWN",
                    f"physical provider does not declare validity for {subject_kind} {port.id!r}",
                    **common,
                )
            )
        if (
            physical_needs_envelope
            and envelope is not None
            and port.state_type in {"number", "integer", "material_state", "sample_state"}
            and envelope.enum is None
            and (envelope.minimum is None or envelope.maximum is None)
        ):
            reasons.append(
                _reason(
                    "PHYSICAL_RANGE_UNKNOWN",
                    f"physical provider has an incomplete range for {subject_kind} {port.id!r}",
                    **common,
                )
            )
        if envelope is not None and (
            envelope.value_type != port.state_type or envelope.unit != port.unit
        ):
            reasons.append(
                _reason(
                    "PROVIDER_CONTRACT_MISMATCH",
                    f"provider validity contract differs for {subject_kind} {port.id!r}",
                    **common,
                )
            )
        if subject_kind == "condition":
            if envelope is None:
                reasons.append(
                    _reason(
                        "REQUIRED_CONDITION_UNENFORCED",
                        f"provider has no explicit validity binding for required "
                        f"condition {port.id!r}",
                        **common,
                    )
                )
            continue
        binding = bindings.get(port.id)
        source = (
            inputs.get(binding.source_id)
            if binding is not None and binding.source_kind == "campaign_input"
            else None
        )
        if envelope is None or source is None or source.value is None:
            continue
        numeric = isinstance(source.value, (int, float)) and not isinstance(source.value, bool)
        if numeric and envelope.minimum is not None and source.value < envelope.minimum:
            reasons.append(
                _reason(
                    "VALUE_OUT_OF_RANGE",
                    f"input {port.id!r} is below the provider validity range",
                    **common,
                )
            )
        if numeric and envelope.maximum is not None and source.value > envelope.maximum:
            reasons.append(
                _reason(
                    "VALUE_OUT_OF_RANGE",
                    f"input {port.id!r} is above the provider validity range",
                    **common,
                )
            )
        if envelope.enum is not None and source.value not in envelope.enum:
            reasons.append(
                _reason(
                    "VALUE_OUT_OF_RANGE",
                    f"input {port.id!r} is outside provider validity values",
                    **common,
                )
            )
    reasons.extend(_parameter_reasons(step, capability, provider))
    return reasons


def _transport_candidate(
    operation_id: str,
    source_facility: str,
    target_facility: str,
    registry: CapabilityRegistry,
    request: CampaignRequirement,
    minimum_evidence: EvidenceClass,
) -> CapabilityProvider | None:
    capability = next(
        (item for item in registry.capabilities if item.operation_id == operation_id), None
    )
    if capability is None or capability.kind != "transport":
        return None
    providers = [
        provider
        for provider in registry.providers
        if provider.operation_id == operation_id
        and provider.admission.status == "admitted"
        and provider.availability.available
        and provider.policy.permitted
        and not provider.policy.required_approval_ids
        and {source_facility, target_facility}.issubset(provider.facility_ids)
    ]
    transport_step = CampaignStepRequirement(
        step_id=f"transport-to-{target_facility}",
        operation_id=operation_id,
        minimum_evidence_class=minimum_evidence,
        parameters=[],
        input_bindings=[],
        depends_on=[],
        required_policy_tags=[],
    )
    providers = [
        provider
        for provider in providers
        if not _provider_reasons(
            transport_step,
            capability,
            provider,
            minimum_evidence,
            request,
        )
    ]
    if not providers:
        return None
    return min(
        providers,
        key=lambda item: (
            EVIDENCE_RANK[item.evidence_class],
            item.cost.fixed,
            item.duration.typical_s,
            item.provider_id,
        ),
    )


def _topology_bindings(
    request: CampaignRequirement,
    selected: dict[str, _Candidate],
    registry: CapabilityRegistry,
) -> tuple[list[TransportBinding], list[CompositionReason]]:
    inputs = {item.id: item for item in request.inputs}
    transports: list[TransportBinding] = []
    reasons: list[CompositionReason] = []
    for step in request.steps:
        target = selected[step.step_id]
        for binding in step.input_bindings:
            source_step_id: str | None = None
            if binding.source_kind == "step_output":
                source_step_id = binding.source_id
                source_facility = selected[source_step_id].facility_id
            else:
                campaign_input = inputs[binding.source_id]
                source_facility = campaign_input.facility_id
            if source_facility is None or source_facility == target.facility_id:
                continue
            if binding.transport_operation_id is None:
                reasons.append(
                    _reason(
                        "TRANSPORT_REQUIRED",
                        f"{source_facility!r} to {target.facility_id!r} needs explicit transport",
                        step_id=step.step_id,
                    )
                )
                continue
            provider = _transport_candidate(
                binding.transport_operation_id,
                source_facility,
                target.facility_id,
                registry,
                request,
                step.minimum_evidence_class,
            )
            if provider is None:
                reasons.append(
                    _reason(
                        "TRANSPORT_UNAVAILABLE",
                        "no admitted transport provider spans "
                        f"{source_facility!r} and {target.facility_id!r}",
                        step_id=step.step_id,
                    )
                )
                continue
            transports.append(
                TransportBinding(
                    source_step_id=source_step_id,
                    target_step_id=step.step_id,
                    target_port_id=binding.target_port_id,
                    operation_id=binding.transport_operation_id,
                    provider_id=provider.provider_id,
                    evidence_class=provider.evidence_class,
                    source_facility_id=source_facility,
                    target_facility_id=target.facility_id,
                    endpoint_id=provider.endpoint_id,
                    adapter_links=provider.adapter_links,
                    capability_contract=next(
                        item
                        for item in registry.capabilities
                        if item.operation_id == binding.transport_operation_id
                    ),
                    validity_envelope=provider.validity_envelope,
                    cost=provider.cost,
                    duration=provider.duration,
                    policy=provider.policy,
                    availability=provider.availability,
                    admission=provider.admission,
                    failures=provider.failures,
                )
            )
    # A single physical move can feed several consumed ports on the same
    # target step (e.g. two output channels of one prior operation). Group by
    # the (source_step_id, target_step_id) pair -- the physical move itself --
    # and keep one deterministic representative so the move is charged once,
    # not once per port it happens to feed.
    grouped: dict[tuple[str | None, str], list[TransportBinding]] = {}
    for transport in transports:
        key = (transport.source_step_id, transport.target_step_id)
        grouped.setdefault(key, []).append(transport)
    deduplicated = [min(group, key=lambda item: item.target_port_id) for group in grouped.values()]
    return deduplicated, reasons


def _resolved_execution_inputs(
    step: CampaignStepRequirement,
    request: CampaignRequirement,
    capabilities: dict[str, Capability],
    selected: dict[str, _Candidate],
    sample_by_step: dict[str, str] | None = None,
) -> list[ResolvedInputBinding]:
    target_capability = capabilities[step.operation_id]
    target_ports = {item.id: item for item in target_capability.input_ports}
    campaign_inputs = {item.id: item for item in request.inputs}
    steps = {item.step_id: item for item in request.steps}
    resolved: list[ResolvedInputBinding] = []
    for binding in sorted(step.input_bindings, key=lambda item: item.target_port_id):
        target = target_ports[binding.target_port_id]
        if binding.source_kind == "campaign_input":
            source = campaign_inputs[binding.source_id]
            # The agent's own declared campaign input is the only place composition
            # can know a sample's identity ahead of execution: a step_output port's
            # runtime value (and any sample it carries) is not known until the
            # producing operation actually runs.
            sample_id = (
                str(source.value)
                if target.state_type == "sample_state" and source.value is not None
                else None
            )
            resolved.append(
                ResolvedInputBinding(
                    target_port_id=target.id,
                    target_state_type=target.state_type,
                    target_unit=target.unit,
                    source_kind=binding.source_kind,
                    source_id=source.id,
                    source_state_type=source.state_type,
                    source_unit=source.unit,
                    value=source.value,
                    source_facility_id=source.facility_id,
                    transport_operation_id=binding.transport_operation_id,
                    sample_id=sample_id,
                )
            )
            continue
        source_step = steps[binding.source_id]
        source_capability = capabilities[source_step.operation_id]
        source_port = next(
            item for item in source_capability.output_ports if item.id == binding.source_port_id
        )
        # A sample_state port fed by a prior step carries that step's sample
        # forward. Without this the identity is dropped at every step_output
        # hop, so a measurement reads as though it were taken on some
        # unrelated material and lineage checking has nothing to attribute.
        # Steps resolve in topological order, so the producer is already known.
        resolved.append(
            ResolvedInputBinding(
                target_port_id=target.id,
                target_state_type=target.state_type,
                target_unit=target.unit,
                source_kind=binding.source_kind,
                source_id=source_step.step_id,
                source_port_id=source_port.id,
                source_state_type=source_port.state_type,
                source_unit=source_port.unit,
                source_facility_id=selected[source_step.step_id].facility_id,
                transport_operation_id=binding.transport_operation_id,
                sample_id=(
                    (sample_by_step or {}).get(source_step.step_id)
                    if target.state_type == "sample_state"
                    else None
                ),
            )
        )
    return resolved


def compose_virtual_sdl(
    request: CampaignRequirement,
    registry: CapabilityRegistry,
) -> CompositionResult:
    """Bind a campaign request only to admitted providers, or return ``HOLD``."""

    ordered_steps, reasons = _topological_steps(request)
    if reasons:
        return _hold(request, registry, reasons)
    reasons = _contract_reasons(request, registry, ordered_steps)
    if reasons:
        return _hold(request, registry, reasons)

    capabilities = {item.operation_id: item for item in registry.capabilities}
    candidates_by_step: dict[str, list[_Candidate]] = {}
    rejected: list[CompositionReason] = []
    for step in ordered_steps:
        capability = capabilities[step.operation_id]
        minimum_evidence = _required_evidence(step, request)
        providers = [item for item in registry.providers if item.operation_id == step.operation_id]
        if not providers:
            rejected.append(
                _reason(
                    "NO_PROVIDER",
                    f"operation {step.operation_id!r} has no providers",
                    step_id=step.step_id,
                )
            )
            candidates_by_step[step.step_id] = []
            continue
        valid: list[_Candidate] = []
        for provider in sorted(providers, key=lambda item: item.provider_id):
            failures = _provider_reasons(step, capability, provider, minimum_evidence, request)
            if failures:
                rejected.extend(failures)
                continue
            for facility_id in sorted(provider.facility_ids):
                valid.append(_Candidate(provider=provider, facility_id=facility_id))
        candidates_by_step[step.step_id] = valid
    if any(not candidates_by_step[item.step_id] for item in ordered_steps):
        return _hold(request, registry, rejected)

    valid_compositions: list[
        tuple[tuple[Any, ...], dict[str, _Candidate], list[TransportBinding], float, float]
    ] = []
    topology_reasons: list[CompositionReason] = []
    budget_reasons: list[CompositionReason] = []
    candidate_lists = [candidates_by_step[item.step_id] for item in ordered_steps]
    combination_count = math.prod(len(item) for item in candidate_lists)
    if combination_count > MAX_CANDIDATE_COMBINATIONS:
        return _hold(
            request,
            registry,
            [
                _reason(
                    "CANDIDATE_SEARCH_BOUNDED",
                    f"{combination_count} admitted provider combinations exceed the "
                    f"explicit search bound of {MAX_CANDIDATE_COMBINATIONS}; composition "
                    "refuses to silently search only part of the space",
                )
            ],
        )
    for combination in itertools.product(*candidate_lists):
        selected = {
            step.step_id: candidate
            for step, candidate in zip(ordered_steps, combination, strict=True)
        }
        transports, failures = _topology_bindings(request, selected, registry)
        if failures:
            topology_reasons.extend(failures)
            continue
        providers = [item.provider for item in combination]
        total_cost = sum(item.cost.fixed for item in providers) + sum(
            item.cost.fixed for item in transports
        )
        total_duration = sum(item.duration.typical_s for item in providers) + sum(
            item.duration.typical_s for item in transports
        )
        if total_cost > request.max_cost_usd:
            budget_reasons.append(
                _reason("BUDGET_EXCEEDED", "all valid provider compositions exceed max_cost_usd")
            )
            continue
        if total_duration > request.max_duration_s:
            budget_reasons.append(
                _reason(
                    "TIME_LIMIT_EXCEEDED",
                    "all valid provider compositions exceed max_duration_s",
                )
            )
            continue
        preference_sign = (
            -1 if request.provider_preference == "prefer_highest_evidence_class" else 1
        )
        score = (
            preference_sign * sum(EVIDENCE_RANK[item.evidence_class] for item in providers),
            total_cost,
            total_duration,
            tuple(
                (item.provider_id, selected[step.step_id].facility_id)
                for step, item in zip(ordered_steps, providers, strict=True)
            ),
        )
        valid_compositions.append((score, selected, transports, total_cost, total_duration))

    if not valid_compositions:
        return _hold(request, registry, [*topology_reasons, *budget_reasons])

    _, selected, transports, total_cost, total_duration = min(
        valid_compositions, key=lambda item: item[0]
    )
    operation_bindings: list[OperationBinding] = []
    sample_by_step: dict[str, str] = {}
    for step in ordered_steps:
        resolved_inputs = _resolved_execution_inputs(
            step, request, capabilities, selected, sample_by_step
        )
        # The operation's own sample identity is whichever of its resolved
        # inputs is bound to a sample_state port -- the material this step
        # acts on, if the agent declared one.
        step_sample_id = next(
            (item.sample_id for item in resolved_inputs if item.sample_id is not None), None
        )
        if step_sample_id is not None:
            # A step that acts on a sample also hands it on: any later step
            # bound to one of this step's outputs inherits the same identity.
            sample_by_step[step.step_id] = step_sample_id
        operation_bindings.append(
            OperationBinding(
                step_id=step.step_id,
                operation_id=step.operation_id,
                provider_id=selected[step.step_id].provider.provider_id,
                evidence_class=selected[step.step_id].provider.evidence_class,
                selected_facility_id=selected[step.step_id].facility_id,
                facility_ids=sorted(selected[step.step_id].provider.facility_ids),
                endpoint_id=selected[step.step_id].provider.endpoint_id,
                adapter_links=sorted(
                    selected[step.step_id].provider.adapter_links,
                    key=lambda item: (item.adapter_id, item.adapter_version),
                ),
                capability_contract=capabilities[step.operation_id],
                validity_envelope=selected[step.step_id].provider.validity_envelope,
                cost=selected[step.step_id].provider.cost,
                duration=selected[step.step_id].provider.duration,
                policy=selected[step.step_id].provider.policy,
                availability=selected[step.step_id].provider.availability,
                admission=selected[step.step_id].provider.admission,
                failures=selected[step.step_id].provider.failures,
                parameters=sorted(step.parameters, key=lambda item: item.name),
                inputs=resolved_inputs,
                sample_id=step_sample_id,
            )
        )
    edge_pairs = {
        (dependency, step.step_id) for step in ordered_steps for dependency in step.depends_on
    }
    # A physical move is itself a dependency: the step it feeds cannot run
    # before it completes. Fold transport pairs into the same edge set so the
    # execution order reflects every move, not only the depends_on edges the
    # agent declared explicitly. A campaign_input-sourced transport has no
    # step_id to anchor an edge to and is excluded.
    edge_pairs |= {
        (transport.source_step_id, transport.target_step_id)
        for transport in transports
        if transport.source_step_id is not None
    }
    edges = sorted(
        (
            DependencyEdge(source_step_id=source, target_step_id=target)
            for source, target in edge_pairs
        ),
        key=lambda item: (item.source_step_id, item.target_step_id),
    )
    virtual_payload = {
        "document_type": "dynamical.virtual-sdl",
        "schema_version": VIRTUAL_SDL_SCHEMA_VERSION,
        "requirement_id": request.requirement_id,
        "objective_id": request.objective.id,
        "operation_bindings": [item.model_dump(mode="json") for item in operation_bindings],
        "transport_bindings": [
            item.model_dump(mode="json", exclude_none=True)
            for item in sorted(
                transports,
                key=lambda item: (
                    item.target_step_id,
                    item.target_port_id,
                    item.provider_id,
                ),
            )
        ],
        "dependency_edges": [item.model_dump(mode="json") for item in edges],
        "proof_requirements": [
            item.model_dump(mode="json") for item in request.objective.proof_requirements
        ],
        "total_cost_usd": total_cost,
        "total_duration_s": total_duration,
    }
    composition_sha256 = canonical_sha256(virtual_payload)
    virtual_sdl = VirtualSDL(
        **virtual_payload,
        virtual_sdl_sha256=composition_sha256,
    )
    base_result = {
        "schema_version": COMPOSITION_SCHEMA_VERSION,
        "status": "COMPILED",
        "request_sha256": canonical_sha256(request.model_dump(mode="json")),
        "registry_sha256": canonical_sha256(registry.model_dump(mode="json")),
        "composition_sha256": composition_sha256,
        "reason_codes": [],
        "reasons": [],
        "virtual_sdl": virtual_sdl.model_dump(mode="json", exclude_none=True),
    }
    return CompositionResult(
        **base_result,
        resolution_sha256=canonical_sha256(base_result),
    )


def demote_untrusted_admissions(
    registry: CapabilityRegistry,
    installed_registry: CapabilityRegistry,
) -> tuple[CapabilityRegistry, list[CompositionReason]]:
    """Demote admitted claims the installed authority does not independently confirm.

    An agent may propose any registry it likes -- that is how a new provider gets
    considered at all -- but it cannot also be the authority that admits its own
    proposal. A provider record is only trusted as admitted when the *entire
    record* -- identity, admission, policy, safety limits, validity envelope,
    cost, duration, availability, and adapter links -- is identical to an
    admitted record in the packaged/installed registry this software ships
    with. A record whose identity matches but whose authority-bearing fields
    differ is a known identity carrying modified authority, and is demoted to
    ``"pending"`` -- a proposal, not an admission -- exactly like a record the
    installed authority has never seen. Demoted proposals flow through the
    ordinary HOLD machinery like any other not-yet-admitted provider.
    """

    installed_admitted = {
        (item.provider_id, item.operation_id, item.evidence_class): item.model_dump(mode="json")
        for item in installed_registry.providers
        if item.admission.status == "admitted"
    }
    installed_authorities = {item.admission.authority_id for item in installed_registry.providers}
    demoted: list[CapabilityProvider] = []
    reasons: list[CompositionReason] = []
    changed = False
    for provider in registry.providers:
        if provider.admission.status != "admitted":
            demoted.append(provider)
            continue
        key = (provider.provider_id, provider.operation_id, provider.evidence_class)
        expected = installed_admitted.get(key)
        if expected is not None and provider.model_dump(mode="json") == expected:
            demoted.append(provider)
            continue
        changed = True
        if provider.admission.authority_id not in installed_authorities:
            code = "AUTHORITY_UNRECOGNIZED"
            detail = (
                f"provider {provider.provider_id!r} claims admission from authority "
                f"{provider.admission.authority_id!r}, which the installed authority does not "
                "recognize; treated as a proposal, not an admission"
            )
        elif expected is None:
            code = "PROVIDER_SELF_ADMITTED"
            detail = (
                f"provider {provider.provider_id!r} claims admitted status for operation "
                f"{provider.operation_id!r} at evidence class {provider.evidence_class!r}, but "
                "no identical admission exists in the installed authority; treated as a "
                "proposal, not an admission"
            )
        else:
            code = "PROVIDER_AUTHORITY_MODIFIED"
            detail = (
                f"provider {provider.provider_id!r} matches an installed admitted identity for "
                f"operation {provider.operation_id!r} at evidence class "
                f"{provider.evidence_class!r}, but its authority-bearing record differs from "
                "the installed authority; treated as a proposal, not an admission"
            )
        reasons.append(_reason(code, detail, provider_id=provider.provider_id))
        demoted.append(
            provider.model_copy(
                update={"admission": provider.admission.model_copy(update={"status": "pending"})}
            )
        )
    if not changed:
        return registry, []
    return registry.model_copy(update={"providers": demoted}), reasons


# Facility sections whose records carry authority: operation contracts, device
# and agent endpoint wiring, executable model bindings, backend adapters,
# provider admission bindings, safety constraints, calibration evidence, asset
# admission, and the declared initial material states (which seed scientific
# conditions an agent must not fabricate). Everything else in a facility
# document -- facility metadata, workstation layout, asset poses -- is topology
# the agent is free to compose.
_FACILITY_AUTHORITY_SECTIONS = (
    "capabilities",
    "devices",
    "agents",
    "model_bindings",
    "adapter_bindings",
    "provider_admission_bindings",
    "constraints",
    "calibration_evidence",
    "asset_sources",
    "material_states",
)


def authority_hold_reasons(
    registry: CapabilityRegistry,
    facility: FacilityDocument | None,
    installed_registry: CapabilityRegistry,
    installed_facility: FacilityDocument | None,
) -> list[CompositionReason]:
    """Compare the authority-bearing projection against the installed bundle.

    The agent stays free to compose topology and to select a *subset* of
    installed records; it cannot modify or introduce authority-bearing
    records. Every operation contract in the supplied registry and every
    record in the supplied facility's authority-bearing sections must be
    identical to the installed record with the same id. Anything unknown or
    modified is a proposal: it earns a typed reason for a structured HOLD,
    never silent acceptance. Provider admission records are handled
    separately by ``demote_untrusted_admissions``.
    """

    reasons: list[CompositionReason] = []
    installed_operations = {
        item.operation_id: item.model_dump(mode="json") for item in installed_registry.capabilities
    }
    for capability in registry.capabilities:
        expected = installed_operations.get(capability.operation_id)
        if expected is None:
            reasons.append(
                _reason(
                    "AUTHORITY_UNRECOGNIZED",
                    f"operation contract {capability.operation_id!r} is not in the installed "
                    "capability authority; treated as a proposal",
                )
            )
        elif capability.model_dump(mode="json") != expected:
            reasons.append(
                _reason(
                    "AUTHORITY_MODIFIED",
                    f"operation contract {capability.operation_id!r} differs from the installed "
                    "capability authority; treated as a proposal",
                )
            )
    if facility is None or installed_facility is None:
        return _deduplicate_reasons(reasons)
    for section in _FACILITY_AUTHORITY_SECTIONS:
        installed_records = {
            record.id: record.model_dump(mode="json")
            for record in getattr(installed_facility, section)
        }
        for record in getattr(facility, section):
            expected = installed_records.get(record.id)
            if expected is None:
                reasons.append(
                    _reason(
                        "AUTHORITY_UNRECOGNIZED",
                        f"facility {section} record {record.id!r} is not in the installed "
                        "facility authority; treated as a proposal",
                    )
                )
            elif record.model_dump(mode="json") != expected:
                reasons.append(
                    _reason(
                        "AUTHORITY_MODIFIED",
                        f"facility {section} record {record.id!r} differs from the installed "
                        "facility authority; treated as a proposal",
                    )
                )
    return _deduplicate_reasons(reasons)


def compose_files(
    requirement_path: str | Path,
    registry_path: str | Path,
    facility_path: str | Path,
    *,
    installed_registry: CapabilityRegistry | None = None,
) -> CompositionResult:
    """Compose a requirement against files an agent supplies.

    ``installed_registry`` is the packaged/installed authority (see
    ``dynamical.cli``'s ``DEFAULT_REGISTRY``), not another agent-suppliable path:
    passing it demotes any admitted claim in ``registry_path`` that authority does
    not independently confirm (see ``demote_untrusted_admissions``) *before*
    composing, so the demoted registry -- not the raw agent-supplied one -- is what
    gets composed and protected as ``sources.registry``. This keeps the saved
    result exactly reproducible from its own protected sources (no reason is added
    here that a plain ``compose_virtual_sdl(sources.requirement, sources.registry)``
    would not also produce); callers that want the demotion explained to an agent
    should call ``demote_untrusted_admissions`` themselves for display, the way
    ``dynamical.cli``'s ``compose`` command does. Omitting ``installed_registry``
    composes ``registry_path`` as given, unchanged -- for callers that already are
    the trust boundary, or that are composing the installed registry itself.
    """

    from .schema import load_facility_manifest

    requirement = load_campaign_requirement(requirement_path)
    registry = load_capability_registry(registry_path)
    facility = load_facility_manifest(facility_path)
    if installed_registry is not None:
        registry, _ = demote_untrusted_admissions(registry, installed_registry)
    result = compose_virtual_sdl(requirement, registry)
    sources = CompositionSources(
        requirement=requirement,
        registry=registry,
        facility=facility,
        requirement_source=str(requirement_path),
        registry_source=str(registry_path),
        facility_source=str(facility_path),
        requirement_sha256=canonical_sha256(requirement.model_dump(mode="json")),
        registry_sha256=canonical_sha256(registry.model_dump(mode="json")),
        facility_sha256=canonical_sha256(facility.model_dump(mode="json")),
    )
    payload = result.model_dump(mode="json", exclude_none=True)
    payload["sources"] = sources.model_dump(mode="json", exclude_none=True)
    payload.pop("resolution_sha256")
    return CompositionResult(**payload, resolution_sha256=canonical_sha256(payload))


def write_composition_result(path: str | Path, result: CompositionResult) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            result.model_dump(mode="json", exclude_none=True),
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def validate_composition_result(value: dict[str, Any] | CompositionResult) -> CompositionResult:
    """Validate result shape and all hashes that do not need source files."""

    result = (
        value if isinstance(value, CompositionResult) else CompositionResult.model_validate(value)
    )
    payload = result.model_dump(mode="json", exclude_none=True)
    resolution_hash = payload.pop("resolution_sha256")
    if canonical_sha256(payload) != resolution_hash:
        raise ValueError("composition resolution hash does not match its content")
    if result.virtual_sdl is not None:
        virtual_payload = result.virtual_sdl.model_dump(mode="json")
        virtual_hash = virtual_payload.pop("virtual_sdl_sha256")
        if canonical_sha256(virtual_payload) != virtual_hash:
            raise ValueError("virtual SDL hash does not match its content")
        if result.composition_sha256 != virtual_hash:
            raise ValueError("composition hash does not match the virtual SDL")
    if result.sources is not None:
        sources = result.sources
        source_records = (
            (sources.requirement, sources.requirement_sha256, "requirement"),
            (sources.registry, sources.registry_sha256, "registry"),
            (sources.facility, sources.facility_sha256, "facility"),
        )
        for document, expected_hash, label in source_records:
            if canonical_sha256(document.model_dump(mode="json")) != expected_hash:
                raise ValueError(f"composition {label} source hash does not match its content")
        if result.request_sha256 != sources.requirement_sha256:
            raise ValueError("composition request hash does not match its source")
        if result.registry_sha256 != sources.registry_sha256:
            raise ValueError("composition registry hash does not match its source")
        _require_source_composition_match(result, sources.requirement, sources.registry)
    return result


def _require_source_composition_match(
    result: CompositionResult,
    request: CampaignRequirement,
    registry: CapabilityRegistry,
) -> None:
    """Require core composition fields to match deterministic protected inputs."""

    actual_payload = result.model_dump(mode="json", exclude_none=True)
    actual_payload.pop("resolution_sha256")
    actual_payload.pop("sources", None)
    expected_payload = compose_virtual_sdl(request, registry).model_dump(
        mode="json", exclude_none=True
    )
    expected_payload.pop("resolution_sha256")
    if actual_payload != expected_payload:
        raise ValueError("composition differs from its requirement and protected registry")


def validate_composition_sources(
    value: dict[str, Any] | CompositionResult,
    request: CampaignRequirement,
    registry: CapabilityRegistry,
) -> CompositionResult:
    """Require a saved result to equal deterministic composition of protected inputs."""

    result = validate_composition_result(value)
    _require_source_composition_match(result, request, registry)
    return result


def export_composition_result_schema(path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(CompositionResult.model_json_schema(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
