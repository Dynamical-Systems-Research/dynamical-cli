# Campaign continuation: restore, branch, replay

Referenced from `SKILL.md`. Read when resuming, branching or replaying a
preserved campaign.

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
