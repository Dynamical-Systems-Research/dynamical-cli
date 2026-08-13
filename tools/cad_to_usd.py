"""Convert admitted source CAD into deterministic USD mesh layers.

BUILD-TIME ONLY. This module is never packaged: it pulls OCCT (LGPL-2.1) via
cascadio and OpenUSD via conda-forge, neither of which may enter the wheel.

Output is binary crate (.usdc): a wheel that includes the bundled assets (Task
10) must stay small enough for `uv tool install` to be practical, and ASCII
.usda text ran ~40x larger than the equivalent .usdc for the same geometry.

The default tolerance (0.05, 0.5) is chosen for execution visualization and
collision, not metrology -- see dynamical/bundle/source-lock.json
for the measured tradeoff. Bounding-box dimensions are identical across every
tolerance tested; only surface tessellation density changes.

Run with the conda env built in the plan's Task 5 Step 1:
    .cadenv/bin/python tools/cad_to_usd.py <input> <output.usdc> --tolerance 0.05,0.5
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import cascadio
import trimesh
from pxr import Sdf, Usd, UsdGeom

M_PER_MM = 0.001


def _load_mesh(source: Path, linear: float, angular: float) -> trimesh.Trimesh:
    """Return the mesh in METRES, whatever the source format's own convention.

    The compiled root stage declares ``metersPerUnit = 1``, and USD reference
    composition does NOT rescale geometry across a differing ``metersPerUnit`` in
    the referenced layer. So a layer authored in millimetres composes as
    millimetre-count *metres* -- a 127.7 mm rack arrives 127.7 m wide. Every
    derived layer is therefore authored in metres to match the stage it is
    referenced into.
    """
    if source.suffix.lower() in {".step", ".stp"}:
        glb = source.with_suffix(".glb")
        cascadio.step_to_glb(str(source), str(glb), linear, angular)
        scene = trimesh.load(glb, force="scene")
        mesh = trimesh.util.concatenate(tuple(scene.geometry.values()))
        glb.unlink()
        # cascadio already emits metres regardless of the STEP file's own units.
        return mesh
    # STL carries no units; these sources are numerically millimetres.
    mesh = trimesh.load(source, force="mesh")
    mesh.apply_scale(M_PER_MM)
    return mesh


def convert(source: Path, target: Path, linear: float, angular: float) -> str:
    mesh = _load_mesh(source, linear, angular)
    stage = Usd.Stage.CreateNew(str(target))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    prim = UsdGeom.Mesh.Define(stage, Sdf.Path("/Root"))
    prim.CreatePointsAttr([tuple(float(v) for v in p) for p in mesh.vertices])
    prim.CreateFaceVertexCountsAttr([3] * len(mesh.faces))
    prim.CreateFaceVertexIndicesAttr([int(i) for f in mesh.faces for i in f])
    stage.SetDefaultPrim(prim.GetPrim())
    stage.GetRootLayer().Save()
    return hashlib.sha256(target.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--tolerance", default="0.05,0.5")
    args = parser.parse_args()
    linear, angular = (float(v) for v in args.tolerance.split(","))
    digest = convert(args.source, args.target, linear, angular)
    print(f"{args.target} sha256={digest} tolerance={args.tolerance}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
