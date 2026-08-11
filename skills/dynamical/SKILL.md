---
name: dynamical
description: Operate Dynamical campaigns from an engineering objective and current evidence. Use when an agent must inspect scientific capabilities and provider admission states, compose a campaign, compile provider artifacts, run simulation or replay, validate evidence, or preserve a HOLD decision.
---

# Dynamical

Confirm that the CLI is installed, then read the engineering objective and current evidence before creating a requirement. Inspect the installed interface and capability and provider admission states:

```bash
command -v dynamical
dynamical --help
dynamical capabilities --help
dynamical capabilities --json
dynamical capabilities --operation <operation-id> --json
```

Before authoring an unfamiliar requirement, inspect its public schema:

```bash
dynamical compose --schema
```

For one mobile sample, declare one `sample_state` campaign input with a stable
sample ID. Materialize it with an initial `transfer-sample` step, bind later
`sample.state` inputs to the original campaign input, and express chronology
with `depends_on`. Add explicit `transfer-sample` steps before workstation
changes. Omit `facility_id` for the mobile sample. Do not thread
`sample.state.transferred` through every later step or add implicit transport
to a route that already contains explicit transfer steps; Dynamical carries
the current state and location by sample identity.

Use these five commands:

- `dynamical capabilities` inspects capabilities, providers, and their admission states.
- `dynamical compose` binds a requirement to admitted providers.
- `dynamical compile` creates backend artifacts from a valid composition.
- `dynamical run` runs simulation or replay.
- `dynamical validate` validates a composition, compiled world, trace, replay, or decision.

Inspect the relevant command with `--help` before use.

Replay a simulator trace directly:

```bash
dynamical run trace.ndjson --mode replay -o replay.ndjson
```

For an embodied trace, also pass both `--compiled-world` and
`--runtime-receipt`; one binding without the other is invalid.

Run long simulations as background jobs. Poll their logs and process state, and check the final exit status before using their outputs.

The agent controls research policy. Dynamical controls admission, safety, evidence, cost, and authority. Do not bypass rejected providers, constraints, budgets, or approval rules.

Use an output as evidence only after its command exits and dynamical validate passes. A running log or partial trace is not evidence.

Validation checks structure, provenance, evidence, and authority. It does not prove scientific truth or optimality.

Inspect `observation` events and their `observation.channels` in a returned NDJSON trace, then validate the trace.

Preserve each `HOLD` receipt. If its reasons identify an incomplete or invalid requirement, author a corrected requirement without changing admission or authority. If no admitted route exists, continue only after the missing evidence, provider, policy, budget, safety condition, or authority changes.
