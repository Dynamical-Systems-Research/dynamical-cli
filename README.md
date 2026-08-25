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

Dynamical CLI is an open-source interface for scientific autoresearch. An agent
starts with a question or engineering objective and decides what evidence could
resolve it. It composes a virtual laboratory from approved instruments and
computational providers. Dynamical compiles and records the campaign. The agent
runs adaptive virtual experiments and can request the next physical experiment.

A virtual laboratory can represent a complete supported laboratory or a small
set of connected instruments. Agents can explore counterfactual experiments and
learn instrument behavior and operating limits. Each recorded
campaign is hashed and can be replayed. An agent can snapshot a campaign and
branch it without changing the record. The same virtual laboratories support
agent evaluation, data generation, and training. Connected facilities can then
return the physical evidence that virtual environments cannot provide.

## Install Dynamical

Install the Codex plugin:

```bash
codex plugin marketplace add Dynamical-Systems-Research/dynamical-cli --json
codex plugin add dynamical@dynamical-systems-research --json
```

Install the Claude Code plugin:

```bash
claude plugin marketplace add Dynamical-Systems-Research/dynamical-cli
claude plugin install dynamical@dynamical-systems-research
```

Install the skills in a different agent. Replace `<name>` with the agent name:

```bash
npx skills add Dynamical-Systems-Research/dynamical-cli \
  --skill '*' --agent <name> --global --yes
```

Then ask a complete scientific question. The agent uses the `dynamical` skill to
plan and run the campaign. It first decides whether it needs literature, data,
models, or instrument capabilities. For example:

> Which catalyst composition should we synthesize and measure next to reduce
> uncertainty about which candidate has the lowest OER overpotential at
> 10 mA/cm²?

