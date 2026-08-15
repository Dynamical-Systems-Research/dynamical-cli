from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from dynamical.composition import compose_virtual_sdl
from dynamical.schema import (
    CampaignRequirement,
    CapabilityRegistry,
)


def _capability(
    operation_id: str = "measure_phase_composition",
    *,
    kind: str = "scientific",
    inputs: list[dict[str, object]] | None = None,
    outputs: list[dict[str, object]] | None = None,
    parameters: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "operation_id": operation_id,
        "kind": kind,
        "description": f"Execute {operation_id}",
        "input_ports": inputs
        if inputs is not None
        else [
            {
                "id": "fluidA/CCCl",
                "state_type": "material_state",
                "unit": "mol/kg",
                "description": "Source concentration state",
            }
        ],
        "output_ports": outputs
        if outputs is not None
        else [
            {
                "id": "phase_fraction",
                "state_type": "number",
                "unit": "1",
                "description": "Measured phase fraction",
            }
        ],
        "parameters": parameters
        if parameters is not None
        else [
            {
                "name": "setpoint",
                "value_type": "number",
                "unit": "K",
                "minimum": 300.0,
                "maximum": 1000.0,
            }
        ],
        "required_conditions": [],
        "possible_failures": ["instrument fault"],
    }


def _provider(
    provider_id: str,
    evidence_class: str,
    facility_id: str,
    *,
    operation_id: str = "measure_phase_composition",
) -> dict[str, object]:
    return {
        "provider_id": provider_id,
        "operation_id": operation_id,
        "evidence_class": evidence_class,
        "endpoint_id": f"{provider_id}-endpoint",
        "facility_ids": [facility_id],
        "availability": {"available": True, "observed_at": "2026-08-06T00:00:00Z"},
        "admission": {
            "status": "admitted",
            "authority_id": "independent-reviewer",
            "authority_kind": "independent_verifier",
            "evidence_refs": ["sha256:admission-receipt"],
        },
        "validity_envelope": [
            {
                "subject_kind": "input",
                "subject_id": "fluidA/CCCl",
                "value_type": "material_state",
                "unit": "mol/kg",
                "minimum": 0.0,
                "maximum": 1.0,
            },
            {
                "subject_kind": "parameter",
                "subject_id": "setpoint",
                "value_type": "number",
                "unit": "K",
                "minimum": 300.0,
                "maximum": 900.0,
            },
        ],
        "cost": {"currency": "USD", "fixed": 1.0},
        "duration": {"minimum_s": 1.0, "typical_s": 2.0, "maximum_s": 3.0},
        "failures": [],
        "policy": {
            "permitted": True,
            "policy_tags": ["standard"],
            "safety_limit_ids": ["temperature-limit"],
            "required_approval_ids": [],
        },
        "adapter_links": [
            {
                "adapter_id": "reference-adapter",
                "adapter_version": "0.1.0",
                "endpoint_ref": f"provider://{provider_id}",
            }
        ],
    }


def _registry() -> dict[str, object]:
    return {
        "document_type": "dynamical.capability-registry",
        "schema_version": "0.1.0",
        "registry_id": "test-registry",
        "capabilities": [_capability()],
        "providers": [
            _provider(
                "figure5-source-condition-simulator",
                "simulator",
                "source-model-facility",
            ),
            _provider(
                "legacy-heater-workstation-simulator",
                "physical",
                "legacy-inorganic-lab",
            ),
        ],
    }


def _request(evidence_class: str = "simulator") -> dict[str, object]:
    return {
        "document_type": "dynamical.campaign-requirement",
        "schema_version": "0.1.0",
        "requirement_id": "phase-decision-campaign",
        "objective": {
            "id": "phase-decision",
            "statement": "Measure phase composition for an engineering decision.",
            "decision": "Select the bounded condition.",
            "proof_requirements": [
                {
                    "id": "phase-proof",
                    "operation_id": "measure_phase_composition",
                    "output_port_ids": ["phase_fraction"],
                    "minimum_evidence_class": evidence_class,
                    "acceptance_rule": "phase_fraction is recorded",
                    "independent_verification_required": True,
                }
            ],
        },
        "inputs": [
            {
                "id": "fluidA/CCCl",
                "state_type": "material_state",
                "unit": "mol/kg",
                "value": 1.0,
            }
        ],
        "steps": [
            {
                "step_id": "measure",
                "operation_id": "measure_phase_composition",
                "minimum_evidence_class": evidence_class,
                "parameters": [
                    {
                        "name": "setpoint",
                        "value_type": "number",
                        "unit": "K",
                        "value": 500.0,
                    }
                ],
                "input_bindings": [
                    {
                        "target_port_id": "fluidA/CCCl",
                        "source_kind": "campaign_input",
                        "source_id": "fluidA/CCCl",
                    }
                ],
                "depends_on": [],
                "required_policy_tags": ["standard"],
            }
        ],
        "max_cost_usd": 100.0,
        "max_duration_s": 100.0,
    }


