---
name: dynamical
description: Compose and run evidence-bound virtual laboratories from admitted capabilities for materials research. Use when an agent must investigate a question, hypothesis, or decision; compose a complete supported virtual laboratory or a purpose-built multi-instrument workflow; run sequential, high-concurrency, or batched adaptive counterfactual campaigns; replay or branch from hash-bound experiment snapshots; prepare validated trajectories for evaluation or post-training; compare matched virtual and physical evidence; request the next physical experiment; or preserve a HOLD result.
---

# Dynamical

## Start from the scientific objective

Use supplied context and capability metadata first. If a missing choice can
change the campaign, ask one concise intake round before creating a requirement.
Use the host's structured question tool when available; otherwise ask in chat.
Ask only for missing items from this list:

- Scientific question, hypothesis, or decision to resolve.
- Material, sample, processing, campaign, and service context.
- Existing computational, archived, and physical evidence.
- Search variables and safe bounds.
- Uncertainty that limits progress, rival hypotheses, required evidence, known
  measurement limits, and the observation that would change the conclusion or
  decision.
- Cost and time budget.
- Selection and stopping rules.

If the user does not supply evidence requirements, selection rules, or stopping
rules, propose bounded defaults from the objective and capability metadata in
the campaign brief and label the assumptions. The single confirmation approves
them.

Return a short campaign brief with the objective, context, limiting uncertainty,
rival hypotheses, observation that would change the conclusion or decision,
design space, available providers and evidence classes, budget, selection rule,
stopping rule, what the available evidence can establish, and what still
requires physical confirmation. Get one confirmation before execution or spend.
If the user already supplied and approved this complete contract, restate it and
continue. Do not request confirmation for each CLI command.

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

Compose admitted capabilities as a complete supported virtual laboratory or a
purpose-built multi-instrument workflow. Select the composition from the
scientific objective and available evidence. Keep each provider's evidence
class; the full composition is not itself a `calibrated_twin`.

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

## Run sequential, concurrent, or batched adaptive autoresearch

Run and validate the unmodified baseline first. Then use the confirmed plan:

1. Propose one sequential study or a bounded batch of `N` isolated concurrent
   arms. Derive `N` from the design space, budget, available compute, provider
   limits, comparison rule, and stopping rule. State the uncertainty or rival
   hypothesis each arm tests and how its result could change the conclusion or
   decision.
2. Give each arm immutable inputs and a private output directory and process.
3. Compose, compile, run, and validate each arm with the same Dynamical version
   and installed authority bundle.
4. Use an output as evidence only after its command exits and
   `dynamical validate` passes.
5. Compare only validated results under the confirmed selection rule.
6. Preserve every arm, receipt, validation result, and available hash.
7. Stop on convergence, budget, repeated invalid routes, or need for physical
   evidence.

Capture each command's structured stdout in its arm directory on first
execution; do not rerun or reconstruct a command only to preserve its receipt.
Use narrow structured queries to inspect only the capability, schema, receipt,
and trace fields needed. When the host supports it, group each arm's ordered,
fail-closed pipeline into one tool call. Do not print complete capability
records, schemas, compositions, receipts, or traces into the agent context.

Sequential studies can use a promoted result to propose the next arm, but must
keep prior arm directories. Concurrent arms must not share mutable sample state
or result files. Run long simulations as background jobs; poll logs and process
state, and check final exit status before reading outputs.

For adaptive studies, validate and compare the current batch before planning the
next batch. Preserve the completed batch as a decision-point snapshot.

Run counterfactual arms only through admitted executable providers that support
the changed inputs. Archived replay can return only realized observations; it
cannot generate unseen outcomes.

Concurrent execution increases throughput, not evidentiary independence. Calls
to the same model or provider share its assumptions and evidence class.

Preserve each arm with one of these statuses:

- `promoted`: valid and selected by the confirmed rule.
- `not_promoted`: valid, including a valid negative result, but not selected.
- `HOLD`: structured domain-negative capability or authority result.
- `invalid`: an artifact exists but validation failed.
- `failed`: execution ended before it produced a valid artifact.

Record `HOLD`, invalid, and failed arms, but do not use them as scientific
evidence. Validate each `HOLD` receipt with `dynamical validate` and preserve the
validation result. If `HOLD` identifies an incomplete requirement, author a
corrected requirement without changing admission or authority. If no admitted
route exists, continue only after the missing evidence, provider, policy,
budget, safety condition, or authority changes.

If `HOLD` identifies a missing capability and source material is available, use
`$dynamical-instrument` with the requirement and `HOLD` receipt to assess or
prepare a pending proposal. The campaign remains `HOLD` until the installed
authority admits the provider; referral grants no admission, facility access,
or physical authority.

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

Replay reproduces the recorded campaign. To branch, create a new isolated arm
from the parent evidence, record the parent hashes, and declare the changed
inputs or research policy. Do not alter the parent artifacts.

For evaluation or post-training, preserve the requirements, immutable arm
inputs, agent transcript, receipts, validation results, status labels, and
available hashes. Keep held-out outcomes sealed from the agent and research
policy. Use these as source artifacts; do not create a second Dynamical schema.

When authorized physical evidence becomes available, compare it with matched
virtual observations for the same material or sample state, conditions,
quantity, and units. Report error, rank preservation, uncertainty coverage, and
validity-envelope failures only when the data support those comparisons. Keep
evidence classes separate, and do not use campaign data as independent
calibration.

## Return an agent-authored study report

Write one concise `study-report.json` from the preserved receipts and validated
traces. It is an agent-authored summary, not a CLI-validated schema or authority
record.

```json
{
  "document_type": "dynamical.agent-study-report",
  "study_id": "...",
  "objective": "...",
  "decision_limiting_uncertainty": "...",
  "selection_rule": "...",
  "budget": {},
  "arms": [
    {
      "arm_id": "...",
      "status": "promoted",
      "snapshot": {},
      "metrics": {},
      "decision_impact": "...",
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

Use `decision_impact` to state what the arm tested and whether its validated
result changed, confirmed, narrowed, or left the conclusion or decision
unresolved.

Validation checks structure, provenance, evidence, and authority. It does not
prove scientific truth or optimality. Inspect observation events and their
`observation.channels`; keep computational predictions, calibrated-model
outputs, archived observations, and physical measurements separate.
