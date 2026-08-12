"""The runtime campaign is a compilation of the step graph, not a fixed script."""

import json

from dynamical.backends._runtime_pack import runtime_campaign, runtime_capability_bindings


def test_campaign_follows_the_composition_order(three_station_composition):
    # Actions are kinded by the facility-declared action_type bound to each
    # operation, not the raw operation_id: manifests/ac-electrodeposition-cell.yaml
    # declares action_type "dispense" for operation_id "dispense-electrolyte"
    # (and "electrodeposit" / "measure" for the other two) -- they are not equal.
    campaign = runtime_campaign(three_station_composition)
    kinds = [action["kind"] for action in campaign["actions"]]
    assert kinds.index("dispense") < kinds.index("electrodeposit")
    assert kinds.index("electrodeposit") < kinds.index("measure")


def test_two_instruments_may_share_an_action_type(two_observer_composition):
    bindings = runtime_capability_bindings(two_observer_composition)
    keys = {(b["instrument_id"], b["action_type"]) for b in bindings}
    assert len(keys) == 2, "keying by action_type alone collides across instruments"


def test_no_action_appears_that_the_composition_did_not_select(three_station_composition):
    campaign = runtime_campaign(three_station_composition)
    bindings = runtime_capability_bindings(
        three_station_composition,
        operation_bindings=three_station_composition.operation_bindings,
    )
    selected = {b["action_type"] for b in bindings}
    emitted = {a["kind"] for a in campaign["actions"] if a["kind"] not in {"wait", "observe"}}
    assert emitted <= selected, f"unselected actions were injected: {emitted - selected}"


def test_repeated_operation_on_shared_device_compiles_each_step(tmp_path):
    from test_electrodeposition_registry import MANIFEST, REGISTRY, _coverage_requirement

    from dynamical.compiler import compile_facility
    from dynamical.composition import compose_virtual_sdl
    from dynamical.schema import CampaignRequirement, load_capability_registry

    payload = _coverage_requirement().model_dump(mode="json")
    steps = payload["steps"]
    first = steps[1]
    first["step_id"] = "aliquot-ni"
    first["operation_id"] = "aliquot-to-well"
    second = json.loads(json.dumps(first))
    second["step_id"] = "aliquot-fe"
    second["parameters"] = [
        {**item, "value": 0.75 if item["name"] == "volume_ml" else "Fe"}
        for item in second["parameters"]
    ]
    second["depends_on"] = ["materialize", "aliquot-ni"]
    steps.insert(2, second)
    steps[3]["depends_on"] = ["aliquot-fe"]

    requirement = CampaignRequirement.model_validate(payload)
    composition = compose_virtual_sdl(requirement, load_capability_registry(REGISTRY))
    assert composition.status == "COMPILED", composition.reason_codes
    world = compile_facility(
        MANIFEST, "isaac", tmp_path / "world", composition_result=composition
    ).output_dir
    campaign = json.loads((world / "runtime_campaign.json").read_text())

    repeated = [action for action in campaign["actions"] if action["kind"] == "aliquot"]
    assert [action["action_id"] for action in repeated] == ["aliquot-ni", "aliquot-fe"]