def _compose(
    request: dict[str, object] | None = None,
    registry: dict[str, object] | None = None,
):
    return compose_virtual_sdl(
        CampaignRequirement.model_validate(request or _request()),
        CapabilityRegistry.model_validate(registry or _registry()),
    )


def test_provider_independent_operation_survives_evidence_provider_change() -> None:
    simulated = _compose(_request("simulator"))
    physical = _compose(_request("physical"))

    assert simulated.status == physical.status == "COMPILED"
    assert simulated.virtual_sdl is not None
    assert physical.virtual_sdl is not None
    sim_binding = simulated.virtual_sdl.operation_bindings[0]
    physical_binding = physical.virtual_sdl.operation_bindings[0]
    assert sim_binding.operation_id == physical_binding.operation_id
    assert sim_binding.provider_id == "figure5-source-condition-simulator"
    assert physical_binding.provider_id == "legacy-heater-workstation-simulator"


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (
            lambda value: value["providers"][1]["admission"].update(status="rejected"),
            "PROVIDER_NOT_ADMITTED",
        ),
        (
            lambda value: value["providers"][1]["availability"].update(available=False),
            "PROVIDER_UNAVAILABLE",
        ),
        (
            lambda value: value["providers"][1]["policy"].update(permitted=False),
            "SAFETY_POLICY_REJECTED",
        ),
    ],
)
def test_provider_admission_availability_and_safety_fail_closed(mutation, code: str) -> None:
    registry = _registry()
    mutation(registry)

    result = _compose(_request("physical"), registry)

    assert result.status == "HOLD"
    assert code in result.reason_codes


def test_missing_capability_returns_stable_hold() -> None:
    request = _request()
    request["steps"][0]["operation_id"] = "missing_operation"
    request["objective"]["proof_requirements"][0]["operation_id"] = "missing_operation"

    result = _compose(request)

    assert result.status == "HOLD"
    assert "PROOF_OPERATION_MISSING" in result.reason_codes


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("unit", "kg/mol", "UNIT_MISMATCH"),
        ("state_type", "sample_state", "STATE_TYPE_MISMATCH"),
    ],
)
def test_exact_input_state_and_unit_contract(field: str, value: str, code: str) -> None:
    request = _request()
    request["inputs"][0][field] = value

    result = _compose(request)

    assert result.status == "HOLD"
    assert code in result.reason_codes


def test_bound_campaign_input_requires_an_executable_value() -> None:
    request = _request()
    request["inputs"][0].pop("value")

    result = _compose(request)

    assert result.status == "HOLD"
    assert result.reason_codes == ["MISSING_INPUT_VALUE"]


def test_invalid_range_and_unknown_physical_setpoint_fail_closed() -> None:
    request = _request("physical")
    request["steps"][0]["parameters"][0]["value"] = 950.0
    result = _compose(request)
    assert result.status == "HOLD"
    assert "VALUE_OUT_OF_RANGE" in result.reason_codes

    registry = _registry()
    registry["providers"][1]["validity_envelope"] = []
    result = _compose(_request("physical"), registry)
    assert result.status == "HOLD"
    assert "PHYSICAL_RANGE_UNKNOWN" in result.reason_codes


def test_physical_provider_needs_complete_required_input_validity() -> None:
    registry = _registry()
    provider = registry["providers"][1]
    provider["validity_envelope"] = [
        item for item in provider["validity_envelope"] if item["subject_kind"] != "input"
    ]

    result = _compose(_request("physical"), registry)

    assert result.status == "HOLD"
    assert "PHYSICAL_RANGE_UNKNOWN" in result.reason_codes


