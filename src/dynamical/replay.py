"""Replay a campaign trace through the same stable event contract."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any

from .backends.compiled_runtime import (
    RuntimeContractError,
    validate_trace,
    verify_compiled_pack,
)
from .campaign import (
    CampaignValidationError,
    ObservationOrigin,
    RunMode,
    file_sha256,
    load_compiled_campaign_contract,
    read_trace,
    validate_events,
    write_trace,
)


def _read_receipt(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise CampaignValidationError(f"runtime receipt is not readable: {path}") from exc
    if not isinstance(value, Mapping):
        raise CampaignValidationError("runtime receipt must be an object")
    return value


_RUNTIME_RECEIPT_FIELDS = {
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


def _runtime_artifact_records(
    receipt_path: Path,
    receipt: Mapping[str, Any],
) -> dict[str, str]:
    """Verify normalized, in-directory runtime artifacts from a complete receipt."""

    if set(receipt) != _RUNTIME_RECEIPT_FIELDS:
        raise CampaignValidationError("runtime receipt fields are incomplete or unsupported")
    if (
        not isinstance(receipt.get("host_id"), str)
        or not receipt["host_id"]
        or not isinstance(receipt.get("backend_revision"), str)
        or not receipt["backend_revision"]
        or receipt.get("intended_exit_code") != 0
        or receipt.get("simulation_app_shutdown_requested") is not True
        or receipt.get("runtime_error") is not None
        or receipt.get("w1_admitted") is not False
        or not isinstance(receipt.get("manual_gates"), list)
        or not receipt["manual_gates"]
    ):
        raise CampaignValidationError("runtime receipt is incomplete")
    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise CampaignValidationError("runtime receipt artifact list is absent")
    run_root = receipt_path.parent.resolve()
    records: dict[str, str] = {}
    for raw in artifacts:
        if not isinstance(raw, Mapping) or set(raw) != {"path", "sha256"}:
            raise CampaignValidationError("runtime receipt artifact record is invalid")
        relative = raw.get("path")
        digest = raw.get("sha256")
        if not isinstance(relative, str) or not relative or "\\" in relative:
            raise CampaignValidationError("runtime artifact path is invalid")
        relative_path = PurePosixPath(relative)
        if (
            relative_path.is_absolute()
            or relative != relative_path.as_posix()
            or any(part in {"", ".", ".."} for part in relative_path.parts)
        ):
            raise CampaignValidationError("runtime artifact path is unsafe")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise CampaignValidationError("runtime artifact hash is invalid")
        if relative in records:
            raise CampaignValidationError("runtime artifact path is duplicated")
        artifact_path = receipt_path.parent.joinpath(*relative_path.parts)
        current = receipt_path.parent
        for part in relative_path.parts:
            current = current / part
            if current.is_symlink():
                raise CampaignValidationError("runtime artifact path contains a symbolic link")
        if (
            not artifact_path.is_file()
            or not artifact_path.resolve().is_relative_to(run_root)
            or file_sha256(artifact_path) != digest
        ):
            raise CampaignValidationError(f"runtime artifact hash mismatch: {relative}")
        records[relative] = digest
    return records


def _read_raw_trace(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CampaignValidationError(f"runtime trace line {line_number} is not JSON") from exc
        if not isinstance(value, dict):
            raise CampaignValidationError(f"runtime trace line {line_number} is not an object")
        events.append(value)
    return events


def _read_json_object(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CampaignValidationError(f"{label} is not readable JSON") from exc
    if not isinstance(value, Mapping):
        raise CampaignValidationError(f"{label} must be an object")
    return value


def _expected_snapshot_channels(
    snapshot: Mapping[str, Any],
    action: Mapping[str, Any],
    pack: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Reconstruct one action's expected observation channels from its raw backend snapshot.

    Generic over every declared channel binding rather than switching on the
    compiled target by name (``isaac`` is the only embodied target): a channel
    is echoed only when the compiled pack's own
    ``observation_bindings`` record declares it ``compiled_stage_state_binding``
    for *this action's own* ``action_type`` (see ``isaac_sim.py``'s
    ``_paired_channels`` / ``isaac_runtime.py``'s ``_channels``, which compute
    this the same way at compile time and at the live run that wrote the raw
    snapshot) and the raw snapshot's own ``commanded_parameters`` genuinely
    carries the echoed parameter's value.
    """

    provider_id = str(action.get("provider_id"))
    evidence_class = str(action.get("evidence_class"))
    backend = pack.get("backend")
    if not isinstance(backend, Mapping):
        raise CampaignValidationError("compiled backend configuration is invalid")
    raw_bindings = backend.get("observation_bindings")
    if not isinstance(raw_bindings, list):
        raise CampaignValidationError("compiled backend observation bindings are absent")
    target = pack.get("manifest", {}).get("target")
    if target != "isaac":
        raise CampaignValidationError("embodied snapshot binding needs an embodied compiled target")
    action_kind = action.get("kind")
    commanded = snapshot.get("commanded_parameters")
    commanded = commanded if isinstance(commanded, Mapping) else {}
    parameters = commanded.get("parameters")
    parameters = parameters if isinstance(parameters, Mapping) else {}
    # The snapshot echoes the parameters the embodied backend commanded; they
    # must equal the parameters the compiled pack pinned for this action. Without
    # this the reconstructed observation channels (and the proof-output gate they
    # feed) could report values the campaign never commanded, even though the
    # snapshot, receipt, and trace all hash consistently.
    pinned = action.get("parameters")
    pinned = pinned if isinstance(pinned, Mapping) else {}
    for name, pinned_value in pinned.items():
        if name == "sample_transition":
            continue
        if name in parameters and parameters[name] != pinned_value:
            raise CampaignValidationError(
                "embodied snapshot commanded a parameter that differs from the compiled "
                f"campaign: {name!r} snapshot={parameters[name]!r} compiled={pinned_value!r}"
            )
    declared = {
        str(raw.get("channel_id")): raw for raw in raw_bindings if isinstance(raw, Mapping)
    }
    provider_bindings = pack.get("campaign", {}).get("provider_bindings", {})
    provider_binding = provider_bindings.get(action.get("action_id"), {})
    model_channels = set(provider_binding.get("output_channel_ids", {}).values())
    snapshot_channels = snapshot.get("observation_channels")
    if not isinstance(snapshot_channels, list) or not snapshot_channels:
        raise CampaignValidationError("raw embodied snapshot has no observation channels")
    channels: list[dict[str, Any]] = []
    for raw_channel in snapshot_channels:
        if not isinstance(raw_channel, Mapping):
            raise CampaignValidationError("raw embodied observation channel is invalid")
        channel = dict(raw_channel)
        binding = declared.get(str(channel.get("name")))
        if binding is None:
            raise CampaignValidationError("raw embodied snapshot reports an undeclared channel")
        if (
            channel.get("provider_id") != provider_id
            or channel.get("evidence_class") != evidence_class
        ):
            raise CampaignValidationError("raw embodied channel provider binding differs")
        origin = channel.get("origin")
        if origin == "backend_state":
            parameter = binding.get("echoes_parameter")
            if (
                binding.get("status") != "compiled_stage_state_binding"
                or binding.get("action_type") != action_kind
                or parameter is None
                or channel.get("value") != parameters.get(parameter)
            ):
                raise CampaignValidationError("raw backend channel differs from its command")
        elif origin == "source_model" and channel.get("name") not in model_channels:
            raise CampaignValidationError("raw model channel differs from its compiled binding")
        elif origin != "source_model":
            raise CampaignValidationError("raw embodied channel origin is not admitted")
        channels.append(channel)
    return channels


