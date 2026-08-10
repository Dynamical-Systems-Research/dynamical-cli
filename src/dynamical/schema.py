"""Strict, backend-independent facility intermediate representation.

The IR uses metres, a Z-up world frame, and named quaternion components. Physical
identity belongs to :class:`Asset`; devices and embodied agents refer to assets.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Annotated, Any, Literal, TypeAlias

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .sources import AssetSource

SCHEMA_VERSION = "0.1.0"
SHA256_PATTERN = r"^[0-9a-f]{64}$"

Identifier = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$", strip_whitespace=True),
]
ChannelId = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_./:-]{0,191}$", strip_whitespace=True),
]
Sha256 = Annotated[str, StringConstraints(pattern=SHA256_PATTERN, to_lower=True)]
FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
Scalar: TypeAlias = str | int | FiniteFloat | bool


class StrictModel(BaseModel):
    """Base for all IR records. Unknown fields are errors."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True)


class Vec3(StrictModel):
    x: FiniteFloat
    y: FiniteFloat
    z: FiniteFloat


class Quaternion(StrictModel):
    """Unit quaternion with explicit scalar-first component names."""

    w: FiniteFloat = 1.0
    x: FiniteFloat = 0.0
    y: FiniteFloat = 0.0
    z: FiniteFloat = 0.0

    @model_validator(mode="after")
    def unit_length(self) -> Quaternion:
        norm = math.sqrt(self.w**2 + self.x**2 + self.y**2 + self.z**2)
        if not math.isclose(norm, 1.0, rel_tol=0.0, abs_tol=1e-6):
            raise ValueError(f"orientation quaternion norm must be 1.0, got {norm:.9g}")
        return self


class Pose(StrictModel):
    position_m: Vec3 = Field(default_factory=lambda: Vec3(x=0.0, y=0.0, z=0.0))
    orientation: Quaternion = Field(default_factory=Quaternion)


class FrameSpec(StrictModel):
    up_axis: Literal["Z"] = "Z"
    length_unit: Literal["m"] = "m"


class SourceRef(StrictModel):
    uri: str = Field(min_length=1)
    role: str = Field(min_length=1)
    sha256: Sha256 | None = None
    revision: str | None = None


class Facility(StrictModel):
    id: Identifier
    name: str = Field(min_length=1)
    schema_version: Literal["0.1.0"] = SCHEMA_VERSION
    frame: FrameSpec = Field(default_factory=FrameSpec)
    workstation_ids: list[Identifier] = Field(min_length=1)
    authoring_basis: Literal["exact", "derived", "representative"]
    source_refs: list[SourceRef] = Field(default_factory=list)
    claim_boundary: list[str] = Field(min_length=1)


class Workstation(StrictModel):
    id: Identifier
    facility_id: Identifier
    member_asset_ids: list[Identifier] = Field(min_length=1)
    local_frame: Pose = Field(default_factory=Pose)
    bounds_m: Vec3 | None = None


class GeometrySpec(StrictModel):
    """Either exact source-derived geometry or a labelled execution proxy."""

    representation: Literal["exact_source_geometry", "execution_visualization_primitive"]
    dimensions_m: Vec3
    mesh_source_id: str | None = None
    portable_primitive: Literal["cube", "cylinder", "sphere", "capsule"] | None = None
    display_color_rgb: Annotated[
        list[Annotated[FiniteFloat, Field(ge=0.0, le=1.0)]],
        Field(min_length=3, max_length=3),
    ] = Field(default_factory=lambda: [0.55, 0.58, 0.62])

    @model_validator(mode="after")
    def representation_matches_geometry(self) -> GeometrySpec:
        if min(self.dimensions_m.x, self.dimensions_m.y, self.dimensions_m.z) <= 0:
            raise ValueError("geometry dimensions must be positive")
        if self.representation == "exact_source_geometry":
            if self.mesh_source_id is None:
                raise ValueError("exact_source_geometry requires mesh_source_id")
            if self.portable_primitive is not None:
                raise ValueError("exact_source_geometry must not declare a portable_primitive")
        else:
            if self.portable_primitive is None:
                raise ValueError("execution_visualization_primitive requires a portable_primitive")
            if self.mesh_source_id is not None:
                raise ValueError("a proxy must not claim a mesh_source_id")
        return self


class CollisionSpec(StrictModel):
    enabled: bool = True
    shape: Literal["geometry", "box", "convex_hull", "none"] = "geometry"

    @model_validator(mode="after")
    def enabled_shape(self) -> CollisionSpec:
        if self.enabled == (self.shape == "none"):
            raise ValueError(
                "enabled collision requires a shape; disabled collision requires 'none'"
            )
        return self