def test_required_condition_needs_its_own_explicit_binding() -> None:
    registry = _registry()
    registry["capabilities"][0]["required_conditions"] = [
        {
            "id": "atmosphere.oxygen_ppm",
            "state_type": "number",
            "unit": "ppm",
            "required": True,
            "description": "Maximum oxygen concentration.",
        }
    ]

    result = _compose(_request(), registry)

    assert result.status == "HOLD"
    assert "REQUIRED_CONDITION_UNENFORCED" in result.reason_codes


def test_provider_input_validity_budget_duration_and_dag_are_checked() -> None:
    request = _request()
    request["inputs"][0]["value"] = 2.0
    result = _compose(request)
    assert result.status == "HOLD"
    assert "VALUE_OUT_OF_RANGE" in result.reason_codes

    request = _request()
    request["max_cost_usd"] = 0.0
    result = _compose(request)
    assert result.status == "HOLD"
    assert "BUDGET_EXCEEDED" in result.reason_codes

    request = _request()
    request["max_duration_s"] = 1.0
    result = _compose(request)
    assert result.status == "HOLD"
    assert "TIME_LIMIT_EXCEEDED" in result.reason_codes

    request = _request()
    request["steps"][0]["depends_on"] = ["measure"]
    result = _compose(request)
    assert result.status == "HOLD"
    assert "DEPENDENCY_CYCLE" in result.reason_codes


def _transport_case(with_transport: bool) -> tuple[dict[str, object], dict[str, object]]:
    registry = _registry()
    registry["capabilities"].insert(
        0,
        _capability(
            "synthesize_sample",
            inputs=[],
            outputs=[
                {
                    "id": "product",
                    "state_type": "material_state",
                    "unit": "mol/kg",
                    "description": "Synthesized sample",
                }
            ],
            parameters=[],
        ),
    )
    registry["providers"].append(
        {
            **_provider(
                "synthesis-provider",
                "simulator",
                "synthesis-facility",
                operation_id="synthesize_sample",
            ),
            "validity_envelope": [],
        }
    )
    if with_transport:
        registry["capabilities"].append(
            _capability("transport_sample", kind="transport", inputs=[], outputs=[], parameters=[])
        )
        transport = _provider(
            "transport-provider", "simulator", "synthesis-facility", operation_id="transport_sample"
        )
        transport["facility_ids"] = ["synthesis-facility", "source-model-facility"]
        transport["validity_envelope"] = []
        registry["providers"].append(transport)

    request = _request()
    request["inputs"] = []
    request["steps"].insert(
        0,
        {
            "step_id": "synthesize",
            "operation_id": "synthesize_sample",
            "minimum_evidence_class": "simulator",
            "parameters": [],
            "input_bindings": [],
            "depends_on": [],
            "required_policy_tags": ["standard"],
        },
    )
    request["steps"][1]["depends_on"] = ["synthesize"]
    request["steps"][1]["input_bindings"] = [
        {
            "target_port_id": "fluidA/CCCl",
            "source_kind": "step_output",
            "source_id": "synthesize",
            "source_port_id": "product",
            **({"transport_operation_id": "transport_sample"} if with_transport else {}),
        }
    ]
    return request, registry


def test_cross_facility_edge_requires_admitted_explicit_transport() -> None:
    request, registry = _transport_case(False)
    result = _compose(request, registry)
    assert result.status == "HOLD"
    assert "TRANSPORT_REQUIRED" in result.reason_codes

    request, registry = _transport_case(True)
    result = _compose(request, registry)
    assert result.status == "COMPILED"
    assert result.virtual_sdl is not None
    assert result.virtual_sdl.transport_bindings[0].provider_id == "transport-provider"


def test_physical_transport_needs_complete_validity_contract() -> None:
    request, registry = _transport_case(True)
    transport_capability = next(
        item for item in registry["capabilities"] if item["operation_id"] == "transport_sample"
    )
    transport_capability["required_conditions"] = [
        {
            "id": "transport.temperature_K",
            "state_type": "number",
            "unit": "K",
            "required": True,
            "description": "Transport temperature range.",
        }
    ]
    transport_provider = next(
        item for item in registry["providers"] if item["provider_id"] == "transport-provider"
    )
    transport_provider["evidence_class"] = "physical"
    transport_provider["validity_envelope"] = []
    transport_provider["policy"]["safety_limit_ids"] = []

    result = _compose(request, registry)

    assert result.status == "HOLD"
    assert "TRANSPORT_UNAVAILABLE" in result.reason_codes


