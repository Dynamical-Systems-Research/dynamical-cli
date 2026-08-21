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

Dynamical CLI is the open-source interface for scientific autoresearch. An agent
starts with a question or engineering objective and decides what evidence could
resolve it. It composes a virtual laboratory from admitted instruments and
computational providers. Dynamical compiles and records the campaign. The agent
runs adaptive virtual experiments and can request the physical experiment worth
running next.

A virtual laboratory can represent a complete supported laboratory or a
purpose-built multi-instrument workflow. Agents can explore counterfactual
experiments and learn instrument behavior and operating limits. Each recorded
campaign is hash-bound and can be replayed. A preserved campaign state can also
start a new experiment as a branch without changing the recorded campaign.
Connected facilities can then return the physical evidence that virtual
environments cannot provide.

## Give your agent Dynamical

For Codex, add the plugin:

```bash
codex plugin marketplace add Dynamical-Systems-Research/dynamical-cli --json
codex plugin add dynamical@dynamical-systems-research --json
```

For Claude Code, install the primary `$dynamical` skill:

```bash
npx skills add Dynamical-Systems-Research/dynamical-cli \
  --skill dynamical --global --copy --yes
```

Then ask one complete question:

> Which catalyst composition should we synthesize and measure next to reduce
> uncertainty about which candidate has the lowest OER overpotential at
> 10 mA/cm²?

The agent first verifies the Dynamical runtime and its capabilities. It uses the
[supplied candidate set](https://github.com/Dynamical-Systems-Research/dynamical-cli/blob/main/examples/fastcat-oer/candidate-set.yaml)
and the
[FastCat campaign template](https://github.com/Dynamical-Systems-Research/dynamical-cli/blob/main/examples/fastcat-oer/requirement.yaml)
to compose and validate one isolated arm for each candidate. It returns the
validated campaign record, the evidence boundary, and a proposed physical
experiment or `HOLD`.

If the campaign needs a model, dataset, simulator, or instrument that is not
available, use `$dynamical-instrument` as the next step. The Codex plugin
includes it. For Claude Code, install it directly:

```bash
npx skills add Dynamical-Systems-Research/dynamical-cli \
  --skill dynamical-instrument --global --copy --yes
```

This skill prepares a pending integration for review. It cannot admit its own
provider or grant physical authority.

## See the virtual laboratory run

[Watch four recorded campaigns and their synchronized NVIDIA Isaac Sim replays.](https://dynamicalsystems.ai/scientific-autoresearch#see-the-virtual-laboratory-run)

The portfolio covers water electrolysis, additive-alloy qualification,
critical-mineral recovery, and rare-earth magnet qualification. Each film shows
an agent compose a virtual laboratory, run experiments, respond to validated
evidence, and submit a request to facility authority. The compiled OpenUSD
laboratory runs in NVIDIA Isaac Sim while the recorded agent output remains
linked to the campaign trace.

These are virtual campaign replays. They do not show physical execution, and
the full workstations are not calibrated twins.

The [Scientific Autoresearch study](https://dynamicalsystems.ai/scientific-autoresearch)
reports the matched FastCat outcome study and the later campaign behavior.

## Manual CLI setup

Install the runtime with Python 3.11 or later:

```bash
python -m pip install dynamical-cli
```

The skills use this CLI as their runtime. No MCP server is required.

## Run a virtual campaign

Download the example requirement and run the complete virtual workflow:

See the [examples index](https://github.com/Dynamical-Systems-Research/dynamical-cli/tree/main/examples)
for three public examples. It contains a simulator quickstart and a FastCat
OER reference. It also contains a provider proposal that returns `HOLD`. `HOLD`
means that Dynamical stopped because required evidence or authority is missing.

```bash
curl -fsSLO https://raw.githubusercontent.com/Dynamical-Systems-Research/dynamical-cli/main/examples/quickstart/requirement.yaml

dynamical capabilities --json
dynamical compose requirement.yaml -o composition.json
dynamical compile composition.json -o compiled-world
dynamical run compiled-world -o trace.ndjson
dynamical validate trace.ndjson --json
dynamical run trace.ndjson --mode replay -o replay.ndjson
dynamical validate replay.ndjson --json
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
order, analysis, and stopping decision. Dynamical enforces provider admission,
evidence types, trace integrity, and facility policy. The facility retains
authority over physical execution.

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