This question does not define a candidate pool. To run the public FastCat study,
also give the agent the
[candidate set](https://github.com/Dynamical-Systems-Research/dynamical-cli/blob/main/examples/fastcat-oer/candidate-set.yaml)
and
[campaign template](https://github.com/Dynamical-Systems-Research/dynamical-cli/blob/main/examples/fastcat-oer/requirement.yaml).
The agent then composes and validates one isolated arm for each candidate. It
returns the validated campaign record, the limits of its evidence, and a
proposed physical experiment or `HOLD`.

If the campaign needs a model, dataset, simulator, or instrument that is not
available, use the `dynamical-instrument` skill next. Each install method
includes it. This skill prepares an integration for facility review. It cannot
approve its own provider. It cannot authorize physical work.

## See the virtual laboratory run

[Watch four recorded campaigns and their synchronized NVIDIA Isaac Sim replays.](https://dynamicalsystems.ai/scientific-autoresearch#see-the-virtual-laboratory-run)

The portfolio covers water electrolysis, additive-alloy qualification,
critical-mineral recovery, and rare-earth magnet qualification. Each film shows
how an agent composes a virtual laboratory, runs experiments, responds to
validated evidence, and submits a physical experiment request for review. The
compiled OpenUSD laboratory runs in NVIDIA Isaac Sim while the recorded agent
output remains linked to the campaign trace.

These are virtual campaign replays. They do not show physical execution, and
they do not show that the full virtual workstations match physical laboratory
behavior.

The [Scientific Autoresearch study](https://dynamicalsystems.ai/scientific-autoresearch)
reports the matched FastCat outcome study and the later campaign behavior.

## Manual CLI setup

To install the runtime yourself, prefer an isolated tool environment:

```bash
uv tool install dynamical-cli
```

Without `uv`, install with Python 3.11 or later:

```bash
python3 -m pip install --user dynamical-cli
```

The skills use this CLI as their runtime. No MCP server is required.

## Run a virtual campaign

See the [examples index](https://github.com/Dynamical-Systems-Research/dynamical-cli/tree/main/examples)
for three public examples. It contains a simulator quickstart and a FastCat
OER reference. It also contains a provider proposal that returns `HOLD`. `HOLD`
means that Dynamical stopped because required evidence or authority is missing.

Download the example requirement and run the complete virtual workflow:

```bash
curl -fsSLO https://raw.githubusercontent.com/Dynamical-Systems-Research/dynamical-cli/main/examples/quickstart/requirement.yaml

dynamical capabilities
dynamical compose requirement.yaml -o composition.json
dynamical compile composition.json -o compiled-world
dynamical run compiled-world -o trace.ndjson
dynamical validate trace.ndjson --json
dynamical run trace.ndjson --mode replay -o replay.ndjson
dynamical validate replay.ndjson --json
```

The example transfers one sample into an ultrasonic conditioning station and
runs a virtual process with fixed limits. The trace records each action,
observation, constraint, sample-state change, cost, and duration.

Use `dynamical compose --schema` to inspect the requirement schema. Use
`dynamical capabilities --operation <operation-id> --json` to inspect the typed
contract for one operation.

### Snapshot and branch a campaign

Branch a new campaign from the sample state recorded at one observation in a
completed simulation trace:

```bash
dynamical run child-world \
  --restore-from parent.ndjson \
  --restore-world parent-world \
  --restore-at-event simulate-abc123:event:000006 \
  --dry-run

dynamical run child-world \
  --restore-from parent.ndjson \
  --restore-world parent-world \
  --restore-at-event simulate-abc123:event:000006 \
  -o child.ndjson
```

Restore validates and reruns the parent up to the selected observation. The
rerun must match the recorded trace bytes before Dynamical reads the sample
state. Restore does not change the parent or copy parent actions into the child
campaign. It does not support physical runs, runs from the 3D execution layer,
`HOLD` results, or state supplied directly by a user. The child trace reports
source and child evidence classes separately. A restored child trace can be
replayed, but it cannot be a restore source. Repeating the exact child command
reuses only a matching validated output and returns `"reused": true`; it never
overwrites a conflicting file. Use `dynamical run --help` for the full flag
list.

## Run many campaigns

Dynamical runs one campaign per process. Codex, Claude Code, or a scheduler can
run campaigns sequentially or in parallel and control the model, condition,
candidate pool, repeat, and worker count. Dynamical records the composition,
compiled world, trace, and validation result for each cell.

Store one predeclared requirement in each YAML file under `requirements/`.
Give each file a unique, stable name. This example runs up to eight cells at
the same time:

```bash
mkdir -p runs

find requirements -type f -name '*.yaml' -print0 |
  xargs -0 -P 8 -I {} sh -c '
    set -eu
    requirement_path=$1
    cell_id=$(basename "$requirement_path" .yaml)
    cell_dir="runs/$cell_id"
    mkdir -p "$cell_dir"

    dynamical compose "$requirement_path" \
      -o "$cell_dir/composition.json" \
      > "$cell_dir/compose-receipt.json"

    dynamical compile "$cell_dir/composition.json" \
      -o "$cell_dir/compiled-world" \
      > "$cell_dir/compile-receipt.json"

    dynamical run "$cell_dir/compiled-world" \
      -o "$cell_dir/trace.ndjson" \
      > "$cell_dir/run-receipt.json"

    dynamical validate "$cell_dir/trace.ndjson" --json \
      > "$cell_dir/validation.json"
  ' sh '{}'
```

For agent studies, save the model, prompt, condition, candidate pool, repeat,
and agent transcript beside these artifacts. Add a trace to the final analysis
only after its validation result reports `"valid": true`. Keep every `HOLD`
receipt as a study outcome.

## Run the FastCat candidate study

Give an agent the scientific decision and comparison rule directly:

```text
Use Dynamical with examples/fastcat-oer/candidate-set.yaml. Create one isolated
campaign arm for each of the nine supplied compositions and keep the fixed test
conditions. Validate every arm before comparison. If uncertainty intervals
overlap, do not claim a confirmed winner.
Use a unique lowest point estimate only as the current virtual lead for the next
physical measurement. Preserve all promoted, not_promoted, HOLD, invalid, and
failed arms. Return study-report.json with the supported decision, rival
candidates, uncertainty, experiment snapshots, and next physical experiment or
HOLD.
```

## How Dynamical works

Dynamical exposes five commands:

- `capabilities` lists operations and the providers that can perform them.
- `compose` matches a research requirement to approved providers.
- `compile` builds the virtual laboratory and its execution rules.
- `run` starts a simulation, replay, or branched virtual campaign.
- `validate` checks structure, source records, evidence labels, and authority.

Simulation, calibrated-model output, replay, and physical measurement are
different evidence classes. Validation checks structure and source records. It
does not establish scientific truth.

The reusable unit is a scientific capability. Each capability states its inputs,
outputs, units, limits, uncertainty, failure states, source records, and
execution authority. A provider performs that capability through a simulator,
calibrated instrument model, read-only facility connection, or approved physical
instrument.

The agent controls the scientific objective, experiment parameters, operation
order, analysis, and stopping decision. Dynamical checks provider approval,
evidence labels, trace integrity, and facility rules. The facility controls
physical execution.

## Automation contract

Dynamical uses these process exit codes:

- Exit `0`: the command produced an executable or valid result.
- Exit `1`: the command completed with a result that does not permit execution,
  such as `HOLD` or failed validation.
- Exit `2`: the invocation is invalid or the input is malformed.

Automation must inspect a structured result that returns exit `1`. It must not
treat the result as an ordinary crash.

Receipts include fields that state what evidence exists and what authority
allowed the command: `evidence_classes`,
`execution_status`, `embodied_evidence_bound`, `claim_boundary`,
`authority_anchor`, and `validation_reasons`.

Custom `--registry` and `--facility` inputs are proposals. They cannot grant
themselves approval. In v0.1, the installed bundle is the source of approved
records.

`capabilities --registry <path>` inspects a proposal without activating it. Its
receipt reports whether each provider is approved after comparison with the
installed records. It preserves any self-declared approval as
`proposed_admission`. Compiling a facility manifest directly creates a world for
validation only. It cannot run a campaign, so its `next_command` is `validate`.

## Compiled worlds and verified traces

[OpenUSD](https://openusd.org/release/index.html) carries the portable compiled
world, including the scene, instrument assets, and campaign composition.
[NVIDIA Isaac Sim](https://developer.nvidia.com/isaac/sim) supplies the 3D
execution and visualization layer. Dynamical can compile the same campaign for
each target while keeping the records needed to compare and replay it.

Instrument models produce scientific observations. Each model states its
inputs, outputs, units, operating range, uncertainty, and evidence class. One
trace connects campaign actions, scene state, observations, sample history,
validation, and replay. Its hashes reveal later changes.

Virtual output remains separate from physical evidence. A physical observation
can only come after a facility approves and runs the request.

## Add your own model or instrument

The included
[`dynamical-instrument`](https://github.com/Dynamical-Systems-Research/dynamical-cli/blob/main/skills/dynamical-instrument/SKILL.md)
skill maps source evidence to the existing Dynamical contracts. It adds only
the parts that the supplied evidence supports.

Give the agent:

- The model, dataset, or instrument source, revision, owner, license, and digest.
- The API, SDK, protocol, simulator, or model interface and a test endpoint when
  available.
- The supported inputs, outputs, units, limits, failure states, cost, and
  duration.
- Calibration data, thresholds, covered variables, and operating range.
- The facility endpoint, safety limits, approvals, and physical authority.
- The CAD source, license, and tolerances when geometry is required.

Then ask:

> Use the `dynamical-instrument` skill to create the smallest pending Dynamical
> integration that these sources support. Return the candidate files, source
> evidence, capability operations, validation commands, approval status, and
> missing review items.

A supported contribution can include:

1. Capability definitions with typed inputs and outputs that do not depend on
   one provider.
2. An adapter for a documented simulator or instrument interface.
3. A pending provider and, when supported, candidate facility records.
4. Calibration, source, license, and asset records.
5. A minimal example and targeted validation tests.

The skill reports the exact files and validation commands for the contribution.
Every new provider remains `pending` until the facility approves it. Missing
calibration, licensing, safety review, or physical authority keeps the route
pending or returns `HOLD`.

## Source and licensing

Dynamical is licensed under the
[Apache License 2.0](https://github.com/Dynamical-Systems-Research/dynamical-cli/blob/main/LICENSE).
Third-party geometry and calibration data keep their original licenses and
attribution. See
[THIRD_PARTY_NOTICES.md](https://github.com/Dynamical-Systems-Research/dynamical-cli/blob/main/THIRD_PARTY_NOTICES.md)
for details.

Machine-readable records link derived files to source hashes, source history,
license evidence, and known limits. A derived file does not replace its source
or grant new rights.

## Development

```bash
git clone https://github.com/Dynamical-Systems-Research/dynamical-cli.git
cd dynamical-cli
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```