class PhysicalProperties(StrictModel):
    mass_kg: Annotated[FiniteFloat, Field(gt=0.0)] | None = None
    static_friction: Annotated[FiniteFloat, Field(ge=0.0)] | None = None
    dynamic_friction: Annotated[FiniteFloat, Field(ge=0.0)] | None = None


class Asset(StrictModel):
    id: Identifier
    workstation_id: Identifier
    asset_kind: str = Field(min_length=1)
    geometry: GeometrySpec
    pose: Pose
    collision: CollisionSpec = Field(default_factory=CollisionSpec)
    manipulation_frames: dict[Identifier, Pose] = Field(default_factory=dict)
    physical_properties: PhysicalProperties = Field(default_factory=PhysicalProperties)
    source_refs: list[SourceRef] = Field(default_factory=list)


class ChannelSpec(StrictModel):
    id: ChannelId
    value_type: Literal["number", "integer", "boolean", "string", "vector3"]
    unit: str = Field(min_length=1)
    minimum: FiniteFloat | None = None
    maximum: FiniteFloat | None = None

    @model_validator(mode="after")
    def valid_range(self) -> ChannelSpec:
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("channel minimum must not exceed maximum")
        return self


class Device(StrictModel):
    id: Identifier
    asset_id: Identifier
    state_channels: list[ChannelSpec] = Field(default_factory=list)
    capability_ids: list[Identifier] = Field(default_factory=list)


class Agent(StrictModel):
    id: Identifier
    asset_id: Identifier
    capability_ids: list[Identifier] = Field(min_length=1)
    action_endpoint_id: Identifier


class ParameterSpec(StrictModel):
    name: Identifier
    value_type: Literal["number", "integer", "boolean", "string", "asset_id", "duration"]
    unit: str | None = None
    required: bool = True
    minimum: FiniteFloat | None = None
    maximum: FiniteFloat | None = None
    enum: list[Scalar] | None = None

    @model_validator(mode="after")
    def valid_domain(self) -> ParameterSpec:
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("parameter minimum must not exceed maximum")
        if self.enum is not None and not self.enum:
            raise ValueError("parameter enum must not be empty")
        return self


class FacilityCapability(StrictModel):
    """A provider binding used by the v0.1 embodied facility IR.

    This record is intentionally not the reusable scientific ``Capability``.
    It connects an already selected operation to one device or embodied agent
    inside a compiled facility.
    """

    id: Identifier
    provider_id: Identifier
    action_type: str = Field(min_length=1)
    parameters: list[ParameterSpec] = Field(default_factory=list)
    observation_channel_ids: list[ChannelId] = Field(default_factory=list)
    precondition_constraint_ids: list[Identifier] = Field(default_factory=list)
    postcondition_constraint_ids: list[Identifier] = Field(default_factory=list)
    # Explicit channel_id -> parameter name binding for observation channels this
    # capability's provider can honestly echo from a commanded action (e.g. an
    # embodied backend with no domain model for the operation reporting back
    # exactly the parameter it was commanded with). Declared, not inferred: a
    # backend that paired channels to parameters by list position could silently
    # bind the wrong value the moment either list is reordered or a capability
    # gains a same-typed parameter. Every key must be one of this capability's own
    # ``observation_channel_ids``; every value must name one of its own
    # ``parameters`` (see ``echo_bindings_are_declared`` below).
    echoed_parameter_bindings: dict[ChannelId, Identifier] = Field(default_factory=dict)
    # Explicit channel_id -> registry output_port_id binding: which of this
    # facility's own observation channels reports the value the reusable
    # scientific ``Capability`` (registry-level) declares as that output port.
    # The two are different vocabularies by design (a facility channel is
    # namespaced by device, e.g. ``squidstat.overpotential_v``; a registry
    # output port is namespaced by operation, e.g. ``overpotential_v``), so a
    # proof requirement's declared ``output_port_ids`` cannot be checked
    # against a trace's observation channels without this declared bridge.
    # Every key must be one of this capability's own ``observation_channel_ids``.
    reported_output_port_ids: dict[ChannelId, Identifier] = Field(default_factory=dict)

    @model_validator(mode="after")
    def echo_bindings_are_declared(self) -> FacilityCapability:
        channel_ids = set(self.observation_channel_ids)
        parameter_names = {item.name for item in self.parameters}
        unknown_channels = set(self.echoed_parameter_bindings) - channel_ids
        if unknown_channels:
            raise ValueError(
                f"capability {self.id} echoes unknown channels: {sorted(unknown_channels)}"
            )
        unknown_parameters = set(self.echoed_parameter_bindings.values()) - parameter_names
        if unknown_parameters:
            raise ValueError(
                f"capability {self.id} echoes unknown parameters: {sorted(unknown_parameters)}"
            )
        unknown_reported = set(self.reported_output_port_ids) - channel_ids
        if unknown_reported:
            raise ValueError(
                f"capability {self.id} reports unknown channels: {sorted(unknown_reported)}"
            )
        return self


