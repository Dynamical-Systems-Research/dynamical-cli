"""The registry must expose AC capabilities and keep every physical route on HOLD."""

import hashlib
from pathlib import Path

import pytest

from dynamical.campaign import (
    load_compiled_campaign_contract,
    run_composed_campaign,
    validate_events,
)
from dynamical.campaign import validate_path as campaign_validate_path
from dynamical.compiler import compile_facility
from dynamical.composition import compose_virtual_sdl
from dynamical.schema import CampaignRequirement, load_capability_registry, load_facility_manifest

REPOSITORY = Path(__file__).resolve().parents[1]
REGISTRY = "registries/electrodeposition-capabilities.yaml"
MANIFEST = "manifests/ac-electrodeposition-cell.yaml"


def _parameter(name: str, value_type: str, unit: str, value: object) -> dict[str, object]:
    return {"name": name, "value_type": value_type, "unit": unit, "value": value}


def test_registry_exposes_the_three_workstations():
    registry = load_capability_registry(REGISTRY)
    facilities = {f for p in registry.providers for f in p.facility_ids}
    assert {"ot2-liquid-handling", "arduino-conditioning", "squidstat-echem"} <= facilities


def test_every_physical_provider_stays_unadmitted():
    registry = load_capability_registry(REGISTRY)
    physical = [p for p in registry.providers if p.evidence_class == "physical"]
    assert physical, "the registry must declare physical providers so HOLD is reachable"
    for provider in physical:
        assert provider.admission.status == "pending"
        assert provider.availability.available is False
        assert provider.policy.permitted is False


def test_every_simulator_provider_has_a_registered_model():
    from dynamical import instruments

    registry = load_capability_registry(REGISTRY)
    for provider in registry.providers:
        if provider.evidence_class != "simulator":
            continue
        assert instruments.resolve(provider.operation_id, provider.provider_id) is not None, (
            f"{provider.provider_id} is admitted but has no instrument model; "
            "compose would succeed and run would fail"
        )


def test_model_binding_declared_hash_equals_the_on_disk_implementation():
    """Re-homed from tests/test_thermal.py:157-159 (T16): the only working hash-admission
    of an external input in the repo, and the direct precedent for CAD admission -- a
    manifest's declared ``implementation_sha256`` must equal the sha256 of the file it
    names, not merely be present.
    """

    manifest = load_facility_manifest(MANIFEST)
    assert manifest.model_bindings, "the manifest must declare at least one model binding"
    for model in manifest.model_bindings:
        source = REPOSITORY / model.implementation_ref
        assert hashlib.sha256(source.read_bytes()).hexdigest() == model.implementation_sha256


def test_facility_binding_cannot_widen_a_registry_providers_envelope():
    """Re-homed from tests/test_thermal.py:142-145 (T16): a facility's own admission
    binding for a provider must restate that provider's registry envelope exactly --
    matching endpoint, validity envelope, adapter links, and policy tags -- not a wider
    or narrower one authored independently at the facility layer.
    """

    registry = load_capability_registry(REGISTRY)
    manifest = load_facility_manifest(MANIFEST)
    providers = {(item.provider_id, item.operation_id): item for item in registry.providers}
    facility_bindings = {
        (item.provider_id, item.operation_id): item for item in manifest.provider_admission_bindings
    }

    assert facility_bindings, "the manifest must declare at least one provider admission binding"
    for key, binding in facility_bindings.items():
        provider = providers[key]
        assert provider.evidence_class in {"simulator", "calibrated_twin"}
        assert provider.admission.status == "admitted"
        assert binding.endpoint_id == provider.endpoint_id
        assert binding.validity_envelope == provider.validity_envelope
        assert binding.adapter_links == provider.adapter_links
        assert binding.policy_tags == provider.policy.policy_tags


def _sample_input_binding(source_id: str = "campaign.sample-id") -> dict[str, object]:
    """A step's ``sample.state`` input, bound to the campaign's declared initial sample."""

    return {
        "target_port_id": "sample.state",
        "source_kind": "campaign_input",
        "source_id": source_id,
    }


