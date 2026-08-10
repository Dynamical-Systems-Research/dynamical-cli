from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from _fixtures import write_reference_requirement

from dynamical.campaign import (
    ActionRequest,
    CampaignIdentity,
    CampaignValidationError,
    CompiledCampaignContract,
    EvidenceClass,
    ObservationChannel,
    ObservationFrame,
    ObservationOrigin,
    _parameter_channel_values,
    evaluate_action_constraints,
    load_compiled_campaign_contract,
    read_trace,
    run_cli,
    run_composed_campaign,
    stable_hash,
    validate_events,
)
from dynamical.compiler import compile_facility
from dynamical.composition import compose_files

REPOSITORY = Path(__file__).resolve().parents[1]
REGISTRY = REPOSITORY / "registries" / "electrodeposition-capabilities.yaml"
MANIFEST = REPOSITORY / "manifests" / "ac-electrodeposition-cell.yaml"


def _identity() -> CampaignIdentity:
    digest = stable_hash({"test": "campaign"})
    return CampaignIdentity(
        campaign_id="campaign-test",
        run_id="run-test",
        seed=7,
        backend_revision="source_bounded_python_process_model:not_embodied",
        ir_hash=digest,
        world_hash=digest,
        campaign_hash=digest,
        provenance={"embodied_backend": False, "w1_evidence": False},
    )


def _deposit_ordering_contract(current_a: float) -> CompiledCampaignContract:
    """A transfer followed by a dependent electrodeposition, authored in that order.

    Retargets the deleted thermal "agitate-then-heat" contract: the sample that
    "transfer" moves into custody at "bench-a" is the very one "deposit" acts on
    (sample_id threading -- the electrodeposition facility's real "prior outputs"
    concept, since none of its registered instrument models read numeric
    ``request.inputs`` the way the deleted thermal model did). Varying ``current_a``
    between two full runs and comparing ``deposited_mass_g`` (Faraday's law, strictly
    increasing in current) proves each run is genuinely re-executed against its own
    authored parameters, not memoized or order-independent.
    """

    digest = stable_hash({"test": "composed-deposit-ordering", "current_a": current_a})
    transfer_outputs = (
        ("instrument.sample_station_id", "1"),
        ("instrument.arrival_confirmed", "1"),
        ("sample.state.transferred", "1"),
    )
    deposit_outputs = (
        ("charge_c", "C"),
        ("deposited_mass_g", "g"),
        ("deposited_thickness_um", "um"),
        ("current_density_a_cm2", "A/cm^2"),
    )

    def capability(
        operation_id: str,
        outputs: tuple[tuple[str, str], ...],
        parameters: list[dict[str, object]],
    ) -> dict[str, object]:
        return {
            "id": operation_id,
            "parameters": parameters,
            "precondition_constraint_ids": [],
            "postcondition_constraint_ids": [],
            "output_ports": [{"id": name, "unit": unit} for name, unit in outputs],
        }

    transfer_capability = capability(
        "transfer-sample",
        transfer_outputs,
        [
            {"name": "sample_id", "value_type": "string", "required": True},
            {"name": "from_station", "value_type": "string", "required": True},
            {"name": "to_station", "value_type": "string", "required": True},
            {"name": "quantity", "value_type": "number", "required": True, "minimum": 0.0},
            {"name": "unit", "value_type": "string", "required": True},
            {"name": "arrival_confirmed", "value_type": "boolean", "required": True},
        ],
    )
    deposit_capability = capability(
        "electrodeposit-constant-current",
        deposit_outputs,
        [
            {
                "name": "current_a",
                "value_type": "number",
                "required": True,
                "minimum": 0.0,
                "maximum": 0.010,
            },
            {
                "name": "duration_s",
                "value_type": "number",
                "required": True,
                "minimum": 0.0,
                "maximum": 3600.0,
            },
        ],
    )
    bindings = (
        {
            "step_id": "transfer",
            "operation_id": "transfer-sample",
            "provider_id": "ac-transfer-simulator",
            "evidence_class": "simulator",
            "endpoint_id": "ac-transfer-model",
            "selected_facility_id": "bench-a",
            "parameters": [
                {"name": "sample_id", "value": "sample-1"},
                {"name": "from_station", "value": "intake"},
                {"name": "to_station", "value": "bench-a"},
                {"name": "quantity", "value": 5.0},
                {"name": "unit", "value": "mL"},
                {"name": "arrival_confirmed", "value": True},
            ],
            "inputs": [],
            "capability_contract": transfer_capability,
            "duration": {"typical_s": 0.0},
            "policy": {"safety_limit_ids": []},
        },
        {
            "step_id": "deposit",
            "operation_id": "electrodeposit-constant-current",
            "provider_id": "ac-squidstat-simulator",
            "evidence_class": "simulator",
            "endpoint_id": "ac-squidstat-model",
            "selected_facility_id": "bench-a",
            "sample_id": "sample-1",
            "parameters": [
                {"name": "current_a", "value": current_a},
                {"name": "duration_s", "value": 600.0},
            ],
            "inputs": [],
            "capability_contract": deposit_capability,
            "duration": {"typical_s": 600.0},
            "policy": {"safety_limit_ids": []},
        },
    )
    capabilities = {
        "transfer-sample": {**transfer_capability, "provider_id": "ac-transfer-simulator"},
        "electrodeposit-constant-current": {
            **deposit_capability,
            "provider_id": "ac-squidstat-simulator",
        },
    }
    return CompiledCampaignContract(
        target="ac-electrodeposition",
        manifest_sha256=digest,
        core_ir_sha256=digest,
        world_sha256=digest,
        adapter_pack_sha256=digest,
        facility_ir_sha256=digest,
        action_schema_sha256=digest,
        observation_schema_sha256=digest,
        action_kinds=frozenset(capabilities),
        observation_channels=frozenset(name for name, _ in (*transfer_outputs, *deposit_outputs)),
        channel_units=dict((*transfer_outputs, *deposit_outputs)),
        capability_by_action=capabilities,
        constraint_by_id={},
        composition_sha256=digest,
        operation_bindings=bindings,
    )


