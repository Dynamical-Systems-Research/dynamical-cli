---
name: dynamical-preflight
description: Build a source-bound map of a laboratory starting environment and freeze the smallest verified state for a Dynamical campaign. Use before composition when raw experimental, calibration, model, instrument, or facility records define the starting state. Return READY or a material HOLD without admitting providers or granting physical or qualification authority.
---

# Dynamical Preflight

Map the starting scientific environment, then freeze its supported campaign state.

```text
raw records and installed contracts
→ baseline environment map
→ deterministic finalizer
→ frozen state
→ Dynamical compose
```

This skill ends at the compose handoff. `$dynamical` owns campaign execution,
runtime lineage, replay, and branches. `$dynamical-instrument` assesses a missing
capability and can create only a pending proposal.

## Discover the bounded baseline

Read the scientific requirement first. Search supplied files, manifests, logs,
calibration reports, model records, facility records, and prior campaign artifacts.
Follow links that can change the starting state, reconstruction, evidence class,
authority route, or decision. Record discovery roots. Do not claim that unavailable
or undisclosed sources do not exist.

Capture when available:

- material, sample, lot, custody, process, run, result, and location identity;
- environmental conditions and ordered process history;
- instrument instance, components, configuration, setpoints, readbacks, software,
  calibration identity, validity, geometry, and uncertainty;
- process and instrument model artifacts, runtime, fit data, independent validation,
  uncertainty, and supported domain; and
- facility state, installed registry state, raw-data links, reduction level, failures,
  and prior decisions.

Keep raw data outside the plugin. Reference it by path or URI. Do not modify, copy,
impute, or silently normalize it. Treat MHS, drivers, workbenches, notebooks, and
sensor systems as upstream sources. Do not recreate their interfaces.

Keep setpoints, readbacks, and observations separate. An observation does not
become an applied command value.

## Write one small map

The transient JSON map contains only:

- `created_at_utc` and `discovery_roots`;
- `sources`: `ref`, one of `path` or `uri`, `available_at`, and `disposition` as
  `state`, `context`, or `excluded`; include owner, license, time range, reduction
  level, or exclusion reason when supported. Code supplies local hash, size, and
  format. Remote sources must supply them.
- `entities`: `ref`, `kind`, source-native identifiers, and evidence references;
- `facts`: each value once with `ref`, `subject_ref`, `field`, `value`, `kind`,
  `available_at`, and evidence references. Add source-backed unit, uncertainty,
  observation time, validity, or derivation. Add `state_path` only when the fact is
  part of frozen state. Add an optional requirement pointer when it binds a current
  campaign input.
- `relations`: source-bound subject, predicate, object, join status, availability,
  and evidence. Add explicit time or sequence when order matters. Set
  `state_defining: true` only for a frozen-state relation.
- `gaps`: missing, conflicting, stale, or unverified information, material effect,
  release condition, and optional user question or next route; and
- optional `requested_cutoff` and `parent`.

Use short transient `ref` values only for links. Evidence references name a
`source_ref` and can add a source-local `locator`, such as a JSON pointer, record ID,
table row, HDF5 path, or log event. Use `declared`, `setpoint`, `readback`,
`observation`, `derived`, or `assumption` as fact kinds. Preserve ambiguous joins and
known sides of incomplete state.

## Resolve only material gaps

Ask the user only when a missing fact can change state, reconstruction, the campaign,
evidence class, or authority. Use the native question tool. Group one to three short
questions and state why each answer matters. Keep `HOLD` when the user cannot resolve
the gap. Route a real missing capability to `$dynamical-instrument`; do not admit it.

## Freeze once

Run the deterministic finalizer with exact compose inputs:

```bash
python skills/dynamical-preflight/scripts/validate_receipt.py mapping.json \
  --requirement requirement.yaml --registry registry.yaml \
  --facility facility.yaml --output preflight.json
```

The finalizer hashes sources, assigns IDs, resolves links, checks cutoff closure,
selects state facts and relations, derives `READY` or `HOLD`, binds the existing
registry and facility digests, and calculates the state identity. It does not decide
scientific meaning. Run it as documented. Read its implementation only if it reports
an unexpected failure. Do not edit or repair its receipt by hand.

For `READY`, return the receipt path, state ID, digest, cutoff, and:

```bash
dynamical compose <requirement> --preflight <receipt> -o <composition>
```

For `HOLD`, return the known state, material gaps, and next valid route. Never claim
provider admission, physical authority, exact replay, physical repetition, or
qualification from preflight alone.
