# Reference workflow release scope

On 2026-09-07, Jarrod approved narrowing the release to the evidence available
from open sources. This supersedes the earlier requirement to calibrate every
physical behavior and defers the AMPERE-2 revision. Version 0.1.21 stays staged
until review is complete and publication is explicitly approved.

## What ships

- **SDL1: source-verified virtual workflow.** Published geometry, commands,
  electrode roles and measurement analysis trace to pinned AC sources. Unknown
  delivery, temperature, film properties and electrochemical responses remain
  explicit. Source fidelity does not certify an installed bench.
- **FastCat: bounded empirical predictor.** A separate frozen OER prediction
  table, limited to its admitted conditions. The 54-composition pool evaluation
  reports 48.4 mV MAE and 90.7% coverage with a fixed ±104.969 mV interval.
  Original validation was 22.2 mV on 27 compositions; 43 pool labels are single
  runs. The historical `calibrated_twin` label applies to that predictor only.
- **AMPERE-2: preserved failed evidence, no admitted operation.** Its MAE and
  ranking failures remain visible. Neither revised wording nor passing software
  checks changes those results or the failed FastCat interval gate.

## Release acceptance

1. Facility fields and protocol stages trace to pinned sources or explicitly
   identified software conventions and unknowns. No fabricated measurements.
2. Public examples compose against the correct facility, run, validate and replay;
   unsupported physical claims fail closed with usable recovery information.
3. Tests and source integrity checks pass. Behavior changes receive a closed
   canary on the revised wheel; its conclusions stay limited to what it exercised.
4. Documentation uses the two scopes above consistently. Keep the existing schema,
   provider contracts and tested prompt; add no calibration or evaluation subsystem.

Users connecting physical equipment must establish its configuration, calibration,
response validity and execution authority for their use. The reference examples
provide a reproducible baseline, not that physical qualification.

## Deferred AMPERE revision

Revisit only when protocol-matched independent evidence becomes available.
Freeze the target, domain, QC, uncertainty and acceptance criteria before outcome
access; develop on separate evidence and preserve every validation result.
The exhausted FastCat outcomes cannot supply a new independent test. No physical
acquisition, fitting or new validation program is required for this release.

Evidence: [source boundaries](../dynamical/bundle/TRACEABILITY.md),
[SDL1 protocol](../dynamical/bundle/reference-lab/protocols/sdl1-oer.json),
[AMPERE failures](../dynamical/bundle/reference-lab/calibration/ampere2-oer/calibration_report.json),
[FastCat report](../dynamical/bundle/fastcat/calibration/fastcat-oer/calibration_report.json),
and [software/canary validation](reference-lab-validation.md).