def test_composed_runtime_executes_authored_order_and_prior_outputs(tmp_path: Path) -> None:
    low, _ = run_composed_campaign(
        _deposit_ordering_contract(0.001),
        tmp_path / "low-current.ndjson",
        seed=3,
    )
    high, _ = run_composed_campaign(
        _deposit_ordering_contract(0.009),
        tmp_path / "high-current.ndjson",
        seed=3,
    )

    assert [event.action.kind for event in high if event.action is not None] == [
        "transfer-sample",
        "electrodeposit-constant-current",
    ]
    deposit_action = next(
        event.action
        for event in high
        if event.action is not None and event.action.kind == "electrodeposit-constant-current"
    )
    # "prior outputs": the sample transferred in step one is the very one deposit acts
    # on -- electrodeposition's real cross-step threading, since (unlike the deleted
    # thermal model) no registered instrument here reads a numeric step_output.
    assert deposit_action.sample_id == "sample-1"
    low_deposit = next(
        event.observation
        for event in low
        if event.observation is not None and event.observation.frame_id == "frame-after-deposit"
    )
    high_deposit = next(
        event.observation
        for event in high
        if event.observation is not None and event.observation.frame_id == "frame-after-deposit"
    )
    low_values = {channel.name: channel.value for channel in low_deposit.channels}
    high_values = {channel.name: channel.value for channel in high_deposit.channels}
    assert high_values["deposited_mass_g"] > low_values["deposited_mass_g"]