def test_agent_request_cannot_admit_or_approve_a_provider() -> None:
    request = _request()
    request["provider_admission"] = {
        "provider_id": "legacy-heater-workstation-simulator",
        "status": "admitted",
    }
    with pytest.raises(ValidationError, match="extra"):
        CampaignRequirement.model_validate(request)

    registry = _registry()
    registry["providers"][1]["admission"]["authority_kind"] = "agent"
    with pytest.raises(ValidationError, match="authority_kind"):
        CapabilityRegistry.model_validate(registry)


def test_repeated_resolution_has_identical_content_hashes() -> None:
    first = _compose(copy.deepcopy(_request()), copy.deepcopy(_registry()))
    second = _compose(copy.deepcopy(_request()), copy.deepcopy(_registry()))

    assert first.resolution_sha256 == second.resolution_sha256
    assert first.composition_sha256 == second.composition_sha256
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


# --- Task 12: sample identity, transport dependency edges, and provider preference ---
#
# Synthetic, in the same style as `_registry()`/`_transport_case()` above, rather than
# `dynamical/bundle/registry.yaml`: that registry's real `transfer-sample`
# capability declares a required `to_station` parameter that `_transport_candidate`
# (composition.py `_transport_candidate`) never supplies, so every real transport candidate is
# unconditionally rejected today regardless of these fixes -- a pre-existing gap unrelated to
# sample identity, transport dedup, or provider preference, and out of scope here.


def _transport_registry_document() -> dict[str, object]:
    """Two workstations: ``condition-sample`` feeds ``deposit-sample`` across a transfer."""

    return {
        "document_type": "dynamical.capability-registry",
        "schema_version": "0.1.0",
        "registry_id": "transport-preference-test-registry",
        "capabilities": [
            _capability(
                "condition-sample",
                inputs=[],
                outputs=[
                    {
                        "id": "reading-a",
                        "state_type": "number",
                        "unit": "s",
                        "description": "Conditioning duration reading.",
                    },
                    {
                        "id": "reading-b",
                        "state_type": "number",
                        "unit": "%",
                        "description": "Conditioning setpoint reading.",
                    },
                ],
                parameters=[],
            ),
            _capability(
                "deposit-sample",
                inputs=[
                    {
                        "id": "reading-a",
                        "state_type": "number",
                        "unit": "s",
                        "required": False,
                        "description": "Conditioning duration reading, if consumed.",
                    },
                    {
                        "id": "reading-b",
                        "state_type": "number",
                        "unit": "%",
                        "required": False,
                        "description": "Conditioning setpoint reading, if consumed.",
                    },
                ],
                outputs=[
                    {
                        "id": "deposited_mass_g",
                        "state_type": "number",
                        "unit": "g",
                        "description": "Deposited mass.",
                    }
                ],
                parameters=[],
            ),
            _capability("transfer-sample", kind="transport", inputs=[], outputs=[], parameters=[]),
        ],
        "providers": [
            {
                **_provider(
                    "condition-simulator",
                    "simulator",
                    "arduino-conditioning",
                    operation_id="condition-sample",
                ),
                "validity_envelope": [],
            },
            {
                **_provider(
                    "deposit-simulator",
                    "simulator",
                    "squidstat-echem",
                    operation_id="deposit-sample",
                ),
                "validity_envelope": [],
            },
            {
                **_provider(
                    "ac-squidstat-shadow",
                    "shadow",
                    "squidstat-echem",
                    operation_id="deposit-sample",
                ),
                "validity_envelope": [],
            },
            {
                **_provider(
                    "bench-transfer-simulator",
                    "simulator",
                    "arduino-conditioning",
                    operation_id="transfer-sample",
                ),
                "validity_envelope": [],
                "facility_ids": [
                    "arduino-conditioning",
                    "squidstat-echem",
                    "ot2-liquid-handling",
                ],
            },
        ],
    }


