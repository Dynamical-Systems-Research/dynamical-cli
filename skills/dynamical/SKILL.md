---
name: dynamical
description: Use Dynamical for autonomous research in materials and other physical systems. Start from a scientific question or engineering objective, construct and revise the required research environment, run adaptive virtual and approved physical experiments, preserve reproducible studies and branches, and use the resulting evidence for scientific decisions, model improvement, evaluation, and training.
---

# Dynamical

Use the campaign-planning sections only when starting or continuing a study.
For a direct interface operation such as capability inspection, compilation,
validation, or exact replay, perform the requested operation and preserve its
receipt without creating a study plan or report.

## Verify or set up the runtime before campaign reasoning

First determine whether an active campaign or preserved campaign state already
exists. Never install, update, or switch the runtime during an active campaign.
When continuing, replaying, or branching from a preserved campaign, keep its
recorded Dynamical version. If that version is unavailable, report the mismatch
and ask before changing the runtime.

Before planning a new campaign, check the executable and version:

```bash
command -v dynamical
dynamical --version
```

If the executable is present, use it. Do not update a working runtime unless the
user asks for an update or a required interface is missing.

If the executable is missing, check which supported installer is available:

```bash
command -v uv
command -v python3
python3 --version
```

Propose one setup command and get separate user approval before changing the
environment. If `uv` is installed, prefer its isolated tool environment:

```bash
uv tool install dynamical-cli
```

If `uv` is not installed and Python 3.11 or later is available, use:

```bash
python3 -m pip install --user dynamical-cli
```

After installation, run `command -v dynamical` and `dynamical --version` again.
Then inspect the required commands and capabilities. If setup fails, return the
exact failure. Do not choose another installer or version without approval.

Runtime setup approval covers only the setup command. It does not authorize
external spend, a network change, new provider authority, or physical
execution.

## Start from the scientific objective

Start from the scientist's objective and use supplied context when available.
Decide whether the work needs capability inspection, literature, datasets,
protocols, or experiments. When needed and permitted, search for and download
relevant sources within approved network and cost limits. Record the source,
version, and license, and hash downloaded data used in a campaign. External
sources can inform research policy and campaign inputs; they do not grant
provider admission, change evidence class, or authorize physical execution.
Record only assumptions that materially affect the objective, evidence
boundary, or execution envelope. Ask one concise question only when a missing
choice would materially change the objective or require new authority.

Begin reversible local virtual work without another confirmation. The agent owns
the scientific policy: it can choose and revise hypotheses, instruments,
providers, search variables, concurrency, and stopping as validated evidence
changes. Continue while the campaign remains within admitted capabilities and
the approved compute, cost, and network envelope. Ask before changing the
environment, incurring external spend, seeking new provider authority, or
executing a physical experiment.

## Establish the campaign root

For each new experimental campaign, use `$dynamical-preflight` after the
scientific objective and target decision are clear. Discover available
experimental records, calibration reports, model records, campaign artifacts,
registries, and facility metadata. Do not ask the user to find or manage
metadata that is available from these sources.

Use the preflight receipt as the campaign starting state. Compose only when it
returns `READY`. If it reports a missing capability or unsupported calibration
evidence, use `$dynamical-instrument` to assess the smallest pending proposal.
Do not ask the user to call another Dynamical skill.

```bash
dynamical compose <requirement> --preflight <receipt> -o <composition>
```

Do not rerun preflight for a direct interface operation or an unchanged
continuation, replay, or branch. Run it again when the sample, process,
instrument, calibration, model, facility, or evidence cutoff changes.

## Inspect and operate the installed interface

Use the verified executable from the setup step. Do not install, update, or
select another runtime during a campaign. Select each command in this order:

1. An exact `next_command` from a receipt.
2. An exact command from a supplied example.
3. An exact command in this skill.
4. Targeted subcommand help only when the required syntax is still unknown.

Do not run top-level `dynamical --help` before the first valid attempt. If a
requirement or example names an operation, inspect it directly:

```bash
dynamical capabilities --operation <operation-id> --json
```

Use plain `dynamical capabilities` only to discover an unknown operation ID.
Never run `dynamical capabilities --json` without `--operation`.

Use `dynamical compose --schema` only when command help and operation detail do
not resolve a required field. Inspect only the relevant section.

Capability detail places the operation under `.operation` and provider records
under `.providers`. The operation ID is `.operation.operation_id`. Provider
fields include `.provider_id`, `.evidence_class`, `.admission`, `.availability`,
`.policy`, and `.validity_envelope`. Project only the needed input ports,
parameters, provider fields, and validity limits. Unless authority provenance is
the question, reduce providers to ID, evidence class, admission status,
availability, permission, and validity limits. Reduce ports and parameters to
their ID or name, type, unit, required state, and numeric limits. If a
projection returns `null`, inspect the top-level keys and correct the query; do
not fall back to printing the complete capability record. Use this documented
shape first. Do not inspect keys before the first projection or query the same
capability twice unless the first result is missing a required field.

Use the five commands:

- `dynamical capabilities` inspects capabilities, providers, and admission.
- `dynamical compose` binds a requirement to admitted providers.
- `dynamical compile` creates backend artifacts from a valid composition.
- `dynamical run` runs simulation or replay.
- `dynamical validate` validates a composition, world, trace, or replay.

For a normal new campaign, use this direct path:

```bash
dynamical capabilities --operation <operation-id> --json
dynamical compose <requirement> --preflight <receipt> -o <composition>
dynamical compile <composition> -o <compiled-world>
dynamical run <compiled-world> -o <trace>
dynamical validate <trace> --json
```

