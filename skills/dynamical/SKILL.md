---
name: dynamical
description: Investigate materials decisions with admitted Dynamical capabilities and evidence. Use when an agent must turn a scientific question into a confirmed campaign plan, run sequential or concurrent virtual experiments, inspect provider admission, validate and compare evidence, produce an agent-authored study report, replay a campaign, or preserve a HOLD decision.
---

# Dynamical

## Start from the scientific decision

Ask for or infer these items before creating a requirement:

- Decision the scientist must make.
- Material, sample, processing, campaign, and service context.
- Existing computational, archived, and physical evidence.
- Search variables and safe bounds.
- Required evidence and known measurement limits.
- Cost and time budget.
- Selection and stopping rules.

Return a short campaign brief with the objective, context, design space,
available providers and evidence classes, budget, selection rule, stopping rule,
what the available evidence can establish, and what still requires physical
confirmation. Get one confirmation before execution or spend. If the user
already supplied and approved this complete contract, restate it and continue.
Do not request confirmation for each CLI command.

## Inspect and operate the installed interface

Confirm that the CLI is installed and inspect only the needed command and
capability help:

```bash
command -v dynamical
dynamical --help
dynamical capabilities --json
dynamical capabilities --operation <operation-id> --json
dynamical compose --schema
```

Use the five commands:

- `dynamical capabilities` inspects capabilities, providers, and admission.
- `dynamical compose` binds a requirement to admitted providers.
- `dynamical compile` creates backend artifacts from a valid composition.
- `dynamical run` runs simulation or replay.
- `dynamical validate` validates a composition, world, trace, or replay.

Inspect the relevant command with `--help` before use. The agent controls
research policy. Dynamical controls admission, safety, evidence, cost, and
authority. Do not bypass rejected providers, constraints, budgets, or approval
rules.

For one mobile sample, declare one `sample_state` campaign input with a stable
sample ID. Materialize it with an initial `transfer-sample` step, bind later
`sample.state` inputs to the original campaign input, and express chronology
with `depends_on`. Add explicit `transfer-sample` steps before workstation
changes. Omit `facility_id` for the mobile sample. Do not add implicit transport
to a route that already contains explicit transfer steps; Dynamical carries
current state and location by sample identity.

Replay a simulator trace directly:

```bash
dynamical run trace.ndjson --mode replay -o replay.ndjson
```

For an embodied trace, also pass both `--compiled-world` and
`--runtime-receipt`; one binding without the other is invalid.

## Run sequential or concurrent autoresearch

Run and validate the unmodified baseline first. Then use the confirmed plan:

1. Propose one bounded sequential arm or several independent concurrent arms.
2. Give each arm immutable inputs and a private output directory and process.
3. Compose, compile, run, and validate each arm with the same Dynamical version
   and installed authority bundle.
4. Use an output as evidence only after its command exits and
   `dynamical validate` passes.
5. Compare only validated results under the confirmed selection rule.
6. Preserve every arm, receipt, validation result, and available hash.
7. Stop on convergence, budget, repeated invalid routes, or need for physical
   evidence.

Sequential studies can use a promoted result to propose the next arm, but must
keep prior arm directories. Concurrent arms must not share mutable sample state
or result files. Run long simulations as background jobs; poll logs and process
state, and check final exit status before reading outputs.

Preserve each arm with one of these statuses:

- `promoted`: valid and selected by the confirmed rule.
- `not_promoted`: valid, including a valid negative result, but not selected.
- `HOLD`: structured domain-negative capability or authority result.
- `invalid`: an artifact exists but validation failed.
- `failed`: execution ended before it produced a valid artifact.

Record `HOLD`, invalid, and failed arms, but do not use them as scientific
evidence. If `HOLD` identifies an incomplete requirement, author a corrected
requirement without changing admission or authority. If no admitted route
exists, continue only after the missing evidence, provider, policy, budget,
safety condition, or authority changes.

Modal is optional external orchestration. If the user selects it, use one
isolated function per arm, immutable inputs, private outputs, and the same
Dynamical version and authority bundle. Publish an arm output as evidence only
after validation. Do not add Modal to the Dynamical package or make it required
for local sequential or concurrent studies.

## Preserve experiment snapshots

Use existing receipt hashes as the experiment snapshot. Do not create another
snapshot protocol:

```json
{
  "composition_sha256": "...",
  "world_sha256": "...",
  "adapter_pack_sha256": "...",
  "trace_sha256": "...",
  "source_trace_sha256": "..."
}
```

Omit hashes that an arm did not produce.

## Return an agent-authored study report

Write one concise `study-report.json` from the preserved receipts and validated
traces. It is an agent-authored summary, not a CLI-validated schema or authority
record.

```json
{
  "document_type": "dynamical.agent-study-report",
  "study_id": "...",
  "objective": "...",
  "selection_rule": "...",
  "budget": {},
  "arms": [
    {
      "arm_id": "...",
      "status": "promoted",
      "snapshot": {},
      "metrics": {},
      "evidence_classes": [],
      "validation_reasons": []
    }
  ],
  "decision": "...",
  "supported_claim": "...",
  "rival_hypotheses": [],
  "uncertainty": {},
  "out_of_domain_results": [],
  "raw_evidence_references": [],
  "stopping_reason": "...",
  "next_physical_experiment": "...",
  "physical_execution_status": "HOLD"
}
```

Validation checks structure, provenance, evidence, and authority. It does not
prove scientific truth or optimality. Inspect observation events and their
`observation.channels`; keep computational predictions, calibrated-model
outputs, archived observations, and physical measurements separate.
