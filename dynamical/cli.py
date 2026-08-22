"""Command-line interface for compilation, simulation, replay, and validation."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path

from pydantic import ValidationError

from . import __version__ as _VERSION
from .compiler import compile_facility, validate_path

REFERENCE_LAB = Path(str(files("dynamical").joinpath("bundle", "reference-lab")))
DEFAULT_REGISTRY = REFERENCE_LAB / "registry.yaml"
DEFAULT_FACILITY = REFERENCE_LAB / "facility.yaml"
RESTORE_EXAMPLE = (
    "dynamical run child-world --restore-from parent.ndjson --restore-world parent-world "
    "--restore-at-event simulate-abc123:event:000006 -o child.ndjson"
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
        description="Discover, compose, compile, run, and validate facility operations.",
    )
    parser.add_argument("--version", action="version", version=f"dynamical {_VERSION}")
    commands = parser.add_subparsers(dest="command", required=True)

    capabilities_parser = commands.add_parser(
        "capabilities",
        help="list operations or inspect one operation",
        epilog=(
            "Examples:\n"
            "  dynamical capabilities --json\n"
            "  dynamical capabilities --operation <operation-id> --json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    capabilities_parser.add_argument(
        "--operation",
        help="return one operation and its admitted provider candidates",
    )
    capabilities_parser.add_argument("--json", action="store_true", dest="as_json")
    capabilities_parser.add_argument(
        "--registry",
        type=Path,
        default=DEFAULT_REGISTRY,
        help=(
            "registry to inspect; a custom path is a proposal checked against the installed "
            "registry"
        ),
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
        help="select admitted providers for a requirement",
        epilog=(
            "Example: dynamical compose requirement.yaml -o composition.json\n\n"
            "Non-executable multi-step requirement shape. Replace each operation and port "
            "with values from `dynamical capabilities --operation <operation-id> --json`:\n"
            "  document_type: dynamical.campaign-requirement\n"
            "  schema_version: 0.1.0\n"
            "  requirement_id: example-requirement\n"
            "  objective:\n"
            "    id: example-objective\n"
            "    statement: Process an input through two operations.\n"
            "    decision: Decide if the resulting evidence meets the stated rule.\n"
            "    proof_requirements:\n"
            "      - {id: result-proof, operation_id: operation-b, "
            "output_port_ids: [result.value], minimum_evidence_class: simulator, "
            "acceptance_rule: Apply the stated result rule., "
            "independent_verification_required: true}\n"
            "  inputs:\n"
            "    - {id: input.value, state_type: number, unit: '1', value: 1}\n"
            "  steps:\n"
            "    - step_id: step-a\n"
            "      operation_id: operation-a\n"
            "      input_bindings:\n"
            "        - {target_port_id: input.value, source_kind: campaign_input, "
            "source_id: input.value}\n"
            "    - step_id: step-b\n"
            "      operation_id: operation-b\n"
            "      input_bindings:\n"
            "        - {target_port_id: intermediate.value, source_kind: step_output, "
            "source_id: step-a, source_port_id: intermediate.value}\n"
            "      depends_on: [step-a]\n"
            "  max_cost_usd: 0\n"
            "  max_duration_s: 60"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    compose_parser.add_argument("requirement", nargs="?", type=Path)
    compose_parser.add_argument("-o", "--output", type=Path)
    compose_parser.add_argument(
        "--schema",
        action="store_true",
        help="print the campaign requirement JSON Schema and exit",
    )
    compose_parser.add_argument(
        "--registry",
        type=Path,
        default=DEFAULT_REGISTRY,
        help="registry to use; a custom path is a proposal checked against installed authority",
    )
    compose_parser.add_argument(
        "--facility",
        type=Path,
        default=DEFAULT_FACILITY,
        help="facility to use; a custom path is a proposal checked against installed authority",
    )

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
        "--dry-run", action="store_true", help="run restore preflight without child execution"
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
            "  dynamical validate trace.ndjson --json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    validate_parser.add_argument("path", type=Path)
    validate_parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "capabilities":
            from .composition import authority_hold_reasons, demote_untrusted_admissions
            from .schema import load_capability_registry

            installed_registry = load_capability_registry(DEFAULT_REGISTRY)
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
                if args.registry.resolve() == DEFAULT_REGISTRY.resolve()
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
                    raise ValueError(
                        f"unknown operation {args.operation!r}; available operations: {available}\n"
                        "Example: dynamical capabilities --operation <operation-id> --json"
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
                installed_registry = load_capability_registry(DEFAULT_REGISTRY)
                installed_facility = load_facility_manifest(DEFAULT_FACILITY)
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
                authority_hold_reasons,
                compose_files,
                demote_untrusted_admissions,
                write_composition_result,
            )
            from .schema import (
                CampaignRequirement,
                load_capability_registry,
                load_facility_manifest,
            )

            if args.schema:
                if args.requirement is not None or args.output is not None:
                    raise ValueError(
                        "--schema does not accept a requirement or --output\n"
                        "Example: dynamical compose --schema"
                    )
                print(json.dumps(CampaignRequirement.model_json_schema(), indent=2, sort_keys=True))
                return 0
            if args.requirement is None:
                raise ValueError(
                    "compose requires a campaign requirement path\n"
                    "Example: dynamical compose campaign.yaml -o composition.json"
                )
            if not args.requirement.is_file():
                raise ValueError(f"campaign requirement does not exist: {args.requirement}")

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
            installed_registry = load_capability_registry(DEFAULT_REGISTRY)
            installed_facility = load_facility_manifest(DEFAULT_FACILITY)
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
            result = compose_files(
                args.requirement,
                args.registry,
                args.facility,
                installed_registry=installed_registry,
            )
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
                if untrusted_admissions:
                    receipt["untrusted_admissions"] = untrusted_admissions
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
                if untrusted_admissions:
                    payload["untrusted_admissions"] = untrusted_admissions
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
            if args.as_json:
                print(json.dumps(report, indent=2, sort_keys=True))
            elif report.get("valid"):
                print(f"PASSED: {args.path}")
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
