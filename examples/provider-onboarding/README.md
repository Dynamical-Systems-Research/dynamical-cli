# Provider onboarding

This example is a proposal. It is not an installed provider. A provider supplies
one scientific operation. This proposal declares its input type, output type,
units, source, file hash, and limits.

Replace each `example` value with evidence from your model before review.

Inspect the proposal:

```bash
dynamical capabilities --registry registry.pending.yaml --operation estimate-band-gap --json
```

The result sets `registry_role` to `proposal`. It keeps
`example-band-gap-simulator` at `admission.status: pending`. It also reports that
the installed authority does not contain this operation.

Confirm that the proposal cannot admit itself:

```bash
dynamical compose requirement.yaml --registry registry.pending.yaml -o composition.json
```

The command must exit with status `1`. It must return `HOLD` with reason
`AUTHORITY_UNRECOGNIZED`. `HOLD` means that Dynamical stopped because required
evidence or authority is missing. The command must not create
`composition.json`.

The proposed operation accepts a string and returns a number in `eV`. The
provider supplies simulator evidence for `Si` only. It has no independent
calibration. A calibrated twin is a provider that passed a declared calibration
test within set limits. This proposal cannot claim calibrated-twin evidence. Its
policy is not permitted. It also lacks installed provider admission. It grants
no facility authority. It grants no physical authority.

[Install the Codex plugin or portable skill](../../README.md#install-dynamical).
Then use `$dynamical-instrument` with the real source and this `HOLD` result. Add
only records, tests, calibration evidence, and authority review items that the
source supports.