def _sample_step_output_binding(source_step_id: str) -> dict[str, object]:
    """A step's ``sample.state`` input, bound to a prior transfer's produced sample state.

    Only ``transfer-sample`` declares a ``sample.state.transferred`` output port (see the
    registry's per-capability judgement on which operations produce vs. merely consume sample
    state), so ``source_step_id`` must name a transfer step.
    """

    return {
        "target_port_id": "sample.state",
        "source_kind": "step_output",
        "source_id": source_step_id,
        "source_port_id": "sample.state.transferred",
    }


def _coverage_requirement(
    *,
    to_squidstat_station: str = "squidstat-echem",
    current_a: float = 0.002827,
    chemical: str = "Ni",
) -> CampaignRequirement:
    """A synthetic module-coverage campaign authored by this test harness.

    Ten steps exercise every admitted AC module at least once -- materialize
    (transfer), dispense from a named stock, transfer, ultrasonic
    conditioning, transfer, constant-current electrodeposition,
    electrochemical-cell loading, OER measurement, transfer back, and
    cleaning -- with arbitrary in-envelope parameters chosen here for
    coverage, not as a recommended or optimal experiment.

    ``to_squidstat_station`` names the workstation the pre-deposit transfer
    reports as its destination; overriding it deliberately breaks the
    sample's real path for the negative lineage test. ``chemical`` and
    ``current_a`` let coupling tests vary the process whose result the
    measurement must respond to.
    """

    return CampaignRequirement.model_validate(
        {
            "document_type": "dynamical.campaign-requirement",
            "schema_version": "0.1.0",
            "requirement_id": "ac-module-coverage",
            "objective": {
                "id": "module-coverage-check",
                "statement": (
                    "Exercise each admitted AC module once with harness-selected, "
                    "in-envelope parameters."
                ),
                "decision": (
                    "Confirm the returned evidence validates and stays within the "
                    "declared envelopes."
                ),
                "proof_requirements": [
                    {
                        "id": "oer-proof",
                        "operation_id": "measure-oer",
                        "output_port_ids": ["overpotential_v"],
                        "minimum_evidence_class": "simulator",
                        "acceptance_rule": "overpotential_v is recorded",
                        "independent_verification_required": True,
                    },
                    {
                        "id": "custody-proof",
                        "operation_id": "transfer-sample",
                        "output_port_ids": ["sample.state.transferred"],
                        "minimum_evidence_class": "simulator",
                        "acceptance_rule": "sample identity is recorded after each transfer",
                        "independent_verification_required": True,
                    },
                ],
            },
            "inputs": [
                {
                    "id": "campaign.sample-id",
                    "state_type": "sample_state",
                    "unit": "1",
                    "value": "sample-harness-01",
                }
            ],
            "steps": [
                {
                    "step_id": "materialize",
                    "operation_id": "transfer-sample",
                    "minimum_evidence_class": "simulator",
                    "parameters": [
                        _parameter("to_station", "string", "1", "ot2-liquid-handling"),
                        _parameter("sample_id", "string", "1", "sample-harness-01"),
                        _parameter("quantity", "number", "1", 3.895),
                        _parameter("unit", "string", "1", "mL"),
                    ],
                    "input_bindings": [_sample_input_binding()],
                    "depends_on": [],
                    "required_policy_tags": [],
                },
                {
                    "step_id": "dispense",
                    "operation_id": "dispense-electrolyte",
                    "minimum_evidence_class": "simulator",
                    "parameters": [
                        _parameter("volume_ml", "number", "mL", 3.0),
                        _parameter("chemical", "string", "1", chemical),
                    ],
                    "input_bindings": [_sample_step_output_binding("materialize")],
                    "depends_on": ["materialize"],
                    "required_policy_tags": [],
                },
                {
                    "step_id": "to-arduino",
                    "operation_id": "transfer-sample",
                    "minimum_evidence_class": "simulator",
                    "parameters": [
                        _parameter("to_station", "string", "1", "arduino-conditioning"),
                    ],
                    "input_bindings": [_sample_input_binding()],
                    "depends_on": ["dispense"],
                    "required_policy_tags": [],
                },
                {
                    "step_id": "condition",
                    "operation_id": "condition-ultrasonic",
                    "minimum_evidence_class": "simulator",
                    "parameters": [
                        _parameter("duration_s", "number", "s", 60.0),
                        _parameter("setpoint_percent", "number", "%", 80.0),
                    ],
                    "input_bindings": [_sample_step_output_binding("to-arduino")],
                    "depends_on": ["to-arduino"],
                    "required_policy_tags": [],
                },
                {
                    "step_id": "to-squidstat",
                    "operation_id": "transfer-sample",
                    "minimum_evidence_class": "simulator",
                    "parameters": [_parameter("to_station", "string", "1", to_squidstat_station)],
                    "input_bindings": [_sample_input_binding()],
                    "depends_on": ["condition"],
                    "required_policy_tags": [],
                },
                {
                    "step_id": "deposit",
                    "operation_id": "electrodeposit-constant-current",
                    "minimum_evidence_class": "simulator",
                    "parameters": [
                        _parameter("current_a", "number", "A", current_a),
                        _parameter("duration_s", "number", "s", 600.0),
                    ],
                    "input_bindings": [_sample_step_output_binding("to-squidstat")],
                    "depends_on": ["to-squidstat"],
                    "required_policy_tags": [],
                },
                {
                    "step_id": "load-cell",
                    "operation_id": "load-electrochemical-cell",
                    "minimum_evidence_class": "simulator",
                    "parameters": [
                        _parameter("cell_id", "string", "1", "echem-cell-main-body"),
                    ],
                    "input_bindings": [_sample_input_binding()],
                    "depends_on": ["deposit"],
                    "required_policy_tags": [],
                },
                {
                    "step_id": "measure",
                    "operation_id": "measure-oer",
                    "minimum_evidence_class": "simulator",
                    "parameters": [_parameter("current_density_a_cm2", "number", "A/cm^2", 0.020)],
                    "input_bindings": [_sample_input_binding()],
                    "depends_on": ["load-cell"],
                    "required_policy_tags": [],
                },
                {
                    "step_id": "to-ot2",
                    "operation_id": "transfer-sample",
                    "minimum_evidence_class": "simulator",
                    "parameters": [
                        _parameter("to_station", "string", "1", "ot2-liquid-handling"),
                    ],
                    "input_bindings": [_sample_input_binding()],
                    "depends_on": ["measure"],
                    "required_policy_tags": [],
                },
                {
                    "step_id": "clean",
                    "operation_id": "clean-electrode",
                    "minimum_evidence_class": "simulator",
                    "parameters": [
                        _parameter("rinse_volume_ml", "number", "mL", 6.0),
                        _parameter("ultrasound_s", "number", "s", 30.0),
                    ],
                    "input_bindings": [_sample_step_output_binding("to-ot2")],
                    "depends_on": ["to-ot2"],
                    "required_policy_tags": [],
                },
            ],
            "max_cost_usd": 10.0,
            "max_duration_s": 2500.0,
        }
    )


