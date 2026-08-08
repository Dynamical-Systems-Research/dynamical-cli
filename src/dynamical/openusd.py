"""Deterministic, dependency-free USDA composition for portable inspection."""

from __future__ import annotations

import json
from pathlib import Path

from .schema import Asset, FacilityDocument, Pose, safe_usd_identifier


def _number(value: float) -> str:
    rendered = f"{value:.12g}"
    return "0" if rendered in {"-0", "-0.0"} else rendered


def _string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _vec3(values: tuple[float, float, float]) -> str:
    return f"({_number(values[0])}, {_number(values[1])}, {_number(values[2])})"


def _pose_ops(pose: Pose, indent: str) -> list[str]:
    p = pose.position_m
    q = pose.orientation
    return [
        f"{indent}double3 xformOp:translate = {_vec3((p.x, p.y, p.z))}",
        f"{indent}quatd xformOp:orient = "
        f"({_number(q.w)}, ({_number(q.x)}, {_number(q.y)}, {_number(q.z)}))",
        f'{indent}uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:orient"]',
    ]


def _geometry_lines(asset: Asset, material_path: str, indent: str) -> list[str]:
    geometry = asset.geometry
    primitive = {
        "cube": "Cube",
        "cylinder": "Cylinder",
        "sphere": "Sphere",
        "capsule": "Capsule",
    }[geometry.portable_primitive]
    dimensions = geometry.dimensions_m
    scale_z = dimensions.z / 2.0 if primitive == "Capsule" else dimensions.z
    lines = [
        f'{indent}def {primitive} "Geometry" (',
        f'{indent}    prepend apiSchemas = ["MaterialBindingAPI"]',
        f"{indent})",
        f"{indent}{{",
    ]
    attribute = "size" if primitive == "Cube" else "radius"
    lines.append(f"{indent}    double {attribute} = {1 if primitive == 'Cube' else 0.5}")
    if primitive in {"Cylinder", "Capsule"}:
        lines.append(f"{indent}    double height = 1")
        lines.append(f'{indent}    uniform token axis = "Z"')
    lines.extend(
        [
            f"{indent}    double3 xformOp:scale = {_vec3((dimensions.x, dimensions.y, scale_z))}",
            f"{indent}    color3f[] primvars:displayColor = [{_vec3(geometry.display_color_rgb)}]",
            f'{indent}    uniform token[] xformOpOrder = ["xformOp:scale"]',
            f"{indent}    rel material:binding = <{material_path}>",
            f"{indent}}}",
        ]
    )
    return lines


def layout_layer(document: FacilityDocument) -> str:
    """Create the weak layer that defines physical scene prims."""

    lines = ["#usda 1.0", "", 'def Xform "Facility" (', '    kind = "assembly"', ")", "{"]
    lines.extend(
        [
            '    def Scope "Looks"',
            "    {",
        ]
    )
    for asset in sorted(document.assets, key=lambda item: item.id):
        name = safe_usd_identifier(asset.id)
        color = asset.geometry.display_color_rgb
        lines.extend(
            [
                f'        def Material "{name}"',
                "        {",
                "            token outputs:surface.connect = "
                f"</Facility/Looks/{name}/Preview.outputs:surface>",
                '            def Shader "Preview"',
                "            {",
                '                uniform token info:id = "UsdPreviewSurface"',
                "                color3f inputs:diffuseColor = "
                f"{_vec3((color[0], color[1], color[2]))}",
                "                float inputs:metallic = 0",
                "                float inputs:roughness = 0.55",
                '                token outputs:surface = ""',
                "            }",
                "        }",
            ]
        )
    lines.extend(["    }", "", '    def Scope "Workstations"', "    {"])
    assets_by_workstation = {
        workstation.id: sorted(
            [asset for asset in document.assets if asset.workstation_id == workstation.id],
            key=lambda item: item.id,
        )
        for workstation in document.workstations
    }
    for workstation in sorted(document.workstations, key=lambda item: item.id):
        workstation_name = safe_usd_identifier(workstation.id)
        lines.extend([f'        def Xform "{workstation_name}"', "        {"])
        lines.extend(_pose_ops(workstation.local_frame, "            "))
        lines.extend(['            def Scope "Assets"', "            {"])
        for asset in assets_by_workstation[workstation.id]:
            asset_name = safe_usd_identifier(asset.id)
            material_path = f"/Facility/Looks/{asset_name}"
            lines.extend([f'                def Xform "{asset_name}"', "                {"])
            lines.extend(_pose_ops(asset.pose, "                    "))
            lines.extend(_geometry_lines(asset, material_path, "                    "))
            lines.append("                }")
        lines.extend(["            }", "        }"])
    lines.extend(["    }", "}", ""])
    return "\n".join(lines)