def _verify_raw_snapshot_bindings(
    events: Sequence[Mapping[str, Any]],
    artifact_records: Mapping[str, str],
    *,
    run_root: Path,
    pack: Mapping[str, Any],
) -> None:
    expected_artifacts = {
        path: digest
        for path, digest in artifact_records.items()
        if path.startswith("evidence/") and path.endswith(".json")
    }
    if not expected_artifacts:
        raise CampaignValidationError("runtime receipt has no raw observation artifacts")
    matched: set[str] = set()
    previous_action: Mapping[str, Any] | None = None
    for event in events:
        if event.get("event_type") == "action":
            action = event.get("action")
            if not isinstance(action, Mapping):
                raise CampaignValidationError("runtime trace action is invalid")
            previous_action = action
            continue
        raw_records = event.get("evidence", [])
        if not isinstance(raw_records, list):
            raise CampaignValidationError("runtime trace evidence records are invalid")
        for raw in raw_records:
            if not isinstance(raw, Mapping):
                raise CampaignValidationError("runtime trace evidence record is invalid")
            digest = raw.get("sha256")
            uri = raw.get("uri")
            if not isinstance(digest, str) or not isinstance(uri, str):
                raise CampaignValidationError("runtime trace evidence binding is incomplete")
            candidates = [
                path
                for path, artifact_digest in expected_artifacts.items()
                if artifact_digest == digest and uri.endswith(f"/{path}")
            ]
            if len(candidates) != 1:
                raise CampaignValidationError(
                    "runtime trace evidence does not bind to one receipt artifact"
                )
            matched.add(candidates[0])
            if event.get("event_type") == "observation":
                if previous_action is None:
                    raise CampaignValidationError("runtime observation has no preceding action")
                snapshot = _read_json_object(
                    run_root.joinpath(*PurePosixPath(candidates[0]).parts),
                    "raw backend observation",
                )
                observation = event.get("observation")
                if not isinstance(observation, Mapping):
                    raise CampaignValidationError("runtime observation is invalid")
                if observation.get("channels") != _expected_snapshot_channels(
                    snapshot, previous_action, pack
                ):
                    raise CampaignValidationError(
                        "runtime trace channels differ from the receipt-bound raw snapshot"
                    )
        if event.get("event_type") == "observation":
            if len(raw_records) != 1:
                raise CampaignValidationError("runtime observation must bind one raw snapshot")
            previous_action = None
    if matched != set(expected_artifacts):
        raise CampaignValidationError(
            "runtime receipt observation artifacts are not all trace-bound"
        )


