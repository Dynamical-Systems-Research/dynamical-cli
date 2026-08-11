"""Every vendored artifact must match its lock entry, and OCCT must not ship."""

import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

import yaml
from test_electrodeposition_registry import _coverage_requirement

from dynamical.sources import AssetSource

LOCK = Path("registries/electrodeposition-source-lock.json")


def test_every_locked_artifact_matches_its_digest():
    records = json.loads(LOCK.read_text(encoding="utf-8"))["sources"]
    assert records, "the source lock must not be empty"
    for record in records:
        source = AssetSource(**record)
        path = Path(source.id)
        assert path.is_file(), f"{source.id} is declared but absent"
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == source.sha256, f"{source.id}: declared {source.sha256}, got {actual}"


def test_derived_layers_declare_their_conversion():
    records = json.loads(LOCK.read_text(encoding="utf-8"))["sources"]
    derived = [AssetSource(**r) for r in records if r.get("derived_from_source_id")]
    assert derived, "at least one derived USD layer must be locked"
    for source in derived:
        assert source.conversion_tool and source.conversion_tolerance


def test_wheel_excludes_build_tooling(tmp_path):
    """Build a real wheel and inspect its contents.

    OCCT (LGPL-2.1, via cascadio) and OpenUSD are conda-forge, build-time-only
    dependencies of tools/cad_to_usd.py. Checking an already-installed package
    directory would not catch a packaging regression; this builds the wheel
    hatchling would actually publish and inspects its member list directly.
    """
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = list(tmp_path.glob("*.whl"))
    assert wheels, "uv build did not produce a wheel"
    with zipfile.ZipFile(wheels[0]) as archive:
        names = archive.namelist()
    assert names, "wheel is empty"
    offending = [n for n in names if "tools" in Path(n).parts]
    assert not offending, f"wheel must not ship build tooling, found: {offending}"


def test_compile_resolves_admitted_assets_from_a_clean_wheel_install(tmp_path):
    """A live agent hit this on a real, non-editable install: compile_facility resolved
    admitted asset sources against ``Path(__file__).resolve().parents[2]``, which is only
    the repository root from a repo checkout. In an installed wheel that path lands
    somewhere under site-packages, so every admitted asset silently failed to admit and
    ``compile`` failed outright on the AC electrodeposition manifest -- for every asset.

    Reproducing this requires an actual installed wheel run from a directory that is not
    this repo; nothing short of that exercises the packaged-install code path the bug
    lived in. Build the wheel hatchling would publish, install it into a throwaway venv,
    and drive the real CLI (``compose`` then ``compile``) on a multi-instrument
    coverage campaign from an unrelated cwd.
    """
    dist_dir = tmp_path / "dist"
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(dist_dir)],
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = list(dist_dir.glob("*.whl"))
    assert wheels, "uv build did not produce a wheel"

    venv_dir = tmp_path / "cleanenv"
    subprocess.run(["uv", "venv", str(venv_dir)], check=True, capture_output=True, text=True)
    subprocess.run(
        ["uv", "pip", "install", "--python", str(venv_dir / "bin" / "python"), str(wheels[0])],
        check=True,
        capture_output=True,
        text=True,
    )

    # Deliberately outside the repo checkout: the bug this guards against only reproduces
    # when there is no repository tree anywhere for Path(__file__).resolve().parents[2] to
    # (accidentally) land inside of.
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    requirement_path = run_dir / "coverage-requirement.yaml"
    requirement = _coverage_requirement().model_dump(mode="json", exclude_none=True)
    requirement_path.write_text(yaml.safe_dump(requirement, sort_keys=False), encoding="utf-8")

    dynamical = venv_dir / "bin" / "dynamical"
    compose = subprocess.run(
        [str(dynamical), "compose", "coverage-requirement.yaml", "-o", "composition.json"],
        cwd=run_dir,
        capture_output=True,
        text=True,
    )
    assert compose.returncode == 0, compose.stderr

    compile_result = subprocess.run(
        [str(dynamical), "compile", "composition.json", "-o", "compiled-world"],
        cwd=run_dir,
        capture_output=True,
        text=True,
    )
    assert compile_result.returncode == 0, compile_result.stderr
    receipt = json.loads(compile_result.stdout)
    assert receipt["status"] == "passed"

    staged_assets_dir = run_dir / "compiled-world" / "assets"
    assert staged_assets_dir.is_dir()
    staged_names = {path.name for path in staged_assets_dir.iterdir()}
    assert "vial-rack-v3.usdc" in staged_names, staged_names
    assert not (staged_assets_dir / "usd").exists(), "must stage flat, not nested assets/usd/"

    source_admission = json.loads(
        (run_dir / "compiled-world" / "source_admission.json").read_text(encoding="utf-8")
    )
    assert source_admission["admitted"], "the clean install must admit real assets, not none"
