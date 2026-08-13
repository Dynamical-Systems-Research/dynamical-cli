from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

from dynamical.campaign import (
    ActionRequest,
    ConstraintEvaluation,
    EvidenceClass,
    ObservationChannel,
    ObservationFrame,
    ObservationOrigin,
    RunMode,
    TraceEvent,
    stable_hash,
)
from dynamical.compiler import compile_facility
from dynamical.composition import compose_files

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ELECTRODEPOSITION_REGISTRY = REPOSITORY_ROOT / "dynamical" / "bundle" / "registry.yaml"
ELECTRODEPOSITION_MANIFEST = REPOSITORY_ROOT / "dynamical" / "bundle" / "facility.yaml"

REFERENCE_REQUIREMENT = {
    "document_type": "dynamical.campaign-requirement",
    "schema_version": "0.1.0",
    "requirement_id": "electrodeposition-transfer-and-conditioning-proof",
    "objective": {
        "id": "select-and-check-conditioned-sample",
        "statement": (
            "Transfer one sample to the ultrasonic conditioner and run one bounded "
            "conditioning program."
        ),
        "decision": "Decide if the virtual result merits a later physical experiment.",
        "proof_requirements": [
            {
                "id": "conditioning-proof",
                "operation_id": "condition-ultrasonic",
                "output_port_ids": ["instrument.conditioning_duration_s"],
                "minimum_evidence_class": "simulator",
                "acceptance_rule": "The simulator trace and replay pass.",
                "independent_verification_required": True,
            },
        ],
    },
    "inputs": [
        {
            "id": "campaign.sample-id",
            "state_type": "sample_state",
            "unit": "1",
            "value": "sample-electrodeposition-01",
            "facility_id": "arduino-conditioning",
        },
    ],
    "steps": [
        {
            "step_id": "transfer",
            "operation_id": "transfer-sample",
            "minimum_evidence_class": "simulator",
            "parameters": [
                {
                    "name": "to_station",
                    "value_type": "string",
                    "unit": "1",
                    "value": "arduino-conditioning",
                },
                {
                    "name": "sample_id",
                    "value_type": "string",
                    "unit": "1",
                    "value": "sample-electrodeposition-01",
                },
            ],
            "input_bindings": [
                {
                    "target_port_id": "sample.state",
                    "source_kind": "campaign_input",
                    "source_id": "campaign.sample-id",
                }
            ],
            "depends_on": [],
            "required_policy_tags": ["simulation-only", "custody-bookkeeping-only"],
        },
        {
            "step_id": "condition",
            "operation_id": "condition-ultrasonic",
            "minimum_evidence_class": "simulator",
            "parameters": [
                {
                    "name": "duration_s",
                    "value_type": "number",
                    "unit": "s",
                    "value": 60.0,
                },
                {
                    "name": "setpoint_percent",
                    "value_type": "number",
                    "unit": "%",
                    "value": 80.0,
                },
            ],
            "input_bindings": [
                {
                    "target_port_id": "sample.state",
                    "source_kind": "step_output",
                    "source_id": "transfer",
                    "source_port_id": "sample.state.transferred",
                },
            ],
            "depends_on": ["transfer"],
            "required_policy_tags": ["simulation-only"],
        },
    ],
    "max_cost_usd": 0.0,
    "max_duration_s": 1200.0,
}


def write_reference_requirement(path: Path) -> Path:
    path.write_text(yaml.safe_dump(REFERENCE_REQUIREMENT, sort_keys=False), encoding="utf-8")
    return path


def _identity_fields(run_id: str) -> dict[str, Any]:
    return {
        "campaign_id": f"campaign-{run_id}",
        "run_id": run_id,
        "seed": 1,
        "backend_revision": "fixture-backend:not_embodied",
        "ir_hash": stable_hash({"fixture": run_id, "part": "ir"}),
        "world_hash": stable_hash({"fixture": run_id, "part": "world"}),
        "campaign_hash": stable_hash({"fixture": run_id, "part": "campaign"}),
    }


