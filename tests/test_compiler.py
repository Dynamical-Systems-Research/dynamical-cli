from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from dynamical.compiler import _resolve_asset_root, compile_facility, validate_compiled_world
from dynamical.source_admission import SourceAdmissionError

REPOSITORY = Path(__file__).resolve().parents[1]
MANIFEST = REPOSITORY / "manifests" / "ac-electrodeposition-cell.yaml"
REQUIRED_ARTIFACTS = {
    "action_schema.json",
    "adapter_pack.json",
    "calibration.usda",
    "calibration_bindings.json",
    "campaign_trace.schema.json",
    "capability_graph.json",
    "compile_manifest.json",
    "core_ir.json",
    "facility_ir.json",
    "evidence_report.json",
    "layout.usda",
    "observation_schema.json",
    "physics.usda",
    "root.usda",
    "semantics.usda",
}


def _json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


@pytest.fixture(scope="module")
def compiled_targets(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("compiled")
    outputs: dict[str, Path] = {}
    for target in ("openusd", "isaac"):
        output = root / target
        compile_facility(MANIFEST, target, output)
        outputs[target] = output
    return outputs


def test_all_targets_emit_required_core_artifacts(compiled_targets: dict[str, Path]) -> None:
    for output in compiled_targets.values():
        assert {path.name for path in output.iterdir() if path.is_file()} >= REQUIRED_ARTIFACTS


def test_core_hash_is_equal_across_all_targets(compiled_targets: dict[str, Path]) -> None:
    manifests = [_json(output / "compile_manifest.json") for output in compiled_targets.values()]

    assert len({manifest["core_ir_sha256"] for manifest in manifests}) == 1
    assert len({manifest["adapter_pack_sha256"] for manifest in manifests}) == 2


def test_evidence_report_uses_public_evidence_contract(
    compiled_targets: dict[str, Path],
) -> None:
    for output in compiled_targets.values():
        report = _json(output / "evidence_report.json")
        assert report["evidence_classes"] == []
        assert report["execution_status"] == "not_executed"
        assert report["embodied_evidence_bound"] is False
        assert report["authority_anchor"] == "installed_bundle"
        assert report["validation_reasons"] == []


def test_compiled_artifact_hashes_and_openusd_stage_validate(
    compiled_targets: dict[str, Path],
) -> None:
    for output in compiled_targets.values():
        result = validate_compiled_world(output)
        assert result["valid"] is True, result
        assert result["failures"] == []
        assert result["usdchecker"]["status"] in {"passed", "not_run"}
        if result["usdchecker"]["status"] == "not_run":
            assert result["usdchecker"]["reason"] == "usdchecker not found"


def test_compile_is_deterministic_across_output_directories(tmp_path: Path) -> None:
    first = compile_facility(MANIFEST, "isaac", tmp_path / "first")
    second = compile_facility(MANIFEST, "isaac", tmp_path / "second")

    assert first.core_ir_sha256 == second.core_ir_sha256
    assert first.adapter_pack_sha256 == second.adapter_pack_sha256
    assert first.world_sha256 == second.world_sha256
    first_manifest = _json(first.manifest_path)
    second_manifest = _json(second.manifest_path)
    assert first_manifest["artifacts"] == second_manifest["artifacts"]


def test_recompile_removes_stale_files_and_preserves_world_hash(tmp_path: Path) -> None:
    output = tmp_path / "compiled"
    first = compile_facility(MANIFEST, "openusd", output)
    first_manifest = _json(first.manifest_path)
    first_artifacts = {item["path"] for item in first_manifest["artifacts"]}
    (output / "stale.txt").write_text("not a compiler artifact\n", encoding="utf-8")
    stale_directory = output / "stale-directory"
    stale_directory.mkdir()
    (stale_directory / "old.json").write_text("{}\n", encoding="utf-8")

    second = compile_facility(MANIFEST, "openusd", output)
    second_manifest = _json(second.manifest_path)
    second_artifacts = {item["path"] for item in second_manifest["artifacts"]}
    actual_files = {
        path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()
    }

    assert second.world_sha256 == first.world_sha256
    assert second_artifacts == first_artifacts
    assert actual_files == second_artifacts | {"compile_manifest.json"}
    assert not (output / "stale.txt").exists()
    assert not stale_directory.exists()


def test_compile_refuses_nonempty_unowned_output(tmp_path: Path) -> None:
    output = tmp_path / "unowned"
    output.mkdir()
    existing = output / "keep.txt"
    existing.write_text("user data\n", encoding="utf-8")

    with pytest.raises(ValueError, match="no valid Dynamical ownership receipt"):
        compile_facility(MANIFEST, "openusd", output)

    assert existing.read_text(encoding="utf-8") == "user data\n"


def test_compile_refuses_forged_minimal_ownership_markers(tmp_path: Path) -> None:
    output = tmp_path / "forged"
    output.mkdir()
    existing = output / "user-data.txt"
    existing.write_text("keep me\n", encoding="utf-8")
    (output / "facility_ir.json").write_text(
        '{"document_type":"dynamical.facility"}\n', encoding="utf-8"
    )
    (output / "compile_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "dynamical.compile-manifest.v1",
                "root_stage": "root.usda",
                "artifacts": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="no valid Dynamical ownership receipt"):
        compile_facility(MANIFEST, "openusd", output)

    assert existing.read_text(encoding="utf-8") == "keep me\n"


def test_validator_rejects_artifact_path_escape_and_duplicate(tmp_path: Path) -> None:
    output = compile_facility(MANIFEST, "openusd", tmp_path / "compiled").output_dir
    manifest_path = output / "compile_manifest.json"
    original = _json(manifest_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")

    escaped = json.loads(json.dumps(original))
    escaped["artifacts"][0] = {
        "path": "../outside.txt",
        "sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
    }
    escaped_hashes = {item["path"]: item["sha256"] for item in escaped["artifacts"]}
    from dynamical.schema import canonical_sha256

    escaped["world_sha256"] = canonical_sha256(escaped_hashes)
    manifest_path.write_text(json.dumps(escaped), encoding="utf-8")
    result = validate_compiled_world(output)
    assert result["valid"] is False
    assert "unsafe compiled artifact path" in " ".join(result["failures"])

    duplicate = json.loads(json.dumps(original))
    duplicate["artifacts"].append(dict(duplicate["artifacts"][0]))
    duplicate["artifact_count"] += 1
    manifest_path.write_text(json.dumps(duplicate), encoding="utf-8")
    result = validate_compiled_world(output)
    assert result["valid"] is False
    assert "duplicate compiled artifact path" in " ".join(result["failures"])


def test_validator_rejects_coordinated_adapter_pack_tamper(tmp_path: Path) -> None:
    from dynamical.schema import canonical_sha256

    output = compile_facility(MANIFEST, "openusd", tmp_path / "compiled").output_dir
    adapter_path = output / "adapter_pack.json"
    manifest_path = output / "compile_manifest.json"
    adapter = _json(adapter_path)
    manifest = _json(manifest_path)

    adapter["bindings"] = []
    adapter["adapter_pack_sha256"] = "0" * 64
    adapter_path.write_text(json.dumps(adapter), encoding="utf-8")
    for artifact in manifest["artifacts"]:
        if artifact["path"] == "adapter_pack.json":
            artifact["sha256"] = hashlib.sha256(adapter_path.read_bytes()).hexdigest()
    manifest["adapter_pack_sha256"] = "0" * 64
    manifest["world_sha256"] = canonical_sha256(
        {item["path"]: item["sha256"] for item in manifest["artifacts"]}
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = validate_compiled_world(output)
    assert result["valid"] is False
    assert "adapter pack does not match" in " ".join(result["failures"])


def test_isaac_target_config_records_exact_runtime_revisions(
    compiled_targets: dict[str, Path],
) -> None:
    isaac = _json(compiled_targets["isaac"] / "backend_config.json")

    assert isaac["isaac_sim_version"] == "5.1.0.0"
    assert isaac["python"] == "3.11"


def test_compiled_agent_schemas_require_provider_and_evidence_identity(
    tmp_path: Path,
) -> None:
    result = compile_facility(MANIFEST, "openusd", tmp_path / "typed-agent-schema")
    actions = _json(result.output_dir / "action_schema.json")
    observations = _json(result.output_dir / "observation_schema.json")

    assert {"provider_id", "evidence_class"} <= set(actions["required"])
    assert actions["properties"]["evidence_class"]["enum"] == [
        "simulator",
        "calibrated_twin",
        "shadow",
        "physical",
    ]
    assert {"provider_id", "evidence_class"} <= set(observations["required"])
    channel = observations["properties"]["channels"]["items"]
    assert {"provider_id", "evidence_class"} <= set(channel["required"])


def test_compile_manifest_hashes_every_declared_artifact(
    compiled_targets: dict[str, Path],
) -> None:
    for output in compiled_targets.values():
        manifest = _json(output / "compile_manifest.json")
        for artifact in manifest["artifacts"]:
            path = output / artifact["path"]
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            assert digest == artifact["sha256"]


def test_resolve_asset_root_prefers_the_repository_checkout() -> None:
    """From this repo checkout (no wheel installed here), every real derived layer id
    must resolve under the repository root -- the same root the full compiled_targets
    fixture above already exercises implicitly on every test run.
    """
    real_ids = [
        "assets/usd/vial-rack-v3.usdc",
        "assets/usd/nickel-electrode-v30.usdc",
    ]
    assert _resolve_asset_root(real_ids) == REPOSITORY


def test_resolve_asset_root_fails_loudly_naming_the_id_and_both_paths() -> None:
    """The bug a live agent hit on a clean wheel install: an asset that resolves under
    neither the packaged install nor the repository checkout must never be silently
    treated as absent-but-fine -- it must raise, naming the missing id and both full
    paths that were tried, so a future asset added at a new repo path that
    pyproject.toml's force-include list forgets to mirror fails loudly instead of
    resolving to nothing.
    """
    missing_id = "assets/usd/does-not-exist-anywhere.usdc"
    with pytest.raises(SourceAdmissionError, match=re.escape(missing_id)) as excinfo:
        _resolve_asset_root(["assets/usd/vial-rack-v3.usdc", missing_id])
    message = str(excinfo.value)
    assert "artifact is absent at both" in message
    # Both candidate roots must be named, not just whichever one happened to be tried.
    assert str(REPOSITORY / missing_id) in message
    assert message.count(missing_id) >= 2
