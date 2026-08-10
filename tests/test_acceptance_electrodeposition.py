"""Spec 1 acceptance: design/2026-08-09-electrodeposition-sdl-spec1-design.md section 8.

Every numbered criterion below maps to exactly one test, or to an existing test cited by
its exact id. Criteria already exercised by tests written in Tasks 10-14 are referenced
here rather than duplicated -- writing a second, weaker copy of an existing assertion
would not make the criterion any more true, only harder to keep in sync.

    1.  clean install exposes AC capabilities
        -> test_1_clean_install_exposes_all_ac_operations
    2.  requirement selects admitted providers
        -> test_composition.py::
             test_reference_virtual_sdl_keeps_deterministic_execution_bindings
        -> test_electrodeposition_registry.py::
             test_one_sample_moves_through_three_workstations_by_explicit_transfer
    3.  compiled stage references exact source
        -> test_3_compiled_stage_references_exact_source_geometry_and_labels_the_rest
    4.  source admission fails closed
        -> test_4a_tampered_derived_asset_fails_compilation_closed
        -> test_4b_unlicensed_source_is_refused_by_id
        -> test_source_admission.py::test_tampered_artifact_fails_closed
        -> test_source_admission.py::test_unadmitted_source_is_refused
    5.  Isaac opens the exact compiled stage
        -> test_isaac_backend.py::test_live_run_composes_the_facility_and_writes_a_trace
    6.  one sample, three workstations, all four lineage invariants enforced
        -> test_electrodeposition_registry.py::
             test_one_sample_moves_through_three_workstations_by_explicit_transfer
        -> test_electrodeposition_registry.py::
             test_coverage_campaign_retargeted_transfer_still_fails_lineage (negative control)
    7.  live run produces a trace; replay re-verifies pack/stage/asset/provider/trace hashes
        -> test_isaac_backend.py::
             test_live_kit_run_of_coverage_campaign_has_zero_lineage_findings
    8.  typed failures; non-zero run; invalid validate
        -> test_failure_semantics.py::test_failed_execution_status_makes_validation_invalid
        -> test_failure_semantics.py::test_truncated_trace_fails_step_coverage
    9.  trace carries dataflow graph, requested-vs-applied params, margins, uncertainty,
        envelope, consumed cost
        -> test_telemetry_contract.py (all six tests, one per element)
    10. physical route returns HOLD
        -> test_10_physical_route_returns_hold
    11. no research policy in product code
        -> test_11_no_research_policy_in_product_code

Section 9 (claim boundary) is not a test surface: it bounds what these passing tests are
allowed to be read as proving, and is stated verbatim in README.md.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from dynamical.compiler import compile_facility
from dynamical.schema import load_facility_manifest
from dynamical.sources import staged_asset_basename

REPOSITORY = Path(__file__).resolve().parents[1]
MANIFEST = REPOSITORY / "manifests" / "ac-electrodeposition-cell.yaml"
# The eight operations spanning the seven independent AC capability modules:
# liquid handling (dispense + aliquot), ultrasonic conditioning, sample
# transfer, electrodeposition, cleaning, electrochemical-cell loading, and
# OER measurement.
ALL_OPERATIONS = {
    "aliquot-to-well",
    "clean-electrode",
    "condition-ultrasonic",
    "dispense-electrolyte",
    "electrodeposit-constant-current",
    "load-electrochemical-cell",
    "measure-oer",
    "transfer-sample",
}


def _cli(*args: str, expect: int = 0) -> subprocess.CompletedProcess[str]:
    """Invoke the installed ``dynamical`` console script exactly as an operator or agent
    would -- not ``dynamical.cli.main`` in-process -- because this file's whole job is
    acceptance: proving the shipped entry point behaves, not the library underneath it.
    """
    result = subprocess.run(["dynamical", *args], capture_output=True, text=True, cwd=REPOSITORY)
    assert result.returncode == expect, (
        f"dynamical {' '.join(args)} exited {result.returncode}, expected {expect}\n"
        f"stdout: {result.stdout[-2000:]}\nstderr: {result.stderr[-2000:]}"
    )
    return result


# --- Criterion 1 -------------------------------------------------------------------


def test_1_clean_install_exposes_all_ac_operations() -> None:
    """Design 2.1/8.1: ``dynamical capabilities --json`` must list every AC SDL1
    operation the registry admits -- not merely a subset.

    This exercises the installed console script against the repository's editable
    install, not a freshly built wheel in a throwaway venv. The wheel-install path
    itself (does a clean ``uv tool install`` of a built wheel expose the same
    operations from ``dynamical/data/electrodeposition-capabilities.yaml``) was
    verified manually per the design brief and is not re-automated here; what this
    test pins down is the capability surface content the wheel ships unchanged.
    """
    payload = json.loads(_cli("capabilities", "--json").stdout)
    ids = {op["operation_id"] for op in payload["operations"]}
    assert ids == ALL_OPERATIONS, ids


# --- Criteria 3 and 4 ----------------------------------------------------------------


def test_3_compiled_stage_references_exact_source_geometry_and_labels_the_rest(
    tmp_path: Path,
) -> None:
    """Design 8.3, against the real AC facility manifest (not the synthetic
    reference-arc fixture ``tests/test_openusd_stage.py`` uses): the compiled stage
    must reference every exact-source-derived AC layer by its staged basename, and
    every remaining asset must be the labelled proxy representation -- not merely
    "some assets are exact and some are proxies", but that the two sets partition
    every declared asset in the facility with no third, unlabelled category.

    ``GeometrySpec.representation`` is a two-value ``Literal`` (schema.py), so a
    third representation can never be authored; what this test adds beyond that
    static guarantee is that the *real* manifest's data compiles to a stage whose
    on-disk text actually reflects the split, for the exact assets the facility
    manifest declares today (the OT-2 body/tiprack, the Arduino enclosure, and the
    Squidstat body proxy; the AC cartridges, racks, tools, electrodes and echem
    cell are exact).
    """
    document = load_facility_manifest(MANIFEST)
    exact = [a for a in document.assets if a.geometry.representation == "exact_source_geometry"]
    proxy = [
        a
        for a in document.assets
        if a.geometry.representation == "execution_visualization_primitive"
    ]
    assert exact and proxy, "the facility must exercise both representations"
    assert len(exact) + len(proxy) == len(document.assets)

    world = tmp_path / "world"
    compile_facility(MANIFEST, "openusd", world)
    layout = (world / "layout.usda").read_text(encoding="utf-8")
    semantics = (world / "semantics.usda").read_text(encoding="utf-8")

    for asset in exact:
        assert asset.geometry.mesh_source_id is not None
        basename = staged_asset_basename(asset.geometry.mesh_source_id)
        assert f"prepend references = @./assets/{basename}@</Root>" in layout, asset.id
        assert (world / "assets" / basename).is_file(), asset.id

    assert semantics.count('dynamical:representation = "exact_source_geometry"') == len(exact)
    assert semantics.count('dynamical:representation = "execution_visualization_primitive"') == len(
        proxy
    )


def test_4a_tampered_derived_asset_fails_compilation_closed(tmp_path: Path) -> None:
    """Design 8.4, at the CLI acceptance surface: a single byte appended to an
    admitted derived USD layer on disk must fail the whole compile closed, through
    the exact ``dynamical compile`` invocation an operator would run, not just the
    lower-level ``admit_sources`` unit tests in ``test_source_admission.py``.

    ``dynamical compile`` catches ``SourceAdmissionError`` (a ``ValueError``
    subclass) in its generic error handler and returns 2, the same exit code as
    every other CLI-level input error -- not 1 (reserved for a well-formed ``HOLD``
    composition receipt). Restores the file in a ``finally`` unconditionally, and
    the caller's own end-of-task verification confirms the working tree is clean
    afterward.
    """
    asset = REPOSITORY / "assets" / "usd" / "assembly-well-cartridge-v44.usdc"
    original = asset.read_bytes()
    try:
        asset.write_bytes(original + b"\n# tampered by test_acceptance_electrodeposition\n")
        result = _cli(
            "compile", str(MANIFEST), "--target", "openusd", "-o", str(tmp_path / "world"), expect=2
        )
        assert "digest mismatch" in result.stderr
    finally:
        asset.write_bytes(original)
    assert asset.read_bytes() == original


def test_4b_unlicensed_source_is_refused_by_id() -> None:
    """Design 8.4's other fail-closed axis (a missing/absent licence, not a tampered
    digest): the Opentrons OT-2 and the Squidstat instrument body have no admitted
    source at all -- see design 2.1, "Opentrons/ot2 ... No license, verified three
    ways" -- so the manifest represents them as labelled proxies rather than
    declaring an ``AssetSource`` for them. This is the manifest-level consequence of
    the same fail-closed rule ``test_source_admission.py::test_unadmitted_source_is_refused``
    proves at the ``admit_sources`` unit level: there is no back door where an
    unlicensed body could appear as ``exact_source_geometry`` by omission.
    """
    document = load_facility_manifest(MANIFEST)
    admitted_ids = {source.id for source in document.asset_sources}
    ot2_body = next(a for a in document.assets if a.id == "ot2-robot-body")
    squidstat_body = next(a for a in document.assets if a.id == "squidstat-instrument-body")
    for asset in (ot2_body, squidstat_body):
        assert asset.geometry.representation == "execution_visualization_primitive", asset.id
        assert asset.geometry.mesh_source_id is None, asset.id
        assert asset.geometry.mesh_source_id not in admitted_ids


# --- Criterion 10 --------------------------------------------------------------------


@pytest.fixture
def physical_requirement(tmp_path: Path) -> Path:
    """A multi-instrument coverage campaign projected onto the physical route:
    every step raised to ``minimum_evidence_class: physical`` and tagged
    ``physical-run-required``. No physical provider is admitted for this facility
    by design (see ``test_every_physical_provider_stays_unadmitted``), so
    composing this must HOLD -- the authority gate, not any fixed campaign, is
    what this exercises.
    """
    import test_electrodeposition_registry as coverage

    requirement = coverage._coverage_requirement().model_dump(mode="json", exclude_none=True)
    requirement["requirement_id"] = "ac-electrodeposition-physical-only"
    for step in requirement["steps"]:
        step["minimum_evidence_class"] = "physical"
        step["required_policy_tags"] = ["physical-run-required"]
    requirement["objective"]["proof_requirements"][0]["minimum_evidence_class"] = "physical"
    path = tmp_path / "physical-only.yaml"
    path.write_text(yaml.safe_dump(requirement, sort_keys=False), encoding="utf-8")
    return path


def test_10_physical_route_returns_hold(tmp_path: Path, physical_requirement: Path) -> None:
    result = _cli(
        "compose", str(physical_requirement), "-o", str(tmp_path / "composition.json"), expect=1
    )
    payload = json.loads(result.stdout)
    assert payload["status"] == "HOLD"
    assert payload["reason_codes"]
    assert payload["reasons"]
    assert "next_command" not in payload


# --- Criterion 11 ----------------------------------------------------------------------


def test_11_no_research_policy_in_product_code() -> None:
    """No fixed research policy may live in ``src/dynamical``: not the retired
    thermal campaign's literal fingerprints, not this harness's own synthetic
    coverage campaign, and not any embedded campaign-requirement construct.
    Product code defines contracts and enforces authority; the requirement
    documents that drive research always arrive from outside.

    ``acceptance_rule`` appears as a *quoted dict key* only where code builds
    a requirement document -- schema.py's field declaration does not match
    the token -- so its presence in product code would mean an embedded
    campaign, whatever it happens to be named.
    """
    banned = [
        # Literal fingerprints of the retired thermal policy.
        "309.5473922729492",
        "930.308",
        "reaction_progress",
        "THERMAL_OPERATION_IDS",
        # This harness's own synthetic coverage campaign.
        "ac-module-coverage",
        "sample-harness-01",
        # Any embedded campaign-requirement construct: an acceptance rule only
        # exists inside an objective's proof requirement, and product code
        # never authors one (it forwards compiled contracts, which these
        # quoted-key tokens do not match).
        '"acceptance_rule"',
        "'acceptance_rule'",
    ]
    offenders = []
    for path in sorted((REPOSITORY / "src" / "dynamical").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for token in banned:
            if token in text:
                offenders.append(f"{path.relative_to(REPOSITORY)}: {token!r}")
    assert offenders == []
