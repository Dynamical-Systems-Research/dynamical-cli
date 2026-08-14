"""The emitted stage must compose in a real USD runtime, not merely contain the right text."""

import functools
import json
import os
import subprocess
from pathlib import Path

import pytest

from dynamical.compiler import _staged_asset_basenames, compile_facility, validate_compiled_world
from dynamical.schema import (
    AdapterBinding,
    Asset,
    Device,
    Facility,
    FacilityCapability,
    FacilityDocument,
    GeometrySpec,
    OpenUsdAdapterConfig,
    Pose,
    Vec3,
    Workstation,
    load_facility_manifest,
    safe_usd_identifier,
)
from dynamical.sources import AssetSource

ISAAC = Path(
    os.environ.get("ISAAC_SIM_ROOT", "/home/jarrodbarnes/.local/share/dynamical/isaac-sim-6.0.1")
)
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_LOCK_PATH = REPOSITORY_ROOT / "dynamical" / "bundle" / "source-lock.json"
EXACT_MESH_SOURCE_ID = "assets/vial-rack-v3.usdc"
EXACT_MESH_BASENAME = "vial-rack-v3.usdc"

_PARSE_SCRIPT = """
import json, sys
from pxr import Usd
stage = Usd.Stage.Open(sys.argv[1])
json.dump([p.GetPath().pathString for p in stage.Traverse()], open(sys.argv[2], "w"))
"""


class AmbiguousUsdRuntime(RuntimeError):
    """More than one candidate USD runtime component was found on disk.

    Picking one silently (e.g. via sorted(matches)[0]) would let the gate
    validate against a USD parser that is not necessarily the one driving Kit
    in production -- most plausibly a stale extscache entry left behind by a
    point upgrade. A gate that silently tests the wrong parser is worse than
    no gate, so this refuses to guess.
    """


_BOUNDS_SCRIPT = """
import json, sys
from pxr import Usd, UsdGeom
stage = Usd.Stage.Open(sys.argv[1])
cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default"])
out = {"meters_per_unit": UsdGeom.GetStageMetersPerUnit(stage), "extents": {}}
for prim in stage.Traverse():
    path = prim.GetPath().pathString
    if not path.endswith("/Geometry"):
        continue
    rng = cache.ComputeWorldBound(prim).GetRange()
    if rng.IsEmpty():
        continue
    size = rng.GetMax() - rng.GetMin()
    out["extents"][path] = [size[0], size[1], size[2]]
json.dump(out, open(sys.argv[2], "w"))
"""


