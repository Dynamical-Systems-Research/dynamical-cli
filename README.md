# Dynamical CLI

[![Release](https://img.shields.io/pypi/v/dynamical-cli.svg?label=release)](https://pypi.org/project/dynamical-cli/)
[![CI](https://github.com/Dynamical-Systems-Research/dynamical-cli/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Dynamical-Systems-Research/dynamical-cli/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://pypi.org/project/dynamical-cli/)
[![License](https://img.shields.io/pypi/l/dynamical-cli.svg)](https://github.com/Dynamical-Systems-Research/dynamical-cli/blob/main/LICENSE)

Dynamical CLI is an open-source execution interface for scientific agents and
facilities. It lets agents compose executable virtual laboratories from admitted
capabilities, run campaigns, and route physical requests through a fail-closed
authority gate.

Virtual laboratories let agents explore counterfactual campaigns, learn how
instruments and workflows behave, and generate model-derived observations and
verified execution trajectories. Connected facilities can then supply the
physical evidence that virtual environments cannot establish.

Dynamical is designed for agent use. The Codex plugin and portable Agent Skill
supply the operating interface. The Python package supplies the runtime.

## See the virtual laboratory run

https://github.com/user-attachments/assets/01cbd9b8-33b9-40d7-935b-9b90ab5c8df2

The video pairs a recorded agent campaign with a synchronized Isaac Sim replay.
The agent composes a multi-instrument campaign, receives a model-generated
scientific observation, revises its request for a physical experiment, and gets
`HOLD` because the campaign has no approved physical route.

The replay visualizes the recorded composition in Isaac Sim. It is not a live
physical execution or a calibrated twin of the full workstation.

## Install for an agent

Install the runtime with Python 3.11 or later:

```bash
python -m pip install dynamical-cli
```

Add the Codex plugin:

```bash
codex plugin marketplace add Dynamical-Systems-Research/dynamical-cli --json
codex plugin add dynamical@dynamical-systems-research --json
```

The plugin gives Codex the `dynamical` skill. The same portable skill can run in
Claude Code and other compatible agent environments:

```bash
git clone https://github.com/Dynamical-Systems-Research/dynamical-cli.git
cd dynamical-cli
npx skills add . --skill dynamical --global --copy --yes
```

The repository does not include an MCP server.

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

## Instrument onboarding

The included
[`dynamical-instrument`](https://github.com/Dynamical-Systems-Research/dynamical-cli/blob/main/skills/dynamical-instrument/SKILL.md)
skill turns source evidence into a candidate integration. It works from manuals,
interfaces, calibration records, operating limits, safety controls, facility
authority, and licensed assets.

An integration contains 3 reviewable parts:

1. An instrument skill that describes capabilities, procedures, limits, and recovery.
2. An adapter that binds the contract to a simulator or physical instrument.
3. A typed contract for actions, observations, units, state, errors, and provenance.

Conformance tests check the candidate. The facility decides which capabilities
and physical routes to approve. An integration cannot admit itself. Missing
calibration, licensing, safety, or authority remains an explicit review item.

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
