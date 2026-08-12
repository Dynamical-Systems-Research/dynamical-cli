# Dynamical v0.1

## What Dynamical is

Dynamical is a compiler and runtime for campaign-specific, executable virtual
laboratories. It turns an engineering objective and current evidence into a
campaign made from admitted scientific capabilities and providers. If no valid
route meets the requirement, Dynamical returns `HOLD`.

## Agent and Dynamical responsibilities

The research agent controls the research policy. It selects what to
investigate and reports its rationale and uncertainty. Dynamical controls
provider admission, safety, evidence, cost, and execution authority. The agent
cannot admit its own provider or approve physical execution.

Use an output as evidence only after its command exits and `dynamical
validate` passes. A running log or partial trace is not evidence. Preserve
`HOLD` until the missing evidence, provider, policy, budget, safety condition,
or authority changes.

Validation checks structure, provenance, authority, and execution evidence. It
does not certify that an agent's scientific inference is correct or optimal.

## Five-command CLI

Inspect each command with `--help`. Use `capabilities` to inspect capability,
provider, and admission states, and `compose --schema` to inspect the public
requirement schema before authoring one.

```bash
dynamical capabilities --json
dynamical compose requirement.yaml -o composition.json
dynamical compile composition.json -o compiled-world
dynamical run compiled-world -o trace.ndjson
dynamical validate trace.ndjson --json
```

The registry spans eight operations, including chemical-bath film synthesis
(`deposit-chemical-bath`, W1 simulator) and OER measurement (`measure-oer`,
with both a W1 simulator and the calibrated `ac-oer-twin`).

The five commands are `capabilities`, `compose`, `compile`, `run`, and
`validate`. A `COMPILED` composition can continue to compilation. Saved
compositions carry the source metadata needed by later commands. Scientific
values are returned in `observation` events under `observation.channels` in
the NDJSON trace.

## Installation

From PyPI: `pip install dynamical-cli` (imports as `dynamical`).

The CLI and agent skill are separate installs. A skill or plugin install does
not install the `dynamical` executable.

Install the CLI from one full commit SHA:

```bash
uv tool install 'git+https://github.com/Dynamical-Systems-Research/dynamical-cli.git@<full-commit-sha>'
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

## Embodied execution with Isaac Sim

Isaac Sim 5.1 is a separate, GPU-bound NVIDIA install. Compiling with
`--target isaac` emits a compiled pack whose root stage is a source-backed
OpenUSD scene. Running the pack's `run_isaac_sim.py` with Isaac Sim's own
Python executes the identical campaign inside Omniverse Kit. Each action
advances the generated scene and the hash-bound admitted instrument model.
Both results enter one trace, including sample state and scientific output
channels. The compiled pack, runtime receipt, trace, and replay are bound by
verified hashes; `dynamical run
--mode replay` re-derives the observations from the recorded embodied
evidence and `dynamical validate` checks the full binding.

## Claim boundary

Passing composition, compilation, the live run, replay, and validation proves
source-backed, embodied, replayable **virtual** execution of the AC SDL1
electrodeposition facility. It does not prove physical fidelity, and none of
it should be read as a physical result.

One provider carries a stronger, narrowly bounded class: `ac-oer-twin` is
**calibrated_twin** for exactly one output (OER overpotential at
10 mA/cm^2 on Ni-foam chemical-bath films) on exactly the 72 compositions in
its packaged domain table, admitted against held-out physical measurements
(evidence in `registries/calibration/fastcat-oer/`). Outside that table it
refuses; the class does not extend to any other output, provider, or the
facility as a whole.

**Generating evidence is what this system is for.** Every run produces
observations with declared uncertainty, constraint margins, consumed cost and
duration, a sample lineage, and a replayable trace. That evidence is real
output and is the basis on which an agent revises a campaign and selects a
physical experiment. The limits below are about **what that evidence licenses
you to say about the physical world.**

- This is **W1** for the compiled, executable virtual SDL. It is **not** a
  calibrated digital twin.
- Simulator evidence is not physical evidence. A rendered instrument is not a
  calibrated instrument.
- Instrument models are first-principles idealizations with declared
  uncertainty unless a model's record cites calibration evidence derived from
  independent physical measurements, in which case the calibration record
  names exactly which variables, conditions, and operating ranges that
  evidence covers.
- **W2 claims are bounded by calibration evidence.** Any calibrated
  world-model claim extends only to the named variables, conditions, and
  operating ranges that passed the frozen held-out calibration gates recorded
  in the repository; everything else remains W1.
- **In this release W2 is closed everywhere.** The OER response is fitted to
  physical AMPERE-2 chronopotentiometry, but that evidence FAILED its frozen
  held-out gates (overpotential error and candidate-order preservation), so
  no calibrated-twin claim is made for any channel. The failed report is
  preserved verbatim in `registries/calibration/ampere2-oer/`, and the model
  carries the large declared uncertainty its fit residuals earned.
- Physical execution routes are admitted separately and remain `HOLD` in this
  release.

Geometry is tessellated at a recorded, disclosed tolerance chosen for
execution visualization and collision, not metrology; the referenced mesh,
not the manifest's declarative `dimensions_m` field, is the geometric record
of truth. A wheel install digest-verifies the *derived* USD layers against
the recorded source bindings; it does not re-verify the original source CAD,
which is not packaged.

## Source licensing and attribution

Dynamical source code is licensed under the
[Apache License 2.0](LICENSE). Redistributed third-party geometry and the
physical calibration dataset keep their own licenses and attributions: see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Machine-readable per-file
provenance, hashes, and license evidence, including recorded unresolved
license signals, live in `registries/electrodeposition-source-lock.json`.

## Agent skill and Codex plugin

The portable skill works with Codex and Claude Code. Check out the same full
commit SHA, then install the one shared skill:

```bash
git clone https://github.com/Dynamical-Systems-Research/dynamical-cli.git dynamical-cli-skill
git -C dynamical-cli-skill checkout --detach <full-commit-sha>
npx skills add ./dynamical-cli-skill --skill dynamical --agent codex --agent claude-code --global --copy --yes
```

For another agent harness, install the pinned CLI, load
`skills/dynamical/SKILL.md` from the same commit, and give the agent
filesystem and shell tools.

The Codex plugin supplies the same skill:

```bash
codex plugin marketplace add Dynamical-Systems-Research/dynamical-cli --ref <full-commit-sha> --json
codex plugin add dynamical@dynamical-systems-research --json
```

In all cases, confirm the CLI separately with `dynamical --help`. This
repository includes the portable skill, a thin plugin manifest, and its Codex
marketplace entry. It does not include an MCP server.

## Development tests

```bash
uv run pytest -q
uv run ruff check .
```