EvidenceClass: TypeAlias = Literal["simulator", "calibrated_twin", "shadow", "physical"]
PortValueType: TypeAlias = Literal[
    "number", "integer", "boolean", "string", "material_state", "sample_state"
]
ProviderPreference: TypeAlias = Literal[
    "prefer_lowest_evidence_class", "prefer_highest_evidence_class"
]


class StatePort(StrictModel):
    """Typed state consumed or produced by one scientific operation."""

    id: ChannelId
    state_type: PortValueType
    unit: str = Field(min_length=1)
    required: bool = True
    description: str = Field(min_length=1)


class CapabilityParameter(StrictModel):
    name: Identifier
    value_type: Literal["number", "integer", "boolean", "string"]
    unit: str = Field(min_length=1)
    required: bool = True
    minimum: FiniteFloat | None = None
    maximum: FiniteFloat | None = None
    enum: list[Scalar] | None = None

    @model_validator(mode="after")
    def valid_domain(self) -> CapabilityParameter:
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("parameter minimum must not exceed maximum")
        if self.enum is not None and not self.enum:
            raise ValueError("parameter enum must not be empty")
        if self.value_type in {"boolean", "string"} and (
            self.minimum is not None or self.maximum is not None
        ):
            raise ValueError("only numeric parameters can have numeric bounds")
        return self


class Capability(StrictModel):
    """Provider-independent scientific operation in the module registry."""

    operation_id: Identifier
    kind: Literal["scientific", "transport"] = "scientific"
    description: str = Field(min_length=1)
    input_ports: list[StatePort] = Field(default_factory=list)
    output_ports: list[StatePort] = Field(default_factory=list)
    parameters: list[CapabilityParameter] = Field(default_factory=list)
    required_conditions: list[StatePort] = Field(default_factory=list)
    possible_failures: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_contract_names(self) -> Capability:
        ports = [
            *(port.id for port in self.input_ports),
            *(port.id for port in self.output_ports),
            *(port.id for port in self.required_conditions),
        ]
        if len(ports) != len(set(ports)):
            raise ValueError("capability port IDs must be unique")
        names = [parameter.name for parameter in self.parameters]
        if len(names) != len(set(names)):
            raise ValueError("capability parameter names must be unique")
        return self


class ProviderAvailability(StrictModel):
    available: bool
    observed_at: str = Field(min_length=1)
    availability_ref: str | None = None


class ProviderAdmission(StrictModel):
    status: Literal["admitted", "rejected", "pending"]
    authority_id: Identifier
    authority_kind: Literal["release_authority", "independent_verifier", "facility_operator"]
    evidence_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def admitted_has_evidence(self) -> ProviderAdmission:
        if self.status == "admitted" and not self.evidence_refs:
            raise ValueError("admitted providers require evidence_refs")
        return self


class ValidityEnvelopeEntry(StrictModel):
    subject_kind: Literal["input", "parameter", "condition"]
    subject_id: ChannelId
    value_type: PortValueType
    unit: str = Field(min_length=1)
    minimum: FiniteFloat | None = None
    maximum: FiniteFloat | None = None
    enum: list[Scalar] | None = None

    @model_validator(mode="after")
    def valid_domain(self) -> ValidityEnvelopeEntry:
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("validity minimum must not exceed maximum")
        if self.enum is not None and not self.enum:
            raise ValueError("validity enum must not be empty")
        return self


class ProviderCost(StrictModel):
    currency: Literal["USD"] = "USD"
    fixed: Annotated[FiniteFloat, Field(ge=0.0)]


class ProviderDuration(StrictModel):
    minimum_s: Annotated[FiniteFloat, Field(ge=0.0)]
    typical_s: Annotated[FiniteFloat, Field(ge=0.0)]
    maximum_s: Annotated[FiniteFloat, Field(ge=0.0)]

    @model_validator(mode="after")
    def ordered(self) -> ProviderDuration:
        if not self.minimum_s <= self.typical_s <= self.maximum_s:
            raise ValueError("duration must satisfy minimum <= typical <= maximum")
        return self


class FailureMode(StrictModel):
    code: Identifier
    description: str = Field(min_length=1)
    recoverable: bool


class ProviderPolicy(StrictModel):
    permitted: bool
    policy_tags: list[Identifier] = Field(default_factory=list)
    safety_limit_ids: list[Identifier] = Field(default_factory=list)
    required_approval_ids: list[Identifier] = Field(default_factory=list)


class ProviderAdapterLink(StrictModel):
    adapter_id: Identifier
    adapter_version: str = Field(min_length=1)
    endpoint_ref: str = Field(min_length=1)
    implementation_sha256: Sha256 | None = None


