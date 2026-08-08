"""Compiled MATTERIX adapter runtime copied into a target pack."""

from __future__ import annotations

import argparse
import json
import math
import os
import socket
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

from dynamical_runtime_contract import (
    TraceWriter,
    constraint_evidence,
    file_sha256,
    verify_compiled_pack,
    write_snapshot,
)

EXPECTED_MATTERIX = "3a55f3b2384b8e2bf0adcb83c5219cebbf1a4e56"
EXPECTED_ASSETS = "0d856a0572d3e0823204264fd3d2700e15a43f4b"
MIN_AVAILABLE_BYTES = 32 * 1024**3


def _git_head(path: Path) -> str:
    return subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()


def _available_memory() -> int:
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    raise RuntimeError("MemAvailable is not present in /proc/meminfo")


def _parse_args() -> argparse.Namespace:
    from isaaclab.app import AppLauncher

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiled-world", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--matterix-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-steps-per-action", type=int, default=1200)
    parser.add_argument("--resolution", nargs=2, type=int, default=[1280, 720])
    AppLauncher.add_app_launcher_args(parser)
    return parser.parse_args()


def _preflight(args: argparse.Namespace) -> dict[str, Any]:
    pack = verify_compiled_pack(args.compiled_world)
    config = pack["backend"]
    campaign = pack["campaign"]
    if config.get("runtime_status") != "ready_for_external_runtime_gate":
        raise RuntimeError(str(config.get("runtime_blocker")))
    if campaign.get("execution_status") != "requires_external_runtime_gate":
        raise RuntimeError(str(campaign.get("blocker")))
    if _git_head(args.matterix_root) != EXPECTED_MATTERIX:
        raise RuntimeError("Matterix commit does not match the compiled contract")
    asset_root = args.matterix_root / "source" / "matterix_assets" / "data"
    if _git_head(asset_root) != EXPECTED_ASSETS:
        raise RuntimeError("Matterix asset commit does not match the compiled contract")
    available = _available_memory()
    if available < MIN_AVAILABLE_BYTES:
        found_gib = available / 1024**3
        raise RuntimeError(f"runtime gate needs 32 GiB available; found {found_gib:.1f} GiB")
    return pack


def _configure_runtime_horizon(
    env_cfg: Any,
    campaign: dict[str, Any],
    *,
    max_steps_per_action: int,
) -> dict[str, float | int]:
    """Keep the task horizon from preempting Dynamical's per-action safety limit."""

    if max_steps_per_action < 1:
        raise RuntimeError("max steps per action must be positive")
    simulation_dt = float(env_cfg.sim.dt)
    decimation = int(env_cfg.decimation)
    step_dt = simulation_dt * decimation
    if not math.isfinite(step_dt) or step_dt <= 0:
        raise RuntimeError("MATTERIX task has an invalid environment step size")

    embodied_actions = [
        action for action in campaign.get("actions", []) if isinstance(action, dict)
    ]
    wait_durations = [
        float(action.get("parameters", {}).get("duration", 0.0))
        for action in embodied_actions
        if action.get("kind") == "wait"
    ]
    longest_wait_s = max(wait_durations, default=0.0)
    required_wait_steps = math.ceil(longest_wait_s / step_dt) + 1
    if required_wait_steps > max_steps_per_action:
        raise RuntimeError(
            "compiled wait duration needs at least "
            f"{required_wait_steps} steps per action; received {max_steps_per_action}"
        )

    required_episode_steps = max(1, len(embodied_actions)) * max_steps_per_action + 1
    required_episode_s = required_episode_steps * step_dt
    configured_episode_s = float(env_cfg.episode_length_s)
    env_cfg.episode_length_s = max(configured_episode_s, required_episode_s)
    return {
        "embodied_action_count": len(embodied_actions),
        "step_dt_s": step_dt,
        "longest_wait_s": longest_wait_s,
        "required_wait_steps": required_wait_steps,
        "episode_length_s": float(env_cfg.episode_length_s),
    }