@pytest.fixture
def failed_trace_events() -> list[TraceEvent]:
    """A one-step trace whose terminal event is honestly marked failed.

    The "heat" action carries one violated pre-action constraint (mass must be
    positive but measures -1.0 kg), matching the declared applicability contract
    exactly so the deep constraint cross-checks in validate_events accept it.
    """

    identity = _identity_fields("failed-run")
    declarations = {
        "mass-positive": {
            "phase": "pre_action",
            "channel_id": "material.mass_kg",
            "operator": "gt",
            "bound": 0.0,
            "unit": "kg",
            "enforcement": "reject",
            "verifier_binding_id": "mass-verifier",
        }
    }
    applicability = {"heat": {"pre_action": ["mass-positive"], "observation": []}}
    constraint_contract = {"declarations": declarations, "applicability": applicability}
    constraint_contract_sha256 = stable_hash(constraint_contract)
    shared_provenance = {
        "constraint_contract": constraint_contract,
        "constraint_contract_sha256": constraint_contract_sha256,
    }

    action = ActionRequest(
        action_id="heat",
        kind="apply-thermal-program",
        actor_id="thermal-model",
        provider_id="thermal-program-simulator",
        evidence_class=EvidenceClass.SIMULATOR,
        parameters={"target-temperature": 343.15, "dwell-time": 10.0},
    )
    violated = ConstraintEvaluation(
        constraint_id="mass-positive",
        phase="pre_action",
        passed=False,
        outcome="violated",
        measured_value=-1.0,
        limit={"operator": "gt", "bound": 0.0, "unit": "kg", "enforcement": "reject"},
        verifier="mass-verifier",
    )
    observation = ObservationFrame(
        frame_id="frame-after-heat",
        logical_time_s=1.0,
        provider_id="thermal-program-simulator",
        evidence_class=EvidenceClass.SIMULATOR,
        channels=(
            ObservationChannel(
                name="thermal.sample_temperature_K",
                value=None,
                unit="K",
                quality="unavailable",
                origin=ObservationOrigin.SOURCE_MODEL,
                provider_id="thermal-program-simulator",
                evidence_class=EvidenceClass.SIMULATOR,
                uncertainty={"value": 0.0, "kind": "declared", "origin": "test fixture"},
            ),
        ),
    )
    reasons = [
        {
            "code": "CONSTRAINT_VIOLATED",
            "detail": "constraint mass-positive failed: measured -1.0 kg",
            "step_id": "heat",
            "channel_id": "material.mass_kg",
            "recoverable": False,
        }
    ]

    def event(**kwargs: Any) -> TraceEvent:
        return TraceEvent(mode=RunMode.SIMULATE, **identity, **kwargs)

    return [
        event(
            event_type="campaign_start",
            event_id="failed-run:event:000000",
            sequence=0,
            logical_time_s=0.0,
            provenance={**shared_provenance, "declared_step_ids": ["heat"]},
        ),
        event(
            event_type="action",
            event_id="failed-run:event:000001",
            sequence=1,
            logical_time_s=0.0,
            provenance=dict(shared_provenance),
            action=action,
            constraints=(violated,),
        ),
        event(
            event_type="observation",
            event_id="failed-run:event:000002",
            sequence=2,
            logical_time_s=1.0,
            provenance=dict(shared_provenance),
            observation=observation,
        ),
        event(
            event_type="campaign_end",
            event_id="failed-run:event:000003",
            sequence=3,
            logical_time_s=1.0,
            provenance={
                **shared_provenance,
                "execution_status": "failed",
                "reasons": reasons,
            },
        ),
    ]