def _verify_observation_origins(events: list[Any]) -> None:
    """Require observations from the compiled Isaac or instrument runtime."""

    observation_seen = False
    for event in events:
        if event.observation is None:
            continue
        for channel in event.observation.channels:
            observation_seen = True
            if channel.origin in {
                ObservationOrigin.RUNTIME_SENSOR,
                ObservationOrigin.BACKEND_STATE,
                ObservationOrigin.SOURCE_MODEL,
            }:
                continue
            raise CampaignValidationError(
                "source trace contains an observation origin that is not admitted for replay"
            )
    if not observation_seen:
        raise CampaignValidationError("source trace has no embodied Isaac observation evidence")


def verify_embodied_source_binding(
    source: str | Path,
    *,
    compiled_world: str | Path,
    runtime_receipt: str | Path,
) -> dict[str, Any]:
    """Bind an embodied trace to verified compiled and runtime receipts."""

    source_path = Path(source)
    receipt_path = Path(runtime_receipt)
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise CampaignValidationError("runtime receipt must be a real file")
    expected_source = receipt_path.parent / "campaign_trace.ndjson"
    if (
        source_path.is_symlink()
        or not source_path.is_file()
        or source_path.resolve() != expected_source.resolve()
    ):
        raise CampaignValidationError(
            "source trace must be campaign_trace.ndjson in the runtime receipt directory"
        )
    source_hash = file_sha256(source_path)
    events = read_trace(source_path)
    contract = load_compiled_campaign_contract(compiled_world)
    receipt = _read_receipt(receipt_path)
    if receipt.get("schema_version") != "dynamical.runtime-evidence.v1":
        raise CampaignValidationError("runtime receipt schema version is unsupported")
    if receipt.get("execution_status") != "passed" or receipt.get("receipt_complete") is not True:
        raise CampaignValidationError("runtime receipt does not record a passed execution")
    if receipt.get("trace_sha256") != source_hash:
        raise CampaignValidationError("runtime receipt does not bind the source trace hash")
    if receipt.get("core_ir_sha256") != contract.core_ir_sha256:
        raise CampaignValidationError("runtime receipt core IR differs from the compiled pack")
    if receipt.get("compiled_world_sha256") != contract.world_sha256:
        raise CampaignValidationError("runtime receipt world differs from the compiled pack")
    if (
        events[0].ir_hash != contract.core_ir_sha256
        or events[0].world_hash != contract.world_sha256
    ):
        raise CampaignValidationError("source trace differs from the compiled pack hashes")
    artifact_records = _runtime_artifact_records(receipt_path, receipt)
    if artifact_records.get("campaign_trace.ndjson") != source_hash:
        raise CampaignValidationError("runtime receipt artifact list omits the source trace")
    try:
        pack = verify_compiled_pack(compiled_world)
        raw_events = _read_raw_trace(source_path)
        validate_trace(raw_events, pack)
    except (OSError, json.JSONDecodeError, RuntimeContractError) as exc:
        raise CampaignValidationError(
            f"source trace does not match the verified compiled campaign: {exc}"
        ) from exc
    # Read directly from the compiled pack's own backend_config rather than a
    # hardcoded {backend_name: target_name} table -- "isaac" is the only
    # embodied target, and its adapter self-declares "isaac_sim" as
    # config["target"] (see isaac_sim.py's emit_isaac_sim), the exact string a
    # genuine Isaac receipt's "backend" field carries.
    backend = receipt.get("backend")
    if backend != pack["backend"].get("target"):
        raise CampaignValidationError("runtime backend differs from the compiled target")
    # Render evidence is an optional declared artifact class, not a hard requirement:
    # this portable adapter's Isaac launcher renders nothing, so "exactly one non-empty
    # .mp4" could never pass. At most one video may be bound; if present it must be a
    # real (non-empty) file, but its absence is not itself a binding failure.
    video_paths = [path for path in artifact_records if path.endswith(".mp4")]
    if len(video_paths) > 1:
        raise CampaignValidationError("runtime receipt must bind at most one runtime video")
    if video_paths:
        video_path = receipt_path.parent.joinpath(*PurePosixPath(video_paths[0]).parts)
        if video_path.stat().st_size == 0:
            raise CampaignValidationError("runtime receipt video is empty")
    start_provenance = raw_events[0].get("provenance")
    if (
        not isinstance(start_provenance, Mapping)
        or start_provenance.get("embodied_backend") is not True
        or start_provenance.get("compiled_adapter") is not True
    ):
        raise CampaignValidationError("source trace lacks embodied compiled-adapter provenance")
    expected_revision = f"{backend}:{receipt.get('backend_revision')}"
    if raw_events[0].get("backend_revision") != expected_revision:
        raise CampaignValidationError(
            "source trace backend revision differs from the runtime receipt"
        )
    _verify_raw_snapshot_bindings(
        raw_events,
        artifact_records,
        run_root=receipt_path.parent,
        pack=pack,
    )
    _verify_observation_origins(events)
    # Consult validate_events' own derived status rather than the raw
    # campaign_end provenance value: the backend that wrote this trace can claim
    # "passed" from its own execution-completion terms (every action ran, every
    # constraint was evaluated) while a declared proof requirement's output stays
    # unavailable -- validate_events is where that is actually checked, once,
    # for every backend (see PROOF_OUTPUT_UNAVAILABLE).
    if validate_events(events)["execution_status"] != "passed":
        raise CampaignValidationError("source trace has no passed terminal execution status")
    return {
        "embodied_evidence_bound": True,
        "compiled_manifest_sha256": contract.manifest_sha256,
        "runtime_receipt_sha256": file_sha256(receipt_path),
        "source_trace_sha256": source_hash,
        "backend": backend,
    }