def _transfer_contract() -> CompiledCampaignContract:
    """One sample moving through a transfer, then read by a dependent step.

    Carried-forward fix (Task 7 recon): InstrumentRequest.sample was always
    None in run_composed_campaign, so a transfer's returned Sample had
    nowhere to go and Task 8's lineage enforcement was inert in the composed
    path. This contract exercises the ledger end to end: "transfer" moves
    sample-1 into "bench-a" with no prior ledger entry, and "deposit" reads
    it back at that same station via a declared sample_id binding.
    """

    digest = stable_hash({"test": "composed-transfer"})

    def capability(
        operation_id: str,
        outputs: tuple[tuple[str, str], ...],
        parameters: list[dict[str, object]],
    ) -> dict[str, object]:
        return {
            "id": operation_id,
            "parameters": parameters,
            "precondition_constraint_ids": [],
            "postcondition_constraint_ids": [],
            "output_ports": [{"id": name, "unit": unit} for name, unit in outputs],
        }

    transfer_outputs = (
        ("instrument.sample_station_id", "1"),
        ("instrument.arrival_confirmed", "1"),
        ("sample.state.transferred", "1"),
    )
    deposit_outputs = (
        ("charge_c", "C"),
        ("deposited_mass_g", "g"),
        ("deposited_thickness_um", "um"),
        ("current_density_a_cm2", "A/cm^2"),
    )
    transfer_capability = capability(
        "transfer-sample",
        transfer_outputs,
        [
            {"name": "sample_id", "value_type": "string", "required": True},
            {"name": "from_station", "value_type": "string", "required": True},
            {"name": "to_station", "value_type": "string", "required": True},
            {"name": "quantity", "value_type": "number", "required": True, "minimum": 0.0},
            {"name": "unit", "value_type": "string", "required": True},
            {"name": "arrival_confirmed", "value_type": "boolean", "required": True},
        ],
    )
    deposit_capability = capability(
        "electrodeposit-constant-current",
        deposit_outputs,
        [
            {
                "name": "current_a",
                "value_type": "number",
                "required": True,
                "minimum": 0.0,
                "maximum": 0.010,
            },
            {
                "name": "duration_s",
                "value_type": "number",
                "required": True,
                "minimum": 0.0,
                "maximum": 3600.0,
            },
        ],
    )
    bindings = (
        {
            "step_id": "transfer",
            "operation_id": "transfer-sample",
            "provider_id": "ac-transfer-simulator",
            "evidence_class": "simulator",
            # endpoint_id (the acting instrument's own id) and
            # selected_facility_id (the workstation it acts at) are
            # deliberately different strings here -- Task 8 fix round 1:
            # check_invariants must compare a sample's station against
            # selected_facility_id, not endpoint_id; using the same string
            # for both would let that bug hide.
            "endpoint_id": "ac-transfer-model",
            "selected_facility_id": "bench-a",
            "parameters": [
                {"name": "sample_id", "value": "sample-1"},
                {"name": "from_station", "value": "intake"},
                {"name": "to_station", "value": "bench-a"},
                {"name": "quantity", "value": 5.0},
                {"name": "unit", "value": "mL"},
                {"name": "arrival_confirmed", "value": True},
            ],
            "inputs": [],
            "capability_contract": transfer_capability,
            "duration": {"typical_s": 0.0},
            "policy": {"safety_limit_ids": []},
        },
        {
            "step_id": "deposit",
            "operation_id": "electrodeposit-constant-current",
            "provider_id": "ac-squidstat-simulator",
            "evidence_class": "simulator",
            "endpoint_id": "ac-squidstat-model",
            "selected_facility_id": "bench-a",
            "sample_id": "sample-1",
            "parameters": [
                {"name": "current_a", "value": 0.002827},
                {"name": "duration_s", "value": 600.0},
            ],
            "inputs": [],
            "capability_contract": deposit_capability,
            "duration": {"typical_s": 600.0},
            "policy": {"safety_limit_ids": []},
        },
    )
    capabilities = {
        "transfer-sample": {**transfer_capability, "provider_id": "ac-transfer-simulator"},
        "electrodeposit-constant-current": {
            **deposit_capability,
            "provider_id": "ac-squidstat-simulator",
        },
    }
    return CompiledCampaignContract(
        target="ac-electrodeposition",
        manifest_sha256=digest,
        core_ir_sha256=digest,
        world_sha256=digest,
        adapter_pack_sha256=digest,
        facility_ir_sha256=digest,
        action_schema_sha256=digest,
        observation_schema_sha256=digest,
        action_kinds=frozenset(capabilities),
        observation_channels=frozenset(name for name, _ in (*transfer_outputs, *deposit_outputs)),
        channel_units=dict((*transfer_outputs, *deposit_outputs)),
        capability_by_action=capabilities,
        constraint_by_id={},
        composition_sha256=digest,
        operation_bindings=bindings,
    )