def _mapped_action(action: dict[str, Any], asset_roles: dict[str, str]) -> Any:
    from matterix_sm import PickObjectCfg, PlaceObjectCfg, TurnOnHeaterCfg, WaitCfg
    from matterix_sm.robot_action_spaces import FRANKA_IK_ACTION_SPACE

    kind = action["kind"]
    parameters = action["parameters"]
    if kind == "wait":
        return WaitCfg(duration=float(parameters["duration"]))
    if kind == "set_heater":
        return TurnOnHeaterCfg(
            asset_name="ika_plate",
            value=bool(parameters["enabled"]),
            target_temperature=parameters.get("target-temperature"),
        )
    if kind == "pick":
        if parameters["object"] != asset_roles["beaker"]:
            raise RuntimeError("compiled pick object does not match the beaker role")
        return PickObjectCfg(
            agent_assets="robot",
            object="beaker",
            action_space_info=FRANKA_IK_ACTION_SPACE,
        )
    if kind == "place":
        if parameters["object"] != asset_roles["beaker"]:
            raise RuntimeError("compiled place object does not match the beaker role")
        if parameters["target"] != asset_roles["heater"]:
            raise RuntimeError("compiled place target does not match the heater role")
        return PlaceObjectCfg(
            agent_assets="robot",
            target="ika_plate",
            action_space_info=FRANKA_IK_ACTION_SPACE,
        )
    raise RuntimeError(f"MATTERIX action mapping is absent for {kind!r}")


def _seed_hold_command(state_machine: Any, observation: Any, action_space_info: Any) -> None:
    """Seed MATTERIX's no-agent hold path from the observed robot pose."""
    state_machine.update_scene_data_from_obs(observation)
    scene_data = state_machine.scene_data
    if scene_data is None or "robot" not in scene_data.articulations:
        raise RuntimeError("MATTERIX observation does not contain the robot articulation")
    robot = scene_data.articulations["robot"]
    required_shapes = {
        "root_pos_w": (state_machine.num_envs, 3),
        "root_quat_w": (state_machine.num_envs, 4),
        "ee_pos_w": (state_machine.num_envs, 3),
        "ee_quat_w": (state_machine.num_envs, 4),
    }
    for field, expected_shape in required_shapes.items():
        value = getattr(robot, field, None)
        if value is None or tuple(value.shape) != expected_shape:
            raise RuntimeError(
                f"MATTERIX robot observation {field} is absent or has an invalid shape"
            )
        if not bool(value.isfinite().all().item()):
            raise RuntimeError(f"MATTERIX robot observation {field} is not finite")
    gripper = getattr(robot, "gripper_pos", None)
    if (
        gripper is None
        or len(gripper.shape) != 2
        or gripper.shape[0] != state_machine.num_envs
        or gripper.shape[1] < 1
    ):
        raise RuntimeError(
            "MATTERIX robot observation gripper_pos is absent or has an invalid shape"
        )
    if not bool(gripper.isfinite().all().item()):
        raise RuntimeError("MATTERIX robot observation gripper_pos is not finite")
    command = state_machine._initialize_action_dict_for_agent(
        "robot",
        action_space_info.total_dim,
        action_space_info,
    )
    expected_shape = (state_machine.num_envs, action_space_info.total_dim)
    if command is None or tuple(command.shape) != expected_shape:
        raise RuntimeError("MATTERIX could not create a valid robot hold command")
    if not bool(command.isfinite().all().item()):
        raise RuntimeError("MATTERIX robot hold command is not finite")
    # MATTERIX commit 3a55f3b2 uses this cached value when a semantic action,
    # such as Wait or TurnOnHeater, has no agent_assets of its own.
    state_machine._last_action_result = command


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if hasattr(value, "detach"):
        value = value.detach().cpu()
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _first_scalar(value: Any) -> float | int | bool | str | None:
    converted = _json_value(value)
    while isinstance(converted, list) and converted:
        converted = converted[0]
    return converted if isinstance(converted, (float, int, bool, str)) else None


def _lookup(observation: Any, path: list[str]) -> Any:
    current = observation
    for part in path:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return _first_scalar(current)


