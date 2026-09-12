# Campaign continuation: restore, branch, replay

Referenced from `SKILL.md`. Read when resuming, branching or replaying a
preserved campaign.

### Continue from verified virtual state

Use restore only for a completed, validated simulate trace and approved virtual
source and child worlds. Preserve the parent's frozen preflight state. Run
preflight again only when a state-defining input changed. Let receipts supply
the commands: `dynamical validate` with `--compiled-world` and `--child-world`
returns the checked dry-run restore in `branch_command`, restoring at the
parent's last observation (edit only the event ID to branch earlier), and the
dry-run receipt names the executed restore. Inspect the restore check receipt
before executing.

Preserve the parent trace and world unchanged. Each child is a new campaign;
parent actions validate and derive its initial ledger but are not copied or
counted as child actions. Keep `source_evidence_classes` separate from the child
`evidence_classes`. Physical, embodied, `HOLD`, and user-supplied state restore
are unsupported. Repeat an exact child command only for safe reuse and require
`"reused": true` with unchanged trace bytes. Stop on any prefix, authority,
model, binding, or output conflict. `dynamical run --help` lists the restore
flags.
