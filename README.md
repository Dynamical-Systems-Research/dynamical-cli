# Dynamical v0.1

## What Dynamical is

Dynamical is a compiler and runtime for campaign-specific, executable virtual laboratories. It turns an engineering objective and current evidence into a campaign made from admitted scientific capabilities and providers. If no valid route meets the requirement, Dynamical returns `HOLD`.

## Agent and Dynamical responsibilities

The research agent controls the research policy. It selects what to investigate and reports its rationale and uncertainty. Dynamical controls provider admission, safety, evidence, cost, and execution authority. The agent cannot admit its own provider or approve physical execution.

Use an output as evidence only after its command exits and `dynamical validate` passes. A running log or partial trace is not evidence. Preserve `HOLD` until the missing evidence, provider, policy, budget, safety condition, or authority changes.

Validation checks structure, provenance, authority, and execution evidence. It does not certify that an agent's scientific inference is correct or optimal.

## Five-command CLI

Inspect each command with `--help`. Use `capabilities` to inspect capability, provider, and admission states.

```bash
dynamical capabilities --json
dynamical compose requirement.yaml -o composition.json
dynamical compile composition.json -o compiled-world
dynamical run compiled-world -o trace.ndjson
dynamical validate trace.ndjson --json
```

The five commands are `capabilities`, `compose`, `compile`, `run`, and `validate`. A `COMPILED` composition can continue to compilation. Saved compositions carry the source metadata needed by later commands.

## Installation

The CLI and agent skill are separate installs. A skill or plugin install does not install the `dynamical` executable.

After the approved private push, install the CLI from one full commit SHA:

```bash
uv tool install 'git+ssh://git@github.com/Dynamical-Systems-Research/dynamical-cli.git@<full-commit-sha>'
dynamical --version
dynamical capabilities --json
dynamical compose --schema
```

For source development, use Python 3.11 or later:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
dynamical --version
```

MATTERIX and Isaac Sim need separate NVIDIA runtime environments. Dynamical source code is licensed under the [Apache License 2.0](LICENSE). Upstream software and data keep their own licenses.

## Reproduce

The reproduction script checks that `OPENAI_API_KEY` is present without printing its value. It also requires an NVIDIA GPU and a working MATTERIX runtime through `DYNAMICAL_PROVIDER_RUNTIME_ROOT`. Missing inputs fail before Docker or the research workflow starts.

The script builds the wheel and Luna container, writes persistent ignored outputs under `artifacts/v0.1/<UTC timestamp>-<pid>/`, runs the selected MATTERIX provider task, validates completed evidence, and prints the path to `final.json`.

```bash
./scripts/reproduce-v0.1.sh
```

## Reference demonstration

The fresh reference demonstration gives Luna an engineering objective, current evidence, read-only installed authority data, and general workspace tools. Luna inspects capabilities, authors campaign files, and uses the five public commands to compose, compile, run, and validate its virtual experiments before it writes a small decision record. The reproduction script independently recomposes the selected branch, confirms that the unauthorized physical route is `HOLD`, runs the selected MATTERIX provider task, and validates the completed outputs.

The demonstration does not set a scientific choice or outcome for the agent. The MATTERIX recording is a simulator projection of the heater setpoint and dwell from Luna's physical route. It does not execute the full route, stirring, or measurements. Version 0.1 admits W0 for the machine-readable facility and capability system. A passing reproduction plus preserved independent visual inspection can admit bounded W1 only for the MATTERIX workstation and named provider task. It admits no W2 result and performs no physical experiment. No demonstration evidence is tracked in Git.

## Agent skill and Codex plugin

The portable skill works with Codex and Claude Code. Check out the same full commit SHA, then install the one shared skill:

```bash
git clone git@github.com:Dynamical-Systems-Research/dynamical-cli.git dynamical-cli-skill
git -C dynamical-cli-skill checkout --detach <full-commit-sha>
npx skills add ./dynamical-cli-skill --skill dynamical --agent codex --agent claude-code --global --copy --yes
```

For another agent harness, install the pinned CLI, load `skills/dynamical/SKILL.md` from the same commit, and give the agent filesystem and shell tools.

The Codex plugin supplies the same skill. Add the private marketplace at the same commit, then add the plugin:

```bash
codex plugin marketplace add Dynamical-Systems-Research/dynamical-cli --ref <full-commit-sha> --json
codex plugin add dynamical@dynamical-systems-research --json
```

In all cases, confirm the CLI separately:

```bash
dynamical --help
```

This repository includes the portable skill, a thin plugin manifest, and its Codex marketplace entry. It does not include an MCP server.

## MATTERIX attribution

[MATTERIX](https://github.com/AccelerationConsortium/Matterix) supplies upstream tasks, scientific semantics, and an embodied simulation backend. Its separate [Matterix assets repository](https://github.com/AccelerationConsortium/Matterix_assets) supplies the laboratory asset files. No explicit asset-redistribution license was found, so Dynamical does not vendor those files. Dynamical supplies objective-to-provider composition, admission checks, generated backend bindings, evidence contracts, and validation.

In the reference path, the generated Dynamical adapter runs an admitted upstream MATTERIX task. MATTERIX does not load the generated Dynamical OpenUSD stage. This path can prove the bounded adapter, provider task, receipt, and trace contract. It cannot prove execution of the composed OpenUSD scene, W2 fidelity, or a physical facility.

## Development tests

```bash
uv run pytest -q
uv run ruff check .
```