@pytest.fixture
def truncated_trace_events() -> list[TraceEvent]:
    """A trace that declares two steps but only executed the first.

    The terminal event is (incorrectly) marked "passed", the way a runner that does
    not check step coverage would report it. validate_events must catch the gap
    and override the reported status.
    """

    identity = _identity_fields("truncated-run")
    action = ActionRequest(
        action_id="prepare",
        kind="agitate-sample",
        actor_id="agitation-model",
        provider_id="thermal-agitation-simulator",
        evidence_class=EvidenceClass.SIMULATOR,
        parameters={"agitation-rate": 300.0},
    )
    observation = ObservationFrame(
        frame_id="frame-after-prepare",
        logical_time_s=1.0,
        provider_id="thermal-agitation-simulator",
        evidence_class=EvidenceClass.SIMULATOR,
        channels=(
            ObservationChannel(
                name="instrument.agitation_rate_rpm",
                value=300.0,
                unit="rpm",
                quality="valid",
                origin=ObservationOrigin.SOURCE_MODEL,
                provider_id="thermal-agitation-simulator",
                evidence_class=EvidenceClass.SIMULATOR,
                uncertainty={"value": 0.0, "kind": "declared", "origin": "test fixture"},
            ),
        ),
    )

    def event(**kwargs: Any) -> TraceEvent:
        return TraceEvent(mode=RunMode.SIMULATE, **identity, **kwargs)

    return [
        event(
            event_type="campaign_start",
            event_id="truncated-run:event:000000",
            sequence=0,
            logical_time_s=0.0,
            provenance={"declared_step_ids": ["prepare", "heat"]},
        ),
        event(
            event_type="action",
            event_id="truncated-run:event:000001",
            sequence=1,
            logical_time_s=0.0,
            provenance={},
            action=action,
        ),
        event(
            event_type="observation",
            event_id="truncated-run:event:000002",
            sequence=2,
            logical_time_s=1.0,
            provenance={},
            observation=observation,
        ),
        event(
            event_type="campaign_end",
            event_id="truncated-run:event:000003",
            sequence=3,
            logical_time_s=1.0,
            provenance={"execution_status": "passed"},
        ),
    ]


def _sample_action(
    *,
    action_id: str,
    actor_id: str,
    sample_id: str | None,
    transition: Mapping[str, Any] | None = None,
    station_id: str | None = None,
) -> ActionRequest:
    """One minimal action, optionally carrying an embedded SampleTransition.

    A sample-moving action (transfer, aliquot, consume) encodes its
    ``SampleTransition`` under ``parameters["sample_transition"]``; an action that
    merely operates on a sample already in custody sets only ``sample_id``.

    ``actor_id`` (the acting instrument's own endpoint id) and ``station_id``
    (the workstation it acts at, resolved from the compiled binding) are
    deliberately two different fields: check_invariants compares a sample's
    station against ``station_id``, never ``actor_id`` -- fixtures below use
    visibly different values for the two so a regression back to comparing
    ``actor_id`` would be caught, not silently tolerated by coincidence.
    """

    parameters: dict[str, Any] = {}
    if transition is not None:
        parameters["sample_transition"] = dict(transition)
    return ActionRequest(
        action_id=action_id,
        kind="material_transfer" if transition is not None else "observe",
        actor_id=actor_id,
        provider_id="lineage-fixture-provider",
        evidence_class=EvidenceClass.SIMULATOR,
        parameters=parameters,
        sample_id=sample_id,
        station_id=station_id,
    )


def _sample_observation(
    *, frame_id: str, provider_id: str, logical_time_s: float
) -> ObservationFrame:
    return ObservationFrame(
        frame_id=frame_id,
        logical_time_s=logical_time_s,
        provider_id=provider_id,
        evidence_class=EvidenceClass.SIMULATOR,
        channels=(
            ObservationChannel(
                name="lineage.fixture_channel",
                value=True,
                unit="1",
                quality="valid",
                origin=ObservationOrigin.SOURCE_MODEL,
                provider_id=provider_id,
                evidence_class=EvidenceClass.SIMULATOR,
                uncertainty={"value": 0.0, "kind": "declared", "origin": "test fixture"},
            ),
        ),
    )


def _sample_trace(run_id: str, actions: list[ActionRequest]) -> list[TraceEvent]:
    """Wrap a sequence of sample-lineage actions in a minimal well-formed trace.

    check_invariants only reads event.action, so this trace carries no
    constraints and no declared step coverage -- it is not meant to be passed
    to validate_events directly, only to check_invariants.
    """

    identity = _identity_fields(run_id)

    def event(**kwargs: Any) -> TraceEvent:
        return TraceEvent(mode=RunMode.SIMULATE, **identity, **kwargs)

    events = [
        event(
            event_type="campaign_start",
            event_id=f"{run_id}:event:000000",
            sequence=0,
            logical_time_s=0.0,
            provenance={},
        )
    ]
    sequence = 1
    logical_time = 0.0
    for action in actions:
        logical_time += 1.0
        events.append(
            event(
                event_type="action",
                event_id=f"{run_id}:event:{sequence:06d}",
                sequence=sequence,
                logical_time_s=logical_time,
                provenance={},
                action=action,
            )
        )
        sequence += 1
        events.append(
            event(
                event_type="observation",
                event_id=f"{run_id}:event:{sequence:06d}",
                sequence=sequence,
                logical_time_s=logical_time,
                provenance={},
                observation=_sample_observation(
                    frame_id=f"frame-after-{action.action_id}",
                    provider_id=action.provider_id,
                    logical_time_s=logical_time,
                ),
            )
        )
        sequence += 1
    events.append(
        event(
            event_type="campaign_end",
            event_id=f"{run_id}:event:{sequence:06d}",
            sequence=sequence,
            logical_time_s=logical_time,
            provenance={"execution_status": "passed"},
        )
    )
    return events


