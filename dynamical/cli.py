"""Command-line interface for compilation, simulation, replay, and validation."""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from . import __version__ as _VERSION
from .compiler import compile_facility, validate_path
from .installed import (
    ALIASES,
    SDL1,
    facility_bundle,
    facility_path,
    registry_path,
    requirement_facility,
)

REFERENCE_LAB = SDL1
DEFAULT_REGISTRY = REFERENCE_LAB / "registry.yaml"
DEFAULT_FACILITY = REFERENCE_LAB / "facility.yaml"
RESTORE_EXAMPLE = (
    "dynamical run child-world --restore-from parent.ndjson --restore-world parent-world "
    "--restore-at-event simulate-abc123:event:000006 -o child.ndjson"
)
BRANCH_EXAMPLE = (
    "dynamical validate parent.ndjson --json --compiled-world parent-world "
    "--child-world child-world"
)


def _compile_manifest(world: Path, label: str) -> dict[str, object]:
    manifest = world / "compile_manifest.json"
    if not world.is_dir() or not manifest.is_file():
        raise ValueError(
            f"{label} is not a compiled world: {world}\n"
            f"Example: dynamical compile composition.json -o {shlex.quote(str(world))}"
        )
    return json.loads(manifest.read_text(encoding="utf-8"))


def _branch_command(
    trace: Path, compiled_world: Path, child_world: Path, report: dict[str, object]
) -> str:
    """The exact dry-run restore that branches from ``trace``, or a ValueError.

    Every component is verified before it is named: the trace must be a simulate
    trace that is not itself a restored child, the compiled world must be the one
    the trace ran against, the child world must be a compiled world, the restore
    point is the trace's last observation, and the restore check the named
    command performs must itself pass here first. A command that would fail is
    never emitted.
    """

    from .campaign import CampaignValidationError
    from .restore import _prepare_restore

    if "source_evidence_classes" in report:
        raise ValueError(f"a restored child trace cannot be a restore source: {trace}")
    at_event = report.get("last_observation_event_id")
    if not isinstance(at_event, str):
        raise ValueError(f"trace has no observation to restore at: {trace}")
    manifest = _compile_manifest(compiled_world, "restore source world")
    world_matches = manifest.get("world_sha256") == report.get("world_sha256")
    if not world_matches or manifest.get("core_ir_sha256") != report.get("core_ir_sha256"):
        raise ValueError(
            f"compiled world does not match the trace: {compiled_world}\n"
            f"The trace ran against world_sha256 {report.get('world_sha256')}; pass the "
            "compiled world it was run from."
        )
    _compile_manifest(child_world, "child world")
    try:
        _prepare_restore(
            source_trace=trace,
            source_world=compiled_world,
            child_world=child_world,
            at_event_id=at_event,
            output=None,
            seed=0,
        )
    except CampaignValidationError as exc:
        raise ValueError(f"branch from {trace} at {at_event} is not possible: {exc}") from exc
    return shlex.join(
        [
            "dynamical",
            "run",
            str(child_world),
            "--restore-from",
            str(trace),
            "--restore-world",
            str(compiled_world),
            "--restore-at-event",
            at_event,
            "--dry-run",
        ]
    )


def _preflight_rebind_command(receipt: Path, requirement: Path, facility_alias: str) -> str | None:
    """The exact preflight that freezes a receipt's map against another facility.

    Returns None when the receipt does not record its map or the map is gone; the
    caller then keeps the capability-inspection hint rather than guessing a path.
    """

    try:
        recorded = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    mapping = recorded.get("mapping") if isinstance(recorded, dict) else None
    mapping_path = (
        Path(mapping["path"]) if isinstance(mapping, dict) and mapping.get("path") else None
    )
    if mapping_path is None or not mapping_path.is_file():
        return None
    rebound = receipt.with_name(f"{receipt.stem}.{facility_alias}{receipt.suffix or '.json'}")
    return shlex.join(
        [
            "dynamical",
            "preflight",
            str(mapping_path),
            "--requirement",
            str(requirement),
            "--facility",
            facility_alias,
            "-o",
            str(rebound),
        ]
    )


def _print_json(value: object, *, compact: bool = False) -> None:
    if compact:
        print(json.dumps(value, sort_keys=True, separators=(",", ":")))
    else:
        print(json.dumps(value, indent=2, sort_keys=True))