def _channels(
    observation: Any,
    config: dict[str, Any],
    *,
    provider_id: str,
    evidence_class: str,
) -> list[dict[str, Any]]:
    channels: list[dict[str, Any]] = []
    for binding in config["observation_bindings"]:
        path = binding.get("source_path")
        value = _lookup(observation, path) if isinstance(path, list) else None
        channels.append(
            {
                "name": binding["channel_id"],
                "value": value,
                "unit": binding.get("unit", "1"),
                "quality": "valid" if value is not None else "unavailable",
                "origin": "backend_state",
                "provider_id": provider_id,
                "evidence_class": evidence_class,
            }
        )
    return channels


def _receipt(
    output: Path,
    pack: dict[str, Any],
    status: str,
    trace_hash: str | None,
    *,
    intended_exit_code: int,
    runtime_error: dict[str, str] | None = None,
) -> None:
    orchestration_files = {
        "exit-status",
        "exit-status.tmp",
        "launcher.log",
        "pid",
        "runtime_evidence.json",
    }
    files = sorted(path for path in output.rglob("*") if path.is_file())
    value = {
        "schema_version": "dynamical.runtime-evidence.v1",
        "backend": "matterix",
        "host_id": socket.gethostname(),
        "backend_revision": EXPECTED_MATTERIX,
        "core_ir_sha256": pack["manifest"]["core_ir_sha256"],
        "compiled_world_sha256": pack["manifest"]["world_sha256"],
        "execution_status": status,
        "trace_sha256": trace_hash,
        "receipt_complete": True,
        "intended_exit_code": intended_exit_code,
        "simulation_app_shutdown_requested": True,
        "runtime_error": runtime_error,
        "w1_admitted": False,
        "manual_gates": [
            "inspect workflow terminal state",
            "inspect decoded render and video",
            "validate trace and replay",
            "confirm no Isaac or MATTERIX process remains",
        ],
        "artifacts": [
            {
                "path": str(path.relative_to(output)),
                "sha256": file_sha256(path),
            }
            for path in files
            if path.name not in orchestration_files
        ],
    }
    with (output / "runtime_evidence.json").open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    args = _parse_args()
    pack = _preflight(args)
    args.output.mkdir(parents=True, exist_ok=True)

    from isaaclab.app import AppLauncher

    app_launcher = AppLauncher(args)
    simulation_app = app_launcher.app
    trace_hash: str | None = None
    status = "failed"
    return_code = 1
    runtime_error: dict[str, str] | None = None
    env = None
    try:
        import gymnasium as gym
        import matterix_tasks  # noqa: F401
        import torch
        from isaaclab_tasks.utils import parse_env_cfg
        from matterix_sm import StateMachine
        from matterix_sm.robot_action_spaces import FRANKA_IK_ACTION_SPACE

        torch.manual_seed(args.seed)
        config = pack["backend"]
        env_cfg = parse_env_cfg(config["task_id"], device=args.device, num_envs=1)
        _configure_runtime_horizon(
            env_cfg,
            pack["campaign"],
            max_steps_per_action=args.max_steps_per_action,
        )
        workflow = env_cfg.workflows[config["workflow"]]
        upstream = workflow if isinstance(workflow, list) else [workflow]
        actual_classes = [type(item).__name__ for item in upstream]
        if actual_classes != config["upstream_workflow_action_classes"]:
            raise RuntimeError(
                "upstream workflow action sequence changed from the compiled mapping"
            )
        env_cfg.prepare_for_video_rec(
            fps=5,
            resolution=tuple(args.resolution),
            render="balanced",
            eye=(1.8, 1.8, 1.8),
            lookat=(0.3, 0.0, 0.3),
        )
        env = gym.make(config["task_id"], cfg=env_cfg, render_mode="rgb_array").unwrapped
        env.start_recording()
        observation, _ = env.reset(seed=args.seed)
        writer = TraceWriter(
            pack,
            run_id=f"matterix-{args.seed:08d}",
            seed=args.seed,
            backend_revision=f"matterix:{EXPECTED_MATTERIX}",
            provenance={
                "embodied_backend": True,
                "compiled_adapter": True,
                "w1_admitted": False,
            },
            output_path=args.output / "campaign_trace.ndjson",
        )
        writer.add("campaign_start", 0.0)
        logical_time = 0.0
        all_success = True
        for index, action in enumerate(pack["campaign"]["actions"]):
            checks = constraint_evidence(action, pack, phase="pre_action")
            if any(not item["passed"] for item in checks):
                raise RuntimeError(f"action {action['action_id']} failed a pre-action constraint")
            writer.add("action", logical_time, action=action, constraints=checks)
            state_machine = StateMachine(num_envs=1, dt=env.step_dt, device=env.device)
            state_machine.set_action_sequence(
                [_mapped_action(action, pack["campaign"]["asset_roles"])]
            )
            state_machine.reset()
            _seed_hold_command(state_machine, observation, FRANKA_IK_ACTION_SPACE)
            steps = 0
            while not (
                state_machine.action_sequence_success | state_machine.action_sequence_failure
            ).all():
                command, semantic_actions = state_machine.step(observation)
                if command is None:
                    raise RuntimeError("MATTERIX state machine returned no robot command")
                observation, _, terminated, truncated, _ = env.step(
                    command.to(env.device), semantic_actions=semantic_actions
                )
                steps += 1
                if (terminated | truncated).any() or steps >= args.max_steps_per_action:
                    break
            success = bool(state_machine.action_sequence_success.all().item())
            snapshot = {
                "action_id": action["action_id"],
                "steps": steps,
                "success": success,
                "observation": _json_value(observation),
            }
            observed_channels = _channels(
                observation,
                config,
                provider_id=str(action["provider_id"]),
                evidence_class=str(action["evidence_class"]),
            )
            all_success = all_success and success
            logical_time += steps * float(env.step_dt)
            evidence = write_snapshot(
                args.output / "evidence" / f"observation-{index:03d}.json",
                snapshot,
                provider_id=str(action["provider_id"]),
                evidence_class=str(action["evidence_class"]),
            )
            post_checks = constraint_evidence(
                action,
                pack,
                phase="post_action",
                channels=observed_channels,
            )
            all_success = all_success and all(item["passed"] for item in post_checks)
            writer.add(
                "observation",
                logical_time,
                observation={
                    "frame_id": f"matterix-frame-{index:03d}",
                    "logical_time_s": logical_time,
                    "provider_id": action["provider_id"],
                    "evidence_class": action["evidence_class"],
                    "channels": observed_channels,
                    "evidence_ids": [evidence["evidence_id"]],
                },
                evidence=[evidence],
                constraints=post_checks,
                provenance={"action_status": "succeeded" if success else "failed"},
            )
            if not success:
                break
        writer.add(
            "campaign_end",
            logical_time,
            provenance={"execution_status": "passed" if all_success else "failed"},
        )
        trace_hash = writer.write(args.output / "campaign_trace.ndjson")
        video_path = args.output / "matterix-runtime.mp4"
        env.save_video(str(video_path))
        if not video_path.is_file() or video_path.stat().st_size == 0:
            raise RuntimeError("MATTERIX runtime video is absent or empty")
        status = "passed" if all_success else "failed"
        return_code = 0 if all_success else 1
    except Exception as error:
        runtime_error = {
            "error_type": type(error).__name__,
            "error": str(error),
            "traceback": traceback.format_exc(),
        }
        status = "failed"
        return_code = 1
    finally:
        if env is not None:
            try:
                env.close()
            except Exception:
                status = "failed"
                return_code = 1
        _receipt(
            args.output,
            pack,
            status,
            trace_hash,
            intended_exit_code=return_code,
            runtime_error=runtime_error,
        )
        try:
            simulation_app.close()
        except Exception:
            status = "failed"
            return_code = 1
            _receipt(
                args.output,
                pack,
                status,
                trace_hash,
                intended_exit_code=return_code,
                runtime_error=runtime_error,
            )
    return return_code


if __name__ == "__main__":
    sys.exit(main())
