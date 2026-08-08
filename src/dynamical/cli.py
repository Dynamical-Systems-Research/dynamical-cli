"""Command-line interface for compilation, simulation, replay, and validation."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path

from pydantic import ValidationError

from .compiler import compile_facility, validate_path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PACKAGED_REGISTRY = Path(str(files("dynamical").joinpath("data/reference-capabilities.yaml")))
PACKAGED_FACILITY = Path(str(files("dynamical").joinpath("data/matterix-heater-workstation.yaml")))
DEFAULT_REGISTRY = (
    PACKAGED_REGISTRY
    if PACKAGED_REGISTRY.is_file()
    else REPOSITORY_ROOT / "registries" / "reference-capabilities.yaml"
)
DEFAULT_FACILITY = (
    PACKAGED_FACILITY
    if PACKAGED_FACILITY.is_file()
    else REPOSITORY_ROOT / "manifests" / "matterix-heater-workstation.yaml"
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
    parser.add_argument("--version", action="version", version="dynamical 0.1.0")
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

    compile_parser = commands.add_parser(
        "compile",
        help="compile a manifest or self-contained composition",
        epilog="Example: dynamical compile composition.json -o compiled-world",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    compile_parser.add_argument("input", type=Path)
    compile_parser.add_argument("--target", choices=("matterix", "isaac", "openusd"))
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

    run_parser = commands.add_parser(
        "run",
        help="simulate a compiled world or replay a trace",
        epilog=(
            "Example: dynamical run compiled-world -o trace.ndjson\n\n"
            "Scientific values are in observation events under observation.channels. "
            "Validate the completed trace before using them as evidence."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    run_parser.add_argument("input", type=Path)
    run_parser.add_argument("--mode", default="simulate", choices=("simulate", "replay"))
    run_parser.add_argument("-o", "--output", type=Path)
    run_parser.add_argument("--seed", type=int, default=0)
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
            "  dynamical validate decision.json --json"
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
            from .schema import canonical_sha256, load_capability_registry

            registry = load_capability_registry(DEFAULT_REGISTRY)
            registry_identity_payload = registry.model_dump(mode="json")
            registry_payload = registry.model_dump(mode="json", exclude_none=True)
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
                    "registry_sha256": canonical_sha256(registry_identity_payload),
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
                    providers_by_operation.setdefault(provider["operation_id"], []).append(
                        {
                            "provider_id": provider["provider_id"],
                            "evidence_class": provider["evidence_class"],
                            "admission": provider["admission"]["status"],
                            "available": provider["availability"]["available"],
                        }
                    )
                result = {
                    "schema_version": "dynamical.capability-index.v1",
                    "registry_id": registry.registry_id,
                    "registry_sha256": canonical_sha256(registry_identity_payload),
                    "operations": [
                        {
                            "operation_id": capability["operation_id"],
                            "kind": capability["kind"],
                            "providers": providers_by_operation.get(capability["operation_id"], []),
                        }
                        for capability in registry_payload["capabilities"]
                    ],
                }
            if args.as_json:
                _print_json(result, compact=True)
            else:
                print(f"Registry: {registry.registry_id}")
                for capability in sorted(registry.capabilities, key=lambda item: item.operation_id):
                    providers = sorted(
                        provider.provider_id
                        for provider in registry.providers
                        if provider.operation_id == capability.operation_id
                    )
                    print(f"- {capability.operation_id}: {', '.join(providers) or 'no provider'}")
            return 0
        if args.command == "compile":
            if not args.input.exists():
                raise ValueError(f"compile input does not exist: {args.input}")
            composition = None
            saved = _saved_composition(args.input)
            if saved is not None:
                if saved.sources is None:
                    raise ValueError("saved composition has no protected source snapshots")
                from .composition import validate_composition_sources
                from .schema import load_capability_registry, load_facility_manifest

                authority_registry = load_capability_registry(DEFAULT_REGISTRY)
                authority_facility = load_facility_manifest(DEFAULT_FACILITY)
                try:
                    composition = validate_composition_sources(
                        saved,
                        saved.sources.requirement,
                        authority_registry,
                    )
                except ValueError as exc:
                    raise ValueError(
                        f"composition differs from installed authority: {exc}"
                    ) from exc
                if saved.sources.registry != authority_registry:
                    raise ValueError("composition registry differs from installed authority")
                if saved.sources.facility != authority_facility:
                    raise ValueError("composition facility differs from installed authority")
                if composition.status == "HOLD":
                    _print_json(
                        composition.model_dump(mode="json", exclude_none=True),
                        compact=args.output is not None,
                    )
                    return 1
                facility_manifest = authority_facility
                target = args.target or saved.sources.default_target
            else:
                if args.target is None:
                    raise ValueError("manifest compilation requires --target")
                facility_manifest = args.input
                target = args.target
            result = compile_facility(
                facility_manifest,
                target,
                args.output,
                composition_result=composition,
            )
            receipt = {
                "status": "passed",
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
                "next_command": f"dynamical run {result.output_dir} -o trace.ndjson",
            }
            _print_json(
                receipt,
                compact=args.output is not None,
            )
            return 0
        if args.command == "compose":
            from .composition import compose_files, write_composition_result
            from .schema import CampaignRequirement

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

            result = compose_files(args.requirement, DEFAULT_REGISTRY, DEFAULT_FACILITY)
            if args.output is not None:
                write_composition_result(args.output, result)
                receipt = {
                    "status": result.status,
                    "output": str(args.output),
                    "composition_sha256": result.composition_sha256,
                    "resolution_sha256": result.resolution_sha256,
                    "reason_codes": result.reason_codes,
                    "reasons": [
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
                }
                if result.status == "COMPILED":
                    receipt["next_command"] = f"dynamical compile {args.output} -o compiled-world"
                _print_json(receipt, compact=True)
            else:
                _print_json(result.model_dump(mode="json", exclude_none=True))
            return 0 if result.status == "COMPILED" else 1
        if args.command == "run":
            from .campaign import run_cli

            if not args.input.exists():
                raise ValueError(f"run input does not exist: {args.input}")
            return int(run_cli(args))
        if args.command == "validate":
            if not args.path.exists():
                raise ValueError(f"validation input does not exist: {args.path}")
            report = validate_path(args.path)
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