def test_one_sample_moves_through_three_workstations_by_explicit_transfer():
    """Sample lineage across explicit transfers: one sample crosses three workstations via
    ``transfer-sample`` steps (not the implicit cross-facility transport gate, which stays
    inactive for this single-facility manifest). ``transfer-sample`` has ``kind: transport``;
    composition must admit it as an ordinary step, subject to the same provider, envelope,
    policy, cost and duration checks as any other operation.

    The campaign declares its one sample as a ``sample_state`` campaign input (schema.py's
    existing mechanism -- no bespoke "create sample" operation) and threads it through the
    port graph: every step that acts on the sample consumes ``sample.state``; only
    ``transfer-sample`` (a custody change, not a physics reading) produces a new one. The two
    transfers each feed the very next step from their own output
    (``to-arduino`` -> ``condition``, ``to-squidstat`` -> ``deposit``), so
    ``dataflow_edges`` in the compiled trace is non-empty and reflects the sample's real path,
    not just ``depends_on`` order.
    """

    registry = load_capability_registry(REGISTRY)
    requirement = _coverage_requirement()

    result = compose_virtual_sdl(requirement, registry)

    assert result.status == "COMPILED", result.reason_codes
    assert result.virtual_sdl is not None
    transfer_steps = [
        binding
        for binding in result.virtual_sdl.operation_bindings
        if binding.operation_id == "transfer-sample"
    ]
    assert [item.step_id for item in transfer_steps] == [
        "materialize",
        "to-arduino",
        "to-squidstat",
        "to-ot2",
    ]
    for binding in transfer_steps:
        assert binding.provider_id == "ac-transfer-simulator"
    # The implicit cross-facility gate never fires here: neither transfer step declares a
    # campaign-input-sourced binding with a facility_id, so there is nothing for
    # _topology_bindings to cross-check against a different selected facility.
    assert result.virtual_sdl.transport_bindings == []
    # campaign.py's _dataflow_edges (what the trace calls dataflow_edges) is built from exactly
    # these step_output-sourced ResolvedInputBinding entries -- not from depends_on. Assert the
    # port-level wiring directly: the sample's real path is a step_output chain through the two
    # transfers, not merely dependency order.
    bindings_by_step = {b.step_id: b for b in result.virtual_sdl.operation_bindings}
    condition_sample_input = next(
        item
        for item in bindings_by_step["condition"].inputs
        if item.target_port_id == "sample.state"
    )
    assert condition_sample_input.source_kind == "step_output"
    assert (condition_sample_input.source_id, condition_sample_input.source_port_id) == (
        "to-arduino",
        "sample.state.transferred",
    )
    deposit_sample_input = next(
        item for item in bindings_by_step["deposit"].inputs if item.target_port_id == "sample.state"
    )
    assert deposit_sample_input.source_kind == "step_output"
    assert (deposit_sample_input.source_id, deposit_sample_input.source_port_id) == (
        "to-squidstat",
        "sample.state.transferred",
    )


