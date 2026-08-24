from __future__ import annotations

import json
import tomllib
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]


def test_plugin_version_matches_package_version() -> None:
    project = tomllib.loads((REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))
    plugin = json.loads((REPOSITORY / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))

    assert plugin["version"] == project["project"]["version"]
