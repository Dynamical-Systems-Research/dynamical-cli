# Dynamical CLI

[![Release](https://img.shields.io/pypi/v/dynamical-cli.svg?label=release)](https://pypi.org/project/dynamical-cli/)
[![CI](https://github.com/Dynamical-Systems-Research/dynamical-cli/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Dynamical-Systems-Research/dynamical-cli/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://pypi.org/project/dynamical-cli/)
[![License](https://img.shields.io/pypi/l/dynamical-cli.svg)](https://github.com/Dynamical-Systems-Research/dynamical-cli/blob/main/LICENSE)

Dynamical CLI is the open-source execution interface for Dynamical's autonomous
R&D network. An agent starts with an engineering objective and determines what
evidence could support the decision. It compiles the admitted instruments and
models needed for the campaign, runs virtual experiments, and can request the
physical experiment worth running next. The facility decides what can run.

A virtual laboratory can represent a complete supported laboratory or a
purpose-built multi-instrument workflow. Agents can explore counterfactual
experiments, learn instrument behavior and operating limits, and preserve each
campaign as a hash-bound experiment snapshot. They can replay a campaign or use
its evidence to branch the study from an earlier decision. Connected facilities
can then return the physical evidence that virtual environments cannot provide.

Dynamical provides two workflows:

- Use `$dynamical` to compose, run, and validate scientific campaigns.
- Use `$dynamical-instrument` to propose a computational model, archived
  dataset, calibrated twin, or physical instrument integration.

The agent skills provide the operating instructions. The Python package
supplies the runtime.

## See the virtual laboratory run

[Watch the recorded agent campaign and synchronized NVIDIA Isaac Sim replay.](https://dynamicalsystems.ai/scientific-autoresearch#see-the-virtual-laboratory-run)

An AI agent plans and runs experiments in a virtual lab.
Every result carries its uncertainty and a record of where it came from.
The agent reads that evidence, changes its plan, and asks for one physical experiment.
Dynamical holds that request until a real instrument is approved.

This video replays a recorded run. No physical experiment was performed.

The replay visualizes the recorded composition in NVIDIA Isaac Sim. It is not a
live physical execution or a calibrated twin of the full workstation.

The [Scientific Autoresearch study](https://dynamicalsystems.ai/scientific-autoresearch)
reports the complete 144-trajectory evaluation.

## Install Dynamical

Install the runtime with Python 3.11 or later:

```bash
python -m pip install dynamical-cli
```

Add the Codex plugin:

```bash
codex plugin marketplace add Dynamical-Systems-Research/dynamical-cli --json
codex plugin add dynamical@dynamical-systems-research --json
```

The plugin gives Codex both `$dynamical` for campaigns and
`$dynamical-instrument` for model and instrument integrations.

Claude Code and other Agent Skills-compatible environments can install either
portable skill directly:

```bash
npx skills add Dynamical-Systems-Research/dynamical-cli \
  --skill dynamical --global --copy --yes

npx skills add Dynamical-Systems-Research/dynamical-cli \
  --skill dynamical-instrument --global --copy --yes
```

The skills use the CLI as their runtime. No MCP server is required.

## Run a virtual campaign

Download the example requirement and run the complete virtual workflow:

```bash
curl -fsSLO https://raw.githubusercontent.com/Dynamical-Systems-Research/dynamical-cli/main/examples/quickstart/requirement.yaml

dynamical capabilities --json
dynamical compose requirement.yaml -o composition.json
dynamical compile composition.json -o compiled-world
dynamical run compiled-world -o trace.ndjson
dynamical validate trace.ndjson --json
```

The example transfers one sample into an ultrasonic conditioning station and
runs a bounded virtual process. The trace records each action, observation,
constraint, sample-state change, cost, and duration.

Use `dynamical compose --schema` to inspect the requirement schema. Use
`dynamical capabilities --operation <operation-id> --json` to inspect the typed
contract for one operation.

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

## Run a concurrent autoresearch study

Give an agent the scientific decision and comparison rule directly:

```text
Use Dynamical to run 16 independent virtual experiment arms over these candidate
compositions. Keep the processing route fixed. Compare overpotential and
uncertainty lexicographically. Confirm the study plan before execution. Stop
when the budget is exhausted or the top candidate is stable across two rounds.
Validate every arm before comparison. Preserve all promoted, not_promoted, HOLD,
invalid, and failed arms. Return study-report.json with the supported decision,
rival candidates, uncertainty, experiment snapshots, and next physical experiment.
```

## How Dynamical works

Dynamical exposes five commands:

- `capabilities` lists operations, providers, and admission states.
- `compose` binds a research requirement to compatible admitted providers.
- `compile` creates a target-specific virtual laboratory and execution contract.
- `run` executes a simulation or replays a recorded campaign.
- `validate` checks structure, provenance, evidence, and authority.

The reusable unit is a scientific capability. Each capability declares typed
inputs, outputs, units, limits, uncertainty, failure states, provenance, and
execution authority. A provider can bind that contract to a simulator, a
calibrated instrument model, a read-only facility connection, or an approved
physical instrument.

The agent controls the scientific objective, experiment parameters, operation
order, analysis, and stopping decision. Dynamical controls provider admission,
evidence types, trace integrity, facility policy, and physical authority.

## Automation contract

Dynamical uses these process exit codes:

- Exit `0`: the command produced an executable or valid result.
- Exit `1`: the command produced a structured domain-negative result, such as
  `HOLD` or failed validation.
- Exit `2`: the invocation is invalid or the input is malformed.

Automation must inspect a structured result that returns exit `1`. It must not
treat the result as an ordinary crash.

Default receipts use public evidence and authority facts: `evidence_classes`,
`execution_status`, `embodied_evidence_bound`, `claim_boundary`,
`authority_anchor`, and `validation_reasons`. Internal maturity rubrics are not
part of the CLI protocol.

Custom `--registry` and `--facility` inputs are proposals. They cannot grant
themselves authority. In v0.1, the installed bundle is the local authority
anchor.

`capabilities --registry <path>` inspects a proposal without activating it. Its
receipt reports effective admission after comparison with the installed
authority and preserves any self-declared admission as `proposed_admission`.
Direct manifest compilation creates a validation-only world with no campaign
execution route; its `next_command` is `validate`, not `run`.

## Portable worlds and trace-bound execution

[OpenUSD](https://openusd.org/release/index.html) carries the portable compiled
world, including the scene, instrument assets, and campaign composition.
[NVIDIA Isaac Sim](https://developer.nvidia.com/isaac/sim) supplies the embodied
visualization layer. Dynamical can compile the same campaign composition for
each target while preserving the bindings needed to compare and replay it.

Instrument models produce scientific observations. Each model declares its
inputs, outputs, units, operating range, uncertainty, and evidence class. One
hash-bound trace connects campaign actions, scene state, observations, sample
lineage, validation, and replay.

Virtual output remains separate from physical evidence. A physical observation
can only come from a facility-authorized execution.

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

> Use `$dynamical-instrument` to create the smallest pending Dynamical
> integration that these sources support. Return the candidate files, source
> evidence, capability operations, conformance commands, admission status, and
> missing review items.

A supported contribution can include:

1. Provider-independent capability definitions with typed inputs and outputs.
2. An adapter for a documented simulator or instrument interface.
3. A pending provider and, when supported, candidate facility records.
4. Calibration, provenance, license, and asset bindings.
5. A minimal example and targeted conformance tests.

The skill reports the exact files and validation commands for the contribution.
Every new provider remains `pending` until the installed facility authority
admits it. Missing calibration, licensing, safety review, or physical authority
keeps the route pending or returns `HOLD`.

## Evidence and authority

Simulation, calibrated-model output, replay, and physical measurements are
different evidence classes. Dynamical preserves that distinction in the
capability registry and campaign trace.

Validation confirms that an artifact follows its declared contract. It does not
establish scientific truth or optimality. Physical execution requires an
admitted provider, facility policy, and independent approval. A missing route or
authority record returns a structured `HOLD` receipt.

## Source and licensing

Dynamical is licensed under the
[Apache License 2.0](https://github.com/Dynamical-Systems-Research/dynamical-cli/blob/main/LICENSE).
Third-party geometry and calibration data keep their original licenses and
attribution. See
[THIRD_PARTY_NOTICES.md](https://github.com/Dynamical-Systems-Research/dynamical-cli/blob/main/THIRD_PARTY_NOTICES.md)
for details.

Machine-readable source records bind derived artifacts to source hashes,
provenance, license evidence, and known limits. A derived asset does not replace
its source or grant new rights.

## Development

```bash
git clone https://github.com/Dynamical-Systems-Research/dynamical-cli.git
cd dynamical-cli
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```
