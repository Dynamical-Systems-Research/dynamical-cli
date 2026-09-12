---
name: dynamical
description: Run evidence-grounded research campaigns on materials and other physical systems. Use when someone states a scientific objective or engineering decision and wants to build a virtual lab, run and adapt experiments, replay or branch a study, or decide what to measure next. Owns campaign execution, lineage, and reporting.
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
provider approval, change evidence class, or authorize physical execution.
Record only assumptions that materially affect the objective, evidence
boundary, or execution envelope. Ask one concise question only when a missing
choice would materially change the objective or require new authority.

Begin reversible local virtual work without another confirmation. The agent owns
the scientific policy: it can choose and revise hypotheses, instruments,
providers, search variables, concurrency, and stopping as validated evidence
changes. Continue while the campaign remains within approved capabilities and
the approved compute, cost, and network envelope. Ask before changing the
environment, incurring external spend, seeking new provider authority, or
executing a physical experiment.

## Establish the campaign root

For each new experimental campaign, use the `dynamical-preflight` skill after the
scientific objective and target decision are clear. Discover available
experimental records, calibration reports, model records, campaign artifacts,
registries, and facility metadata. Do not ask the user to find or manage
metadata that is available from these sources.

Use the preflight receipt as the campaign starting state. `compose` accepts only
a `READY` receipt; run the receipt's `next_command` as written. If preflight
reports a missing capability or unsupported calibration evidence, use the
`dynamical-instrument` skill to assess the smallest pending proposal. Do not ask
the user to call another Dynamical skill. Pass `--no-preflight --reason <text>`
only when the user explicitly waives the frozen starting state; the receipt
records the omission.

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

If a requirement or example names an operation, inspect it directly:

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

For a normal new campaign, start here and follow each receipt's `next_command`
through compose, compile, run, and validate:

```bash
dynamical capabilities --operation <operation-id> --json
dynamical preflight <mapping> --requirement <requirement> -o <receipt>
```

Compose approved capabilities as a complete supported virtual laboratory or a
purpose-built multi-instrument workflow. Select the composition from the
scientific objective and available evidence. Keep each provider's evidence
class; the full composition is not itself a `calibrated_twin`.

The agent controls research policy. Dynamical controls provider approval,
safety, evidence, cost, and authority. Do not bypass rejected providers,
constraints, budgets, or approval rules.

Treat action parameters as command provenance. Record the exact commanded value
as `requested`. Observations never supply an `applied` command value. Use the provider's explicit `applied` value; null means delivery is unknown.
Do not replace null with the requested value.

For one sample, declare one `sample_state` campaign input with a stable sample
ID and its source workstation in `facility_id`. Bind later `sample.state`
inputs to that input and express chronology with `depends_on`. Compose selects
the installed facility from that workstation; `--facility` explicitly overrides
selection. Use the same selector for `capabilities`. Add transport only when the
selected facility approves it and the physical workflow requires it.

For a physical request, set `minimum_evidence_class: physical` on every custody,
transfer, preparation, synthesis, and measurement step. A physical measurement
step alone does not make the full campaign physical.

A validated simulate trace's receipt names the replay command in
`next_command` and its restore point in `last_observation_event_id`.
`dynamical validate <trace> --compiled-world <parent-world> --child-world
<child-world>` returns a checked `branch_command` once the child world is
compiled; without those flags, validate returns the restore point as data. For
an embodied replay, pass both `--compiled-world` and `--runtime-receipt` to
`run`; one binding without the other is invalid.

Restore and replay rules for continuing a preserved campaign are in
`references/campaign-continuation.md`. Read it before resuming, branching or
replaying one.

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

Continue without renewed approval while the campaign stays inside approved
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

Run counterfactual arms only through approved executable providers that support
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
without changing approval or authority. Do not repeat an unchanged command after `HOLD`. Correct a wrong facility
selector when the receipt or capability discovery identifies it; changing the
selector does not require changing a scientifically valid requirement. If no approved route exists, continue
only after the missing evidence, provider, policy, budget, safety condition, or
authority changes.

If `HOLD` identifies a missing capability and source material is available, use
the `dynamical-instrument` skill with the requirement and `HOLD` receipt to assess or
prepare a pending proposal. The campaign remains `HOLD` until the installed
authority approves the provider; referral grants no approval, facility access,
or physical authority.

## Preserve snapshots and return the study report

Use existing receipt hashes as the experiment snapshot; do not create another
snapshot protocol. For a multi-arm or adaptive study, write one concise
agent-authored `study-report.json` from the preserved receipts and validated
traces. The snapshot fields, the full report format, the branch rules and the
physical-comparison rules are in `references/study-report.md`. Read it before
writing either artifact.

Validation checks structure, provenance, evidence, and authority. It does not
prove scientific truth or optimality. Keep computational predictions,
calibrated-model outputs, archived observations, and physical measurements
separate.
