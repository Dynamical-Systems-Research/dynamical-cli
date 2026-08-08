from __future__ import annotations

import hashlib
import math
from pathlib import Path

import pytest

from dynamical.composition import compose_virtual_sdl
from dynamical.schema import CampaignRequirement, load_capability_registry, load_facility_manifest
from dynamical.thermal import (
    OPERATION_IDS,
    SOURCE_REFERENCE_TEMPERATURE_K,
    SOURCE_REFERENCE_TIME_S,
    ThermalControl,
    ThermalModelConfig,
    advance_two_zone,
    agitate_sample,
    apply_thermal_program,
    estimate_reaction_progress,
    estimate_temperature_gradient,
    initial_state,
    measure_mixing_load,
    measure_plate_temperature,
    measure_reaction_progress,
    measure_sample_mass,
    measure_sample_temperature,
    simulate_two_zone,
)

REPOSITORY = Path(__file__).resolve().parents[1]
MANIFEST = REPOSITORY / "manifests" / "matterix-heater-workstation.yaml"
REGISTRY = REPOSITORY / "registries" / "reference-capabilities.yaml"

VIRTUAL_PROVIDERS = {
    "virtual-agitate-sample": "agitate-sample",
    "matterix-heater-workstation-simulator": "apply-thermal-program",
    "virtual-plate-temperature-instrument": "measure-plate-temperature",
    "virtual-sample-temperature-instrument": "measure-sample-temperature",
    "virtual-temperature-gradient-estimator": "estimate-temperature-gradient",
    "virtual-sample-mass-instrument": "measure-sample-mass",
    "virtual-mixing-load-instrument": "measure-mixing-load",
    "virtual-reaction-progress-estimator": "estimate-reaction-progress",
}


def _execute_virtual_graph() -> dict[str, dict[str, object]]:
    material = {"material.mass_kg": 0.25, "material.temperature_K": 298.15}
    agitation = agitate_sample(material, {"agitation-rate": 300.0})
    thermal = apply_thermal_program(
        {**material, **agitation},
        {"target-temperature": 343.15, "dwell-time": 1010.004},
    )
    return {
        "agitate-sample": agitation,
        "apply-thermal-program": thermal,
        "measure-plate-temperature": measure_plate_temperature(thermal, {}),
        "measure-sample-temperature": measure_sample_temperature(thermal, {}),
        "estimate-temperature-gradient": estimate_temperature_gradient(thermal, {}),
        "measure-sample-mass": measure_sample_mass(material, {}),
        "measure-mixing-load": measure_mixing_load({**agitation, **thermal}, {}),
        "estimate-reaction-progress": estimate_reaction_progress(thermal, {}),
    }


def test_two_zone_model_is_fixed_to_025_kg_and_resolves_wall_core_gradient() -> None:
    with pytest.raises(ValueError, match="exactly 0.25 kg"):
        ThermalModelConfig(sample_mass_kg=0.03375)

    no_mix = simulate_two_zone(300.0, control=ThermalControl(agitation_rate_rpm=0.0)).channels
    mixed = simulate_two_zone(300.0, control=ThermalControl(agitation_rate_rpm=600.0)).channels

    assert mixed["instrument.sample_wall_temperature_K"] > 298.15
    assert mixed["instrument.sample_core_temperature_K"] > 298.15
    assert abs(float(mixed["instrument.sample_gradient_K"])) < abs(
        float(no_mix["instrument.sample_gradient_K"])
    )
    assert mixed["instrument.sample_mass_kg"] == 0.25
    assert mixed["simulator.scale_transfer_validated"] is False


def test_source_exposure_proxy_integrates_time_above_frozen_threshold() -> None:
    config = ThermalModelConfig(
        initial_sample_temperature_K=SOURCE_REFERENCE_TEMPERATURE_K + 1.0,
        initial_plate_temperature_K=SOURCE_REFERENCE_TEMPERATURE_K + 1.0,
        ambient_temperature_K=SOURCE_REFERENCE_TEMPERATURE_K + 1.0,
    )
    state = advance_two_zone(
        initial_state(config),
        ThermalControl(target_plate_temperature_K=SOURCE_REFERENCE_TEMPERATURE_K + 1.0),
        config,
        1.0,
    )
    assert state.time_above_reference_temperature_s == pytest.approx(1.0)
    assert state.reaction_progress_estimate == pytest.approx(1.0 / SOURCE_REFERENCE_TIME_S)

    snapshot = simulate_two_zone(1010.004)
    reference_time = float(snapshot.channels["thermal.time_above_reference_temperature_s"])
    progress = float(snapshot.channels["thermal.reaction_progress_estimate"])
    assert 0.0 < reference_time < 1010.004
    assert progress == pytest.approx(min(1.0, reference_time / SOURCE_REFERENCE_TIME_S))
    assert "kinetic fit" in snapshot.claim_boundary


