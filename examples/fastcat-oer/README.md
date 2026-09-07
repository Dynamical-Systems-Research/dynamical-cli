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
preserve the evidence behind the physical measurement it requests next, or its
reason for requesting none.

This example records one nominal chemical-bath composition and queries its
frozen oxygen evolution reaction (OER) prediction at `0.010 A/cm^2`. Both
operations use the FastCat facility and the same sample identity. FastCat is a
DTU platform, separate from the Acceleration Consortium SDL1 bench.

The OER provider retains its historical calibrated-twin admission within the
frozen table. Its [calibration report](../../dynamical/bundle/fastcat/calibration/fastcat-oer/calibration_report.json)
records 22.2 mV MAE on the original 27-composition validation cohort and
48.4 mV MAE on a distinct 54-composition pool evaluation; 43 pool labels are
single runs. These cohorts are not interchangeable. No untouched FastCat
cohort remains for another held-out validation.

Run from this directory:

```bash
dynamical capabilities --facility fastcat --operation deposit-chemical-bath --json
dynamical capabilities --facility fastcat --operation measure-oer --json
dynamical compose requirement.yaml --facility fastcat -o composition.json
dynamical compile composition.json -o compiled-world
dynamical run compiled-world -o trace.ndjson
dynamical validate trace.ndjson --json
dynamical run trace.ndjson --mode replay -o replay.ndjson
dynamical validate replay.ndjson --json
```

Dynamical writes `root.usda` and the selected provider records to the compiled
directory. The trace must bind `measure-oer` to the installed `ac-oer-twin`
provider. For this input, the provider returns an overpotential of `0.263047 V`.
Its frozen conformal half-width is `0.104969 V`, with a 90% coverage target.
The same width applies to every admitted composition; it is not a
candidate-specific confidence score or proof of a physical ranking.

## What this example shows

| Operation | Provider | Evidence type | Declared limit |
| --- | --- | --- | --- |
| Chemical-bath deposition | `ac-bath-simulator` | `simulator` | Nominal fractions from 0 to 1; sum within 0.0025 of 1. Synthesis-time parameter from `0` to `3600 s`; these are adapter limits, not calibrated physical response. |
| OER measurement | `ac-oer-twin` | `calibrated_twin` | Frozen FastCat composition table at exactly `0.010 A/cm^2`. Inputs outside these limits are refused. |

The result is virtual evidence. Trace validation establishes the recorded
workflow and provider bindings. Choosing the lowest table prediction is a
finite comparison; it does not establish learning, a physical winner, or the
accuracy of a prospective measurement. A proposed physical experiment must
state the prediction and decision it would test.

The calibrated-twin claim applies only to the frozen OER predictor, not the
full facility, handling, geometry, motion, or time. Full reference-facility
calibration remains a release prerequisite. This example has no physical
execution authority.

`candidate-set.yaml` is a repository input for this example. It is not bundled
in the Python package and does not add capabilities or providers to the
installed authority records.