def replay_trace(
    source: str | Path,
    output: str | Path,
    *,
    compiled_world: str | Path | None = None,
    runtime_receipt: str | Path | None = None,
) -> dict[str, Any]:
    source_path = Path(source)
    source_hash = file_sha256(source_path)
    source_events = read_trace(source_path)
    if (compiled_world is None) != (runtime_receipt is None):
        raise CampaignValidationError(
            "embodied replay binding requires both compiled_world and runtime_receipt"
        )
    binding: dict[str, Any] | None = None
    if compiled_world is not None and runtime_receipt is not None:
        binding = verify_embodied_source_binding(
            source_path,
            compiled_world=compiled_world,
            runtime_receipt=runtime_receipt,
        )
    replay_run_id = f"replay-{source_hash[:12]}"
    replayed = [
        replace(
            event,
            event_id=f"{replay_run_id}:event:{event.sequence:06d}",
            run_id=replay_run_id,
            mode=RunMode.REPLAY,
            source_trace_sha256=source_hash,
            provenance={
                **event.provenance,
                "replay_source_ref": source_path.name,
                "replay_preserved_observation_origin": True,
            },
        )
        for event in source_events
    ]
    result = validate_events(replayed)
    result["trace_sha256"] = write_trace(output, replayed)
    result["source_trace_sha256"] = source_hash
    # Compare against the replayed trace re-read from the file replay_trace just wrote,
    # not against `replayed` itself: `replayed`'s entries are built by dataclasses.replace()
    # over the very same `source_events` objects, so `original.observation ==
    # replayed_entry.observation` would always be True by construction (same object),
    # proving nothing about whether replay actually preserved the data. Re-reading the
    # written file gives an independently deserialized copy to compare against.
    written_events = read_trace(output)
    result["observation_origins_preserved"] = all(
        original.observation == written.observation
        for original, written in zip(source_events, written_events, strict=True)
    )
    result["embodied_evidence_bound"] = False
    result["w1_evidence"] = False
    if binding is not None:
        result.update(binding)
        result["w1_evidence"] = False
        result["w1_blocker"] = (
            "The embodied replay binding does not by itself prove render inspection or "
            "independent W1 review."
        )
    else:
        result["w1_blocker"] = (
            "Replay is not bound to a verified compiled pack and runtime receipt."
        )
    return result