@pytest.fixture
def trace_with_vanished_sample() -> list[TraceEvent]:
    """A sample departs a station and the trace ends without a confirmed arrival."""

    action = _sample_action(
        action_id="transfer-out",
        actor_id="ac-transfer-model",
        station_id="bench-a",
        sample_id="sample-1",
        transition={
            "kind": "transfer",
            "sample_id": "sample-1",
            "from_station": "bench-a",
            "to_station": "bench-b",
            "quantity_delta": 0.0,
            "unit": "mL",
            "timestamp_s": 5.0,
            "arrival_confirmed": False,
            "parent_sample_ids": [],
        },
    )
    return _sample_trace("vanished-sample", [action])


@pytest.fixture
def trace_with_forked_sample() -> list[TraceEvent]:
    """The same sample is transferred out of a station it never left."""

    create = _sample_action(
        action_id="create",
        actor_id="ac-transfer-model",
        station_id="bench-a",
        sample_id="sample-x",
        transition={
            "kind": "transfer",
            "sample_id": "sample-x",
            "from_station": "intake",
            "to_station": "bench-a",
            "quantity_delta": 5.0,
            "unit": "mL",
            "timestamp_s": 1.0,
            "arrival_confirmed": True,
            "parent_sample_ids": [],
        },
    )
    fork = _sample_action(
        action_id="fork",
        actor_id="ac-transfer-model",
        station_id="bench-c",
        sample_id="sample-x",
        transition={
            "kind": "transfer",
            "sample_id": "sample-x",
            "from_station": "intake",
            "to_station": "bench-c",
            "quantity_delta": 0.0,
            "unit": "mL",
            "timestamp_s": 2.0,
            "arrival_confirmed": True,
            "parent_sample_ids": [],
        },
    )
    return _sample_trace("forked-sample", [create, fork])


@pytest.fixture
def trace_with_missing_transfer() -> list[TraceEvent]:
    """A sample is read at a station it was never transferred to.

    ``prep`` is the sample's first-ever mention in the trace -- like a
    campaign-declared sample_state input, it has no upstream "create sample"
    operation -- so it honestly establishes the sample's origin at
    "ot2-liquid-handling" and must NOT itself be flagged (see
    dynamical.samples.establish_origin). ``measure`` then reads the same
    sample_id at a completely different station, with no transfer of any
    kind in between: that is the genuine missing-transfer case.
    """

    prep = _sample_action(
        action_id="prep",
        actor_id="ac-opentron-model",
        station_id="ot2-liquid-handling",
        sample_id="sample-q",
    )
    measure = _sample_action(
        action_id="measure",
        actor_id="ac-oer-model",
        station_id="squidstat-echem",
        sample_id="sample-q",
    )
    return _sample_trace("missing-transfer", [prep, measure])


class _CompositionDocument(dict):
    """A mapping that also exposes its top-level keys as attributes.

    ``_runtime_pack.py`` reads a "composition" both as a document (``records()``
    / ``document_mapping()`` treat it as a ``Mapping``) and, in test code, the
    way the real ``VirtualSDL`` model is read (``composition.operation_bindings``
    as an attribute). This stands in for both without needing a full pydantic
    model in test fixtures.
    """

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def _electrodeposition_capability_contract(
    operation_id: str, parameters: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "kind": "scientific",
        "description": f"fixture contract for {operation_id}",
        "input_ports": [],
        "output_ports": [],
        "parameters": parameters,
        "required_conditions": [],
        "possible_failures": [],
    }