def _run_coverage_campaign(
    tmp_path: Path,
    *,
    to_squidstat_station: str = "squidstat-echem",
    current_a: float = 0.002827,
    chemical: str = "Ni",
):
    registry = load_capability_registry(REGISTRY)
    requirement = _coverage_requirement(
        to_squidstat_station=to_squidstat_station, current_a=current_a, chemical=chemical
    )
    composition = compose_virtual_sdl(requirement, registry)
    assert composition.status == "COMPILED", composition.reason_codes

    compiled = compile_facility(
        MANIFEST, "openusd", tmp_path / "compiled", composition_result=composition
    ).output_dir
    contract = load_compiled_campaign_contract(compiled)
    events, _ = run_composed_campaign(contract, tmp_path / "trace.ndjson", seed=11)
    summary = dict(validate_events(events))
    for event in events:
        if event.observation is None:
            continue
        for channel in event.observation.channels:
            for name in ("deposited_mass_g", "overpotential_v"):
                if channel.name.endswith(name) and channel.value is not None:
                    summary[name] = float(channel.value)
    return summary


def test_coverage_campaign_compiles_and_runs_with_zero_lineage_findings(tmp_path: Path):
    """Lineage continuity end to end (positive direction): compose -> compile -> run ->
    validate a multi-instrument coverage campaign. One sample honestly threads through three
    workstations (dispense at ot2-liquid-handling, condition at arduino-conditioning,
    deposit/measure at squidstat-echem) via instrument endpoints whose own ids
    (``ac-opentron-model``, ``ac-oer-model``, ...) never equal any workstation id. A run
    this honest must not be flagged for its own honesty: zero SAMPLE_* reasons, a clean
    execution_status, and the full 14-event trace (start + 6 x (action, observation) + end).
    """

    result = _run_coverage_campaign(tmp_path)

    assert result["execution_status"] == "passed"
    assert result["valid"] is True
    assert result["event_count"] == 22
    assert result["reasons"] == []