def _transport_requirement_document(
    ports: list[str], *, provider_preference: str | None = None
) -> dict[str, object]:
    condition = {
        "step_id": "condition",
        "operation_id": "condition-sample",
        "minimum_evidence_class": "simulator",
        "parameters": [],
        "input_bindings": [],
        "depends_on": [],
        "required_policy_tags": [],
    }
    deposit = {
        "step_id": "deposit",
        "operation_id": "deposit-sample",
        "minimum_evidence_class": "simulator",
        "parameters": [],
        "input_bindings": [
            {
                "target_port_id": port,
                "source_kind": "step_output",
                "source_id": "condition",
                "source_port_id": port,
                "transport_operation_id": "transfer-sample",
            }
            for port in ports
        ],
        "depends_on": ["condition"] if ports else [],
        "required_policy_tags": [],
    }
    document: dict[str, object] = {
        "document_type": "dynamical.campaign-requirement",
        "schema_version": "0.1.0",
        "requirement_id": "transport-preference-test",
        "objective": {
            "id": "transport-preference-objective",
            "statement": "Exercise cross-workstation transport and provider preference.",
            "decision": "Select the admitted provider.",
            "proof_requirements": [
                {
                    "id": "deposit-proof",
                    "operation_id": "deposit-sample",
                    "output_port_ids": ["deposited_mass_g"],
                    "minimum_evidence_class": "simulator",
                    "acceptance_rule": "deposited_mass_g is recorded",
                    "independent_verification_required": True,
                }
            ],
        },
        "inputs": [],
        "steps": [condition, deposit] if ports else [deposit],
        "max_cost_usd": 100.0,
        "max_duration_s": 100.0,
    }
    if provider_preference is not None:
        document["provider_preference"] = provider_preference
    return document


@pytest.fixture
def registry() -> CapabilityRegistry:
    return CapabilityRegistry.model_validate(_transport_registry_document())


@pytest.fixture
def cross_station_requirement() -> CampaignRequirement:
    return CampaignRequirement.model_validate(_transport_requirement_document(["reading-a"]))


@pytest.fixture
def two_port_requirement() -> CampaignRequirement:
    return CampaignRequirement.model_validate(
        _transport_requirement_document(["reading-a", "reading-b"])
    )


@pytest.fixture
def preference_requirement() -> CampaignRequirement:
    return CampaignRequirement.model_validate(
        _transport_requirement_document([], provider_preference="prefer_highest_evidence_class")
    )


def test_transports_appear_in_the_dependency_graph(cross_station_requirement, registry) -> None:
    result = compose_virtual_sdl(cross_station_requirement, registry)
    assert result.status == "COMPILED"
    assert result.virtual_sdl is not None
    edge_pairs = {(e.source_step_id, e.target_step_id) for e in result.virtual_sdl.dependency_edges}
    assert result.virtual_sdl.transport_bindings, "fixture must actually exercise a transport"
    for transport in result.virtual_sdl.transport_bindings:
        assert (transport.source_step_id, transport.target_step_id) in edge_pairs


def test_one_move_feeding_two_ports_is_charged_once(two_port_requirement, registry) -> None:
    result = compose_virtual_sdl(two_port_requirement, registry)
    assert result.status == "COMPILED"
    assert result.virtual_sdl is not None
    moves = [
        item for item in result.virtual_sdl.transport_bindings if item.source_step_id == "condition"
    ]
    assert len(moves) == 1, "one physical move must not be billed per consumed port"


def test_requirement_may_prefer_a_higher_evidence_class(preference_requirement, registry) -> None:
    result = compose_virtual_sdl(preference_requirement, registry)
    assert result.status == "COMPILED"
    assert result.virtual_sdl is not None
    chosen = {b.provider_id for b in result.virtual_sdl.operation_bindings}
    assert "ac-squidstat-shadow" in chosen, (
        "selection is hardcoded to the lowest admissible evidence class, so an "
        "admitted higher-fidelity provider is silently passed over"
    )


def test_provider_preference_defaults_to_lowest_evidence_class(registry) -> None:
    request = CampaignRequirement.model_validate(_transport_requirement_document([]))
    result = compose_virtual_sdl(request, registry)
    assert result.status == "COMPILED"
    assert result.virtual_sdl is not None
    chosen = {b.provider_id for b in result.virtual_sdl.operation_bindings}
    assert "deposit-simulator" in chosen


def test_candidate_search_is_bounded_and_truncation_is_visible(
    monkeypatch: pytest.MonkeyPatch, registry: CapabilityRegistry
) -> None:
    """A search past the explicit bound HOLDs instead of silently sampling a subset."""

    import dynamical.composition as composition_module

    monkeypatch.setattr(composition_module, "MAX_CANDIDATE_COMBINATIONS", 1)
    request = CampaignRequirement.model_validate(
        _transport_requirement_document(["reading-a", "reading-b"])
    )

    result = composition_module.compose_virtual_sdl(request, registry)

    assert result.status == "HOLD"
    assert "CANDIDATE_SEARCH_BOUNDED" in result.reason_codes