Compose admitted capabilities as a complete supported virtual laboratory or a
purpose-built multi-instrument workflow. Select the composition from the
scientific objective and available evidence. Keep each provider's evidence
class; the full composition is not itself a `calibrated_twin`.

The agent controls research policy. Dynamical controls admission, safety,
evidence, cost, and authority. Do not bypass rejected providers, constraints,
budgets, or approval rules.

Treat action parameters as command provenance. Record the exact commanded value
as `requested`. Observations never supply an `applied` command value. Use a
different `applied` value only when the provider explicitly reports the value it
delivered; otherwise keep it equal to `requested`.

For one mobile sample, declare one `sample_state` campaign input with a stable
sample ID. Materialize it with an initial `transfer-sample` step, bind later
`sample.state` inputs to the original campaign input, and express chronology
with `depends_on`. Add explicit `transfer-sample` steps before workstation
changes. Omit `facility_id` for the mobile sample. Do not add implicit transport
to a route that already contains explicit transfer steps; Dynamical carries
current state and location by sample identity.

For a physical request, set `minimum_evidence_class: physical` on every custody,
transfer, preparation, synthesis, and measurement step. A physical measurement
step alone does not make the full campaign physical.

Replay a simulator trace directly:

```bash
dynamical run trace.ndjson --mode replay -o replay.ndjson
```

For an embodied trace, also pass both `--compiled-world` and
`--runtime-receipt`; one binding without the other is invalid.

### Continue from verified virtual state

Use restore only for a completed, validated simulate trace and admitted virtual
source and child worlds. Preserve the parent's frozen preflight state. Run
preflight again only when a state-defining input changed, then execute with the
same inputs:

```bash
dynamical run child-world \
  --restore-from parent.ndjson \
  --restore-world parent-world \
  --restore-at-event <observation-event-id> \
  --dry-run

dynamical run child-world \
  --restore-from parent.ndjson \
  --restore-world parent-world \
  --restore-at-event <observation-event-id> \
  -o child.ndjson
```

Inspect the structured preflight receipt before execution. Preserve the parent
trace and world unchanged. Each child is a new campaign; parent actions validate
and derive its initial ledger but are not copied or counted as child actions.
Keep `source_evidence_classes` separate from the child `evidence_classes`.
Physical, embodied, `HOLD`, and user-supplied state restore are unsupported.
Repeat an exact child command only for safe reuse and require `"reused": true`
with unchanged trace bytes. Stop on any prefix, authority, model, binding, or
output conflict. Use `dynamical run --help` for the complete flag reference.

## Run adaptive autoresearch

Treat the first experiments as an initial study, not the complete campaign.
Choose sequential or concurrent work from the question, current evidence,
available compute, and provider limits. Revise the laboratory and research
policy as validated evidence changes.

For every experiment:

1. Give each arm immutable inputs and a private output directory and process.
2. Compose, compile, run, and validate it with the same Dynamical version and
   installed authority bundle.
3. Use an output as evidence only after its command exits and
   `dynamical validate` passes.
4. Compare only validated results. Check derived rankings and intervals
   mechanically before writing the report.
5. Preserve the arm, receipts, validation results, and available hashes.

Continue without renewed approval while the campaign stays inside admitted
capabilities and its approved compute, cost, network, and authority envelope.
Pause when the agent's scientific stopping condition is met or when progress
requires an environment change, external spend, new provider authority, or
physical execution.

Redirect each command's structured stdout to its receipt file on first
execution; do not use `tee`, which prints the complete receipt into context.
Do not rerun or reconstruct a command only to preserve its receipt. After the
command exits, use narrow structured queries to inspect only the capability,
schema, receipt, and trace fields needed. When the host supports it, group each
arm's ordered, fail-closed pipeline into one tool call. Do not print complete
capability records, schemas, compositions, receipts, or traces into the agent
context.
Trace files store `observation.channels` as a list of channel records. Select
records by `name`; do not treat the list as an object keyed by channel name.
Do not use `head`, `sed`, or `rg` on NDJSON traces, because each line is a
complete event. Use a structured query that returns only the required fields.

Sequential studies can use a promoted result to propose the next arm, but must
keep prior arm directories. Concurrent arms must not share mutable sample state
or result files. Use a host-supplied executor when available. Otherwise, run
long simulations as background jobs; poll logs and process state, and check
final exit status before reading outputs.

Validate completed experiments before they change the next decision. Preserve a
decision-point snapshot when evidence changes the research policy.

Run counterfactual arms only through admitted executable providers that support
the changed inputs. Archived replay can return only realized observations; it
cannot generate unseen outcomes.

Concurrent execution increases throughput, not evidentiary independence. Calls
to the same model or provider share its assumptions and evidence class.

Preserve each arm with one of these statuses:

- `promoted`: valid and selected by the current documented decision rule.
- `not_promoted`: valid, including a valid negative result, but not selected.
- `HOLD`: structured domain-negative capability or authority result.
- `invalid`: an artifact exists but validation failed.
- `failed`: execution ended before it produced a valid artifact.

Record `HOLD`, invalid, and failed arms, but do not use them as scientific
evidence. Preserve every `HOLD` receipt. If the receipt names a saved output,
validate that output with `dynamical validate` and preserve the validation
result. Do not pass the command receipt itself to `dynamical validate`. If
`HOLD` identifies an incomplete requirement, author a corrected requirement
without changing admission or authority. Do not resubmit the same requirement
unchanged; a repeated `HOLD` means the requirement must change or the campaign
must stop. If no admitted route exists, continue
only after the missing evidence, provider, policy, budget, safety condition, or
authority changes.

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

For a multi-arm or adaptive study, write one concise `study-report.json` from
the preserved receipts and validated traces. It is an agent-authored summary,
not a CLI-validated schema or authority record.

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