def test_coverage_campaign_retargeted_transfer_still_fails_lineage(tmp_path: Path):
    """Fix round 1 verification (negative direction): an invariant that stops firing is
    worse than one that fires wrongly. Retarget only ``to-squidstat``'s declared
    destination to a workstation the sample is never transferred to again
    (``arduino-conditioning``, where ``condition`` already ran) -- ``deposit`` and
    ``measure`` are untouched and still independently resolve to squidstat-echem through
    their own provider bindings, so this is a genuine custody mismatch, not a
    reintroduction of the actor_id/station_id bug this round fixed.
    """

    result = _run_coverage_campaign(tmp_path, to_squidstat_station="arduino-conditioning")

    assert result["execution_status"] == "failed"
    assert result["valid"] is False
    assert any(reason["code"] == "SAMPLE_TRANSFER_MISSING" for reason in result["reasons"])


def test_tampered_instrument_module_fails_closed_on_declared_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defect 2 repro: a manifest's declared ``implementation_sha256`` must bind the
    module that actually executes, not merely be present alongside it. Point the
    OER model's already-resolved module at tampered bytes -- simulating someone
    modifying the installed instrument after the facility declared its hash -- and
    confirm the run fails closed with a typed ``MODEL_IMPLEMENTATION_MISMATCH``
    reason rather than silently executing the tampered module and reporting its
    output as if the declared hash still bound it.
    """

    import dynamical.instruments.ac_oer as ac_oer

    tampered = tmp_path / "ac_oer_tampered.py"
    tampered.write_text(
        Path(ac_oer.__file__).read_text(encoding="utf-8") + "\n# tampered\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ac_oer, "__file__", str(tampered))

    result = _run_coverage_campaign(tmp_path)

    assert result["execution_status"] == "failed"
    assert result["valid"] is False
    assert any(reason["code"] == "MODEL_IMPLEMENTATION_MISMATCH" for reason in result["reasons"])


def test_repeated_action_kinds_each_get_their_own_provider_binding(tmp_path: Path):
    """Provider bindings are keyed by action_id, not action kind. The coverage
    campaign has four ``transfer-sample`` steps (kind ``transfer``); each must
    have its own compiled binding, and every action must validate against its
    own binding -- not against whichever same-kind step compiled last."""

    import importlib.util
    import json as json_module

    registry = load_capability_registry(REGISTRY)
    composition = compose_virtual_sdl(_coverage_requirement(), registry)
    assert composition.status == "COMPILED", composition.reason_codes
    compiled = compile_facility(
        MANIFEST, "isaac", tmp_path / "isaac", composition_result=composition
    ).output_dir

    campaign = json_module.loads((compiled / "runtime_campaign.json").read_text(encoding="utf-8"))
    bindings = campaign["provider_bindings"]
    action_ids = [a["action_id"] for a in campaign["actions"]]
    transfer_ids = [a["action_id"] for a in campaign["actions"] if a["kind"] == "transfer"]

    # One binding per action, not one collapsed entry per kind.
    assert len(transfer_ids) >= 4, transfer_ids
    assert set(bindings) == set(action_ids)
    assert all(tid in bindings for tid in transfer_ids)
    assert "transfer" not in bindings  # the old kind-keyed collapse is gone

    # Every action validates against the verified pack via its own binding.
    spec = importlib.util.spec_from_file_location(
        "_rt_p1", compiled / "dynamical_runtime_contract.py"
    )
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    pack = runtime.verify_compiled_pack(compiled)
    for action in campaign["actions"]:
        runtime.validate_action(action, pack)  # must not raise

    # A malformed (unhashable) action_id fails through the runtime contract's
    # normal path (the pack's own RuntimeContractError), not a raw TypeError on
    # the binding lookup.
    malformed = dict(campaign["actions"][0])
    malformed["action_id"] = ["not", "a", "string"]
    with pytest.raises(runtime.RuntimeContractError):
        runtime.validate_action(malformed, pack)


def test_cross_surface_identity_binds_one_composition_everywhere(tmp_path: Path):
    """Criterion 6: simulation, Isaac compilation, replay, and validation all
    carry the identical composition identity -- no surface silently re-hashes
    its own copy of the campaign contract."""

    import json as json_module

    from dynamical.replay import replay_trace

    registry = load_capability_registry(REGISTRY)
    composition = compose_virtual_sdl(_coverage_requirement(), registry)
    assert composition.status == "COMPILED", composition.reason_codes
    expected = composition.composition_sha256

    packs = {
        target: compile_facility(
            MANIFEST, target, tmp_path / target, composition_result=composition
        ).output_dir
        for target in ("openusd", "isaac")
    }
    for target, pack in packs.items():
        saved = json_module.loads((pack / "composition_result.json").read_text(encoding="utf-8"))
        assert saved["composition_sha256"] == expected, target
        graph = json_module.loads(
            (pack / "selected_capability_graph.json").read_text(encoding="utf-8")
        )
        assert graph["composition_sha256"] == expected, target

    contract = load_compiled_campaign_contract(packs["openusd"])
    trace_path = tmp_path / "trace.ndjson"
    events, _ = run_composed_campaign(contract, trace_path, seed=3)
    assert events[0].provenance["compiled_pack"]["composition_sha256"] == expected

    replay_path = tmp_path / "replay.ndjson"
    replay_trace(trace_path, replay_path)
    replay_start = json_module.loads(replay_path.read_text(encoding="utf-8").splitlines()[0])
    assert replay_start["provenance"]["compiled_pack"]["composition_sha256"] == expected

    for surface in (trace_path, replay_path):
        report = campaign_validate_path(surface)
        assert report["valid"] is True, report["reasons"]


def test_tampered_sample_state_digest_fails_validation(tmp_path: Path):
    """Criterion 5 evidence: scientific state is continuous across every
    instrument action, verifiably in the trace itself. An observation whose
    recorded read-state digest does not match the last written state must
    fail validation with a typed reason."""

    import json as json_module

    registry = load_capability_registry(REGISTRY)
    composition = compose_virtual_sdl(_coverage_requirement(), registry)
    assert composition.status == "COMPILED", composition.reason_codes
    compiled = compile_facility(
        MANIFEST, "openusd", tmp_path / "compiled", composition_result=composition
    ).output_dir
    contract = load_compiled_campaign_contract(compiled)
    trace_path = tmp_path / "trace.ndjson"
    run_composed_campaign(contract, trace_path, seed=11)

    tampered_lines = []
    flipped = False
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        event = json_module.loads(line)
        provenance = event.get("provenance") or {}
        if (
            not flipped
            and event.get("event_type") == "observation"
            and provenance.get("sample_state_written") is False
            and provenance.get("sample_state_sha256")
        ):
            provenance["sample_state_sha256"] = "0" * 64
            flipped = True
        tampered_lines.append(json_module.dumps(event, sort_keys=True, separators=(",", ":")))
    assert flipped, "the coverage trace must contain at least one pure-read observation"
    tampered_path = tmp_path / "tampered.ndjson"
    tampered_path.write_text("\n".join(tampered_lines) + "\n", encoding="utf-8")

    result = campaign_validate_path(tampered_path)
    assert result["valid"] is False
    assert any(reason["code"] == "SAMPLE_STATE_DISCONTINUOUS" for reason in result["reasons"])


def test_deposition_condition_changes_the_measured_activity(tmp_path: Path):
    """The measurement must be evidence about the film this campaign deposited.

    Two coupled checks: changing the deposition current changes the deposited
    mass the trace reports, and changing the dispensed precursor chemistry
    changes the measured overpotential -- the measurement is a function of the
    sample the campaign made, not of its own requested parameters. A campaign
    whose measurement cannot see its own process is ordered choreography, not
    a coupled multi-instrument SDL.
    """
    nickel = _run_coverage_campaign(tmp_path, current_a=0.002827, chemical="Ni")
    low_current = _run_coverage_campaign(tmp_path / "low", current_a=0.001000, chemical="Ni")
    iron = _run_coverage_campaign(tmp_path / "iron", chemical="Fe")

    assert nickel["deposited_mass_g"] > low_current["deposited_mass_g"] * 2
    # The fitted response orders these two chemistries distinctly.
    assert iron["overpotential_v"] != nickel["overpotential_v"]
    assert iron["overpotential_v"] < nickel["overpotential_v"]