def test_composed_runtime_threads_a_sample_through_transfer_and_a_dependent_step(
    tmp_path: Path,
) -> None:
    events, _ = run_composed_campaign(_transfer_contract(), tmp_path / "transfer.ndjson", seed=5)

    action_events = {
        event.action.action_id: event.action for event in events if event.action is not None
    }
    transition = action_events["transfer"].parameters["sample_transition"]
    assert transition["sample_id"] == "sample-1"
    assert transition["from_station"] == "intake"
    assert transition["to_station"] == "bench-a"
    assert transition["arrival_confirmed"] is True
    assert action_events["transfer"].sample_id == "sample-1"
    assert action_events["deposit"].sample_id == "sample-1"
    assert "sample_transition" not in action_events["deposit"].parameters
    # deposit's actor_id ("ac-squidstat-model") differs from its
    # station_id ("bench-a") on purpose -- proves check_invariants compares
    # the sample's station against the resolved workstation, not the acting
    # instrument's own endpoint id.
    assert action_events["deposit"].actor_id == "ac-squidstat-model"
    assert action_events["deposit"].station_id == "bench-a"

    result = validate_events(events)
    assert result["valid"] is True
    assert not any(reason["code"].startswith("SAMPLE_") for reason in result["reasons"])


def test_independent_verifier_rejects_out_of_range_action() -> None:
    request = ActionRequest(
        action_id="a-test",
        kind="set-actuator",
        actor_id="actuator",
        provider_id="figure5-source-condition-simulator",
        evidence_class=EvidenceClass.SIMULATOR,
        parameters={"enabled": True, "setpoint_c": 71.0},
    )

    declarations = {
        "setpoint": {
            "phase": "pre_action",
            "channel_id": "actuator/setpoint",
            "operator": "between",
            "bound": {"minimum": 30.0, "maximum": 70.0},
            "unit": "degC",
            "enforcement": "reject",
            "verifier_binding_id": "independent-verifier",
        }
    }
    evaluations = evaluate_action_constraints(
        request,
        declared_constraints=declarations,
        constraint_ids=("setpoint",),
        measured_channels={"actuator/setpoint": (71.0, "degC")},
    )

    assert len(evaluations) == 1
    assert evaluations[0].passed is False
    assert evaluations[0].verifier == "independent-verifier"


def test_observation_rejects_duplicate_channels() -> None:
    channel = ObservationChannel(
        name="process/progress",
        value=0.2,
        unit="mol/kg",
        quality="valid",
        origin=ObservationOrigin.SOURCE_MODEL,
        provider_id="figure5-source-condition-simulator",
        evidence_class=EvidenceClass.SIMULATOR,
        uncertainty={"value": 0.0, "kind": "declared", "origin": "test fixture"},
    )

    with pytest.raises(CampaignValidationError, match="unique"):
        ObservationFrame(
            frame_id="duplicate",
            logical_time_s=0.0,
            provider_id="figure5-source-condition-simulator",
            evidence_class=EvidenceClass.SIMULATOR,
            channels=(channel, channel),
        )


