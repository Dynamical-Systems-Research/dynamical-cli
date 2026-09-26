<p align="center">
  <img src="https://raw.githubusercontent.com/Dynamical-Systems-Research/dynamical-cli/main/.github/assets/dynamical-systems-banner.webp" width="1584" height="396" alt="Dynamical Systems">
</p>

# Dynamical CLI

<p align="center">
  <a href="https://github.com/Dynamical-Systems-Research/dynamical-cli/releases/latest"><img src="https://img.shields.io/github/v/release/Dynamical-Systems-Research/dynamical-cli?label=release&amp;cacheSeconds=300" alt="Latest release"></a>
  <a href="https://github.com/Dynamical-Systems-Research/dynamical-cli/actions/workflows/ci.yml"><img src="https://github.com/Dynamical-Systems-Research/dynamical-cli/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI status"></a>
  <a href="https://pypi.org/project/dynamical-cli/"><img src="https://img.shields.io/badge/python-3.11%2B-blue.svg" alt="Python 3.11 or later"></a>
  <a href="https://github.com/Dynamical-Systems-Research/dynamical-cli/blob/main/LICENSE"><img src="https://img.shields.io/pypi/l/dynamical-cli.svg" alt="Apache 2.0 license"></a>
</p>

Dynamical CLI is an open-source interface for scientific investigations across
simulation and physical experiments. Agents use models, data and instrument
workflows to investigate a scientific question or engineering requirement,
interpret results and decide what to test next.

Dynamical preserves campaign inputs, execution traces and results for inspection,
replay, evaluation and training. Connected facilities control physical execution
and return measurements to the investigation.

[Research](https://dynamicalsystems.ai/scientific-autoresearch) ·
[Virtual campaign demos](https://dynamicalsystems.ai/scientific-autoresearch#see-the-virtual-laboratory-run) ·
[Examples](https://github.com/Dynamical-Systems-Research/dynamical-cli/tree/main/examples)

## Start with your agent

**Codex**

```bash
codex plugin marketplace add Dynamical-Systems-Research/dynamical-cli --json
codex plugin add dynamical@dynamical-systems-research --json
```

**Claude Code**

```bash
claude plugin marketplace add Dynamical-Systems-Research/dynamical-cli
claude plugin install dynamical@dynamical-systems-research
```

<details>
<summary>Other agents</summary>

Replace `<name>` with your agent's name:

```bash
npx skills add Dynamical-Systems-Research/dynamical-cli \
  --skill '*' --agent <name> --global --yes
```

</details>

Each option includes skills for running campaigns, checking the starting state
and preparing new integrations. The agent checks for the CLI and helps set it up
if needed. The runtime requires Python 3.11 or later.

## Try a scientific investigation

Give your agent the FastCat [candidate set](https://github.com/Dynamical-Systems-Research/dynamical-cli/blob/main/examples/fastcat-oer/candidate-set.yaml)
and [campaign template](https://github.com/Dynamical-Systems-Research/dynamical-cli/blob/main/examples/fastcat-oer/requirement.yaml), then ask:

> Use Dynamical's FastCat facility to compare these nine catalyst compositions.
> Which should we synthesize and measure next to find a catalyst with lower
> OER overpotential at 10 mA/cm²? Run and validate a separate virtual experiment
> for each candidate. Explain the evidence, uncertainty and what result from
> the proposed physical measurement would change the decision.

Ask for the campaign record, a recommendation with its limits, and a
proposed physical experiment or a reason to stop. `HOLD` means required evidence
or authority is missing. FastCat supplies predictions within a fixed composition
table; this example does not run physical hardware.

For your own investigation, provide the question or engineering requirement,
relevant models or data, available instruments and resource limits. Ask the agent
to identify what remains uncertain, choose a test that distinguishes the relevant
possibilities and use the result to guide its next decision.

## Use the CLI directly

```bash
uv tool install dynamical-cli
```

Without `uv`, use `python3 -m pip install --user dynamical-cli`.

The workflow is:

`capabilities → preflight → compose → compile → run → validate`

<details>
<summary>Run the virtual workflow quickstart</summary>

Run in an empty working directory:

```bash
base=https://raw.githubusercontent.com/Dynamical-Systems-Research/dynamical-cli/main/examples/quickstart
curl -fsSLO "$base/requirement.yaml"
curl -fsSLO "$base/records.json"
curl -fsSLO "$base/mapping.json"

dynamical capabilities --facility sdl1 --operation condition-ultrasonic --json
dynamical preflight mapping.json --requirement requirement.yaml -o preflight.json
dynamical compose requirement.yaml --preflight preflight.json -o composition.json
dynamical compile composition.json -o compiled-world
dynamical run compiled-world -o trace.ndjson
dynamical validate trace.ndjson --json
dynamical run trace.ndjson --mode replay -o trace.replay.ndjson
dynamical validate trace.replay.ndjson --json
```

This SDL1 example records temperature and ultrasound commands. It checks the
virtual workflow; physical temperature and conditioning responses remain unknown.
The FastCat scientific comparison is a separate example and facility.

</details>

Receipts include a `next_command` when another step is available. Validation
checks the workflow, provenance and permissions; scientific accuracy depends on
the selected models and data. Predictions, replayed observations and physical
measurements remain distinct in the record.

See the [runtime reference](https://github.com/Dynamical-Systems-Research/dynamical-cli/blob/main/docs/runtime-reference.md) for commands, exit codes,
branching, parallel campaigns and evidence limits.

## Connect a model or instrument

Use the included `dynamical-instrument` skill with the source code or protocol,
inputs and outputs, units, operating limits and available calibration data. It
prepares the supported integration files and validation checks for facility
review. New providers remain pending until approved.

[Integration skill](https://github.com/Dynamical-Systems-Research/dynamical-cli/blob/main/skills/dynamical-instrument/SKILL.md) ·
[Provider example](https://github.com/Dynamical-Systems-Research/dynamical-cli/tree/main/examples/provider-onboarding)

## Development and license

```bash
git clone https://github.com/Dynamical-Systems-Research/dynamical-cli.git
cd dynamical-cli
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

[Apache 2.0](https://github.com/Dynamical-Systems-Research/dynamical-cli/blob/main/LICENSE).
Third-party data and assets retain their own licenses; see
[THIRD_PARTY_NOTICES.md](https://github.com/Dynamical-Systems-Research/dynamical-cli/blob/main/THIRD_PARTY_NOTICES.md).
