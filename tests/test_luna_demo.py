from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from types import SimpleNamespace

import yaml

REPOSITORY = Path(__file__).resolve().parents[1]
EXAMPLE = REPOSITORY / "examples/luna-demo"
RUNNER = EXAMPLE / "run_demo.py"


def _module(name: str):
    spec = importlib.util.spec_from_file_location(name, RUNNER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_demo_is_one_lean_example() -> None:
    assert not (REPOSITORY / "examples/autonomous-research-session").exists()
    assert {path.name for path in EXAMPLE.iterdir() if path.is_file()} == {
        "Dockerfile",
        "README.md",
        "WORKSPACE.md",
        "agent-prompt.md",
        "objective.yaml",
        "requirements.txt",
        "run_demo.py",
    }
    assert 250 <= len(RUNNER.read_text(encoding="utf-8").splitlines()) <= 350
    assert (EXAMPLE / "requirements.txt").read_text(encoding="utf-8") == "openai==2.53.0\n"
    readme = (EXAMPLE / "README.md").read_text(encoding="utf-8")
    assert "Reference agent: GPT-5.6 Luna." in readme
    assert readme.count("docker build ") == 1
    assert readme.count("docker run ") == 1
    assert '--user "$(id -u):$(id -g)"' in readme
    assert "`bash` is unrestricted only inside the disposable virtual workspace." in readme

    dockerfile = (EXAMPLE / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG WHEEL_FILE=dynamical-0.1.0-py3-none-any.whl" in dockerfile
    assert "COPY --from=wheel ${WHEEL_FILE} /tmp/${WHEEL_FILE}" in dockerfile
    assert "COPY --from=demo requirements.txt /tmp/requirements.txt" in dockerfile
    assert "RUN install -d -m 0555 /opt/luna-demo" in dockerfile
    assert "COPY --chmod=0444 --from=demo run_demo.py agent-prompt.md objective.yaml" in dockerfile
    assert "COPY ." not in dockerfile
    assert "src/" not in dockerfile


def test_objective_has_only_customer_inputs_and_no_selected_provider() -> None:
    objective = yaml.safe_load((EXAMPLE / "objective.yaml").read_text(encoding="utf-8"))
    assert set(objective) == {
        "engineering_objective",
        "required_proof",
        "budget",
        "time_limit",
        "current_evidence",
        "dynamical_tool_access",
    }
    assert objective["engineering_objective"] == (
        "From a source-backed 33.75 g thermal process, select one 0.25 kg physical "
        "scale-transfer experiment."
    )
    proof = " ".join(objective["required_proof"]).lower()
    assert "what it cannot establish" in proof
    assert "comparative simulator evidence" in proof
    assert "physical validation request" in proof
    surface = json.dumps(objective).lower()
    for forbidden in (
        "matterix-heater",
        "capability",
        "condition",
        "order",
        "stopping",
        "heater setpoint",
        "dwell time",
        "343.15",
        "0.20 mol",
        "source-b-",
        "passed",
        "failed",
    ):
        assert forbidden not in surface


def test_prompt_assigns_research_and_authority_ownership() -> None:
    prompt = (EXAMPLE / "agent-prompt.md").read_text(encoding="utf-8")
    assert "Use only `read`, `write`, `edit`, and `bash`." in prompt
    assert (
        "You own the capability choices, experimental conditions, order, experiment\n"
        "count, analysis, and stopping decision."
    ) in prompt
    assert (
        "Dynamical owns admission, validation,\nevidence, cost, safety, and physical authority."
    ) in prompt
    for hidden_policy in ("at least two", "fixed sequence", "source-b-", "MATTERIX"):
        assert hidden_policy not in prompt


def test_workspace_teaches_only_public_cli_and_hold_boundary() -> None:
    workspace = (EXAMPLE / "WORKSPACE.md").read_text(encoding="utf-8")
    invocations = re.findall(r"^dynamical(?: ([a-z]+))? --help$", workspace, re.MULTILINE)
    assert invocations == ["", "capabilities", "compose", "compile", "run", "validate"]
    assert "background job" in workspace
    assert "job/status" in workspace
    assert "valid JSON in `decision.json`" in workspace
    for field in (
        "selected_virtual_campaign",
        "physical_route_requirement",
        "selected_physical_experiment",
        "decision_rationale",
        "uncertainty",
        "submitted",
    ):
        assert f"`{field}`" in workspace
    assert "`submitted`: `false`." in workspace
    assert "Preserve any `HOLD`" in workspace
    assert "returned by Dynamical" in workspace
    assert "Physical work stays on `HOLD`" not in workspace
    assert "`observation.channels`" in workspace
    assert "author a corrected requirement" in workspace
    assert "Do not add hashes, receipts, evidence paths, route status" in workspace
    for leak in ("src/", "/authority", "registries/", "MATTERIX", "source-b-", "passed"):
        assert leak not in workspace


def test_runner_exposes_four_tools_and_raw_api_stream(capsys) -> None:
    runner = _module("luna_demo_stream")
    assert [tool["name"] for tool in runner.tool_definitions()] == [
        "read",
        "write",
        "edit",
        "bash",
    ]

    class Event:
        def model_dump_json(self):
            return '{"type":"response.output_text.delta","delta":"raw","opaque":"kept"}'

    final_response = object()

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def __iter__(self):
            return iter([Event()])

        def get_final_response(self):
            return final_response

    class Responses:
        def stream(self, **request):
            assert request == {
                "model": "gpt-5.6-" + "luna",
                "context_management": [{"type": "compaction", "compact_threshold": 200000}],
            }
            return Stream()

    class Client:
        responses = Responses()

    assert (
        runner._stream_response(
            Client(),
            model="gpt-5.6-" + "luna",
            context_management=[{"type": "compaction", "compact_threshold": 200000}],
        )
        is final_response
    )
    assert capsys.readouterr().out.strip() == (
        '{"type":"response.output_text.delta","delta":"raw","opaque":"kept"}'
    )


def test_runner_only_runs_model_loop_in_existing_workspace(tmp_path: Path) -> None:
    runner = _module("luna_demo_boundary")
    source = RUNNER.read_text(encoding="utf-8")
    assert 'parser.add_argument("--max-turns", type=int, default=200)' in source
    assert source.count('"context_management"') == 1
    for removed_surface in (
        "validate_demo",
        "final-report",
        "sha256",
        "Docker",
        "MATTERIX",
        "video",
        "receipt",
        "cleanup",
        "fixed sequence",
    ):
        assert removed_surface not in source

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "WORKSPACE.md").write_text("guide\n", encoding="utf-8")
    tools = runner.Workspace(workspace)

    class WriteCall:
        name = "write"
        arguments = json.dumps({"path": "notes/result.txt", "content": "one\n"})

    class EditCall:
        name = "edit"
        arguments = json.dumps({"path": "notes/result.txt", "old_text": "one", "new_text": "two"})

    assert tools.execute(WriteCall())["ok"] is True
    assert tools.execute(EditCall())["ok"] is True
    assert (workspace / "notes/result.txt").read_text(encoding="utf-8") == "two\n"
    assert tools.read("notes/result.txt", None, None)["content"] == "two\n"


def test_reproduction_keeps_raw_events_and_uses_one_chronological_render() -> None:
    script = (REPOSITORY / "scripts/reproduce-v0.1.sh").read_text(encoding="utf-8")
    assert script.count("ffmpeg -hide_banner") == 1
    assert '"$LUNA_LOG" | fold -s -w 145' in script
    assert "substr($0, 1, 150)" not in script
    assert "response.reasoning_summary_text.delta" in script
    assert "workspace.tool" in script
    assert "start_duration=180" in script
    assert "Phase 1 - recorded Luna campaign" in script
    assert "Phase 2 - MATTERIX thermal branch projection" in script
    assert 'compile "$VIRTUAL_COMPOSITION" -o "$VIRTUAL_WORLD"' in script
    assert 'compile "$MATTERIX_COMPOSITION" -o "$COMPILED_WORLD"' in script
    assert '"$PHYSICAL_REQUIREMENT" >"$MATTERIX_REQUIREMENT"' in script
    assert "It does not execute the full physical route" in script
    assert "load the generated Dynamical OpenUSD stage" in script


def test_bash_does_not_receive_the_api_key(tmp_path: Path, monkeypatch) -> None:
    runner = _module("luna_demo_secret_boundary")
    captured: dict[str, object] = {}

    def command_runner(*args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    workspace = runner.Workspace(tmp_path, command_runner=command_runner)
    assert workspace.bash("true", 1)["ok"] is True
    assert "OPENAI_API_KEY" not in captured["env"]
