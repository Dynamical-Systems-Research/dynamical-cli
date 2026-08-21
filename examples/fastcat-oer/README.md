# FastCat OER reference

Use this example to ask:

> Which catalyst composition should we synthesize and measure next to reduce
> uncertainty about which candidate has the lowest OER overpotential at
> 10 mA/cm²?

The public [`candidate-set.yaml`](candidate-set.yaml) supplies nine candidate
compositions, fixed test conditions, and the comparison contract. It contains
no archived physical outcomes. The checked-in `requirement.yaml` runs one
catalyst composition. It does not answer the comparison question by itself. An
agent must create one isolated requirement from that template for each supplied
candidate, validate every arm, compare only valid virtual evidence, and
preserve the evidence behind the physical measurement it requests next.

This example deposits one declared catalyst composition. It transfers the same
sample to an electrochemical station. It then loads the cell. It estimates the
oxygen evolution reaction (OER) overpotential at `0.010 A/cm^2`.

The OER provider is a calibrated twin. This means that it passed a declared
calibration test within set limits.

Run from this directory:

```bash
dynamical capabilities --operation deposit-chemical-bath --json
dynamical capabilities --operation measure-oer --json
dynamical compose requirement.yaml -o composition.json
dynamical compile composition.json -o compiled-world
dynamical run compiled-world -o trace.ndjson
dynamical validate trace.ndjson --json
dynamical run trace.ndjson --mode replay -o replay.ndjson
dynamical validate replay.ndjson --json
```

Dynamical writes `root.usda` and the selected provider records to the compiled
directory. The trace must bind `measure-oer` to the installed `ac-oer-twin`
provider. For this input, the provider returns an overpotential of `0.263047 V`.
It declares an uncertainty of `0.104969 V`.

## What this example shows

| Operation | Provider | Evidence type | Declared limit |
| --- | --- | --- | --- |
| Sample transfer | `ac-transfer-simulator` | `simulator` | Declared sample location and facility selection. |
| Chemical-bath deposition | `ac-bath-simulator` | `simulator` | Each fraction is from 0 to 1. All fractions must sum to 1. The time is from `0` to `3600 s`. |
| Cell loading | `ac-cell-loading-simulator` | `simulator` | Declared cell state. |
| OER measurement | `ac-oer-twin` | `calibrated_twin` | Frozen FastCat composition table at exactly `0.010 A/cm^2`. Inputs outside these limits are refused. |

The result is virtual evidence. The calibrated-twin claim applies only to the
OER measurement provider. It does not apply to the full laboratory. It does not
apply to sample handling, geometry, motion, or time. This example does not
select a physical provider. This example does not authorize hardware. It does
not operate hardware.

`candidate-set.yaml` is a repository input for this example. It is not bundled
in the Python package and does not add capabilities or providers to the
installed authority records.