def test_simulate_rejects_missing_compiled_world(tmp_path: Path) -> None:
    args = argparse.Namespace(
        input=tmp_path / "missing-compiled-world",
        mode="simulate",
        output=tmp_path / "run.ndjson",
        seed=0,
    )

    with pytest.raises(CampaignValidationError, match="path is absent"):
        run_cli(args)


def test_simulate_rejects_invalid_compiled_pack(tmp_path: Path) -> None:
    compiled = compile_facility(MANIFEST, "openusd", tmp_path / "compiled").output_dir
    action_schema_path = compiled / "action_schema.json"
    action_schema = json.loads(action_schema_path.read_text(encoding="utf-8"))
    action_schema["x-dynamical-declared-capability-action-types"] = ["wait"]
    action_schema_path.write_text(json.dumps(action_schema), encoding="utf-8")

    with pytest.raises(CampaignValidationError, match="validation failed"):
        load_compiled_campaign_contract(compiled)


def test_compiled_trace_actions_and_channels_are_declared_subsets(tmp_path: Path) -> None:
    requirement = write_reference_requirement(tmp_path / "requirement.yaml")
    composition = compose_files(requirement, REGISTRY, MANIFEST)
    compiled = compile_facility(
        MANIFEST,
        "openusd",
        tmp_path / "compiled",
        composition_result=composition,
    ).output_dir
    output = tmp_path / "bound.ndjson"
    args = argparse.Namespace(input=compiled, mode="simulate", output=output, seed=19)

    assert run_cli(args) == 0
    events = read_trace(output)
    manifest = json.loads((compiled / "compile_manifest.json").read_text(encoding="utf-8"))
    emitted_actions = [event.action for event in events if event.action]
    emitted_channels = {
        channel.name
        for event in events
        if event.observation
        for channel in event.observation.channels
    }

    assert composition.virtual_sdl is not None
    selected_bindings = {
        binding.operation_id: binding for binding in composition.virtual_sdl.operation_bindings
    }
    assert {action.kind for action in emitted_actions} == set(selected_bindings)
    # Not a subset of observation_schema.json's x-dynamical-declared-channel-ids: that
    # schema is built from the facility's own device state_channels (e.g.
    # "arduino.conditioning_duration_s"), a different vocabulary from the registry
    # capability's own output_ports (e.g. "instrument.conditioning_duration_s"), which
    # is what run_composed_campaign's in-process path actually emits as channel names.
    # The deleted thermal registry/manifest happened to share one vocabulary; this
    # pairing does not, so the true invariant here is against the selected operations'
    # own declared output ports, not the facility's device channels.
    assert emitted_channels == {
        port.id
        for binding in selected_bindings.values()
        for port in binding.capability_contract.output_ports
    }
    assert all(
        action.actor_id == selected_bindings[action.kind].endpoint_id
        and action.provider_id == selected_bindings[action.kind].provider_id
        and action.evidence_class.value == selected_bindings[action.kind].evidence_class
        for action in emitted_actions
    )
    # transfer-sample is a custody change (kind: transport): run_composed_campaign adds
    # one "sample_transition" parameter beyond its declared ones to carry the resulting
    # SampleTransition -- see _fixtures.py's _sample_action docstring. No other selected
    # operation here changes custody, so this is the only action with that extra key.
    assert all(
        set(action.parameters)
        == {item.name for item in selected_bindings[action.kind].parameters}
        | ({"sample_transition"} if action.kind == "transfer-sample" else set())
        for action in emitted_actions
    )
    assert events[0].ir_hash == manifest["core_ir_sha256"]
    assert events[0].world_hash == manifest["world_sha256"]
    assert events[0].provenance["compiled_contract_bound"] is True
    assert events[0].provenance["embodied_backend"] is False
    assert events[0].provenance["w1_evidence"] is False


