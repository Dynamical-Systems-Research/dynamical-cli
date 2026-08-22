"""Private restore preflight for verified virtual sample ledgers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from .campaign import (
    CampaignValidationError,
    CompiledCampaignContract,
    EvidenceClass,
    RunMode,
    TraceEvent,
    _composed_actions,
    _composed_identity,
    _execute_composed_campaign,
    canonical_json,
    load_compiled_campaign_contract,
    stable_hash,
    validate_events,
)
from .samples import Sample

_REFERENCE_LAB = Path(str(files("dynamical").joinpath("bundle", "reference-lab")))


@dataclass(frozen=True)
class _RestoreContext:
    output: Path | None
    child_contract: CompiledCampaignContract
    initial_samples: tuple[Sample, ...]
    restore: dict[str, Any]
    source_trace_sha256: str
    expected_run_id: str
    reused: tuple[tuple[TraceEvent, ...], str] | None


def _resolved(path: str | Path, name: str, *, directory: bool = False) -> Path:
    candidate = Path(path)
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise CampaignValidationError(f"{name} does not exist: {candidate}") from exc
    if directory and not resolved.is_dir():
        raise CampaignValidationError(f"{name} must be a compiled world directory: {candidate}")
    if not directory and not resolved.is_file():
        raise CampaignValidationError(f"{name} must be a file: {candidate}")
    return resolved


def _reject_output_alias(
    output: Path | None, source_trace: Path, worlds: tuple[Path, Path]
) -> None:
    if output is None:
        return
    resolved = output.resolve(strict=False)
    if resolved == source_trace:
        raise CampaignValidationError("restore output aliases the source trace; select a new path")
    for world in worlds:
        if resolved == world or world in resolved.parents:
            raise CampaignValidationError(
                "restore output aliases a compiled-world artifact; select a new path"
            )


def _parse_source(data: bytes) -> tuple[list[TraceEvent], list[bytes]]:
    if not data or not data.endswith(b"\n"):
        raise CampaignValidationError("source trace is partial; NDJSON must end with a newline")
    lines = data.splitlines(keepends=True)
    events: list[TraceEvent] = []
    for line_number, line in enumerate(lines, 1):
        if line in {b"\n", b"\r\n"}:
            raise CampaignValidationError(f"source trace has an empty line at {line_number}")
        try:
            value = json.loads(line)
            if not isinstance(value, dict):
                raise TypeError("event is not an object")
            events.append(TraceEvent.from_dict(value))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise CampaignValidationError(
                f"source trace has invalid NDJSON at line {line_number}: {exc}"
            ) from exc
    return events, lines


def _validated_authority(world: Path) -> tuple[str, str]:
    from .composition import (
        authority_hold_reasons,
        demote_untrusted_admissions,
        validate_composition_result,
        validate_composition_sources,
    )
    from .schema import load_capability_registry, load_facility_manifest

    try:
        raw = json.loads((world / "composition_result.json").read_text(encoding="utf-8"))
        composition = validate_composition_result(raw)
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise CampaignValidationError(f"invalid protected composition: {exc}") from exc
    if composition.status != "COMPILED" or composition.sources is None:
        raise CampaignValidationError("restore requires a COMPILED route with protected sources")
    installed_registry = load_capability_registry(_REFERENCE_LAB / "registry.yaml")
    installed_facility = load_facility_manifest(_REFERENCE_LAB / "facility.yaml")
    sources = composition.sources
    reasons = authority_hold_reasons(
        sources.registry, sources.facility, installed_registry, installed_facility
    )
    registry, demotions = demote_untrusted_admissions(sources.registry, installed_registry)
    if reasons or demotions:
        codes = sorted({item.code for item in [*reasons, *demotions]})
        raise CampaignValidationError(f"restore authority validation failed: {codes}")
    validated = validate_composition_sources(composition, sources.requirement, registry)
    if validated.status != "COMPILED" or validated.virtual_sdl is None:
        raise CampaignValidationError("restore route is HOLD under the installed authority")
    bindings = validated.virtual_sdl.operation_bindings
    if any(item.evidence_class == EvidenceClass.PHYSICAL.value for item in bindings):
        raise CampaignValidationError("physical restore routes are unsupported")
    return sources.registry_sha256, sources.facility_sha256


def _matching_output(
    output: Path | None,
    contract: CompiledCampaignContract,
    initial_samples: tuple[Sample, ...],
    restore: dict[str, Any],
    seed: int,
) -> tuple[tuple[TraceEvent, ...], str] | None:
    if output is None or not output.exists():
        return None
    try:
        data = output.read_bytes()
    except OSError as exc:
        raise CampaignValidationError(
            f"restore output exists but is invalid; select a new path: {output}: {exc}"
        ) from exc
    expected_events, _ = _execute_composed_campaign(
        contract,
        seed=seed,
        initial_samples=initial_samples,
        restore=restore,
    )
    expected_data = "".join(
        canonical_json(event.to_dict()) + "\n" for event in expected_events
    ).encode("utf-8")
    if data != expected_data or not validate_events(expected_events)["valid"]:
        raise CampaignValidationError(
            f"restore output conflicts with the expected run; select a new path: {output}"
        )
    return tuple(expected_events), hashlib.sha256(data).hexdigest()


def _prepare_restore(
    *,
    source_trace: str | Path,
    source_world: str | Path,
    child_world: str | Path,
    at_event_id: str,
    output: str | Path | None,
    seed: int,
) -> _RestoreContext:
    source_path = _resolved(source_trace, "restore source trace")
    source_world_path = _resolved(source_world, "restore source world", directory=True)
    child_world_path = _resolved(child_world, "child world", directory=True)
    output_path = Path(output).resolve(strict=False) if output is not None else None
    _reject_output_alias(output_path, source_path, (source_world_path, child_world_path))

    source_bytes = source_path.read_bytes()
    source_trace_sha256 = hashlib.sha256(source_bytes).hexdigest()
    source_events, source_lines = _parse_source(source_bytes)
    source_validation = validate_events(source_events)
    if not source_validation["valid"] or source_validation["execution_status"] != "passed":
        raise CampaignValidationError(
            "restore source trace did not complete with passed validation"
        )
    start = source_events[0]
    if start.source_trace_sha256 is not None or start.provenance.get("restore") is not None:
        raise CampaignValidationError("restored traces cannot be restore sources")
    physical = EvidenceClass.PHYSICAL.value
    if (
        start.mode is not RunMode.SIMULATE
        or start.provenance.get("embodied_backend")
        or start.provenance.get("embodied_evidence_bound")
        or physical in source_validation["evidence_classes"]
    ):
        raise CampaignValidationError("restore source must be a non-embodied simulate trace")

    source_contract = load_compiled_campaign_contract(source_world_path)
    child_contract = load_compiled_campaign_contract(child_world_path)
    source_registry_sha256, source_facility_sha256 = _validated_authority(source_world_path)
    child_registry_sha256, child_facility_sha256 = _validated_authority(child_world_path)
    if (source_registry_sha256, source_facility_sha256) != (
        child_registry_sha256,
        child_facility_sha256,
    ):
        raise CampaignValidationError("source and child registry or facility authority differs")
    if source_contract.target == "isaac" or child_contract.target == "isaac":
        raise CampaignValidationError("embodied restore routes are unsupported")

    selected = [event for event in source_events if event.event_id == at_event_id]
    if len(selected) != 1:
        raise CampaignValidationError(
            f"restore event ID must match exactly one event: {at_event_id}"
        )
    observation = selected[0]
    if observation.event_type != "observation":
        raise CampaignValidationError(f"restore event is not an observation: {at_event_id}")

    reproduced, ledger = _execute_composed_campaign(
        source_contract,
        seed=start.seed,
        stop_at_event_id=at_event_id,
    )
    recorded_prefix = b"".join(source_lines[: observation.sequence + 1])
    reproduced_prefix = "".join(
        canonical_json(event.to_dict()) + "\n" for event in reproduced
    ).encode("utf-8")
    if reproduced_prefix != recorded_prefix:
        raise CampaignValidationError("source prefix differs from exact current re-execution")

    referenced_samples = {
        event.action.sample_id
        for event in reproduced
        if event.action is not None and event.action.sample_id is not None
    }
    incomplete_samples = sorted(referenced_samples - ledger.keys())
    if incomplete_samples:
        raise CampaignValidationError(
            f"restore source has custody without complete sample state: {incomplete_samples}"
        )
    initial_samples = tuple(ledger[key] for key in sorted(ledger))
    canonical_samples = [sample.model_dump(mode="json") for sample in initial_samples]
    source_classes = sorted(
        {event.action.evidence_class.value for event in reproduced if event.action is not None}
        | {item.evidence_class.value for event in reproduced for item in event.evidence}
    )
    source_prefix_sha256 = hashlib.sha256(recorded_prefix).hexdigest()
    restore = {
        "at_event_id": at_event_id,
        "source_prefix_sha256": source_prefix_sha256,
        "source_world_sha256": source_contract.world_sha256,
        "source_registry_sha256": source_registry_sha256,
        "source_facility_sha256": source_facility_sha256,
        "source_logical_time_s": observation.logical_time_s,
        "initial_samples": canonical_samples,
        "restored_state_sha256": stable_hash(canonical_samples),
        "source_evidence_classes": source_classes,
    }
    binding = stable_hash(restore)
    identity = _composed_identity(
        child_contract, _composed_actions(child_contract), seed, binding, source_trace_sha256
    )
    execution_binding = {
        "source_trace_sha256": source_trace_sha256,
        "restore_binding_sha256": binding,
        "restore": restore,
    }
    reused = _matching_output(output_path, child_contract, initial_samples, execution_binding, seed)
    return _RestoreContext(
        output_path,
        child_contract,
        initial_samples,
        restore,
        source_trace_sha256,
        identity.run_id,
        reused,
    )