def physics_layer(document: FacilityDocument) -> str:
    """Create collision and physical-property overlays without vendor schemas."""

    lines = ["#usda 1.0", "", 'over "Facility"', "{", '    over "Workstations"', "    {"]
    for workstation in sorted(document.workstations, key=lambda item: item.id):
        workstation_name = safe_usd_identifier(workstation.id)
        lines.extend([f'        over "{workstation_name}"', "        {"])
        lines.extend(['            over "Assets"', "            {"])
        assets = sorted(
            [asset for asset in document.assets if asset.workstation_id == workstation.id],
            key=lambda item: item.id,
        )
        for asset in assets:
            name = safe_usd_identifier(asset.id)
            lines.extend(
                [
                    f'                over "{name}"',
                    "                {",
                    f"                    custom bool dynamical:collisionEnabled = "
                    f"{str(asset.collision.enabled).lower()}",
                    f"                    custom string dynamical:collisionShape = "
                    f"{_string(asset.collision.shape)}",
                ]
            )
            if asset.physical_properties.mass_kg is not None:
                lines.append(
                    "                    custom double dynamical:massKg = "
                    f"{_number(asset.physical_properties.mass_kg)}"
                )
            lines.append("                }")
        lines.extend(["            }", "        }"])
    lines.extend(["    }", "}", ""])
    return "\n".join(lines)


def semantics_layer(document: FacilityDocument, core_ir_sha256: str) -> str:
    """Create Dynamical semantic overlays and endpoint relationships."""

    device_by_asset = {item.asset_id: item for item in document.devices}
    agent_by_asset = {item.asset_id: item for item in document.agents}
    lines = [
        "#usda 1.0",
        "",
        'over "Facility"',
        "{",
        f"    custom string dynamical:coreIrSha256 = {_string(core_ir_sha256)}",
        f"    custom string dynamical:facilityId = {_string(document.facility.id)}",
        "    custom string dynamical:authoringBasis = "
        f"{_string(document.facility.authoring_basis)}",
        '    over "Workstations"',
        "    {",
    ]
    for workstation in sorted(document.workstations, key=lambda item: item.id):
        workstation_name = safe_usd_identifier(workstation.id)
        lines.extend([f'        over "{workstation_name}"', "        {"])
        lines.extend(['            over "Assets"', "            {"])
        assets = sorted(
            [asset for asset in document.assets if asset.workstation_id == workstation.id],
            key=lambda item: item.id,
        )
        for asset in assets:
            name = safe_usd_identifier(asset.id)
            lines.extend(
                [
                    f'                over "{name}"',
                    "                {",
                    f"                    custom string dynamical:assetId = {_string(asset.id)}",
                    "                    custom string dynamical:assetKind = "
                    f"{_string(asset.asset_kind)}",
                ]
            )
            if asset.id in device_by_asset:
                lines.append(
                    "                    custom string dynamical:deviceId = "
                    f"{_string(device_by_asset[asset.id].id)}"
                )
            if asset.id in agent_by_asset:
                lines.append(
                    "                    custom string dynamical:agentId = "
                    f"{_string(agent_by_asset[asset.id].id)}"
                )
            lines.append("                }")
        lines.extend(["            }", "        }"])
    lines.extend(["    }", "}", ""])
    return "\n".join(lines)


def calibration_layer(document: FacilityDocument) -> str:
    """Create an evidence-binding overlay. It does not assign a W level."""

    evidence_ids = sorted(evidence.id for evidence in document.calibration_evidence)
    model_ids = sorted(model.id for model in document.model_bindings)
    evidence_values = ", ".join(_string(value) for value in evidence_ids)
    model_values = ", ".join(_string(value) for value in model_ids)
    return "\n".join(
        [
            "#usda 1.0",
            "",
            'over "Facility"',
            "{",
            f"    custom string[] dynamical:calibrationEvidenceIds = [{evidence_values}]",
            f"    custom string[] dynamical:modelBindingIds = [{model_values}]",
            "    custom bool dynamical:w2Admitted = false",
            "}",
            "",
        ]
    )


def root_layer() -> str:
    """Create the composed stage. Sublayers are listed strongest to weakest."""

    return "\n".join(
        [
            "#usda 1.0",
            "(",
            '    defaultPrim = "Facility"',
            "    metersPerUnit = 1",
            '    upAxis = "Z"',
            "    subLayers = [",
            "        @./calibration.usda@,",
            "        @./semantics.usda@,",
            "        @./physics.usda@,",
            "        @./layout.usda@",
            "    ]",
            ")",
            "",
        ]
    )


def write_openusd_layers(
    document: FacilityDocument, output_dir: str | Path, core_ir_sha256: str
) -> list[Path]:
    """Write all portable layers and return their paths in stable order."""

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    contents = {
        "calibration.usda": calibration_layer(document),
        "layout.usda": layout_layer(document),
        "physics.usda": physics_layer(document),
        "root.usda": root_layer(),
        "semantics.usda": semantics_layer(document, core_ir_sha256),
    }
    paths: list[Path] = []
    for name, content in sorted(contents.items()):
        path = destination / name
        path.write_text(content, encoding="utf-8")
        paths.append(path)
    return paths