class FacilityProviderBinding(StrictModel):
    """Facility-side admission for one registry provider used by a composition."""

    id: Identifier
    provider_id: Identifier
    operation_id: Identifier
    evidence_classes: list[EvidenceClass] = Field(min_length=1)
    facility_ids: list[Identifier] = Field(min_length=1)
    endpoint_id: Identifier
    adapter_links: list[ProviderAdapterLink] = Field(min_length=1)
    validity_envelope: list[ValidityEnvelopeEntry] = Field(default_factory=list)
    safety_limit_ids: list[Identifier] = Field(default_factory=list)
    policy_tags: list[Identifier] = Field(default_factory=list)
    required_approval_ids: list[Identifier] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_binding_values(self) -> FacilityProviderBinding:
        for label, values in (
            ("evidence_classes", self.evidence_classes),
            ("facility_ids", self.facility_ids),
            ("safety_limit_ids", self.safety_limit_ids),
            ("policy_tags", self.policy_tags),
            ("required_approval_ids", self.required_approval_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"facility provider binding {label} must be unique")
        link_keys = [
            (link.adapter_id, link.adapter_version, link.endpoint_ref)
            for link in self.adapter_links
        ]
        if len(link_keys) != len(set(link_keys)):
            raise ValueError("facility provider adapter links must be unique")
        return self


class CapabilityProvider(StrictModel):
    """One independently admitted way to execute a scientific capability."""

    provider_id: Identifier
    operation_id: Identifier
    evidence_class: EvidenceClass
    endpoint_id: Identifier
    facility_ids: list[Identifier] = Field(min_length=1)
    availability: ProviderAvailability
    admission: ProviderAdmission
    validity_envelope: list[ValidityEnvelopeEntry] = Field(default_factory=list)
    cost: ProviderCost
    duration: ProviderDuration
    failures: list[FailureMode] = Field(default_factory=list)
    policy: ProviderPolicy
    adapter_links: list[ProviderAdapterLink] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_provider_records(self) -> CapabilityProvider:
        if len(self.facility_ids) != len(set(self.facility_ids)):
            raise ValueError("provider facility_ids must be unique")
        envelope_keys = [(item.subject_kind, item.subject_id) for item in self.validity_envelope]
        if len(envelope_keys) != len(set(envelope_keys)):
            raise ValueError("provider validity entries must be unique")
        return self


class CapabilityRegistry(StrictModel):
    document_type: Literal["dynamical.capability-registry"] = "dynamical.capability-registry"
    schema_version: Literal["0.1.0"] = SCHEMA_VERSION
    registry_id: Identifier
    capabilities: list[Capability] = Field(min_length=1)
    providers: list[CapabilityProvider] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_registry_graph(self) -> CapabilityRegistry:
        operation_ids = [item.operation_id for item in self.capabilities]
        if len(operation_ids) != len(set(operation_ids)):
            raise ValueError("registry operation IDs must be unique")
        provider_keys = [(item.operation_id, item.provider_id) for item in self.providers]
        if len(provider_keys) != len(set(provider_keys)):
            raise ValueError("registry (operation_id, provider_id) pairs must be unique")
        unknown = {item.operation_id for item in self.providers} - set(operation_ids)
        if unknown:
            raise ValueError(f"providers refer to unknown operations: {sorted(unknown)}")
        return self


class ProofRequirement(StrictModel):
    id: Identifier
    operation_id: Identifier
    output_port_ids: list[ChannelId] = Field(min_length=1)
    minimum_evidence_class: EvidenceClass
    acceptance_rule: str = Field(min_length=1)
    independent_verification_required: Literal[True] = True


class EngineeringObjective(StrictModel):
    id: Identifier
    statement: str = Field(min_length=1)
    decision: str = Field(min_length=1)
    proof_requirements: list[ProofRequirement] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_proofs(self) -> EngineeringObjective:
        ids = [item.id for item in self.proof_requirements]
        if len(ids) != len(set(ids)):
            raise ValueError("proof requirement IDs must be unique")
        return self


class CampaignInput(StrictModel):
    id: ChannelId
    state_type: PortValueType
    unit: str = Field(min_length=1)
    value: Scalar | None = None
    facility_id: Identifier | None = None


class RequestedParameter(StrictModel):
    name: Identifier
    value_type: Literal["number", "integer", "boolean", "string"]
    unit: str = Field(min_length=1)
    value: Scalar


class StateInputBinding(StrictModel):
    target_port_id: ChannelId
    source_kind: Literal["campaign_input", "step_output"]
    source_id: ChannelId
    source_port_id: ChannelId | None = None
    transport_operation_id: Identifier | None = None

    @model_validator(mode="after")
    def source_shape(self) -> StateInputBinding:
        if self.source_kind == "step_output" and self.source_port_id is None:
            raise ValueError("step_output binding requires source_port_id")
        if self.source_kind == "campaign_input" and self.source_port_id is not None:
            raise ValueError("campaign_input binding must not set source_port_id")
        return self


class CampaignStepRequirement(StrictModel):
    step_id: Identifier
    operation_id: Identifier
    minimum_evidence_class: EvidenceClass = "simulator"
    parameters: list[RequestedParameter] = Field(default_factory=list)
    input_bindings: list[StateInputBinding] = Field(default_factory=list)
    depends_on: list[Identifier] = Field(default_factory=list)
    required_policy_tags: list[Identifier] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_step_inputs(self) -> CampaignStepRequirement:
        names = [item.name for item in self.parameters]
        if len(names) != len(set(names)):
            raise ValueError("requested parameter names must be unique")
        targets = [item.target_port_id for item in self.input_bindings]
        if len(targets) != len(set(targets)):
            raise ValueError("step input bindings must have unique targets")
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("step dependencies must be unique")
        return self


class CampaignRequirement(StrictModel):
    document_type: Literal["dynamical.campaign-requirement"] = "dynamical.campaign-requirement"
    schema_version: Literal["0.1.0"] = SCHEMA_VERSION
    requirement_id: Identifier
    objective: EngineeringObjective
    inputs: list[CampaignInput] = Field(default_factory=list)
    steps: list[CampaignStepRequirement] = Field(min_length=1)
    max_cost_usd: Annotated[FiniteFloat, Field(ge=0.0)]
    max_duration_s: Annotated[FiniteFloat, Field(ge=0.0)]
    provider_preference: ProviderPreference = "prefer_lowest_evidence_class"

    @model_validator(mode="after")
    def unique_requirement_records(self) -> CampaignRequirement:
        input_ids = [item.id for item in self.inputs]
        if len(input_ids) != len(set(input_ids)):
            raise ValueError("campaign input IDs must be unique")
        step_ids = [item.step_id for item in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("campaign step IDs must be unique")
        return self


class ChannelValue(StrictModel):
    channel_id: ChannelId
    value: Scalar | list[FiniteFloat]
    unit: str = Field(min_length=1)


class MaterialState(StrictModel):
    id: Identifier
    container_asset_id: Identifier
    initial_channels: list[ChannelValue] = Field(min_length=1)
    source_refs: list[SourceRef] = Field(default_factory=list)


class ModelBinding(StrictModel):
    id: Identifier
    implementation_ref: str = Field(min_length=1)
    implementation_sha256: Sha256
    input_channel_map: dict[ChannelId, ChannelId] = Field(default_factory=dict)
    output_channel_map: dict[ChannelId, ChannelId] = Field(default_factory=dict)
    parameter_map: dict[Identifier, Scalar] = Field(default_factory=dict)
    update_interval_s: Annotated[FiniteFloat, Field(gt=0.0)]
    calibration_evidence_ids: list[Identifier] = Field(default_factory=list)


class IsaacAdapterConfig(StrictModel):
    isaac_sim_version: str = Field(min_length=1)
    isaac_lab_revision: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    experience: str | None = None
    vendor_asset_paths: dict[Identifier, str] = Field(default_factory=dict)


class OpenUsdAdapterConfig(StrictModel):
    material_policy: Literal["UsdPreviewSurface"] = "UsdPreviewSurface"
    vendor_references: bool = True


class ReplayAdapterConfig(StrictModel):
    trace_contract_version: Literal["0.1.0"] = "0.1.0"
    preserve_observation_origin: Literal[True] = True


AdapterConfiguration: TypeAlias = IsaacAdapterConfig | OpenUsdAdapterConfig | ReplayAdapterConfig


class AdapterBinding(StrictModel):
    id: Identifier
    target: Literal["isaac", "openusd", "replay"]
    subject_id: Identifier
    adapter_id: Identifier
    adapter_version: str = Field(min_length=1)
    binding_schema_ref: str = Field(min_length=1)
    configuration: AdapterConfiguration

    @model_validator(mode="after")
    def configuration_matches_target(self) -> AdapterBinding:
        expected = {
            "isaac": IsaacAdapterConfig,
            "openusd": OpenUsdAdapterConfig,
            "replay": ReplayAdapterConfig,
        }[self.target]
        if not isinstance(self.configuration, expected):
            raise ValueError(f"target {self.target!r} requires {expected.__name__}")
        return self


class BoundRange(StrictModel):
    minimum: FiniteFloat
    maximum: FiniteFloat

    @model_validator(mode="after")
    def ordered(self) -> BoundRange:
        if self.minimum > self.maximum:
            raise ValueError("constraint minimum must not exceed maximum")
        return self


class Constraint(StrictModel):
    id: Identifier
    phase: Literal["pre_action", "runtime", "post_action"]
    channel_id: ChannelId
    operator: Literal["lt", "le", "eq", "ge", "gt", "between"]
    bound: FiniteFloat | BoundRange
    unit: str = Field(min_length=1)
    enforcement: Literal["reject", "terminate", "report"]
    verifier_binding_id: Identifier
    # Declared, not inferred: the one action parameter (by name, on whichever
    # operation this constraint is bound to via a capability's
    # precondition/postcondition_constraint_ids) that carries the value this
    # constraint checks. Without this, a pre-action evaluator would have to guess
    # a binding by matching units, which silently picks the wrong parameter when
    # two of an operation's parameters share a unit. Optional: a constraint whose
    # measured channel is never sourced from a commanded parameter (e.g. it reads
    # a device's own sensor) has nothing to name here.
    constrained_parameter_name: Identifier | None = None

    @model_validator(mode="after")
    def comparator_bound(self) -> Constraint:
        if (self.operator == "between") != isinstance(self.bound, BoundRange):
            raise ValueError("'between' requires a range; other operators require a scalar")
        return self


class CalibrationMetric(StrictModel):
    name: Identifier
    value: FiniteFloat
    unit: str = Field(min_length=1)
    split: Literal["fit", "guard", "temporal_holdout", "independent_test"]
    # A gated metric declares the frozen threshold it was evaluated against.
    # Pass/fail is always recomputed from value, threshold, and comparator --
    # never trusted from a stored boolean.
    threshold: FiniteFloat | None = None
    comparator: Literal["le", "ge"] | None = None

    @model_validator(mode="after")
    def threshold_and_comparator_travel_together(self) -> CalibrationMetric:
        if (self.threshold is None) != (self.comparator is None):
            raise ValueError("a gated metric needs both threshold and comparator")
        return self

    def gate_passed(self) -> bool | None:
        """True/False for gated metrics, None for ungated ones."""

        if self.threshold is None or self.comparator is None:
            return None
        if self.comparator == "le":
            return self.value <= self.threshold
        return self.value >= self.threshold


class CalibrationEvidence(StrictModel):
    id: Identifier
    source_artifact_ref: str = Field(min_length=1)
    source_sha256: Sha256
    applies_to_model_binding_id: Identifier
    supported_channel_ids: list[ChannelId] = Field(min_length=1)
    condition_domain: dict[ChannelId, str] = Field(min_length=1)
    split_rule_ref: str = Field(min_length=1)
    split_rule_sha256: Sha256
    fit_artifact_ref: str = Field(min_length=1)
    fit_artifact_sha256: Sha256
    validation_design: Literal["within_trajectory", "cross_condition", "independent_facility_runs"]
    metrics: list[CalibrationMetric] = Field(default_factory=list)
    limitations: list[str] = Field(min_length=1)


class FacilityDocument(StrictModel):
    """Complete, versioned facility IR document."""

    document_type: Literal["dynamical.facility"] = "dynamical.facility"
    schema_version: Literal["0.1.0"] = SCHEMA_VERSION
    facility: Facility
    workstations: list[Workstation] = Field(min_length=1)
    assets: list[Asset] = Field(min_length=1)
    devices: list[Device] = Field(default_factory=list)
    agents: list[Agent] = Field(default_factory=list)
    capabilities: list[FacilityCapability] = Field(min_length=1)
    material_states: list[MaterialState] = Field(default_factory=list)
    model_bindings: list[ModelBinding] = Field(default_factory=list)
    adapter_bindings: list[AdapterBinding] = Field(min_length=1)
    provider_admission_bindings: list[FacilityProviderBinding] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)
    calibration_evidence: list[CalibrationEvidence] = Field(default_factory=list)
    asset_sources: list[AssetSource] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_graph(self) -> FacilityDocument:
        collections: dict[str, list[Any]] = {
            "workstation": self.workstations,
            "asset": self.assets,
            "device": self.devices,
            "agent": self.agents,
            "capability": self.capabilities,
            "material state": self.material_states,
            "model binding": self.model_bindings,
            "adapter binding": self.adapter_bindings,
            "provider admission binding": self.provider_admission_bindings,
            "constraint": self.constraints,
            "calibration evidence": self.calibration_evidence,
            "asset source": self.asset_sources,
        }
        all_ids: set[str] = {self.facility.id}
        ids_by_kind: dict[str, set[str]] = {}
        for kind, records in collections.items():
            ids = [record.id for record in records]
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate {kind} id")
            overlap = all_ids.intersection(ids)
            if overlap:
                raise ValueError(f"IDs must be globally unique; duplicate: {sorted(overlap)}")
            all_ids.update(ids)
            ids_by_kind[kind] = set(ids)

        workstation_ids = ids_by_kind["workstation"]
        if set(self.facility.workstation_ids) != workstation_ids:
            raise ValueError("facility.workstation_ids must exactly match declared workstations")
        for workstation in self.workstations:
            if workstation.facility_id != self.facility.id:
                raise ValueError(f"workstation {workstation.id} has a different facility_id")
            declared_assets = {
                asset.id for asset in self.assets if asset.workstation_id == workstation.id
            }
            if set(workstation.member_asset_ids) != declared_assets:
                raise ValueError(
                    f"workstation {workstation.id} member_asset_ids must match its assets"
                )

        asset_ids = ids_by_kind["asset"]
        capability_ids = ids_by_kind["capability"]
        constraint_ids = ids_by_kind["constraint"]
        model_ids = ids_by_kind["model binding"]
        evidence_ids = ids_by_kind["calibration evidence"]
        providers = ids_by_kind["device"] | ids_by_kind["agent"]
        channel_declarations = [
            channel.id for device in self.devices for channel in device.state_channels
        ] + [
            value.channel_id
            for material in self.material_states
            for value in material.initial_channels
        ]
        if len(channel_declarations) != len(set(channel_declarations)):
            raise ValueError("channel IDs must be unique across all facility providers")
        channel_ids = set(channel_declarations)

        admitted_source_ids = {
            source.id for source in self.asset_sources if source.admission == "admitted"
        }
        for asset in self.assets:
            if asset.workstation_id not in workstation_ids:
                raise ValueError(f"asset {asset.id} refers to an unknown workstation")
            mesh_source_id = asset.geometry.mesh_source_id
            if mesh_source_id is not None and mesh_source_id not in admitted_source_ids:
                raise ValueError(
                    f"asset {asset.id} geometry mesh_source_id {mesh_source_id!r} does not "
                    "resolve to an admitted asset source"
                )
        for endpoint in [*self.devices, *self.agents]:
            if endpoint.asset_id not in asset_ids:
                raise ValueError(f"{endpoint.id} refers to an unknown asset")
            unknown = set(endpoint.capability_ids) - capability_ids
            if unknown:
                raise ValueError(f"{endpoint.id} refers to unknown capabilities: {sorted(unknown)}")
            expected = {
                capability.id
                for capability in self.capabilities
                if capability.provider_id == endpoint.id
            }
            if len(endpoint.capability_ids) != len(set(endpoint.capability_ids)):
                raise ValueError(f"{endpoint.id} has duplicate capability IDs")
            if set(endpoint.capability_ids) != expected:
                raise ValueError(f"{endpoint.id} capability_ids must match provider ownership")
        for capability in self.capabilities:
            if capability.provider_id not in providers:
                raise ValueError(f"capability {capability.id} refers to an unknown provider")
            unknown_constraints = (
                set(capability.precondition_constraint_ids)
                | set(capability.postcondition_constraint_ids)
            ) - constraint_ids
            if unknown_constraints:
                raise ValueError(
                    f"capability {capability.id} refers to unknown constraints: "
                    f"{sorted(unknown_constraints)}"
                )
            unknown_channels = set(capability.observation_channel_ids) - channel_ids
            if unknown_channels:
                raise ValueError(
                    f"capability {capability.id} refers to unknown channels: "
                    f"{sorted(unknown_channels)}"
                )
        for material in self.material_states:
            if material.container_asset_id not in asset_ids:
                raise ValueError(f"material state {material.id} refers to an unknown container")
        for model in self.model_bindings:
            unknown_evidence = set(model.calibration_evidence_ids) - evidence_ids
            if unknown_evidence:
                raise ValueError(
                    f"model {model.id} refers to unknown evidence: {sorted(unknown_evidence)}"
                )
            expected_evidence = {
                evidence.id
                for evidence in self.calibration_evidence
                if evidence.applies_to_model_binding_id == model.id
            }
            if len(model.calibration_evidence_ids) != len(set(model.calibration_evidence_ids)):
                raise ValueError(f"model {model.id} has duplicate calibration evidence IDs")
            if set(model.calibration_evidence_ids) != expected_evidence:
                raise ValueError(
                    f"model {model.id} calibration_evidence_ids must match evidence ownership"
                )
            unknown_inputs = set(model.input_channel_map) - channel_ids
            unknown_outputs = set(model.output_channel_map.values()) - channel_ids
            if unknown_inputs or unknown_outputs:
                raise ValueError(
                    f"model {model.id} has unknown facility-side channels: "
                    f"{sorted(unknown_inputs | unknown_outputs)}"
                )
        for binding in self.adapter_bindings:
            if binding.subject_id not in all_ids:
                raise ValueError(f"adapter {binding.id} refers to an unknown subject")
        provider_endpoint_ids = ids_by_kind["device"] | ids_by_kind["agent"] | model_ids
        for binding in self.provider_admission_bindings:
            if binding.endpoint_id not in provider_endpoint_ids:
                raise ValueError(f"provider admission {binding.id} has an unknown endpoint")
            link_endpoints = {link.endpoint_ref for link in binding.adapter_links}
            unknown_endpoints = link_endpoints - provider_endpoint_ids
            if unknown_endpoints:
                raise ValueError(
                    f"provider admission {binding.id} has unknown adapter endpoints: "
                    f"{sorted(unknown_endpoints)}"
                )
            unknown_limits = set(binding.safety_limit_ids) - constraint_ids
            if unknown_limits:
                raise ValueError(
                    f"provider admission {binding.id} has unknown safety limits: "
                    f"{sorted(unknown_limits)}"
                )
        for constraint in self.constraints:
            if constraint.verifier_binding_id not in model_ids:
                raise ValueError(f"constraint {constraint.id} has an unknown verifier binding")
            if constraint.channel_id not in channel_ids:
                raise ValueError(f"constraint {constraint.id} has an unknown channel")
        for evidence in self.calibration_evidence:
            if evidence.applies_to_model_binding_id not in model_ids:
                raise ValueError(f"evidence {evidence.id} refers to an unknown model binding")
            unknown_channels = set(evidence.supported_channel_ids) - channel_ids
            if unknown_channels:
                raise ValueError(
                    f"evidence {evidence.id} refers to unknown channels: {sorted(unknown_channels)}"
                )
            unsupported_domain = set(evidence.condition_domain) - set(
                evidence.supported_channel_ids
            )
            if unsupported_domain:
                raise ValueError(
                    f"evidence {evidence.id} condition domain is not supported: "
                    f"{sorted(unsupported_domain)}"
                )
        return self

    def canonical_payload(self, *, include_adapters: bool = True) -> dict[str, Any]:
        payload = self.model_dump(mode="json", exclude_none=True)
        if not include_adapters:
            payload.pop("adapter_bindings", None)
        return payload

    def core_ir_sha256(self) -> str:
        return canonical_sha256(self.canonical_payload(include_adapters=False))

    def adapter_pack_sha256(self, target: str) -> str:
        bindings = [
            binding.model_dump(mode="json", exclude_none=True)
            for binding in sorted(self.adapter_bindings, key=lambda item: item.id)
            if binding.target == target
        ]
        return canonical_sha256(
            {"schema_version": self.schema_version, "target": target, "bindings": bindings}
        )


def canonical_json_bytes(value: Any) -> bytes:
    """Return UTF-8 canonical JSON bytes for content addressing."""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def load_facility_manifest(path: str | Path) -> FacilityDocument:
    """Load JSON or YAML and validate it as a strict facility document."""

    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    raw_text = source.read_text(encoding="utf-8")
    if source.suffix.lower() == ".json":
        raw = json.loads(raw_text)
    elif source.suffix.lower() in {".yaml", ".yml"}:
        raw = yaml.safe_load(raw_text)
    else:
        raise ValueError("facility manifest must use .json, .yaml, or .yml")
    if not isinstance(raw, dict):
        raise ValueError("facility manifest root must be an object")
    return FacilityDocument.model_validate(raw)


def _load_versioned_document(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    raw_text = source.read_text(encoding="utf-8")
    if source.suffix.lower() == ".json":
        raw = json.loads(raw_text)
    elif source.suffix.lower() in {".yaml", ".yml"}:
        raw = yaml.safe_load(raw_text)
    else:
        raise ValueError("document must use .json, .yaml, or .yml")
    if not isinstance(raw, dict):
        raise ValueError("document root must be an object")
    return raw


def load_capability_registry(path: str | Path) -> CapabilityRegistry:
    """Load a strict provider registry. Registry admission is not read from requests."""

    return CapabilityRegistry.model_validate(_load_versioned_document(path))


def load_campaign_requirement(path: str | Path) -> CampaignRequirement:
    """Load a strict engineering objective and campaign capability request."""

    return CampaignRequirement.model_validate(_load_versioned_document(path))


def export_facility_schema(path: str | Path) -> None:
    """Write the JSON Schema generated from the executable validator."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(FacilityDocument.model_json_schema(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def export_composition_schemas(directory: str | Path) -> None:
    """Publish executable request and registry schemas."""

    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    schemas = {
        "capability-registry.schema.json": CapabilityRegistry.model_json_schema(),
        "campaign-requirement.schema.json": CampaignRequirement.model_json_schema(),
    }
    for name, schema in schemas.items():
        (target / name).write_text(
            json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


def safe_usd_identifier(value: str) -> str:
    """Map an IR identifier to a stable USD-compatible identifier."""

    result = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if not result or result[0].isdigit():
        result = f"_{result}"
    return result