@pytest.fixture
def three_station_composition() -> _CompositionDocument:
    """Three real AC SDL1 operations, deliberately unordered, with real dependency edges.

    Grounded in the instrument models and endpoint ids declared in
    ``dynamical/bundle/registry.yaml``: dispense on the OT-2,
    deposit on the Squidstat, measure OER on the Squidstat. The bindings list is
    given out of dependency order so the test only passes if runtime_campaign
    performs a real topological walk of dependency_edges rather than trusting
    input order.
    """

    operation_bindings = [
        {
            "step_id": "measure",
            "operation_id": "measure-oer",
            "provider_id": "ac-oer-simulator",
            "evidence_class": "simulator",
            "endpoint_id": "ac-oer-model",
            "parameters": [{"name": "current_density_a_cm2", "value": 0.002}],
            "capability_contract": _electrodeposition_capability_contract(
                "measure-oer",
                [
                    {
                        "name": "current_density_a_cm2",
                        "value_type": "number",
                        "unit": "A/cm^2",
                        "required": True,
                        "minimum": 0.0001,
                        "maximum": 0.1,
                    }
                ],
            ),
        },
        {
            "step_id": "dispense",
            "operation_id": "dispense-electrolyte",
            "provider_id": "ac-ot2-simulator",
            "evidence_class": "simulator",
            "endpoint_id": "ac-opentron-model",
            "parameters": [{"name": "volume_ml", "value": 20.0}],
            "capability_contract": _electrodeposition_capability_contract(
                "dispense-electrolyte",
                [
                    {
                        "name": "volume_ml",
                        "value_type": "number",
                        "unit": "mL",
                        "required": True,
                        "minimum": 0.0,
                        "maximum": 25.0,
                    }
                ],
            ),
        },
        {
            "step_id": "electrodeposit",
            "operation_id": "electrodeposit-constant-current",
            "provider_id": "ac-squidstat-simulator",
            "evidence_class": "simulator",
            "endpoint_id": "ac-potentiostat-model",
            "parameters": [
                {"name": "current_a", "value": 0.002827},
                {"name": "duration_s", "value": 600.0},
            ],
            "capability_contract": _electrodeposition_capability_contract(
                "electrodeposit-constant-current",
                [
                    {
                        "name": "current_a",
                        "value_type": "number",
                        "unit": "A",
                        "required": True,
                        "minimum": 0.0,
                        "maximum": 0.010,
                    },
                    {
                        "name": "duration_s",
                        "value_type": "number",
                        "unit": "s",
                        "required": True,
                        "minimum": 0.0,
                        "maximum": 3600.0,
                    },
                ],
            ),
        },
    ]
    dependency_edges = [
        {"source_step_id": "dispense", "target_step_id": "electrodeposit"},
        {"source_step_id": "electrodeposit", "target_step_id": "measure"},
    ]
    # runtime_campaign() resolves each selected operation to the one
    # facility-declared action_type it embodies, so the fixture must declare a
    # device per operation binding's endpoint_id with exactly one capability.
    # Real short verbs from dynamical/bundle/facility.yaml's
    # capabilities section (dispense/electrodeposit/measure), keyed here by
    # the operation binding's own endpoint_id rather than the real manifest's
    # separate device id, to keep the fixture self-contained.
    devices = [
        {
            "id": "ac-opentron-model",
            "asset_id": "ot2-robot-body",
            "state_channels": [],
            "capability_ids": ["dispense-electrolyte-capability"],
        },
        {
            "id": "ac-potentiostat-model",
            "asset_id": "squidstat-instrument-body",
            "state_channels": [],
            "capability_ids": ["electrodeposit-constant-current-capability"],
        },
        {
            "id": "ac-oer-model",
            "asset_id": "squidstat-instrument-body",
            "state_channels": [],
            "capability_ids": ["measure-oer-capability"],
        },
    ]
    capabilities = [
        {
            "id": "dispense-electrolyte-capability",
            "provider_id": "ac-opentron-model",
            "action_type": "dispense",
            "parameters": [],
        },
        {
            "id": "electrodeposit-constant-current-capability",
            "provider_id": "ac-potentiostat-model",
            "action_type": "electrodeposit",
            "parameters": [],
        },
        {
            "id": "measure-oer-capability",
            "provider_id": "ac-oer-model",
            "action_type": "measure",
            "parameters": [],
        },
    ]
    return _CompositionDocument(
        {
            "devices": devices,
            "agents": [],
            "capabilities": capabilities,
            "operation_bindings": operation_bindings,
            "dependency_edges": dependency_edges,
        }
    )


