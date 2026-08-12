# Dynamical

[![Release](https://img.shields.io/pypi/v/dynamical-cli.svg?label=release)](https://pypi.org/project/dynamical-cli/)
[![CI](https://github.com/Dynamical-Systems-Research/dynamical-cli/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Dynamical-Systems-Research/dynamical-cli/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://pypi.org/project/dynamical-cli/)
[![License](https://img.shields.io/pypi/l/dynamical-cli.svg)](https://github.com/Dynamical-Systems-Research/dynamical-cli/blob/main/LICENSE)

Dynamical helps materials R&D teams assemble verified virtual labs from real
facility capabilities, explore research campaigns, and run approved physical
experiments through one interface. It is infrastructure for physical
autoresearch.

The current release runs virtual labs and calibrated digital twins. Physical
execution requires a facility integration and approval.

## Quickstart

Install the CLI with Python 3.11 or later:

```bash
python -m pip install dynamical-cli
```

Download the example campaign and run the full virtual workflow:

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

Use `dynamical compose --schema` before you write a new requirement. Use
`dynamical capabilities --operation <operation-id> --json` to inspect the typed
contract for one operation.

## What Dynamical does

Dynamical gives a research system five commands:

- `capabilities` lists available operations, instrument models, and facility routes.
- `compose` maps a research requirement to compatible capabilities.
- `compile` creates a portable virtual lab and its execution contract.
- `run` executes a virtual campaign or replays a recorded campaign.
- `validate` checks the result, its provenance, and its execution record.

Each campaign uses the same action, observation, sample-lineage, and evidence
contracts across virtual and physical routes. A virtual result is simulation or
digital-twin output. A physical result can only come from an approved facility
execution.

## Instrument onboarding

Dynamical starts with evidence from the instrument and facility:

- Manuals and operating procedures.
- APIs, drivers, and simulator interfaces.
- Operating limits, failure modes, and safety controls.
- Calibration records and covered operating ranges.
- CAD, asset provenance, and license rights.

That evidence becomes three reviewable parts:

1. An instrument skill that describes capabilities, procedures, limits, and recovery.
2. An adapter that connects the contract to a simulator or physical instrument.
3. A typed contract for actions, observations, units, state, errors, and provenance.

Conformance tests check the integration. The facility then reviews the proposed
capabilities, evidence, and execution limits before approval. Approved parts enter
the verified instrument registry. An integration cannot approve itself.

Use the included
[`dynamical-instrument`](https://github.com/Dynamical-Systems-Research/dynamical-cli/blob/main/skills/dynamical-instrument/SKILL.md)
skill to prepare a candidate integration from source material. The skill preserves
missing calibration, licensing, safety, and facility evidence as explicit review
items.

## Architecture

Dynamical is the control plane. It composes capabilities, applies facility policy,
routes execution, and records evidence.

[OpenUSD](https://openusd.org/release/index.html) is the portable compiled world.
It carries the facility scene, instrument assets, and composition needed by each
campaign.

[NVIDIA Isaac Sim](https://developer.nvidia.com/isaac/sim) is the embodied
execution layer. It runs the compiled scene and connects physical state to the
campaign contract.

Instrument models produce scientific observations. These models can be simulators,
calibrated digital twins, or approved facility adapters. Each model declares its
inputs, outputs, units, limits, uncertainty, and evidence.

One hash-bound trace connects campaign actions, scene state, scientific
observations, sample lineage, and replay. The trace makes each result inspectable
without treating virtual output as a physical measurement.

## Agent use

The portable `dynamical` skill lets a research agent inspect capabilities, compose
a campaign, run it, and validate the returned evidence. Install the CLI and skill
from the same commit:

```bash
git clone https://github.com/Dynamical-Systems-Research/dynamical-cli.git
cd dynamical-cli
npx skills add . --skill dynamical --global --copy --yes
dynamical --help
```

The Codex plugin exposes the same skill:

```bash
codex plugin marketplace add Dynamical-Systems-Research/dynamical-cli --json
codex plugin add dynamical@dynamical-systems-research --json
```

The repository does not include an MCP server.

## Source and licensing

Dynamical is licensed under the
[Apache License 2.0](https://github.com/Dynamical-Systems-Research/dynamical-cli/blob/main/LICENSE).
Third-party geometry and calibration data keep their original licenses and
attribution. See
[THIRD_PARTY_NOTICES.md](https://github.com/Dynamical-Systems-Research/dynamical-cli/blob/main/THIRD_PARTY_NOTICES.md)
for details.

Machine-readable source records include artifact hashes, provenance, license
evidence, and known limits. A packaged derived asset does not replace its original
source or grant new rights.

## Development

```bash
git clone https://github.com/Dynamical-Systems-Research/dynamical-cli.git
cd dynamical-cli
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```