def test_constraint_verifier_rejects_unit_mismatch() -> None:
    action = ActionRequest(
        action_id="unit-test",
        kind="wait",
        actor_id="heater",
        provider_id="figure5-source-condition-simulator",
        evidence_class=EvidenceClass.SIMULATOR,
        parameters={"duration_s": 1.0},
    )
    declarations = {
        "dwell": {
            "phase": "pre_action",
            "channel_id": "dwell",
            "operator": "between",
            "bound": {"minimum": 0.0, "maximum": 10.0},
            "unit": "s",
            "enforcement": "reject",
            "verifier_binding_id": "verifier",
        }
    }

    with pytest.raises(CampaignValidationError, match="unit mismatch"):
        evaluate_action_constraints(
            action,
            declared_constraints=declarations,
            constraint_ids=("dwell",),
            measured_channels={"dwell": (1.0, "mol/kg")},
        )


def test_validate_events_rejects_a_missing_terminal_execution_status(tmp_path: Path) -> None:
    """Repair-2 defect 3: a missing/unrecognized terminal execution_status must not
    silently default to a false "completed" success. compiled_runtime.py's own
    (stricter) copy of this rule already requires ``passed`` or ``failed``; the
    public validator every trace passes through (a local run's own self-check,
    ``dynamical validate``, and replay) must enforce the identical rule.
    """

    events, _ = run_composed_campaign(_transfer_contract(), tmp_path / "trace.ndjson", seed=3)
    assert events[-1].provenance.get("execution_status") == "passed"
    tampered_terminal = replace(
        events[-1],
        provenance={
            key: value for key, value in events[-1].provenance.items() if key != "execution_status"
        },
    )
    tampered_events = [*events[:-1], tampered_terminal]

    with pytest.raises(CampaignValidationError, match="passed or failed"):
        validate_events(tampered_events)


def test_observation_channel_accepts_unknown_uncertainty() -> None:
    """Repair-2 defect 4: an unavailable or unreported measurement's uncertainty is
    genuinely unknown, not an honest zero -- zero claims an exact measurement no
    provider that reports nothing has actually made. The trace schema must permit
    ``null`` rather than forcing a fabricated number.
    """

    channel = ObservationChannel(
        name="squidstat.overpotential_v",
        value=None,
        unit="V",
        quality="unavailable",
        origin=ObservationOrigin.BACKEND_STATE,
        provider_id="ac-squidstat-simulator",
        evidence_class=EvidenceClass.SIMULATOR,
        uncertainty={"value": None, "kind": "declared", "origin": "no bound value"},
    )
    assert channel.uncertainty["value"] is None
    assert channel.to_dict()["uncertainty"]["value"] is None


def test_parameter_channel_values_does_not_guess_by_shared_unit() -> None:
    """Repair-2 defect 5: a pre-action constraint's parameter binding is read from
    its own declared ``constrained_parameter_name``, never inferred by matching
    units. Unit-matching can silently pick the wrong value: here the capability's
    only ``s``-unit parameter is ``cooldown_s``, but the constraint is declared
    against a different parameter (``duration_s``) this capability does not even
    have -- the old unit-inference would have found exactly one ``s``-unit
    candidate and bound ``cooldown_s``'s value to a constraint that was never
    about it. The declared name must instead leave this constraint's channel
    unmeasured (MEASUREMENT_UNAVAILABLE upstream), not guess.
    """

    action = ActionRequest(
        action_id="cool-down",
        kind="condition-ultrasonic",
        actor_id="ac-arduino-model",
        provider_id="ac-arduino-simulator",
        evidence_class=EvidenceClass.SIMULATOR,
        parameters={"cooldown_s": 42.0},
    )
    capability = {
        "parameters": [
            {"name": "cooldown_s", "value_type": "number", "unit": "s", "required": True},
        ],
    }
    contract = SimpleNamespace(
        constraint_by_id={
            "post-cool-envelope": {
                "channel_id": "arduino.post_cool_s",
                "unit": "s",
                "enforcement": "reject",
                "phase": "pre_action",
                "constrained_parameter_name": "duration_s",
            }
        }
    )

    measured = _parameter_channel_values(action, capability, ("post-cool-envelope",), contract)

    assert measured == {}
