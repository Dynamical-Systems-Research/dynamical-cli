"""Run one lean Luna model loop in a caller-prepared disposable workspace."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from time import monotonic
from typing import Any

MODEL = "gpt-5.6-luna"
MAX_TEXT_BYTES = 64 * 1024
HERE = Path(__file__).resolve().parent


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace",
        required=True,
        type=Path,
        help="Existing disposable directory that contains WORKSPACE.md.",
    )
    parser.add_argument("--objective", type=Path, default=HERE / "objective.yaml")
    parser.add_argument("--prompt", type=Path, default=HERE / "agent-prompt.md")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--reasoning-effort", default="max")
    parser.add_argument("--max-turns", type=int, default=200)
    parser.add_argument("--max-output-tokens", type=int, default=32768)
    return parser.parse_args()


def _function(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str],
) -> dict[str, Any]:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


def tool_definitions() -> list[dict[str, Any]]:
    text = {"type": "string"}
    return [
        _function(
            "read",
            "Read a UTF-8 text file in the disposable workspace.",
            {
                "path": {**text, "minLength": 1},
                "offset": {"type": ["integer", "null"], "minimum": 1},
                "limit": {"type": ["integer", "null"], "minimum": 1},
            },
            ["path", "offset", "limit"],
        ),
        _function(
            "write",
            "Create or replace a UTF-8 text file in the disposable workspace.",
            {"path": {**text, "minLength": 1}, "content": text},
            ["path", "content"],
        ),
        _function(
            "edit",
            "Replace one unique exact text block in a workspace file.",
            {
                "path": {**text, "minLength": 1},
                "old_text": {**text, "minLength": 1},
                "new_text": text,
            },
            ["path", "old_text", "new_text"],
        ),
        _function(
            "bash",
            "Run a Bash command from the disposable workspace.",
            {
                "command": {**text, "minLength": 1},
                "timeout_seconds": {
                    "type": ["integer", "null"],
                    "minimum": 1,
                    "maximum": 3600,
                },
            },
            ["command", "timeout_seconds"],
        ),
    ]


def _trim(value: str, *, tail: bool = False) -> str:
    data = value.encode("utf-8", errors="replace")
    if len(data) <= MAX_TEXT_BYTES:
        return value
    selected = data[-MAX_TEXT_BYTES:] if tail else data[:MAX_TEXT_BYTES]
    decoded = selected.decode("utf-8", errors="replace")
    edge = "last" if tail else "first"
    return f"{decoded}\n[Truncated: {edge} {MAX_TEXT_BYTES} of {len(data)} bytes.]"


def _stream_response(client: Any, **request: Any) -> Any:
    """Print each OpenAI stream event without rewriting its JSON payload."""
    with client.responses.stream(**request) as stream:
        for event in stream:
            print(event.model_dump_json(), flush=True)
        return stream.get_final_response()


class Workspace:
    """Implement the four general tools against one disposable directory."""

    def __init__(self, root: Path, *, command_runner: Any = subprocess.run):
        self.root = root.resolve()
        self.command_runner = command_runner

    def _path(self, value: str) -> Path:
        requested = Path(value)
        path = requested.resolve() if requested.is_absolute() else (self.root / requested).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"path is outside the disposable workspace: {value}") from exc
        return path

    def read(self, path: str, offset: int | None, limit: int | None) -> dict[str, Any]:
        selected = self._path(path)
        lines = selected.read_text(encoding="utf-8").splitlines(keepends=True)
        start = (offset or 1) - 1
        end = start + limit if limit is not None else None
        return {
            "ok": True,
            "path": str(selected.relative_to(self.root)),
            "content": _trim("".join(lines[start:end])),
            "line_count": len(lines),
        }

    def write(self, path: str, content: str) -> dict[str, Any]:
        selected = self._path(path)
        selected.parent.mkdir(parents=True, exist_ok=True)
        selected.write_text(content, encoding="utf-8")
        return {
            "ok": True,
            "path": str(selected.relative_to(self.root)),
            "bytes_written": len(content.encode("utf-8")),
        }

    def edit(self, path: str, old_text: str, new_text: str) -> dict[str, Any]:
        selected = self._path(path)
        content = selected.read_text(encoding="utf-8")
        occurrences = content.count(old_text)
        if occurrences != 1:
            raise ValueError(f"old_text must occur exactly once; found {occurrences}")
        selected.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
        return {"ok": True, "path": str(selected.relative_to(self.root))}

    def bash(self, command: str, timeout_seconds: int | None) -> dict[str, Any]:
        started = monotonic()
        tool_environment = os.environ.copy()
        tool_environment.pop("OPENAI_API_KEY", None)
        try:
            completed = self.command_runner(
                ["/bin/bash", "--noprofile", "--norc", "-lc", command],
                cwd=self.root,
                env=tool_environment,
                text=True,
                capture_output=True,
                timeout=timeout_seconds or 120,
                check=False,
            )
            return {
                "ok": completed.returncode == 0,
                "exit_code": completed.returncode,
                "stdout": _trim(completed.stdout, tail=True),
                "stderr": _trim(completed.stderr, tail=True),
                "wall_time_seconds": monotonic() - started,
            }
        except subprocess.TimeoutExpired as exc:
            return {
                "ok": False,
                "error": "command timed out",
                "stdout": _trim(exc.stdout or "", tail=True),
                "stderr": _trim(exc.stderr or "", tail=True),
                "wall_time_seconds": monotonic() - started,
            }

    def execute(self, call: Any) -> dict[str, Any]:
        try:
            arguments = json.loads(call.arguments)
            if not isinstance(arguments, dict):
                raise TypeError("tool arguments must be a JSON object")
            handler = getattr(self, call.name, None)
            if call.name not in {"read", "write", "edit", "bash"} or handler is None:
                raise ValueError(f"unknown tool: {call.name}")
            result = handler(**arguments)
        except Exception as exc:
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        print(
            json.dumps(
                {
                    "type": "workspace.tool",
                    "name": call.name,
                    "arguments": arguments if "arguments" in locals() else {},
                    "result": result,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return result


def _initial_input(objective: str) -> list[dict[str, Any]]:
    return [
        {
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": (
                        f"Read WORKSPACE.md, then pursue this customer objective.\n\n{objective}"
                    ),
                }
            ],
        }
    ]


def run(args: argparse.Namespace, *, client: Any | None = None) -> int:
    workspace_root = args.workspace.resolve()
    if not workspace_root.is_dir():
        raise ValueError("--workspace must be an existing disposable directory")
    if not (workspace_root / "WORKSPACE.md").is_file():
        raise ValueError("the disposable workspace must contain WORKSPACE.md")
    if args.max_turns < 1:
        raise ValueError("--max-turns must be positive")

    prompt = args.prompt.read_text(encoding="utf-8")
    objective = args.objective.read_text(encoding="utf-8")
    workspace = Workspace(workspace_root)
    if client is None:
        from openai import OpenAI

        api = OpenAI()
    else:
        api = client
    inputs = _initial_input(objective)
    previous_response_id: str | None = None

    for _turn in range(1, args.max_turns + 1):
        request: dict[str, Any] = {
            "model": args.model,
            "instructions": prompt,
            "input": inputs,
            "tools": tool_definitions(),
            "tool_choice": "auto",
            "reasoning": {"effort": args.reasoning_effort, "summary": "auto"},
            "max_output_tokens": args.max_output_tokens,
            "context_management": [{"type": "compaction", "compact_threshold": 200000}],
            "store": True,
        }
        if previous_response_id is not None:
            request["previous_response_id"] = previous_response_id
        response = _stream_response(api, **request)
        calls = [item for item in response.output if item.type == "function_call"]
        if not calls:
            return 0
        inputs = [
            {
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": json.dumps(workspace.execute(call)),
            }
            for call in calls
        ]
        previous_response_id = response.id

    raise RuntimeError(f"model did not stop within {args.max_turns} turns")


def main() -> int:
    return run(_arguments())


if __name__ == "__main__":
    raise SystemExit(main())
