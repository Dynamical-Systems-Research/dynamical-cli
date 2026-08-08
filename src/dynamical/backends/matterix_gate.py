"""Outer fail-closed launcher for a compiled MATTERIX runtime child."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_receipt(output: Path) -> tuple[bool, str]:
    """Validate the final receipt that the child flushes before Kit shutdown."""

    receipt_path = output / "runtime_evidence.json"
    if not receipt_path.is_file():
        return False, "runtime receipt is absent"
    try:
        value: Any = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return False, f"runtime receipt is invalid: {error}"
    if not isinstance(value, dict):
        return False, "runtime receipt is not an object"
    if value.get("receipt_complete") is not True:
        return False, "runtime receipt is incomplete"
    if value.get("execution_status") != "passed":
        return False, "runtime execution did not pass"
    if value.get("intended_exit_code") != 0:
        return False, "child intended exit code is not zero"
    trace_hash = value.get("trace_sha256")
    trace_path = output / "campaign_trace.ndjson"
    if not isinstance(trace_hash, str) or not trace_path.is_file():
        return False, "campaign trace receipt is absent"
    if _sha256(trace_path) != trace_hash:
        return False, "campaign trace hash does not match the receipt"
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, list):
        return False, "runtime artifact list is absent"
    video_records = [
        item
        for item in artifacts
        if isinstance(item, dict) and item.get("path") == "matterix-runtime.mp4"
    ]
    if len(video_records) != 1:
        return False, "runtime video receipt is absent or ambiguous"
    video_path = output / "matterix-runtime.mp4"
    if not video_path.is_file() or video_path.stat().st_size == 0:
        return False, "runtime video is absent or empty"
    video_hash = video_records[0].get("sha256")
    if not isinstance(video_hash, str) or _sha256(video_path) != video_hash:
        return False, "runtime video hash does not match the receipt"
    return True, "passed"


def _output_from_args(arguments: list[str]) -> Path:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--output", required=True, type=Path)
    parsed, _ = parser.parse_known_args(arguments)
    return parsed.output


def main(arguments: list[str] | None = None) -> int:
    child_arguments = list(sys.argv[1:] if arguments is None else arguments)
    output = _output_from_args(child_arguments)
    runner = Path(__file__).with_name("run_matterix.py")
    completed = subprocess.run([sys.executable, str(runner), *child_arguments], check=False)
    passed, reason = validate_receipt(output)
    if completed.returncode != 0:
        print(f"MATTERIX child process failed with status {completed.returncode}", file=sys.stderr)
        return 1
    if not passed:
        print(f"MATTERIX compiled runtime failed: {reason}", file=sys.stderr)
        return 1
    print("MATTERIX compiled runtime passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
