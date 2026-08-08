from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
import yaml
from _fixtures import REFERENCE_REQUIREMENT, write_reference_requirement
from jsonschema import Draft202012Validator

from dynamical.backends.matterix import (
    _observation_bindings,
    matterix_xyzw_to_named_quaternion,
    named_quaternion_to_matterix_xyzw,
)
from dynamical.campaign import CampaignValidationError, read_trace
from dynamical.compiler import compile_facility
from dynamical.composition import CompositionResult, compose_files
from dynamical.replay import _expected_snapshot_channels, replay_trace
from dynamical.schema import canonical_sha256, load_facility_manifest

REPOSITORY = Path(__file__).resolve().parents[1]
HEATER_MANIFEST = REPOSITORY / "manifests" / "matterix-heater-workstation.yaml"


def _json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _load_runtime_contract(output: Path) -> ModuleType:
    path = output / "dynamical_runtime_contract.py"
    spec = importlib.util.spec_from_file_location(f"_dynamical_runtime_{output.name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_matterix_launcher(output: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(output))
    path = output / "run_matterix.py"
    spec = importlib.util.spec_from_file_location(f"_dynamical_matterix_{output.name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_isaac_launcher(output: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(output))
    path = output / "run_isaac_sim.py"
    spec = importlib.util.spec_from_file_location(f"_dynamical_isaac_{output.name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FiniteValue:
    def __init__(self, value: bool) -> None:
        self.value = value

    def all(self) -> _FiniteValue:
        return self

    def item(self) -> bool:
        return self.value


class _FakeTensor:
    def __init__(self, shape: tuple[int, ...], *, finite: bool = True) -> None:
        self.shape = shape
        self.finite = finite

    def isfinite(self) -> _FiniteValue:
        return _FiniteValue(self.finite)


class _FakeStateMachine:
    def __init__(self, robot: object | None, command: _FakeTensor | None = None) -> None:
        self.num_envs = 1
        self.scene_data = None
        self.robot = robot
        self.command = command or _FakeTensor((1, 8))
        self._last_action_result = None

    def update_scene_data_from_obs(self, observation: object) -> None:
        del observation
        articulations = {} if self.robot is None else {"robot": self.robot}
        self.scene_data = SimpleNamespace(articulations=articulations)

    def _initialize_action_dict_for_agent(
        self,
        agent_name: str,
        action_dim: int,
        action_space_info: object,
    ) -> _FakeTensor:
        del agent_name, action_dim, action_space_info
        return self.command


def _complete_robot() -> SimpleNamespace:
    return SimpleNamespace(
        root_pos_w=_FakeTensor((1, 3)),
        root_quat_w=_FakeTensor((1, 4)),
        ee_pos_w=_FakeTensor((1, 3)),
        ee_quat_w=_FakeTensor((1, 4)),
        gripper_pos=_FakeTensor((1, 2)),
    )


@pytest.fixture(scope="module")
def backend_packs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("backend-packs")
    requirement = write_reference_requirement(root / "requirement.yaml")
    composition = compose_files(
        requirement,
        REPOSITORY / "registries" / "reference-capabilities.yaml",
        HEATER_MANIFEST,
    )
    outputs: dict[str, Path] = {}
    for target in ("matterix", "isaac"):
        output = root / target
        compile_facility(
            HEATER_MANIFEST,
            target,
            output,
            composition_result=composition,
        )
        outputs[target] = output
    return outputs


@pytest.fixture(scope="module")
def composed_backend_packs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("composed-backend-packs")
    requirement = write_reference_requirement(root / "requirement.yaml")
    composition = compose_files(
        requirement,
        REPOSITORY / "registries" / "reference-capabilities.yaml",
        HEATER_MANIFEST,
    )
    assert composition.status == "COMPILED"
    outputs: dict[str, Path] = {}
    for target in ("matterix", "isaac"):
        output = root / target
        compile_facility(
            HEATER_MANIFEST,
            target,
            output,
            composition_result=composition,
        )
        outputs[target] = output
    return outputs


def _canonical_writer(
    runtime: ModuleType,
    pack: dict[str, Any],
    evidence_dir: Path,
    *,
    run_id: str,
    output_path: Path | None = None,
) -> Any:
    writer = runtime.TraceWriter(
        pack,
        run_id=run_id,
        seed=7,
        backend_revision="static-test:not-embodied",
        provenance={"embodied_backend": False, "w1_admitted": False},
        output_path=output_path,
    )
    writer.add("campaign_start", 0.0)
    logical_time = 0.0
    for index, action in enumerate(pack["campaign"]["actions"]):
        action_constraints = runtime.constraint_evidence(
            action,
            pack,
            phase="pre_action",
        )
        writer.add(
            "action",
            logical_time,
            action=copy.deepcopy(action),
            constraints=action_constraints,
        )
        logical_time += 1.0
        evidence = runtime.write_snapshot(
            evidence_dir / f"observation-{index:03d}.json",
            {"action_id": action["action_id"], "heater.on": False},
            provider_id=action["provider_id"],
            evidence_class=action["evidence_class"],
        )
        post_constraints = runtime.constraint_evidence(
            action,
            pack,
            phase="post_action",
            channels=[{"name": "material.temperature_K", "value": 298.15}],
        )
        writer.add(
            "observation",
            logical_time,
            observation={
                "frame_id": f"static-frame-{index:03d}",
                "logical_time_s": logical_time,
                "provider_id": action["provider_id"],
                "evidence_class": action["evidence_class"],
                "channels": [
                    {
                        "name": "heater.on",
                        "value": False,
                        "unit": "1",
                        "quality": "valid",
                        "origin": "backend_state",
                        "provider_id": action["provider_id"],
                        "evidence_class": action["evidence_class"],
                    }
                ],
                "evidence_ids": [evidence["evidence_id"]],
            },
            evidence=[evidence],
            constraints=post_constraints,
        )
    writer.add(
        "campaign_end",
        logical_time,
        provenance={"execution_status": "passed"},
    )
    return writer


def test_trace_writer_streams_each_event_before_final_validation(
    backend_packs: dict[str, Path],
    tmp_path: Path,
) -> None:
    output = backend_packs["matterix"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    trace = tmp_path / "campaign_trace.ndjson"
    writer = _canonical_writer(
        runtime,
        pack,
        tmp_path / "evidence",
        run_id="streamed-trace",
        output_path=trace,
    )

    assert len(trace.read_text(encoding="utf-8").splitlines()) == len(writer.events)
    assert writer.write(trace) == runtime.file_sha256(trace)


def _resequence(events: list[dict[str, Any]]) -> None:
    for sequence, event in enumerate(events):
        event["sequence"] = sequence


@pytest.mark.parametrize(
    ("named", "xyzw"),
    [
        ({"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0}, [0.0, 0.0, 0.0, 1.0]),
        ({"w": 0.5, "x": 0.5, "y": 0.5, "z": 0.5}, [0.5, 0.5, 0.5, 0.5]),
    ],
)
def test_matterix_quaternion_order_round_trip(
    named: dict[str, float],
    xyzw: list[float],
) -> None:
    assert named_quaternion_to_matterix_xyzw(named) == xyzw
    assert matterix_xyzw_to_named_quaternion(xyzw) == named


def test_matterix_quaternion_conversion_rejects_nonunit_input() -> None:
    with pytest.raises(ValueError, match="unit norm"):
        named_quaternion_to_matterix_xyzw({"w": 1.0, "x": 1.0, "y": 0.0, "z": 0.0})


@pytest.mark.parametrize("target", ["matterix", "isaac"])
def test_backend_runtime_pack_verifies_all_compiler_artifacts(
    backend_packs: dict[str, Path],
    target: str,
) -> None:
    output = backend_packs[target]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)

    assert pack["manifest"]["core_ir_sha256"] == pack["backend"]["core_ir_sha256"]
    assert pack["campaign"]["execution_status"] == "requires_external_runtime_gate"
    assert [action["kind"] for action in pack["campaign"]["actions"]] == [
        "wait",
        "set_heater",
        "pick",
        "place",
        "wait",
        "set_heater",
        "wait",
    ]
    assert pack["action_schema"]["x-dynamical-declared-capability-action-types"] == [
        "observe",
        "pick",
        "place",
        "set_heater",
        "wait",
    ]
    assert "heater.on" in pack["observation_schema"]["x-dynamical-declared-channel-ids"]


def test_standalone_runtime_rejects_artifact_path_escape(tmp_path: Path) -> None:
    output = compile_facility(HEATER_MANIFEST, "matterix", tmp_path / "matterix").output_dir
    runtime = _load_runtime_contract(output)
    manifest_path = output / "compile_manifest.json"
    manifest = _json(manifest_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    manifest["artifacts"][0] = {
        "path": "../outside.txt",
        "sha256": runtime.file_sha256(outside),
    }
    manifest["world_sha256"] = runtime.stable_hash(
        {item["path"]: item["sha256"] for item in manifest["artifacts"]}
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(runtime.RuntimeContractError, match="unsafe compiled artifact path"):
        runtime.verify_compiled_pack(output)


def test_standalone_runtime_rejects_required_json_symlink_before_read(tmp_path: Path) -> None:
    output = compile_facility(HEATER_MANIFEST, "matterix", tmp_path / "matterix").output_dir
    runtime = _load_runtime_contract(output)
    outside = tmp_path / "external-invalid.json"
    outside.write_text("not json\n", encoding="utf-8")
    backend = output / "backend_config.json"
    backend.unlink()
    backend.symlink_to(outside)

    with pytest.raises(runtime.RuntimeContractError, match="symbolic link"):
        runtime.verify_compiled_pack(output)


@pytest.mark.parametrize("target", ["matterix", "isaac"])
def test_runtime_campaign_uses_exact_facility_parameter_names(
    backend_packs: dict[str, Path],
    target: str,
) -> None:
    campaign = _json(backend_packs[target] / "runtime_campaign.json")
    actions = campaign["actions"]

    assert [action["parameters"] for action in actions] == [
        {"duration": 2.0},
        {"enabled": True, "target-temperature": 343.15},
        {"object": "reaction-beaker"},
        {"object": "reaction-beaker", "target": "ika-heater"},
        {"duration": 10.0},
        {"enabled": False, "target-temperature": 343.15},
        {"duration": 5.0},
    ]


def test_matterix_launcher_maps_compiled_campaign_to_upstream_task(
    backend_packs: dict[str, Path],
) -> None:
    output = backend_packs["matterix"]
    config = _json(output / "backend_config.json")
    campaign = _json(output / "runtime_campaign.json")
    launcher = (output / "run_matterix.py").read_text(encoding="utf-8")

    assert config["runtime_status"] == "ready_for_external_runtime_gate"
    assert config["compiled_world_loading"] == {
        "contract": "verify all compile-manifest hashes before AppLauncher starts",
        "execution_world": "selected upstream MATTERIX task",
        "stage_role": (
            "The composed Dynamical stage is the layout and identity contract. MATTERIX "
            "executes the mapped upstream task because it does not load this stage as "
            "a task environment."
        ),
    }
    assert config["upstream_workflow_action_classes"] == [
        "WaitCfg",
        "TurnOnHeaterCfg",
        "PickObjectCfg",
        "PlaceObjectCfg",
        "WaitCfg",
        "TurnOnHeaterCfg",
        "WaitCfg",
    ]
    override = config["workflow_parameter_overrides"]
    assert override["TurnOnHeaterCfg.target_temperature"]["compiled_value_k"] == 343.15
    assert campaign["asset_roles"] == {
        "beaker": "reaction-beaker",
        "heater": "ika-heater",
        "robot": "franka-robot",
    }
    for symbol in (
        "verify_compiled_pack(args.compiled_world)",
        'env_cfg.workflows[config["workflow"]]',
        "StateMachine",
        "TurnOnHeaterCfg",
        "PickObjectCfg",
        "PlaceObjectCfg",
        "_seed_hold_command(state_machine, observation, FRANKA_IK_ACTION_SPACE)",
        '"robot" not in scene_data.articulations',
        "MATTERIX state machine returned no robot command",
        "TraceWriter",
        "write_snapshot",
        'parameters["duration"]',
        'parameters.get("target-temperature")',
    ):
        assert symbol in launcher
    assert "fps=5" in launcher
    assert "test_video_recording.py" not in launcher
    assert "runtime/action_success" not in launcher


def test_matterix_runtime_horizon_cannot_preempt_action_limits(
    backend_packs: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _load_matterix_launcher(backend_packs["matterix"], monkeypatch)
    env_cfg = SimpleNamespace(
        sim=SimpleNamespace(dt=1.0 / 60.0),
        decimation=5,
        episode_length_s=100.0,
    )
    campaign = {
        "actions": [
            {"kind": "wait", "parameters": {"duration": 100.772}},
            {"kind": "set_heater", "parameters": {"enabled": False}},
        ]
    }

    budget = launcher._configure_runtime_horizon(
        env_cfg,
        campaign,
        max_steps_per_action=1500,
    )

    assert budget["required_wait_steps"] == 1211
    assert budget["embodied_action_count"] == 2
    assert env_cfg.episode_length_s > 250.0


def test_matterix_runtime_horizon_rejects_short_per_action_limit(
    backend_packs: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _load_matterix_launcher(backend_packs["matterix"], monkeypatch)
    env_cfg = SimpleNamespace(
        sim=SimpleNamespace(dt=1.0 / 60.0),
        decimation=5,
        episode_length_s=100.0,
    )
    campaign = {"actions": [{"kind": "wait", "parameters": {"duration": 100.772}}]}

    with pytest.raises(RuntimeError, match="needs at least 1211 steps"):
        launcher._configure_runtime_horizon(
            env_cfg,
            campaign,
            max_steps_per_action=1200,
        )


def test_matterix_hold_seed_accepts_complete_finite_robot_observation(
    backend_packs: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _load_matterix_launcher(backend_packs["matterix"], monkeypatch)
    state_machine = _FakeStateMachine(_complete_robot())

    launcher._seed_hold_command(
        state_machine,
        observation=object(),
        action_space_info=SimpleNamespace(total_dim=8),
    )

    assert state_machine._last_action_result is state_machine.command


def test_matterix_hold_seed_rejects_missing_robot_articulation(
    backend_packs: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _load_matterix_launcher(backend_packs["matterix"], monkeypatch)

    with pytest.raises(RuntimeError, match="robot articulation"):
        launcher._seed_hold_command(
            _FakeStateMachine(None),
            observation=object(),
            action_space_info=SimpleNamespace(total_dim=8),
        )


def test_matterix_hold_seed_rejects_missing_pose_field(
    backend_packs: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _load_matterix_launcher(backend_packs["matterix"], monkeypatch)
    robot = _complete_robot()
    robot.ee_quat_w = None

    with pytest.raises(RuntimeError, match="ee_quat_w"):
        launcher._seed_hold_command(
            _FakeStateMachine(robot),
            observation=object(),
            action_space_info=SimpleNamespace(total_dim=8),
        )


def test_matterix_hold_seed_rejects_invalid_shape_and_nonfinite_command(
    backend_packs: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _load_matterix_launcher(backend_packs["matterix"], monkeypatch)
    robot = _complete_robot()
    robot.root_pos_w = _FakeTensor((2, 3))
    with pytest.raises(RuntimeError, match="root_pos_w"):
        launcher._seed_hold_command(
            _FakeStateMachine(robot),
            observation=object(),
            action_space_info=SimpleNamespace(total_dim=8),
        )

    with pytest.raises(RuntimeError, match="hold command is not finite"):
        launcher._seed_hold_command(
            _FakeStateMachine(_complete_robot(), _FakeTensor((1, 8), finite=False)),
            observation=object(),
            action_space_info=SimpleNamespace(total_dim=8),
        )


def test_isaac_target_declares_physics_and_loads_compiled_stage(
    backend_packs: dict[str, Path],
) -> None:
    output = backend_packs["isaac"]
    config = _json(output / "backend_config.json")
    physics = (output / "isaac_physics.usda").read_text(encoding="utf-8")
    root = (output / "root.usda").read_text(encoding="utf-8")
    launcher = (output / "run_isaac_sim.py").read_text(encoding="utf-8")

    assert config["runtime_status"] == "ready_for_external_runtime_gate"
    assert config["compiled_world_loading"]["execution_world"] == (
        "the composed Dynamical root.usda stage"
    )
    assert config["compiled_world_loading"]["stage_open_api"] == (
        "omni.usd.get_context().open_stage"
    )
    for api in (
        "PhysicsCollisionAPI",
        "PhysicsRigidBodyAPI",
        "PhysicsMassAPI",
        "PhysicsArticulationRootAPI",
    ):
        assert api in physics
    assert "@./isaac_physics.usda@" in root
    for symbol in (
        "verify_compiled_pack(args.compiled_world)",
        "open_stage(str(stage_path))",
        "UsdPhysics.FixedJoint.Define",
        "world.step(render=True)",
        "TraceWriter",
        "write_snapshot",
        'parameters["duration"]',
        'parameters["target-temperature"]',
    ):
        assert symbol in launcher
    assert "W1 still requires" in config["claim_boundary"]
    assert "runtime/action_success" not in launcher


@pytest.mark.parametrize("target", ["matterix", "isaac"])
def test_standalone_runtime_writes_complete_canonical_dynamical_trace(
    composed_backend_packs: dict[str, Path],
    tmp_path: Path,
    target: str,
) -> None:
    output = composed_backend_packs[target]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    writer = _canonical_writer(
        runtime,
        pack,
        tmp_path,
        run_id=f"static-contract-{target}",
    )

    trace_path = tmp_path / "trace.ndjson"
    trace_hash = writer.write(trace_path)
    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    runtime.validate_trace(events, pack)
    parsed_events = read_trace(trace_path)
    trace_schema = _json(output / "campaign_trace.schema.json")
    validator = Draft202012Validator(trace_schema)
    for event in events:
        validator.validate(event)
    replay_result = replay_trace(
        trace_path,
        tmp_path / f"replay-{target}.ndjson",
    )

    assert len(trace_hash) == 64
    assert len(parsed_events) == len(events)
    assert replay_result["valid"] is True
    assert [event["event_type"] for event in events] == [
        "campaign_start",
        *[item for _ in pack["campaign"]["actions"] for item in ("action", "observation")],
        "campaign_end",
    ]
    assert all(event["schema_version"] == "dynamical.campaign.v0.1" for event in events)
    assert events[2]["evidence"][0]["sha256"] == runtime.file_sha256(
        tmp_path / "observation-000.json"
    )


@pytest.mark.parametrize("target", ["matterix", "isaac"])
@pytest.mark.parametrize(
    ("defect", "message"),
    [
        ("action_kind", "not declared by the compiled schema"),
        ("parameter", "undeclared parameters"),
        ("actor", "does not provide"),
        ("channel", "not declared by the compiled schema"),
    ],
)
def test_trace_validator_rejects_values_outside_compiled_pack_contract(
    backend_packs: dict[str, Path],
    tmp_path: Path,
    target: str,
    defect: str,
    message: str,
) -> None:
    output = backend_packs[target]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    writer = _canonical_writer(
        runtime,
        pack,
        tmp_path,
        run_id=f"rejection-{target}-{defect}",
    )
    events = copy.deepcopy(writer.events)
    if defect == "action_kind":
        events[1]["action"]["kind"] = "drop"
    elif defect == "parameter":
        events[1]["action"]["parameters"]["duration_s"] = 2.0
    elif defect == "actor":
        events[1]["action"]["actor_id"] = "franka-agent"
    else:
        events[2]["observation"]["channels"][0]["name"] = "runtime/action_success"

    with pytest.raises(runtime.RuntimeContractError, match=message):
        runtime.validate_trace(events, pack)


def test_trace_validator_and_writer_reject_shortened_campaign(
    backend_packs: dict[str, Path],
    tmp_path: Path,
) -> None:
    output = backend_packs["matterix"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    writer = _canonical_writer(
        runtime,
        pack,
        tmp_path,
        run_id="shortened-campaign",
    )
    events = copy.deepcopy(writer.events)
    del events[-3:-1]
    _resequence(events)

    with pytest.raises(runtime.RuntimeContractError, match="complete compiled campaign"):
        runtime.validate_trace(events, pack)

    writer.events = events
    with pytest.raises(runtime.RuntimeContractError, match="complete compiled campaign"):
        writer.write(tmp_path / "shortened.ndjson")
    assert not (tmp_path / "shortened.ndjson").exists()


def test_trace_validator_rejects_longer_campaign(
    backend_packs: dict[str, Path],
    tmp_path: Path,
) -> None:
    output = backend_packs["matterix"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    writer = _canonical_writer(runtime, pack, tmp_path, run_id="longer-campaign")
    events = copy.deepcopy(writer.events)
    events[-1:-1] = copy.deepcopy(events[-3:-1])
    _resequence(events)

    with pytest.raises(runtime.RuntimeContractError, match="complete compiled campaign"):
        runtime.validate_trace(events, pack)


@pytest.mark.parametrize(
    "field",
    ["campaign_id", "ir_hash", "world_hash", "campaign_hash"],
)
def test_trace_validator_rejects_identity_not_bound_to_pack(
    backend_packs: dict[str, Path],
    tmp_path: Path,
    field: str,
) -> None:
    output = backend_packs["matterix"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    writer = _canonical_writer(runtime, pack, tmp_path, run_id=f"changed-{field}")
    events = copy.deepcopy(writer.events)
    replacement = "different-campaign" if field == "campaign_id" else "0" * 64
    for event in events:
        event[field] = replacement

    with pytest.raises(runtime.RuntimeContractError, match=f"trace {field} does not bind"):
        runtime.validate_trace(events, pack)


def test_trace_validator_rejects_unknown_event_type(
    backend_packs: dict[str, Path],
    tmp_path: Path,
) -> None:
    output = backend_packs["matterix"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    writer = _canonical_writer(runtime, pack, tmp_path, run_id="unknown-event")
    events = copy.deepcopy(writer.events)
    events[2]["event_type"] = "telemetry"

    with pytest.raises(runtime.RuntimeContractError, match="unknown event types"):
        runtime.validate_trace(events, pack)


def test_trace_validator_rejects_reordered_actions(
    backend_packs: dict[str, Path],
    tmp_path: Path,
) -> None:
    output = backend_packs["matterix"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    writer = _canonical_writer(runtime, pack, tmp_path, run_id="reordered-actions")
    events = copy.deepcopy(writer.events)
    events[1]["action"], events[3]["action"] = events[3]["action"], events[1]["action"]

    with pytest.raises(runtime.RuntimeContractError, match="differs from the compiled campaign"):
        runtime.validate_trace(events, pack)


def test_composed_runtime_uses_selected_provider_graph(tmp_path: Path) -> None:
    requirement = write_reference_requirement(tmp_path / "requirement.yaml")
    composition = compose_files(
        requirement,
        REPOSITORY / "registries" / "reference-capabilities.yaml",
        HEATER_MANIFEST,
    )
    assert composition.status == "COMPILED"
    output = tmp_path / "composed-matterix"
    compile_facility(
        HEATER_MANIFEST,
        "matterix",
        output,
        composition_result=composition,
    )
    campaign = _json(output / "runtime_campaign.json")
    config = _json(output / "backend_config.json")
    assert config["runtime_scope"] == "selected_bounded_provider_only"
    assert config["module_status"]["representative_full_facility"] == "blocked"
    assert config["full_facility_runtime_blocker"]
    assert len(campaign["actions"]) == 7
    assert {action["provider_id"] for action in campaign["actions"]} == {
        "matterix-heater-workstation-simulator",
    }
    assert {action["evidence_class"] for action in campaign["actions"]} == {"simulator"}
    assert {binding["operation_id"] for binding in campaign["provider_bindings"].values()} == {
        "apply-thermal-program",
    }
    heater_on = next(
        action
        for action in campaign["actions"]
        if action["kind"] == "set_heater" and action["parameters"]["enabled"] is True
    )
    heated_dwell = next(
        action
        for action in campaign["actions"]
        if action["kind"] == "wait" and action["parameters"]["duration"] == 10.0
    )
    assert heater_on["parameters"]["target-temperature"] == 343.15
    assert heated_dwell["provider_id"] == "matterix-heater-workstation-simulator"
    assert campaign["selected_operation"] == {
        "operation_id": "apply-thermal-program",
        "provider_id": "matterix-heater-workstation-simulator",
        "evidence_class": "simulator",
        "adapter_ids": [
            "dynamical-matterix-franka-control",
            "dynamical-matterix-heater-control",
            "dynamical-two-zone-thermal-python",
        ],
        "parameters": {"dwell-time": 10.0, "target-temperature": 343.15},
    }
    runtime = _load_runtime_contract(output)
    verified = runtime.verify_compiled_pack(output)
    runtime.validate_action(campaign["actions"][0], verified)
    manifest = _json(output / "compile_manifest.json")
    assert "composition_result.json" in {item["path"] for item in manifest["artifacts"]}


def test_composed_runtime_uses_selected_thermal_parameters(tmp_path: Path) -> None:
    request = copy.deepcopy(REFERENCE_REQUIREMENT)
    thermal_step = next(step for step in request["steps"] if step["step_id"] == "heat")
    parameter_values = {"target-temperature": 333.15, "dwell-time": 42.0}
    for parameter in thermal_step["parameters"]:
        parameter["value"] = parameter_values[parameter["name"]]
    requirement = tmp_path / "requirement.yaml"
    requirement.write_text(yaml.safe_dump(request, sort_keys=False), encoding="utf-8")

    composition = compose_files(
        requirement,
        REPOSITORY / "registries" / "reference-capabilities.yaml",
        HEATER_MANIFEST,
    )
    assert composition.status == "COMPILED"
    output = compile_facility(
        HEATER_MANIFEST,
        "matterix",
        tmp_path / "compiled",
        composition_result=composition,
    ).output_dir
    campaign = _json(output / "runtime_campaign.json")

    assert campaign["selected_operation"]["parameters"] == {
        "dwell-time": 42.0,
        "target-temperature": 333.15,
    }
    heater_actions = [action for action in campaign["actions"] if action["kind"] == "set_heater"]
    assert {action["parameters"]["target-temperature"] for action in heater_actions} == {333.15}
    assert any(
        action["kind"] == "wait" and action["parameters"] == {"duration": 42.0}
        for action in campaign["actions"]
    )


def test_matterix_observation_units_stay_manifest_bound_across_actions(
    composed_backend_packs: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = composed_backend_packs["matterix"]
    config = _json(output / "backend_config.json")
    campaign = _json(output / "runtime_campaign.json")
    document = load_facility_manifest(HEATER_MANIFEST)
    declared_units = {
        channel.id: channel.unit for device in document.devices for channel in device.state_channels
    } | {
        channel.channel_id: channel.unit
        for material in document.material_states
        for channel in material.initial_channels
    }
    binding_units = {
        binding["channel_id"]: binding["unit"] for binding in config["observation_bindings"]
    }
    assert binding_units == declared_units
    assert {
        channel: binding_units[channel]
        for channel in (
            "balance.mass_kg",
            "material.mass_kg",
            "fluidA/CCCl",
            "heater.target_temperature_K",
        )
    } == {
        "balance.mass_kg": "kg",
        "material.mass_kg": "kg",
        "fluidA/CCCl": "mol/kg",
        "heater.target_temperature_K": "K",
    }

    launcher = _load_matterix_launcher(output, monkeypatch)
    observation = {
        "policy": {
            "ika_plate_is_heater_on": True,
            "ika_plate_temperature": 343.15,
            "beaker_temperature": 332.22,
            "beaker_is_in_contact": True,
        }
    }
    for action in campaign["actions"]:
        channels = launcher._channels(
            observation,
            config,
            provider_id=action["provider_id"],
            evidence_class=action["evidence_class"],
        )
        for channel in channels:
            assert channel["unit"] == declared_units[channel["name"]]


def test_matterix_observation_unit_conflicts_fail_closed() -> None:
    document = {
        "devices": [
            {
                "id": "balance",
                "state_channels": [{"id": "sample.mass", "value_type": "number", "unit": "kg"}],
            }
        ],
        "material_states": [
            {
                "id": "sample",
                "initial_channels": [{"channel_id": "sample.mass", "value": 1.0, "unit": "g"}],
            }
        ],
    }
    with pytest.raises(ValueError, match="conflicting units"):
        _observation_bindings(document)


def test_compiled_matterix_gate_rejects_a_masked_child_failure(
    backend_packs: dict[str, Path],
    tmp_path: Path,
) -> None:
    output = backend_packs["matterix"]
    gate_path = output / "run_matterix_gate.py"
    assert gate_path.is_file()
    spec = importlib.util.spec_from_file_location("_dynamical_matterix_gate", gate_path)
    assert spec is not None and spec.loader is not None
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)

    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "runtime_evidence.json").write_text(
        json.dumps(
            {
                "receipt_complete": True,
                "execution_status": "failed",
                "intended_exit_code": 1,
                "trace_sha256": None,
            }
        ),
        encoding="utf-8",
    )
    assert gate.validate_receipt(evidence) == (False, "runtime execution did not pass")

    launcher = (output / "run_matterix.py").read_text(encoding="utf-8")
    assert launcher.index("_receipt(", launcher.index("finally:")) < launcher.index(
        "simulation_app.close()"
    )


def test_matterix_and_isaac_emit_the_same_runtime_receipt_contract(
    backend_packs: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_fields = {
        "schema_version",
        "backend",
        "host_id",
        "backend_revision",
        "core_ir_sha256",
        "compiled_world_sha256",
        "execution_status",
        "trace_sha256",
        "receipt_complete",
        "intended_exit_code",
        "simulation_app_shutdown_requested",
        "runtime_error",
        "w1_admitted",
        "manual_gates",
        "artifacts",
    }
    for target in ("matterix", "isaac"):
        output = backend_packs[target]
        runtime = _load_runtime_contract(output)
        pack = runtime.verify_compiled_pack(output)
        evidence = tmp_path / target
        evidence.mkdir()
        trace = evidence / "campaign_trace.ndjson"
        trace.write_text("{}\n", encoding="utf-8")
        if target == "matterix":
            for name in ("launcher.log", "pid", "exit-status"):
                (evidence / name).write_text("orchestration\n", encoding="utf-8")
        trace_hash = hashlib.sha256(trace.read_bytes()).hexdigest()
        if target == "matterix":
            launcher = _load_matterix_launcher(output, monkeypatch)
            launcher._receipt(
                evidence,
                pack,
                "passed",
                trace_hash,
                intended_exit_code=0,
            )
        else:
            launcher = _load_isaac_launcher(output, monkeypatch)
            launcher._write_receipt(evidence, pack, "passed", trace_hash)
        receipt = _json(evidence / "runtime_evidence.json")
        assert set(receipt) == expected_fields
        assert receipt["receipt_complete"] is True
        assert receipt["intended_exit_code"] == 0
        assert receipt["runtime_error"] is None
        if target == "matterix":
            assert not {item["path"] for item in receipt["artifacts"]}.intersection(
                {"launcher.log", "pid", "exit-status"}
            )


def test_compiled_matterix_gate_requires_hash_bound_video(
    backend_packs: dict[str, Path],
    tmp_path: Path,
) -> None:
    output = backend_packs["matterix"]
    gate_path = output / "run_matterix_gate.py"
    spec = importlib.util.spec_from_file_location("_dynamical_matterix_video_gate", gate_path)
    assert spec is not None and spec.loader is not None
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)

    evidence = tmp_path / "evidence"
    evidence.mkdir()
    trace = evidence / "campaign_trace.ndjson"
    trace.write_text("trace\n", encoding="utf-8")
    trace_hash = hashlib.sha256(trace.read_bytes()).hexdigest()

    def write_receipt(artifacts: list[dict[str, str]]) -> None:
        (evidence / "runtime_evidence.json").write_text(
            json.dumps(
                {
                    "receipt_complete": True,
                    "execution_status": "passed",
                    "intended_exit_code": 0,
                    "trace_sha256": trace_hash,
                    "artifacts": artifacts,
                }
            ),
            encoding="utf-8",
        )

    write_receipt([])
    assert gate.validate_receipt(evidence) == (
        False,
        "runtime video receipt is absent or ambiguous",
    )

    video = evidence / "matterix-runtime.mp4"
    video.write_bytes(b"")
    write_receipt([{"path": video.name, "sha256": hashlib.sha256(b"").hexdigest()}])
    assert gate.validate_receipt(evidence) == (False, "runtime video is absent or empty")

    video.write_bytes(b"video")
    write_receipt([{"path": video.name, "sha256": hashlib.sha256(b"changed").hexdigest()}])
    assert gate.validate_receipt(evidence) == (
        False,
        "runtime video hash does not match the receipt",
    )

    write_receipt([{"path": video.name, "sha256": hashlib.sha256(b"video").hexdigest()}])
    assert gate.validate_receipt(evidence) == (True, "passed")


def test_direct_embodied_replay_rejects_path_escape_and_forged_campaign(
    composed_backend_packs: dict[str, Path],
    tmp_path: Path,
) -> None:
    output = composed_backend_packs["matterix"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    writer = _canonical_writer(runtime, pack, evidence, run_id="direct-replay")
    trace = evidence / "campaign_trace.ndjson"
    writer.write(trace)

    def write_receipt(artifacts: list[dict[str, str]]) -> Path:
        receipt = evidence / "runtime_evidence.json"
        receipt.write_text(
            json.dumps(
                {
                    "schema_version": "dynamical.runtime-evidence.v1",
                    "backend": "matterix",
                    "host_id": "test-host",
                    "backend_revision": "test-revision",
                    "core_ir_sha256": pack["manifest"]["core_ir_sha256"],
                    "compiled_world_sha256": pack["manifest"]["world_sha256"],
                    "execution_status": "passed",
                    "trace_sha256": hashlib.sha256(trace.read_bytes()).hexdigest(),
                    "receipt_complete": True,
                    "intended_exit_code": 0,
                    "simulation_app_shutdown_requested": True,
                    "runtime_error": None,
                    "w1_admitted": False,
                    "manual_gates": ["independent review required"],
                    "artifacts": artifacts,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return receipt

    trace_hash = hashlib.sha256(trace.read_bytes()).hexdigest()
    receipt = write_receipt([{"path": trace.name, "sha256": trace_hash}])
    replay = evidence / "replay.ndjson"
    with pytest.raises(CampaignValidationError, match="runtime video"):
        replay_trace(
            trace,
            replay,
            compiled_world=output,
            runtime_receipt=receipt,
        )
    outside = tmp_path / "outside.json"
    outside.write_text("{}\n", encoding="utf-8")
    outside_hash = hashlib.sha256(outside.read_bytes()).hexdigest()
    write_receipt(
        [
            {"path": trace.name, "sha256": trace_hash},
            {"path": "../outside.json", "sha256": outside_hash},
        ]
    )
    with pytest.raises(CampaignValidationError, match="artifact path is unsafe"):
        replay_trace(
            trace,
            replay,
            compiled_world=output,
            runtime_receipt=receipt,
        )
    forged = copy.deepcopy(writer.events)
    heater = next(
        event
        for event in forged
        if isinstance(event.get("action"), dict)
        and event["action"].get("kind") == "set_heater"
        and event["action"]["parameters"]["enabled"] is True
    )
    heater["action"]["parameters"]["enabled"] = False
    trace.write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in forged),
        encoding="utf-8",
    )
    forged_hash = hashlib.sha256(trace.read_bytes()).hexdigest()
    write_receipt([{"path": trace.name, "sha256": forged_hash}])
    with pytest.raises(CampaignValidationError, match="differs from the compiled campaign"):
        replay_trace(
            trace,
            replay,
            compiled_world=output,
            runtime_receipt=receipt,
        )


def test_embodied_binding_rejects_missing_bound_backend_channel(
    composed_backend_packs: dict[str, Path],
) -> None:
    output = composed_backend_packs["matterix"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    action = next(item for item in pack["campaign"]["actions"] if item["kind"] == "set_heater")

    with pytest.raises(CampaignValidationError, match="bound backend channel"):
        _expected_snapshot_channels({"observation": {}}, action, pack)


def test_runtime_requires_bound_constraints_and_rejects_self_admission(
    backend_packs: dict[str, Path],
    tmp_path: Path,
) -> None:
    output = backend_packs["matterix"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    writer = _canonical_writer(runtime, pack, tmp_path, run_id="constraint-required")

    missing = copy.deepcopy(writer.events)
    missing[1]["constraints"] = []
    with pytest.raises(runtime.RuntimeContractError, match="constraint coverage differs"):
        runtime.validate_trace(missing, pack)

    extra = copy.deepcopy(writer.events)
    extra[1]["constraints"].extend(
        runtime.constraint_evidence(
            pack["campaign"]["actions"][1],
            pack,
            phase="pre_action",
        )
    )
    with pytest.raises(runtime.RuntimeContractError, match="extra=.*setpoint-range"):
        runtime.validate_trace(extra, pack)

    self_admitted = copy.deepcopy(writer.events)
    self_admitted[0]["provenance"]["w1_admitted"] = True
    with pytest.raises(runtime.RuntimeContractError, match="cannot self-admit W1"):
        runtime.validate_trace(self_admitted, pack)


def test_runtime_emits_canonical_scalar_range_and_runtime_constraints(
    backend_packs: dict[str, Path],
) -> None:
    output = backend_packs["matterix"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    wait_action = pack["campaign"]["actions"][0]
    heater_action = pack["campaign"]["actions"][1]
    heated_wait_action = pack["campaign"]["actions"][4]

    mass = runtime.constraint_evidence(wait_action, pack, phase="pre_action")
    setpoint = runtime.constraint_evidence(heater_action, pack, phase="pre_action")
    safety = runtime.constraint_evidence(
        heated_wait_action,
        pack,
        phase="post_action",
        channels=[{"name": "material.temperature_K", "value": 310.0}],
    )

    assert mass[0]["limit"] == {
        "operator": "gt",
        "bound": 0.0,
        "unit": "kg",
        "enforcement": "reject",
    }
    assert setpoint[0]["limit"] == {
        "operator": "between",
        "bound": {"minimum": 303.15, "maximum": 343.15},
        "unit": "K",
        "enforcement": "reject",
    }
    assert safety[0]["phase"] == "runtime"
    assert safety[0]["limit"]["enforcement"] == "terminate"


@pytest.mark.parametrize(
    ("field", "invalid_value", "message"),
    [
        ("limit", 0.0, "limit differs"),
        ("limit", {"operator": "gt"}, "limit differs"),
        ("phase", "runtime", "phase differs"),
        ("verifier", "self-approved", "verifier differs"),
    ],
)
def test_runtime_rejects_noncanonical_constraint_records(
    backend_packs: dict[str, Path],
    tmp_path: Path,
    field: str,
    invalid_value: object,
    message: str,
) -> None:
    output = backend_packs["matterix"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    writer = _canonical_writer(runtime, pack, tmp_path, run_id=f"invalid-{field}")
    events = copy.deepcopy(writer.events)
    events[1]["constraints"][0][field] = invalid_value

    with pytest.raises(runtime.RuntimeContractError, match=message):
        runtime.validate_trace(events, pack)


def test_runtime_recomputes_constraint_truth_from_measured_value(
    backend_packs: dict[str, Path],
    tmp_path: Path,
) -> None:
    output = backend_packs["matterix"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    writer = _canonical_writer(runtime, pack, tmp_path, run_id="forged-constraint")
    events = copy.deepcopy(writer.events)
    events[1]["constraints"][0]["measured_value"] = -1.0
    events[1]["constraints"][0]["passed"] = True

    with pytest.raises(runtime.RuntimeContractError, match="differs from its measured value"):
        runtime.validate_trace(events, pack)


def test_runtime_binds_snapshot_id_to_evidence_hash(
    backend_packs: dict[str, Path],
    tmp_path: Path,
) -> None:
    output = backend_packs["matterix"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    writer = _canonical_writer(runtime, pack, tmp_path, run_id="forged-evidence")
    events = copy.deepcopy(writer.events)
    events[2]["evidence"][0]["sha256"] = "0" * 64

    with pytest.raises(runtime.RuntimeContractError, match="content hash"):
        runtime.validate_trace(events, pack)


def test_runtime_rejects_misplaced_constraint_record(
    backend_packs: dict[str, Path],
    tmp_path: Path,
) -> None:
    output = backend_packs["matterix"]
    runtime = _load_runtime_contract(output)
    pack = runtime.verify_compiled_pack(output)
    writer = _canonical_writer(runtime, pack, tmp_path, run_id="misplaced-constraint")
    events = copy.deepcopy(writer.events)
    events[0]["constraints"] = copy.deepcopy(events[1]["constraints"])

    with pytest.raises(runtime.RuntimeContractError, match="campaign_start cannot carry"):
        runtime.validate_trace(events, pack)


def _rehash_composition(value: dict[str, Any]) -> CompositionResult:
    value.pop("sources", None)
    parsed = CompositionResult.model_validate(value)
    assert parsed.virtual_sdl is not None
    virtual_payload = parsed.virtual_sdl.model_dump(mode="json")
    virtual_payload.pop("virtual_sdl_sha256")
    virtual_hash = canonical_sha256(virtual_payload)
    value["virtual_sdl"]["virtual_sdl_sha256"] = virtual_hash
    value["composition_sha256"] = virtual_hash
    parsed = CompositionResult.model_validate(value)
    result_payload = parsed.model_dump(mode="json", exclude_none=True)
    result_payload.pop("resolution_sha256")
    value["resolution_sha256"] = canonical_sha256(result_payload)
    return CompositionResult.model_validate(value)


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("provider_id", "unrelated-provider", "not admitted"),
        ("selected_facility_id", "unrelated-facility", "facility is not admitted"),
        ("endpoint_id", "franka-agent", "endpoint differs"),
    ],
)
def test_compiler_rejects_composition_not_bound_to_facility_admission(
    tmp_path: Path,
    field: str,
    replacement: str,
    message: str,
) -> None:
    requirement = write_reference_requirement(tmp_path / "requirement.yaml")
    composition = compose_files(
        requirement,
        REPOSITORY / "registries" / "reference-capabilities.yaml",
        HEATER_MANIFEST,
    )
    value = composition.model_dump(mode="json", exclude_none=True)
    value["virtual_sdl"]["operation_bindings"][1][field] = replacement
    forged = _rehash_composition(value)

    with pytest.raises(ValueError, match=message):
        compile_facility(
            HEATER_MANIFEST,
            "matterix",
            tmp_path / field,
            composition_result=forged,
        )


def test_compiler_rejects_forged_adapter_and_safety_bindings(tmp_path: Path) -> None:
    requirement = write_reference_requirement(tmp_path / "requirement.yaml")
    composition = compose_files(
        requirement,
        REPOSITORY / "registries" / "reference-capabilities.yaml",
        HEATER_MANIFEST,
    )
    value = composition.model_dump(mode="json", exclude_none=True)
    selected = value["virtual_sdl"]["operation_bindings"][1]
    selected["adapter_links"][0]["adapter_id"] = "unrelated-adapter"
    selected["policy"]["safety_limit_ids"] = ["nonexistent-safety-rule"]
    forged = _rehash_composition(value)

    with pytest.raises(ValueError, match="adapter links differ"):
        compile_facility(
            HEATER_MANIFEST,
            "matterix",
            tmp_path / "forged-adapter",
            composition_result=forged,
        )