@functools.lru_cache(maxsize=1)
def _isaac_usd_env() -> dict[str, str] | None:
    """Build the environment Isaac's bin/python needs to import pxr without booting Kit.

    Kit does not put pxr on the base interpreter's sys.path: it lives inside a
    versioned omni.usd.libs extension bundle under extscache, and its compiled
    modules need that bundle's bin/ (libusd_tf.so) plus the embedded CPython's
    lib/ on LD_LIBRARY_PATH. Returns None if no such
    bundle is present at all, so the gate skips cleanly. Raises
    AmbiguousUsdRuntime if more than one candidate bundle or libpython is
    found -- see that class's docstring.

    Cached: this is called once by the module-level skipif marker at
    collection time and again inside parse_stage_prims, and resolution shells
    out to Isaac's interpreter, so recomputing it on every call is wasteful.
    """
    if not (ISAAC / "bin" / "python").is_file():
        return None
    python_version = subprocess.run(
        [
            str(ISAAC / "bin" / "python"),
            "-c",
            "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    extscache = (
        ISAAC / "lib" / f"python{python_version}" / "site-packages" / "isaacsim" / "extscache"
    )
    usd_libs_matches = sorted(extscache.glob("omni.usd.libs-*"))
    if not usd_libs_matches:
        return None
    if len(usd_libs_matches) > 1:
        raise AmbiguousUsdRuntime(
            f"found {len(usd_libs_matches)} omni.usd.libs bundles under {extscache}, "
            f"refusing to guess which one Kit uses: {[str(p) for p in usd_libs_matches]}"
        )
    usd_libs = usd_libs_matches[0]

    python_home = Path(
        subprocess.run(
            [str(ISAAC / "bin" / "python"), "-c", "import sys; print(sys.base_prefix)"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    libpython_dir = python_home / "lib"
    libpython_matches = sorted(libpython_dir.glob("libpython3.*.so.1.0"))
    if not libpython_matches:
        return None
    if len(libpython_matches) > 1:
        raise AmbiguousUsdRuntime(
            f"found {len(libpython_matches)} libpython3.*.so.1.0 candidates under "
            f"{libpython_dir}, refusing to guess which one Isaac's interpreter links: "
            f"{[str(p) for p in libpython_matches]}"
        )
    libpython = libpython_matches[0]

    env = dict(os.environ)
    env["PYTHONPATH"] = str(usd_libs)
    env["LD_LIBRARY_PATH"] = f"{usd_libs / 'bin'}:{libpython.parent}"
    # Not consumed by the subprocess; carried on env so failure/skip messages
    # built from this dict can name the exact bundle a run validated against.
    env["DYNAMICAL_TEST_USD_BUNDLE"] = str(usd_libs)
    return env


def parse_stage_prims(stage_dir: Path, tmp_path: Path) -> list[str]:
    """Open root.usda in a real USD runtime and return every composed prim path."""
    env = _isaac_usd_env()
    assert env is not None, "could not locate Isaac's pxr extension bundle"
    script = tmp_path / "_parse.py"
    script.write_text(_PARSE_SCRIPT, encoding="utf-8")
    out = tmp_path / "_prims.json"
    subprocess.run(
        [str(ISAAC / "bin" / "python"), str(script), str(stage_dir / "root.usda"), str(out)],
        check=True,
        capture_output=True,
        env=env,
    )
    return json.loads(out.read_text(encoding="utf-8"))


requires_usd = pytest.mark.skipif(_isaac_usd_env() is None, reason="no local USD runtime")


@requires_usd
def test_compiled_stage_composes_every_facility_prim(tmp_path):
    document = load_facility_manifest("dynamical/bundle/facility.yaml")
    world = tmp_path / "world"
    compile_facility(document, "openusd", world)

    prims = parse_stage_prims(world, tmp_path)
    facility = [p for p in prims if p.startswith("/Facility")]

    expected_assets = len(document.assets)
    asset_prims = [p for p in facility if "/Assets/" in p and p.endswith("/Geometry")]
    bundle = (_isaac_usd_env() or {}).get("DYNAMICAL_TEST_USD_BUNDLE", "<unknown>")
    assert len(asset_prims) == expected_assets, (
        f"stage composed {len(asset_prims)} asset prims, manifest declares {expected_assets}; "
        f"a sublayer was silently skipped. facility prims: {facility}. "
        f"validated against USD bundle: {bundle}"
    )


def _admitted_source(source_id: str) -> AssetSource:
    """Load one real, already-vendored AssetSource record from the source lock."""
    lock = json.loads(SOURCE_LOCK_PATH.read_text(encoding="utf-8"))
    for entry in lock["sources"]:
        if entry["id"] == source_id:
            return AssetSource.model_validate(entry)
    raise AssertionError(f"{source_id} is absent from {SOURCE_LOCK_PATH}")


@pytest.fixture
def electrodeposition_document() -> FacilityDocument:
    """One exact-source-geometry asset and one labelled proxy, in the same facility.

    Uses a real, already-vendored derived layer (Task 5) and its real AssetSource
    record so admission and the reference arc are exercised against genuine
    on-disk artifacts, not synthetic fixtures.
    """
    vial_rack_source = _admitted_source(EXACT_MESH_SOURCE_ID)
    return FacilityDocument(
        facility=Facility(
            id="ed-reference-arc-facility",
            name="Electrodeposition reference-arc test facility",
            workstation_ids=["ed-reference-arc-workstation"],
            authoring_basis="exact",
            claim_boundary=["Test fixture only; not a claim about a real facility."],
        ),
        workstations=[
            Workstation(
                id="ed-reference-arc-workstation",
                facility_id="ed-reference-arc-facility",
                member_asset_ids=["vial-rack", "proxy-tool"],
            )
        ],
        assets=[
            Asset(
                id="vial-rack",
                workstation_id="ed-reference-arc-workstation",
                asset_kind="vial rack",
                geometry=GeometrySpec(
                    representation="exact_source_geometry",
                    dimensions_m=Vec3(x=0.127, y=0.086, z=0.045),
                    mesh_source_id=EXACT_MESH_SOURCE_ID,
                ),
                pose=Pose(),
            ),
            Asset(
                id="proxy-tool",
                workstation_id="ed-reference-arc-workstation",
                asset_kind="execution proxy tool",
                geometry=GeometrySpec(
                    representation="execution_visualization_primitive",
                    dimensions_m=Vec3(x=0.05, y=0.05, z=0.05),
                    portable_primitive="cube",
                ),
                pose=Pose(),
            ),
        ],
        capabilities=[
            FacilityCapability(
                id="ed-observe-vial-rack",
                provider_id="ed-observation-device",
                action_type="observe",
            )
        ],
        devices=[
            Device(
                id="ed-observation-device",
                asset_id="vial-rack",
                capability_ids=["ed-observe-vial-rack"],
            )
        ],
        adapter_bindings=[
            AdapterBinding(
                id="ed-openusd-adapter",
                target="openusd",
                subject_id="ed-reference-arc-facility",
                adapter_id="dynamical-openusd",
                adapter_version="0.1.0",
                binding_schema_ref="dynamical.openusd-target.v1",
                configuration=OpenUsdAdapterConfig(),
            )
        ],
        asset_sources=[vial_rack_source],
    )


def test_exact_geometry_stamps_source_provenance_in_the_stage(tmp_path, electrodeposition_document):
    world = tmp_path / "world"
    compile_facility(electrodeposition_document, "openusd", world)
    semantics = (world / "semantics.usda").read_text(encoding="utf-8")
    source = _admitted_source(EXACT_MESH_SOURCE_ID)
    assert f'dynamical:sourceSha256 = "{source.sha256}"' in semantics
    assert 'dynamical:licenseId = "zenodo-record-metadata"' in semantics


def test_admitted_derived_layer_is_staged_as_a_declared_artifact(
    tmp_path, electrodeposition_document
):
    world = tmp_path / "world"
    result = compile_facility(electrodeposition_document, "openusd", world)
    staged_layer = world / "assets" / EXACT_MESH_BASENAME
    assert staged_layer.is_file()
    assert not staged_layer.is_symlink()
    # flattened: no nested assets/assets/... -- the source id's own directory
    # namespace must not survive into the compiled world.
    assert not (world / "assets" / "usd").exists()

    manifest = json.loads((world / "compile_manifest.json").read_text(encoding="utf-8"))
    declared_paths = {artifact["path"] for artifact in manifest["artifacts"]}
    assert f"assets/{EXACT_MESH_BASENAME}" in declared_paths

    layout = (world / "layout.usda").read_text(encoding="utf-8")
    assert f"prepend references = @./assets/{EXACT_MESH_BASENAME}@</Root>" in layout

    validated = validate_compiled_world(result.output_dir)
    assert validated["valid"] is True, validated


def test_asset_source_admission_resolves_regardless_of_process_cwd(
    tmp_path, electrodeposition_document, monkeypatch
):
    """Known gap from Task 4: ``admit_sources`` used to resolve against ``Path(".")``.

    That happened to work only because tests run with cwd already at the repo
    root. Prove the fix by compiling from an unrelated cwd.
    """
    world = tmp_path / "world"
    monkeypatch.chdir(tmp_path)
    compile_facility(electrodeposition_document, "openusd", world)
    assert (world / "assets" / EXACT_MESH_BASENAME).is_file()


def test_colliding_staged_basenames_are_refused_by_name(electrodeposition_document):
    """Two admitted derived layers that flatten to the same basename must not silently
    overwrite each other in the compiled world; the compiler must fail closed and name
    both source ids.
    """
    colliding_source = AssetSource(
        id="assets/alt-cartridge/vial-rack-v3.usdc",
        retrieval_uri="https://example.invalid/alt-cartridge/vial-rack-v3.usdc",
        sha256="1" * 64,
        admission="admitted",
        authority_id="test-authority",
        spdx_id="MIT",
        derived_from_source_id="sources/ac-sdl1/alt vial rack v3.step",
        conversion_tool="tools/cad_to_usd.py",
        conversion_tolerance="0.05,0.5",
    )
    original_source = electrodeposition_document.asset_sources[0]
    assert original_source.id == EXACT_MESH_SOURCE_ID

    with pytest.raises(ValueError, match="flatten to the same staged basename") as excinfo:
        _staged_asset_basenames([original_source, colliding_source])
    assert original_source.id in str(excinfo.value)
    assert colliding_source.id in str(excinfo.value)


@requires_usd
def test_referenced_mesh_composes_points_through_the_arc(tmp_path, electrodeposition_document):
    world = tmp_path / "world"
    compile_facility(electrodeposition_document, "openusd", world)
    prims = parse_stage_prims(world, tmp_path)
    assert any("/Assets/" in p and p.endswith("/Geometry") for p in prims)


def stage_world_extents(stage_dir: Path, tmp_path: Path) -> dict:
    """Compose the stage in a real USD runtime and return per-asset world extents."""
    env = _isaac_usd_env()
    assert env is not None, "no local USD runtime"
    script = tmp_path / "_bounds.py"
    script.write_text(_BOUNDS_SCRIPT, encoding="utf-8")
    out = tmp_path / "_bounds.json"
    subprocess.run(
        [str(ISAAC / "bin" / "python"), str(script), str(stage_dir / "root.usda"), str(out)],
        check=True,
        capture_output=True,
        env=env,
    )
    return json.loads(out.read_text(encoding="utf-8"))


@requires_usd
def test_source_geometry_composes_at_laboratory_scale(tmp_path):
    """Referenced geometry must arrive in metres, not millimetre-count metres.

    USD reference composition does not rescale across a differing metersPerUnit in
    the referenced layer, so a layer authored in millimetres composes 1000x too
    large. Prim count cannot see that; only world bounds can.
    """
    document = load_facility_manifest("dynamical/bundle/facility.yaml")
    world = tmp_path / "world"
    compile_facility(document, "openusd", world)

    measured = stage_world_extents(world, tmp_path)
    assert measured["meters_per_unit"] == 1.0
    exact = {
        asset.id
        for asset in document.assets
        if asset.geometry.representation == "exact_source_geometry"
    }
    assert exact, "manifest declares no exact source geometry"

    checked = 0
    for path, size in measured["extents"].items():
        asset_id = path.split("/Assets/")[-1].split("/")[0]
        if not any(asset_id == safe_usd_identifier(name) for name in exact):
            continue
        checked += 1
        longest = max(size)
        assert 0.001 < longest < 1.0, (
            f"{asset_id} composes {longest:.3f} m along its longest axis; "
            "bench hardware between 1 mm and 1 m is expected, so this is a unit error"
        )
    assert checked == len(exact), f"measured {checked} of {len(exact)} exact-geometry assets"