@pytest.fixture
def two_observer_composition() -> _CompositionDocument:
    """Two distinct instruments that both declare the ``observe`` action type.

    The real electrodeposition registry has no two instruments sharing an
    action_type today, so this is deliberately synthetic -- it exists purely
    to prove runtime_capability_bindings keys by (instrument_id, action_type)
    rather than colliding on action_type alone.
    """

    return _CompositionDocument(
        {
            "devices": [
                {
                    "id": "thermocouple-device",
                    "asset_id": "thermocouple-1",
                    "state_channels": [],
                    "capability_ids": ["thermocouple-observe-capability"],
                },
                {
                    "id": "ph-meter-device",
                    "asset_id": "ph-meter-1",
                    "state_channels": [],
                    "capability_ids": ["ph-meter-observe-capability"],
                },
            ],
            "agents": [],
            "capabilities": [
                {
                    "id": "thermocouple-observe-capability",
                    "provider_id": "thermocouple-device",
                    "action_type": "observe",
                    "parameters": [],
                },
                {
                    "id": "ph-meter-observe-capability",
                    "provider_id": "ph-meter-device",
                    "action_type": "observe",
                    "parameters": [],
                },
            ],
        }
    )


@pytest.fixture
def trace_with_complete_lineage() -> list[TraceEvent]:
    """A prep transfer followed by a measurement at the sample's confirmed station.

    ``measure``'s actor_id ("ac-oer-model") is deliberately NOT its
    station_id ("bench-a") -- exactly the shape a real instrument endpoint
    takes in the electrodeposition facility. This fixture would spuriously
    fail under a comparison against actor_id; it must pass under the
    corrected station_id comparison.
    """

    prep = _sample_action(
        action_id="prep",
        actor_id="ac-transfer-model",
        station_id="bench-a",
        sample_id="sample-1",
        transition={
            "kind": "transfer",
            "sample_id": "sample-1",
            "from_station": "intake",
            "to_station": "bench-a",
            "quantity_delta": 5.0,
            "unit": "mL",
            "timestamp_s": 1.0,
            "arrival_confirmed": True,
            "parent_sample_ids": [],
        },
    )
    measure = _sample_action(
        action_id="measure",
        actor_id="ac-oer-model",
        station_id="bench-a",
        sample_id="sample-1",
    )
    return _sample_trace("complete-lineage", [prep, measure])


@pytest.fixture
def compiled_electrodeposition_world(tmp_path: Path) -> Path:
    """The shared electrodeposition reference requirement (transfer + condition),
    composed and compiled for the ``isaac`` target -- a real compiled pack a live
    Isaac Sim run can open and execute, not a synthetic fixture.
    """

    requirement = write_reference_requirement(tmp_path / "requirement.yaml")
    composition = compose_files(requirement, ELECTRODEPOSITION_REGISTRY, ELECTRODEPOSITION_MANIFEST)
    assert composition.status == "COMPILED", composition.reason_codes
    result = compile_facility(
        ELECTRODEPOSITION_MANIFEST,
        "isaac",
        tmp_path / "isaac-world",
        composition_result=composition,
    )
    return result.output_dir


@pytest.fixture
def compiled_electrodeposition_coverage_world(tmp_path: Path) -> Path:
    """A synthetic multi-instrument coverage campaign compiled for the ``isaac``
    target -- one sample moving across workstations by explicit transfer
    actions, exercising the mechanisms a live Kit run must prove: composition,
    multi-instrument execution, and continuous sample lineage. The step order
    and parameters are harness-selected coverage, not a recommended experiment.

    Reuses ``test_electrodeposition_registry``'s own ``_coverage_requirement``
    (imported locally, not duplicated) so there is exactly one definition of
    this fixture campaign to drift out of sync.
    """

    import test_electrodeposition_registry as coverage

    from dynamical.composition import compose_virtual_sdl
    from dynamical.schema import load_capability_registry

    registry = load_capability_registry(coverage.REGISTRY)
    requirement = coverage._coverage_requirement()
    composition = compose_virtual_sdl(requirement, registry)
    assert composition.status == "COMPILED", composition.reason_codes
    result = compile_facility(
        coverage.MANIFEST,
        "isaac",
        tmp_path / "isaac-coverage-world",
        composition_result=composition,
    )
    return result.output_dir
