# Experiment snapshots and the agent-authored study report

Referenced from `SKILL.md`. Read before writing a snapshot or a
`study-report.json`.

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

For a multi-arm or adaptive study, write one concise `study-report.json` from
the preserved receipts and validated traces. It is an agent-authored summary,
it has no validator and is not an authority record.

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
  "next_physical_experiment": "... or \"none\"",
  "no_request_reason": "required when next_physical_experiment is none",
  "physical_execution_status": "HOLD"
}
```

`next_physical_experiment` is either the next physical request or `"none"`.
When it is `"none"`, `no_request_reason` states why no physical measurement
would change the decision. Ending without a physical request is a valid
outcome of the study, not a failure.

Use `decision_impact` to state what the arm tested and whether its validated
result changed, confirmed, narrowed, or left the conclusion or decision
unresolved.

Validation checks structure, provenance, evidence, and authority. It does not
prove scientific truth or optimality. Inspect observation events and their
`observation.channels`; keep computational predictions, calibrated-model
outputs, archived observations, and physical measurements separate.