def test_eight_virtual_callables_match_their_declared_output_ports() -> None:
    registry = load_capability_registry(REGISTRY)
    outputs_by_operation = {
        capability.operation_id: {port.id for port in capability.output_ports}
        for capability in registry.capabilities
    }
    results = _execute_virtual_graph()

    assert set(results) == set(OPERATION_IDS) - {"measure-reaction-progress"}
    for operation_id, result in results.items():
        assert set(result) == outputs_by_operation[operation_id]
        assert all(
            not isinstance(value, float) or math.isfinite(value) for value in result.values()
        )
    with pytest.raises(RuntimeError, match="no admitted provider"):
        measure_reaction_progress({"material.mass_kg": 0.25}, {})


def test_registry_and_manifest_bind_the_exact_virtual_instrument_graph() -> None:
    registry = load_capability_registry(REGISTRY)
    manifest = load_facility_manifest(MANIFEST)
    capabilities = {item.operation_id for item in registry.capabilities}
    providers = {item.provider_id: item for item in registry.providers}
    facility_bindings = {
        (item.provider_id, item.operation_id): item for item in manifest.provider_admission_bindings
    }

    assert set(OPERATION_IDS).issubset(capabilities)
    assert {
        provider_id: providers[provider_id].operation_id for provider_id in VIRTUAL_PROVIDERS
    } == VIRTUAL_PROVIDERS
    for provider_id, operation_id in VIRTUAL_PROVIDERS.items():
        provider = providers[provider_id]
        binding = facility_bindings[(provider_id, operation_id)]
        assert provider.evidence_class == "simulator"
        assert provider.admission.status == "admitted"
        assert provider.availability.available is True
        assert binding.endpoint_id == provider.endpoint_id == "two-zone-thermal-model"
        assert binding.validity_envelope == provider.validity_envelope
        assert binding.adapter_links == provider.adapter_links
        assert binding.policy_tags == provider.policy.policy_tags

    physical = [item for item in registry.providers if item.evidence_class == "physical"]
    assert {item.operation_id for item in physical} == set(OPERATION_IDS)
    assert all(item.admission.status == "pending" for item in physical)
    assert all(item.availability.available is False for item in physical)
    assert all(item.policy.permitted is False for item in physical)
    assert not any(
        item.operation_id == "measure-reaction-progress" and item.admission.status == "admitted"
        for item in registry.providers
    )

    model = next(item for item in manifest.model_bindings if item.id == "two-zone-thermal-model")
    source = REPOSITORY / model.implementation_ref
    assert hashlib.sha256(source.read_bytes()).hexdigest() == model.implementation_sha256
    claim = " ".join(manifest.facility.claim_boundary)
    assert "0.03375 kg" in claim
    assert "0.25 kg" in claim
    assert str(SOURCE_REFERENCE_TIME_S) in claim
    assert str(SOURCE_REFERENCE_TEMPERATURE_K) in claim
    assert "source does not define this as exposure or kinetics" in claim.lower()


def test_physical_reaction_progress_requirement_holds_without_admitted_provider() -> None:
    request = CampaignRequirement.model_validate(
        {
            "document_type": "dynamical.campaign-requirement",
            "schema_version": "0.1.0",
            "requirement_id": "physical-reaction-progress-hold",
            "objective": {
                "id": "require-physical-reaction-progress",
                "statement": "Require a physical reaction-progress measurement.",
                "decision": "Hold unless an admitted physical instrument is available.",
                "proof_requirements": [
                    {
                        "id": "physical-progress-proof",
                        "operation_id": "measure-reaction-progress",
                        "output_port_ids": ["material.reaction_progress"],
                        "minimum_evidence_class": "physical",
                        "acceptance_rule": "An admitted physical instrument records the value.",
                        "independent_verification_required": True,
                    }
                ],
            },
            "inputs": [
                {
                    "id": "material.mass_kg",
                    "state_type": "number",
                    "unit": "kg",
                    "value": 0.25,
                    "facility_id": "matterix-heater-facility",
                }
            ],
            "steps": [
                {
                    "step_id": "measure-physical-progress",
                    "operation_id": "measure-reaction-progress",
                    "minimum_evidence_class": "physical",
                    "parameters": [],
                    "input_bindings": [
                        {
                            "target_port_id": "material.mass_kg",
                            "source_kind": "campaign_input",
                            "source_id": "material.mass_kg",
                        }
                    ],
                    "depends_on": [],
                    "required_policy_tags": ["physical-run-required"],
                }
            ],
            "max_cost_usd": 100.0,
            "max_duration_s": 2000.0,
        }
    )
    result = compose_virtual_sdl(request, load_capability_registry(REGISTRY))

    assert result.status == "HOLD"
    assert "PROVIDER_NOT_ADMITTED" in result.reason_codes
    assert "PROVIDER_UNAVAILABLE" in result.reason_codes