def _model_backed_requirement():
    """A campaign whose scientific work is done only by model-backed providers.

    ``deposit-chemical-bath`` (ac-bath-simulator) and ``measure-oer``
    (ac-oer-twin) declare no device-control adapter link, because no physical
    device drives them. Neither of their device siblings -- dispense/aliquot on
    ot2-device, electrodeposit on squidstat-device -- is selected here, so this
    campaign never names ``ot2-device`` or ``squidstat-device`` through any
    binding's endpoint refs. Every other campaign in this suite does, which is
    why the shared-device resolution path was never exercised for a
    model-backed provider alone.
    """

    from test_electrodeposition_registry import _parameter

    from dynamical.schema import CampaignRequirement

    def _campaign_sample_binding() -> dict[str, object]:
        return {
            "target_port_id": "sample.state",
            "source_kind": "campaign_input",
            "source_id": "sample.state",
        }

    return CampaignRequirement.model_validate(
        {
            "document_type": "dynamical.campaign-requirement",
            "schema_version": "0.1.0",
            "requirement_id": "model-backed-only",
            "objective": {
                "id": "model-backed-check",
                "statement": "Deposit a film and measure its OER response with model providers.",
                "decision": "Confirm the model-backed evidence validates.",
                "proof_requirements": [
                    {
                        "id": "oer-proof",
                        "operation_id": "measure-oer",
                        "output_port_ids": ["overpotential_v"],
                        "minimum_evidence_class": "calibrated_twin",
                        "acceptance_rule": "overpotential_v is recorded",
                        "independent_verification_required": True,
                    }
                ],
            },
            "inputs": [
                {
                    "id": "sample.state",
                    "state_type": "sample_state",
                    "unit": "1",
                    "value": "SMP-model-01",
                }
            ],
            "steps": [
                {
                    "step_id": "mount-sample",
                    "operation_id": "transfer-sample",
                    "parameters": [
                        _parameter("to_station", "string", "1", "ot2-liquid-handling"),
                        _parameter("sample_id", "string", "1", "SMP-model-01"),
                        _parameter("arrival_confirmed", "boolean", "1", True),
                    ],
                    "input_bindings": [_campaign_sample_binding()],
                },
                {
                    "step_id": "deposit-film",
                    "operation_id": "deposit-chemical-bath",
                    "parameters": [
                        _parameter("fraction_al", "number", "1", 0.0),
                        _parameter("fraction_co", "number", "1", 0.05),
                        _parameter("fraction_cr", "number", "1", 0.25),
                        _parameter("fraction_cu", "number", "1", 0.0),
                        _parameter("fraction_fe", "number", "1", 0.25),
                        _parameter("fraction_mn", "number", "1", 0.0),
                        _parameter("fraction_ni", "number", "1", 0.45),
                        _parameter("fraction_zn", "number", "1", 0.0),
                        _parameter("synthesis_time_s", "number", "s", 600.0),
                    ],
                    "input_bindings": [_campaign_sample_binding()],
                    "depends_on": ["mount-sample"],
                },
                {
                    "step_id": "move-to-echem",
                    "operation_id": "transfer-sample",
                    "parameters": [
                        _parameter("to_station", "string", "1", "squidstat-echem"),
                        _parameter("arrival_confirmed", "boolean", "1", True),
                    ],
                    "input_bindings": [_campaign_sample_binding()],
                    "depends_on": ["deposit-film"],
                },
                {
                    "step_id": "load-cell",
                    "operation_id": "load-electrochemical-cell",
                    "parameters": [
                        _parameter("cell_id", "string", "1", "echem-cell-1"),
                        _parameter("seated", "boolean", "1", True),
                    ],
                    "input_bindings": [_campaign_sample_binding()],
                    "depends_on": ["move-to-echem"],
                },
                {
                    "step_id": "measure-oer-10ma",
                    "operation_id": "measure-oer",
                    "minimum_evidence_class": "calibrated_twin",
                    "parameters": [
                        _parameter("current_density_a_cm2", "number", "A/cm^2", 0.010),
                    ],
                    "input_bindings": [_campaign_sample_binding()],
                    "depends_on": ["load-cell"],
                },
            ],
            "max_cost_usd": 0,
            "max_duration_s": 3600,
        }
    )


def _compile_model_backed_world(tmp_path):
    from test_electrodeposition_registry import MANIFEST, REGISTRY

    from dynamical.compiler import compile_facility
    from dynamical.composition import compose_virtual_sdl
    from dynamical.schema import load_capability_registry

    composition = compose_virtual_sdl(
        _model_backed_requirement(), load_capability_registry(REGISTRY)
    )
    assert composition.status == "COMPILED", composition.reason_codes
    return compile_facility(
        MANIFEST, "isaac", tmp_path / "world", composition_result=composition
    ).output_dir


def test_model_backed_operations_reach_the_isaac_runtime_pack(tmp_path):
    """A model-backed provider's action must compile even with no device sibling selected.

    Resolution runs through the action's selected capability, so a capability
    whose device declares no adapter link for this operation is still bound.
    """

    world = _compile_model_backed_world(tmp_path)
    campaign = json.loads((world / "runtime_campaign.json").read_text())
    kinds = {action["kind"] for action in campaign["actions"]}
    assert "deposit" in kinds, "deposit-chemical-bath did not reach the runtime pack"
    assert "measure" in kinds, "measure-oer did not reach the runtime pack"


def test_model_backed_actions_keep_their_admitted_provider(tmp_path):
    """Resolving the capability must not reassign the admitted provider."""

    world = _compile_model_backed_world(tmp_path)
    campaign = json.loads((world / "runtime_campaign.json").read_text())
    providers = {action["kind"]: action["provider_id"] for action in campaign["actions"]}
    assert providers["deposit"] == "ac-bath-simulator"
    assert providers["measure"] == "ac-oer-twin"


def test_capability_without_a_declared_contract_fails_closed():
    """An unresolvable capability mapping fails closed rather than guessing."""

    import pytest

    from dynamical.backends._runtime_pack import _resolve_action_kind

    with pytest.raises(ValueError, match="no facility-declared action type binding"):
        _resolve_action_kind("unmapped-operation", {})
    with pytest.raises(ValueError, match="multiple facility action types"):
        _resolve_action_kind("ambiguous-operation", {"ambiguous-operation": {"deposit", "measure"}})


def test_model_backed_campaign_leaves_physical_authority_on_hold(tmp_path):
    """Compiling a model-backed campaign admits no physical provider."""

    world = _compile_model_backed_world(tmp_path)
    campaign = json.loads((world / "runtime_campaign.json").read_text())
    classes = {action["evidence_class"] for action in campaign["actions"]}
    assert "physical" not in classes, "a physical evidence class was admitted"

    from test_electrodeposition_registry import REGISTRY

    from dynamical.schema import load_capability_registry

    registry = load_capability_registry(REGISTRY)
    physical = [p for p in registry.providers if p.evidence_class == "physical"]
    assert physical, "the registry must still declare physical counterparts"
    assert all(p.admission.status == "pending" for p in physical)
    assert all(not p.policy.permitted for p in physical)
