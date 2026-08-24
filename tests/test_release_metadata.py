from __future__ import annotations

import json
import tomllib
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]


def test_plugin_version_matches_package_version() -> None:
    project = tomllib.loads((REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))
    plugin = json.loads((REPOSITORY / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))

    assert plugin["version"] == project["project"]["version"]


def test_plugin_default_prompts_fit_manifest_limit() -> None:
    plugin = json.loads((REPOSITORY / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    prompts = plugin["interface"]["defaultPrompt"]

    assert prompts
    assert all(isinstance(prompt, str) and len(prompt) <= 128 for prompt in prompts)