def _saved_composition(path: Path):
    """Return a validated saved composition, or None for another input type."""

    if path.suffix.lower() != ".json" or not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != "dynamical.composition-result.v1":
        return None
    from .composition import validate_composition_result

    return validate_composition_result(raw)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dynamical",
        description=(
            "Discover, preflight, compose, compile, run, and validate facility operations."
        ),
    )
    parser.add_argument("--version", action="version", version=f"dynamical {_VERSION}")
    commands = parser.add_subparsers(dest="command", required=True)

    capabilities_parser = commands.add_parser(
        "capabilities",
        help="list operations or inspect one operation",
        epilog=(
            "Examples:\n"
            "  dynamical capabilities\n"
            "  dynamical capabilities --operation <operation-id> --json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    capabilities_parser.add_argument(
        "--operation",
        help="return one operation and its approved provider candidates",
    )
    capabilities_parser.add_argument("--json", action="store_true", dest="as_json")
    capabilities_parser.add_argument(
        "--registry",
        type=Path,
        default=None,
        help=(
            "registry to inspect; a custom path is a proposal checked against the installed "
            "registry"
        ),
    )

    capabilities_parser.add_argument(
        "--facility",
        type=Path,
        default=DEFAULT_FACILITY,
        help="installed facility (sdl1 or fastcat) or proposal manifest path",
    )

    compile_parser = commands.add_parser(
        "compile",
        help="compile a manifest or self-contained composition",
        epilog="Example: dynamical compile composition.json -o compiled-world",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    compile_parser.add_argument(
        "input",
        type=Path,
        help="composition for an executable world, or manifest for a validation-only world",
    )
    compile_parser.add_argument("--target", choices=("isaac", "openusd"))
    compile_parser.add_argument("-o", "--output", type=Path)

    compose_parser = commands.add_parser(
        "compose",
        help="select approved providers for a requirement",
        epilog=(
            "Examples:\n"
            "  dynamical compose requirement.yaml --preflight preflight.json "
            "-o composition.json\n"
            "  dynamical compose --schema\n\n"
            "A new campaign composes only against a READY preflight receipt; "
            "dynamical preflight writes one. "
            "Use capability detail for operation ports and parameters. "
            "Use --schema for requirement fields."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    compose_parser.add_argument("requirement", nargs="?", type=Path)
    compose_parser.add_argument("-o", "--output", type=Path)
    compose_parser.add_argument(
        "--preflight",
        type=Path,
        help="READY preflight receipt to bind by digest; requires --output",
    )
    compose_parser.add_argument(
        "--no-preflight",
        action="store_true",
        help=(
            "compose without a frozen starting state; requires --reason, which the receipt "
            "records as preflight_skipped"
        ),
    )
    compose_parser.add_argument(
        "--reason",
        help="why this campaign composes without a preflight receipt; only with --no-preflight",
    )
    compose_parser.add_argument(
        "--schema",
        action="store_true",
        help="print the campaign requirement JSON Schema and exit",
    )
    compose_parser.add_argument(
        "--registry",
        type=Path,
        default=None,
        help="registry to use; a custom path is a proposal checked against installed authority",
    )
    compose_parser.add_argument(
        "--facility",
        type=Path,
        default=None,
        help=(
            "override the requirement-selected facility (sdl1 or fastcat), or proposal path "
            "checked against installed authority"
        ),
    )

    preflight_parser = commands.add_parser(
        "preflight",
        help="freeze the starting state from a map of lab records",
        epilog=(
            "Examples:\n"
            "  dynamical preflight mapping.json --requirement requirement.yaml "
            "-o preflight.json\n"
            "  dynamical preflight mapping.json --requirement requirement.yaml "
            "--registry registry.yaml --facility facility.yaml -o preflight.json\n\n"
            "A READY receipt names the compose handoff in next_command. "
            "A HOLD receipt lists its material gaps; it approves nothing."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    preflight_parser.add_argument(
        "mapping",
        nargs="?",
        type=Path,
        help="agent-authored JSON map of sources, entities, facts, relations, and gaps",
    )
    preflight_parser.add_argument(
        "--requirement",
        type=Path,
        help="campaign requirement the frozen state will be composed against",
    )
    preflight_parser.add_argument(
        "--registry",
        type=Path,
        default=None,
        help="registry to bind by digest; defaults to the selected facility's installed registry",
    )
    preflight_parser.add_argument(
        "--facility",
        type=Path,
        default=None,
        help=(
            "installed facility (sdl1 or fastcat) or manifest path; defaults to the "
            "requirement-selected facility, exactly as compose resolves it"
        ),
    )
    preflight_parser.add_argument("-o", "--output", type=Path, help="receipt path")

    run_parser = commands.add_parser(
        "run",
        help="simulate a compiled world or replay a trace",
        epilog=(
            "Examples:\n"
            "  dynamical run compiled-world -o trace.ndjson\n"
            "  dynamical run child-world --restore-from parent.ndjson "
            "--restore-world parent-world --restore-at-event "
            "simulate-abc123:event:000006 --seed 0 -o child.ndjson\n"
            "  dynamical run child-world --restore-from parent.ndjson "
            "--restore-world parent-world --restore-at-event "
            "simulate-abc123:event:000006 --dry-run\n\n"
            "Scientific values are in observation events under observation.channels. "
            "Validate the completed trace before using them as evidence."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    run_parser.add_argument("input", type=Path)
    run_parser.add_argument("--mode", default="simulate", choices=("simulate", "replay"))
    run_parser.add_argument("-o", "--output", type=Path)
    run_parser.add_argument("--seed", type=int, default=0)
    run_parser.add_argument("--restore-from", type=Path, help="completed source trace")
    run_parser.add_argument("--restore-world", type=Path, help="source compiled world")
    run_parser.add_argument("--restore-at-event", help="source observation event ID")
    run_parser.add_argument(
        "--dry-run", action="store_true", help="run the restore check without child execution"
    )
    run_parser.add_argument(
        "--compiled-world",
        type=Path,
        help="compiled pack used to verify an embodied replay source",
    )
    run_parser.add_argument(
        "--runtime-receipt",
        type=Path,
        help="runtime receipt used to verify an embodied replay source",
    )

    validate_parser = commands.add_parser(
        "validate",
        help="validate a manifest, composition, world, or trace",
        epilog=(
            "Examples:\n"
            "  dynamical validate compiled-world --json\n"
            "  dynamical validate trace.ndjson --json\n"
            f"  {BRANCH_EXAMPLE}\n\n"
            "A valid simulate trace names its replay in next_command and its last "
            "observation in last_observation_event_id. With both --compiled-world and "
            "--child-world it also names the exact dry-run restore in branch_command.\n\n"
            "Campaign requirements are compose inputs:\n"
            "  dynamical compose requirement.yaml --preflight preflight.json "
            "-o composition.json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    validate_parser.add_argument("path", type=Path)
    validate_parser.add_argument("--json", action="store_true", dest="as_json")
    validate_parser.add_argument(
        "--compiled-world",
        type=Path,
        help="the compiled world this trace ran against; checked against the trace",
    )
    validate_parser.add_argument(
        "--child-world",
        type=Path,
        help="the compiled child world to branch into; requires --compiled-world",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command in {"capabilities", "compose", "preflight"} and not getattr(
            args, "schema", False
        ):
            # preflight and compose resolve the facility and registry identically, so
            # the receipt's handoff digests are the ones compose recomputes.
            args.supplied_facility = args.facility
            args.supplied_registry = args.registry
            if args.command in {"compose", "preflight"} and args.facility is None:
                args.facility = (
                    requirement_facility(args.requirement)
                    if args.requirement is not None and args.requirement.is_file()
                    else DEFAULT_FACILITY
                )
            args.facility = facility_path(args.facility)
            args.registry = registry_path(args.registry, args.facility)
        if args.command == "preflight":
            from .preflight import finalize, load_mapping, material_gaps
            from .preflight import write_receipt as write_preflight_receipt

            if args.mapping is None or args.requirement is None or args.output is None:
                message = (
                    "preflight requires a mapping, --requirement, and --output\n"
                    "Example: dynamical preflight mapping.json --requirement requirement.yaml "
                    "-o preflight.json"
                )
                if args.mapping is not None and args.requirement is not None:
                    # Next: names only the real inputs supplied plus the receipt this
                    # command creates; an unknown input is never guessed.
                    next_command = [
                        "dynamical",
                        "preflight",
                        str(args.mapping),
                        "--requirement",
                        str(args.requirement),
                    ]
                    if args.supplied_registry is not None:
                        next_command += ["--registry", str(args.supplied_registry)]
                    if args.supplied_facility is not None:
                        next_command += ["--facility", str(args.supplied_facility)]
                    next_command += ["-o", "preflight.json"]
                    message += f"\nNext: {shlex.join(next_command)}"
                raise ValueError(message)
            if not args.mapping.is_file():
                raise ValueError(f"preflight mapping does not exist: {args.mapping}")
            if not args.requirement.is_file():
                raise ValueError(f"campaign requirement does not exist: {args.requirement}")
            receipt = finalize(
                load_mapping(args.mapping),
                args.mapping,
                requirement=args.requirement,
                registry=args.registry,
                facility=args.facility,
            )
            # Record the map this receipt froze, so a later recovery (a facility
            # rebind after a HOLD) can name the exact preflight to run again.
            receipt["mapping"] = {
                "path": str(args.mapping.resolve()),
                "sha256": hashlib.sha256(args.mapping.read_bytes()).hexdigest(),
            }
            ready = receipt["status"] == "READY"
            if ready:
                # The chain entry: the exact compose handoff, with the selectors the
                # caller supplied so compose resolves the same records.
                command = ["dynamical", "compose", str(args.requirement)]
                command += ["--preflight", str(args.output)]
                if args.supplied_registry is not None:
                    command += ["--registry", str(args.supplied_registry)]
                if args.supplied_facility is not None:
                    command += ["--facility", str(args.supplied_facility)]
                command += ["-o", "composition.json"]
                receipt["next_command"] = shlex.join(command)
            write_preflight_receipt(receipt, args.output)
            summary = {
                "status": receipt["status"],
                "execution_status": "not_executed",
                "output": str(args.output),
                "state_id": receipt["state"]["state_id"],
                "state_sha256": receipt["state"]["state_sha256"],
                "evidence_cutoff": receipt["state"]["evidence_cutoff"],
                "next_action": receipt["next_action"],
                "evidence_classes": [],
                "embodied_evidence_bound": False,
                "claim_boundary": (
                    "Frozen starting state only; no provider approval, physical authority, "
                    "or qualification."
                ),
                "authority_anchor": "installed_bundle",
                "validation_reasons": [],
            }
            if ready:
                summary["next_command"] = receipt["next_command"]
            else:
                summary["material_gaps"] = material_gaps(receipt)
            _print_json(summary, compact=True)
            return 0 if ready else 1
        if args.command == "capabilities":
            from .composition import authority_hold_reasons, demote_untrusted_admissions
            from .schema import load_capability_registry, load_facility_manifest

            bundle = facility_bundle(load_facility_manifest(args.facility).facility.id)
            installed_registry = load_capability_registry(bundle / "registry.yaml")
            proposed_registry = load_capability_registry(args.registry)
            registry, admission_reasons = demote_untrusted_admissions(
                proposed_registry, installed_registry
            )
            authority_reasons = authority_hold_reasons(
                proposed_registry,
                None,
                installed_registry,
                None,
            )
            validation_reasons = [
                item.model_dump(mode="json", exclude_none=True)
                for item in [*authority_reasons, *admission_reasons]
            ]
            registry_role = (
                "installed_authority"
                if args.registry.resolve() == (bundle / "registry.yaml").resolve()
                else "proposal"
            )
            registry_payload = registry.model_dump(mode="json", exclude_none=True)
            for provider in registry_payload["providers"]:
                if authority_reasons and provider["admission"]["status"] == "admitted":
                    provider["admission"]["status"] = "pending"
            proposed_admissions = {
                (item.provider_id, item.operation_id, item.evidence_class): item.admission.status
                for item in proposed_registry.providers
            }
            for provider in registry_payload["providers"]:
                key = (
                    provider["provider_id"],
                    provider["operation_id"],
                    provider["evidence_class"],
                )
                proposed_status = proposed_admissions[key]
                if proposed_status != provider["admission"]["status"]:
                    provider["proposed_admission"] = proposed_status
            if args.operation is not None:
                selected_capabilities = [
                    item
                    for item in registry_payload["capabilities"]
                    if item["operation_id"] == args.operation
                ]
                if not selected_capabilities:
                    available = ", ".join(
                        sorted(item.operation_id for item in registry.capabilities)
                    )
                    other_alias = "fastcat" if bundle == SDL1 else "sdl1"
                    other_registry = load_capability_registry(
                        ALIASES[other_alias] / "registry.yaml"
                    )
                    next_command = f"dynamical capabilities --facility {other_alias}"
                    if any(
                        item.operation_id == args.operation for item in other_registry.capabilities
                    ):
                        next_command += f" --operation {shlex.quote(args.operation)} --json"
                    raise ValueError(
                        f"unknown operation {args.operation!r}; available operations: {available}\n"
                        "Installed facilities: sdl1, fastcat. Select the requirement's facility.\n"
                        f"Next: {next_command}"
                    )
                result = {
                    "schema_version": "dynamical.capability-detail.v1",
                    "registry_id": registry.registry_id,
                    "operation": selected_capabilities[0],
                    "providers": [
                        item
                        for item in registry_payload["providers"]
                        if item["operation_id"] == args.operation
                    ],
                }
            else:
                providers_by_operation: dict[str, list[dict[str, object]]] = {}
                for provider in registry_payload["providers"]:
                    provider_summary = {
                        "provider_id": provider["provider_id"],
                        "evidence_class": provider["evidence_class"],
                        "admission": provider["admission"]["status"],
                        "available": provider["availability"]["available"],
                        "policy": provider["policy"],
                        "cost": provider["cost"],
                        "duration": provider["duration"],
                        "validity_envelope": provider["validity_envelope"],
                    }
                    if "proposed_admission" in provider:
                        provider_summary["proposed_admission"] = provider["proposed_admission"]
                    providers_by_operation.setdefault(provider["operation_id"], []).append(
                        provider_summary
                    )
                result = {
                    "schema_version": "dynamical.capability-index.v1",
                    "registry_id": registry.registry_id,
                    "operations": [
                        {
                            "operation_id": capability["operation_id"],
                            "kind": capability["kind"],
                            "providers": providers_by_operation.get(capability["operation_id"], []),
                        }
                        for capability in registry_payload["capabilities"]
                    ],
                }
            result.update(
                {
                    "registry_role": registry_role,
                    "execution_status": "not_executed",
                    "evidence_classes": [],
                    "embodied_evidence_bound": False,
                    "claim_boundary": (
                        "Capability inspection only; execution requires composition against "
                        "the installed authority."
                    ),
                    "authority_anchor": "installed_bundle",
                    "validation_reasons": validation_reasons,
                }
            )
            # The hash covers exactly the bytes this command emits in --json mode
            # (this dict, compact and key-sorted) so a consumer can recompute it
            # from the printed output alone; it must be the last key added.
            result["registry_sha256"] = hashlib.sha256(
                json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            if args.as_json:
                _print_json(result, compact=True)
            else:
                print(f"Registry: {registry.registry_id} ({registry_role})")
                if args.operation is not None:
                    providers = sorted(item["provider_id"] for item in result["providers"])
                    operation_id = result["operation"]["operation_id"]
                    print(f"- {operation_id}: {', '.join(providers) or 'no provider'}")
                else:
                    for operation in result["operations"]:
                        providers = sorted(item["provider_id"] for item in operation["providers"])
                        operation_id = operation["operation_id"]
                        print(f"- {operation_id}: {', '.join(providers) or 'no provider'}")
            return 0
        if args.command == "compile":
            from .schema import load_facility_manifest

            if not args.input.exists():
                raise ValueError(f"compile input does not exist: {args.input}")
            composition = None
            saved = _saved_composition(args.input)
            if saved is not None:
                if saved.sources is None:
                    raise ValueError("saved composition has no protected source snapshots")
                from .composition import (
                    authority_hold_reasons,
                    demote_untrusted_admissions,
                    validate_composition_sources,
                )
                from .schema import load_capability_registry

                # The authority anchor is always the packaged/installed bundle --
                # never a CLI-suppliable path. The saved composition's protected
                # sources may subset the installed records and re-pose topology,
                # but every authority-bearing record they carry must be identical
                # to the installed one; anything unknown or modified is a
                # proposal and holds here.
                bundle = facility_bundle(saved.sources.facility.facility.id)
                installed_registry = load_capability_registry(bundle / "registry.yaml")
                installed_facility = load_facility_manifest(bundle / "facility.yaml")
                hold_reasons = authority_hold_reasons(
                    saved.sources.registry,
                    saved.sources.facility,
                    installed_registry,
                    installed_facility,
                )
                demoted_registry, demotion_reasons = demote_untrusted_admissions(
                    saved.sources.registry, installed_registry
                )
                hold_reasons = [*hold_reasons, *demotion_reasons]
                if hold_reasons:
                    _print_json(
                        {
                            "status": "HOLD",
                            "execution_status": "blocked",
                            "evidence_classes": [],
                            "embodied_evidence_bound": False,
                            "claim_boundary": installed_facility.facility.claim_boundary,
                            "authority_anchor": "installed_bundle",
                            "reason_codes": sorted({item.code for item in hold_reasons}),
                            "validation_reasons": [
                                item.model_dump(mode="json", exclude_none=True)
                                for item in hold_reasons
                            ],
                        },
                        compact=args.output is not None,
                    )
                    return 1
                try:
                    composition = validate_composition_sources(
                        saved,
                        saved.sources.requirement,
                        demoted_registry,
                    )
                except ValueError as exc:
                    raise ValueError(
                        f"composition differs from installed authority: {exc}"
                    ) from exc
                if composition.status == "HOLD":
                    payload = composition.model_dump(mode="json", exclude_none=True)
                    payload["execution_status"] = "blocked"
                    payload["evidence_classes"] = []
                    payload["embodied_evidence_bound"] = False
                    payload["claim_boundary"] = installed_facility.facility.claim_boundary
                    payload["authority_anchor"] = "installed_bundle"
                    payload["validation_reasons"] = payload.pop("reasons", [])
                    _print_json(
                        payload,
                        compact=args.output is not None,
                    )
                    return 1
                facility_manifest = saved.sources.facility
                target = args.target or saved.sources.default_target
            else:
                if args.target is None:
                    raise ValueError("manifest compilation requires --target")
                facility_manifest = load_facility_manifest(args.input)
                target = args.target
            result = compile_facility(
                facility_manifest,
                target,
                args.output,
                composition_result=composition,
            )
            executable = result.execution_status == "ready"
            receipt = {
                "status": "passed",
                "execution_status": result.execution_status,
                "target": result.target,
                "output_dir": str(result.output_dir),
                "root_stage": str(result.stage_path),
                "core_ir_sha256": result.core_ir_sha256,
                "adapter_pack_sha256": result.adapter_pack_sha256,
                "world_sha256": result.world_sha256,
                "composition_sha256": composition.composition_sha256 if composition else None,
                "provider_ids": (
                    sorted(
                        {item.provider_id for item in composition.virtual_sdl.operation_bindings}
                    )
                    if composition and composition.virtual_sdl
                    else []
                ),
                "evidence_classes": (
                    sorted(
                        {item.evidence_class for item in composition.virtual_sdl.operation_bindings}
                    )
                    if composition and composition.virtual_sdl
                    else []
                ),
                "embodied_evidence_bound": False,
                "claim_boundary": result.claim_boundary,
                "authority_anchor": result.authority_anchor,
                "validation_reasons": [],
            }
            receipt["next_command"] = (
                f"dynamical run {result.output_dir} -o trace.ndjson"
                if executable
                else f"dynamical validate {result.output_dir} --json"
            )
            _print_json(
                receipt,
                compact=args.output is not None,
            )
            return 0
        if args.command == "compose":
            from .composition import (
                PreflightSkip,
                authority_hold_reasons,
                compose_files,
                demote_untrusted_admissions,
                load_preflight_binding,
                write_composition_result,
            )
            from .schema import (
                CampaignRequirement,
                load_campaign_requirement,
                load_capability_registry,
                load_facility_manifest,
            )

            if args.schema:
                if (
                    args.requirement is not None
                    or args.output is not None
                    or args.preflight
                    or args.no_preflight
                    or args.reason
                ):
                    raise ValueError(
                        "--schema does not accept a requirement, --output, or preflight flags\n"
                        "Example: dynamical compose --schema"
                    )
                print(json.dumps(CampaignRequirement.model_json_schema(), indent=2, sort_keys=True))
                return 0
            if args.requirement is None:
                raise ValueError(
                    "compose requires a campaign requirement path\n"
                    "Example: dynamical compose campaign.yaml --preflight preflight.json "
                    "-o composition.json"
                )
            if not args.requirement.is_file():
                raise ValueError(f"campaign requirement does not exist: {args.requirement}")
            # A wrong document type must surface as such before the preflight gate.
            load_campaign_requirement(args.requirement)
            requirement_arg = shlex.quote(str(args.requirement))
            if args.no_preflight and args.preflight is not None:
                raise ValueError(
                    "--no-preflight cannot be combined with --preflight\n"
                    f"Example: dynamical compose {requirement_arg} --preflight preflight.json "
                    "-o composition.json"
                )
            if args.no_preflight and not args.reason:
                raise ValueError(
                    "--no-preflight requires --reason so the receipt records the omission\n"
                    f"Example: dynamical compose {requirement_arg} --no-preflight "
                    "--reason 'why the starting state is not frozen' -o composition.json"
                )
            if args.reason and not args.no_preflight:
                raise ValueError(
                    "--reason is accepted only with --no-preflight\n"
                    f"Example: dynamical compose {requirement_arg} --preflight preflight.json "
                    "-o composition.json"
                )
            if args.preflight is None and not args.no_preflight:
                # Fail closed: the frozen starting state is the campaign root, so a new
                # campaign cannot compose without it. The Next line is the chain entry.
                raise ValueError(
                    "compose requires a READY preflight receipt for a new campaign\n"
                    f"Example: dynamical compose {requirement_arg} --preflight preflight.json "
                    "-o composition.json\n"
                    f"Next: dynamical preflight mapping.json --requirement {requirement_arg} "
                    "-o preflight.json"
                )
            if args.preflight is not None and args.output is None:
                raise ValueError(
                    "--preflight requires --output so the receipt stays out of agent context\n"
                    "Example: dynamical compose campaign.yaml --preflight preflight.json "
                    "-o composition.json"
                )
            if args.preflight is not None and not args.preflight.is_file():
                raise ValueError(f"preflight receipt does not exist: {args.preflight}")

            # Proposing a candidate --registry is legitimate; granting it authority is
            # not. installed_registry is always the packaged/installed one (never the
            # agent-suppliable path above), so an admitted claim only survives compose
            # when the installed authority independently confirms it -- see
            # demote_untrusted_admissions. That demotion is computed a second time
            # here, purely for display: compose_files bakes it into the composed
            # registry so the saved result stays exactly reproducible from its own
            # protected sources, which leaves no room in that result to also explain
            # *why* a provider was demoted -- so this CLI-only receipt field is the
            # one place PROVIDER_SELF_ADMITTED is legible to the agent.
            supplied_facility = load_facility_manifest(args.facility)
            bundle = facility_bundle(supplied_facility.facility.id)
            installed_registry = load_capability_registry(bundle / "registry.yaml")
            installed_facility = load_facility_manifest(bundle / "facility.yaml")
            supplied_registry = load_capability_registry(args.registry)
            # Modified or unknown authority-bearing records are proposals, not
            # authorities: they hold here with typed reasons before anything is
            # composed against them. Provider admission claims are handled by
            # demotion below instead, so a proposal registry can still compose
            # through the providers the installed authority confirms.
            authority_reasons = authority_hold_reasons(
                supplied_registry,
                load_facility_manifest(args.facility),
                installed_registry,
                installed_facility,
            )
            if authority_reasons:
                _print_json(
                    {
                        "status": "HOLD",
                        "execution_status": "blocked",
                        "evidence_classes": [],
                        "embodied_evidence_bound": False,
                        "claim_boundary": installed_facility.facility.claim_boundary,
                        "authority_anchor": "installed_bundle",
                        "reason_codes": sorted({item.code for item in authority_reasons}),
                        "validation_reasons": [
                            item.model_dump(mode="json", exclude_none=True)
                            for item in authority_reasons
                        ],
                    },
                    compact=args.output is not None,
                )
                return 1
            _, self_admission_reasons = demote_untrusted_admissions(
                supplied_registry, installed_registry
            )
            preflight_binding = (
                load_preflight_binding(
                    args.preflight,
                    args.requirement,
                    args.registry,
                    args.facility,
                )
                if args.preflight is not None
                else None
            )
            result = compose_files(
                args.requirement,
                args.registry,
                args.facility,
                installed_registry=installed_registry,
                preflight_binding=preflight_binding,
                preflight_skip=PreflightSkip(reason=args.reason) if args.no_preflight else None,
            )
            routing_hint = {}
            if result.status == "HOLD":
                alias = next(name for name, root in ALIASES.items() if root == bundle)
                routing_hint = {
                    "facility_id": installed_facility.facility.id,
                    "installed_facilities": list(ALIASES),
                    "next_command": f"dynamical capabilities --facility {alias}",
                }
                suggested = requirement_facility(args.requirement).parent
                if suggested != bundle and args.registry == bundle / "registry.yaml":
                    suggested_alias = next(
                        name for name, root in ALIASES.items() if root == suggested
                    )
                    if args.preflight is None:
                        # No receipt in play: re-compose against the declared facility,
                        # carrying the waiver that let this compose run at all.
                        command = [
                            "dynamical",
                            "compose",
                            str(args.requirement),
                            "--facility",
                            suggested_alias,
                        ]
                        if args.output is not None:
                            command.extend(["-o", str(args.output)])
                        if args.no_preflight:
                            command.extend(["--no-preflight", "--reason", args.reason])
                        routing_hint["next_command"] = shlex.join(command)
                    else:
                        rebind = _preflight_rebind_command(
                            args.preflight, args.requirement, suggested_alias
                        )
                        if rebind is not None:
                            # The receipt is bound to the wrong facility's records, so
                            # reusing it cannot compose; freeze the same map against the
                            # declared facility instead.
                            routing_hint["next_command"] = rebind
            untrusted_admissions = [
                item.model_dump(mode="json", exclude_none=True) for item in self_admission_reasons
            ]
            if args.output is not None:
                write_composition_result(args.output, result)
                receipt = {
                    "status": result.status,
                    "execution_status": ("passed" if result.status == "COMPILED" else "blocked"),
                    "output": str(args.output),
                    "composition_sha256": result.composition_sha256,
                    "resolution_sha256": result.resolution_sha256,
                    "reason_codes": result.reason_codes,
                    "validation_reasons": [
                        item.model_dump(mode="json", exclude_none=True) for item in result.reasons
                    ],
                    "provider_ids": (
                        sorted({item.provider_id for item in result.virtual_sdl.operation_bindings})
                        if result.virtual_sdl
                        else []
                    ),
                    "evidence_classes": (
                        sorted(
                            {item.evidence_class for item in result.virtual_sdl.operation_bindings}
                        )
                        if result.virtual_sdl
                        else []
                    ),
                    "embodied_evidence_bound": False,
                    "claim_boundary": installed_facility.facility.claim_boundary,
                    "authority_anchor": "installed_bundle",
                }
                receipt.update(routing_hint)
                if untrusted_admissions:
                    receipt["untrusted_admissions"] = untrusted_admissions
                if result.sources is not None and result.sources.requirement.prospective_ref:
                    # the agent's prospective record for this arm, repeated so the
                    # receipt joins to the prediction made before composing
                    receipt["prospective_ref"] = (
                        result.sources.requirement.prospective_ref.model_dump(mode="json")
                    )
                if preflight_binding is not None:
                    receipt["preflight"] = {
                        "receipt_sha256": preflight_binding.receipt_sha256,
                        "state_id": preflight_binding.state_id,
                        "state_sha256": preflight_binding.state_sha256,
                        "evidence_cutoff": preflight_binding.evidence_cutoff,
                    }
                if args.no_preflight:
                    receipt["preflight_skipped"] = {"reason": args.reason}
                if result.status == "COMPILED":
                    receipt["next_command"] = f"dynamical compile {args.output} -o compiled-world"
                _print_json(receipt, compact=True)
            else:
                payload = result.model_dump(mode="json", exclude_none=True)
                payload["execution_status"] = "passed" if result.status == "COMPILED" else "blocked"
                payload["evidence_classes"] = (
                    sorted({item.evidence_class for item in result.virtual_sdl.operation_bindings})
                    if result.virtual_sdl
                    else []
                )
                payload["embodied_evidence_bound"] = False
                payload["claim_boundary"] = installed_facility.facility.claim_boundary
                payload["authority_anchor"] = "installed_bundle"
                payload["validation_reasons"] = payload.pop("reasons", [])
                payload.update(routing_hint)
                if untrusted_admissions:
                    payload["untrusted_admissions"] = untrusted_admissions
                if args.no_preflight:
                    payload["preflight_skipped"] = {"reason": args.reason}
                _print_json(payload)
            return 0 if result.status == "COMPILED" else 1
        if args.command == "run":
            from .campaign import run_cli

            restore_values = (args.restore_from, args.restore_world, args.restore_at_event)
            if any(value is not None for value in restore_values) and not all(
                value is not None for value in restore_values
            ):
                raise ValueError(
                    f"the three restore flags must appear together\nExample: {RESTORE_EXAMPLE}"
                )
            if args.dry_run and not all(value is not None for value in restore_values):
                raise ValueError(
                    f"--dry-run requires all three restore flags\nExample: {RESTORE_EXAMPLE}"
                )
            if all(value is not None for value in restore_values) and args.mode != "simulate":
                raise ValueError(f"restore requires --mode simulate\nExample: {RESTORE_EXAMPLE}")
            if (
                all(value is not None for value in restore_values)
                and not args.dry_run
                and not args.output
            ):
                raise ValueError(f"executed restore requires -o\nExample: {RESTORE_EXAMPLE}")
            if not args.input.exists():
                raise ValueError(f"run input does not exist: {args.input}")
            return int(run_cli(args))
        if args.command == "validate":
            if not args.path.exists():
                raise ValueError(f"validation input does not exist: {args.path}")
            branch_worlds = (args.compiled_world, args.child_world)
            branching = any(value is not None for value in branch_worlds)
            if branching and not all(value is not None for value in branch_worlds):
                raise ValueError(
                    "--compiled-world and --child-world must appear together\n"
                    f"Example: {BRANCH_EXAMPLE}"
                )
            report = validate_path(args.path)
            report.setdefault("execution_status", "passed" if report.get("valid") else "failed")
            report.setdefault("evidence_classes", [])
            report.setdefault("embodied_evidence_bound", False)
            report.setdefault(
                "claim_boundary",
                "Artifact contract validation only; no new scientific or physical evidence.",
            )
            report.setdefault("authority_anchor", "installed_bundle")
            report.setdefault("validation_reasons", report.get("failures", []))
            if report.get("valid"):
                # The chain exit: a validated artifact names what it can feed next.
                kind = report.get("kind")
                path_arg = str(args.path)
                if kind == "campaign" and report.get("mode") == "simulate":
                    # Derived from the source stem so the command can never name its
                    # own source as the output; replay refuses that alias regardless.
                    replay_output = args.path.with_name(f"{args.path.stem}.replay.ndjson")
                    report["next_command"] = shlex.join(
                        ["dynamical", "run", path_arg, "--mode", "replay", "-o", str(replay_output)]
                    )
                    if branching:
                        report["branch_command"] = _branch_command(
                            args.path, args.compiled_world, args.child_world, report
                        )
                elif branching:
                    raise ValueError(
                        "--compiled-world and --child-world apply to a valid simulate trace\n"
                        f"Example: {BRANCH_EXAMPLE}"
                    )
                elif kind == "composition_result" and report.get("status") == "COMPILED":
                    report["next_command"] = shlex.join(
                        ["dynamical", "compile", path_arg, "-o", "compiled-world"]
                    )
                elif kind == "compiled_world" and report.get("execution_status") == "ready":
                    report["next_command"] = shlex.join(
                        ["dynamical", "run", path_arg, "-o", "trace.ndjson"]
                    )
            if args.as_json:
                print(json.dumps(report, indent=2, sort_keys=True))
            elif report.get("valid"):
                summary = " ".join(
                    f"{key}={report[key]}"
                    for key in ("kind", "status", "execution_status")
                    if key in report
                )
                print(f"VALID: {args.path} [{summary}]")
                if "next_command" in report:
                    print(f"Next: {report['next_command']}")
                if "branch_command" in report:
                    print(f"Branch: {report['branch_command']}")
            else:
                print(f"FAILED: {args.path}")
                for failure in report.get("failures", []):
                    print(f"- {failure}")
            return 0 if report.get("valid") else 1
    except (FileNotFoundError, ValueError, RuntimeError, ValidationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
