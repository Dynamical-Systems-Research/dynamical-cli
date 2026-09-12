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
This example uses the separate DTU FastCat facility: select `--facility fastcat`
for capability discovery and composition. The default `sdl1` facility describes
a source-verified virtual workflow of the Acceleration Consortium bench.
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
base=https://raw.githubusercontent.com/Dynamical-Systems-Research/dynamical-cli/main/examples/quickstart
curl -fsSLO "$base/requirement.yaml"
curl -fsSLO "$base/records.json"
curl -fsSLO "$base/mapping.json"

dynamical capabilities --facility sdl1
dynamical preflight mapping.json --requirement requirement.yaml -o preflight.json
dynamical compose requirement.yaml --preflight preflight.json -o composition.json
dynamical compile composition.json -o compiled-world
dynamical run compiled-world -o trace.ndjson
dynamical validate trace.ndjson --json
dynamical run trace.ndjson --mode replay -o trace.replay.ndjson
dynamical validate trace.replay.ndjson --json
```

The quickstart records a 35 °C temperature setpoint and a 30 s ultrasound
command in one stationary SDL1 well. Its proof covers command bookkeeping;
physical temperature and conditioning response remain unknown. Traces record
commands, available observations, constraints, sample history, and accounting;
command-only adapters do not claim physically applied settings.

`preflight` freezes the declared starting state in `records.json` and returns
`READY` or `HOLD`. `compose` refuses a new campaign without a `READY` receipt.
A receipt names the next command in `next_command` when a next step exists, so an
agent follows the chain from preflight to validation without reconstructing a
command. A `HOLD` and a validated replay name none.

Use `dynamical compose --schema` to inspect the requirement schema. Use
`dynamical capabilities --operation <operation-id> --json` to inspect the typed
contract for one operation.

### Snapshot and branch a campaign

A validated simulate trace can seed a new campaign from the sample state
recorded at one of its observations. Restore reruns the parent up to that
observation and requires the rerun to match the recorded trace bytes before
Dynamical reads the sample state. It never changes the parent or copies parent
actions into the child, and it does not support physical runs, runs from the 3D
execution layer, `HOLD` results, or state supplied directly by a user. The child
trace reports source and child evidence classes separately; a restored child
can be replayed but cannot be a restore source. Once the child world is
compiled, `dynamical validate <trace> --compiled-world <parent-world>
--child-world <child-world>` returns the checked branch command in
`branch_command`; `dynamical run --help` lists the restore flags.

## Run many campaigns

Dynamical runs one campaign per process. Codex, Claude Code, or a scheduler can
run arms sequentially or in parallel and control the model, condition,
candidate pool, repeat, and worker count. Give each arm its own requirement,
starting-state map, and output directory; Dynamical records the composition,
compiled world, trace, and validation result for each arm, and each receipt
names the next command.

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
Treat the lowest point estimate as the current virtual lead, not the decision.
Before requesting a physical measurement, state the prediction it tests and
the result that would change the decision. Preserve all promoted,
not_promoted, HOLD, invalid, and failed arms. Return study-report.json with the
supported decision, rival candidates, uncertainty, experiment snapshots, and
the next physical experiment, a no-request decision with its reason, or HOLD.
```

## How Dynamical works

Dynamical exposes six commands:

- `capabilities` lists operations and the providers that can perform them.
- `preflight` freezes the starting state from lab records and returns `READY`
  or `HOLD`.
- `compose` matches a research requirement to approved providers.
- `compile` builds the virtual laboratory and its execution rules.
- `run` starts a simulation, replay, or branched virtual campaign.
- `validate` checks structure, source records, evidence labels, and authority.

Simulation, calibrated-model output, replay, and physical measurement are
different evidence classes. Validation checks structure and source records. It
does not establish scientific truth.

The reference bundles have two distinct scopes:

- **SDL1: source-verified virtual workflow.** Commands, electrode roles and the
  measurement protocol trace to pinned AC sources. Unavailable physical responses
  remain explicit.
- **FastCat: bounded empirical predictor.** The frozen OER table reports 48.4 mV
  MAE and 90.7% interval coverage on the 54-composition pool, with a fixed
  ±104.969 mV interval. The original 27-composition validation MAE was 22.2 mV;
  43 pool labels are single runs. See the [report](dynamical/bundle/fastcat/calibration/fastcat-oer/calibration_report.json).

Neither is a calibrated physical bench. Users connecting hardware must validate
its delivery and responses for their intended use and authorize physical work.

A valid workflow, a finite comparison of predictions, and a prospective
physical prediction are different claims. None alone demonstrates agent
learning. The agent owns the scientific decision; the CLI must preserve the
approval, provenance, and limits of the evidence used for that decision.

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

The installed `--facility sdl1` and `--facility fastcat` selectors choose
separate authority bundles. Custom registry and facility paths are proposals;
they cannot grant themselves approval. In v0.1, installed records define
provider approval.

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
