# AMPERE-2 calibration revision — follow-on scope

Status: scoped after the six reference-lab fixes; no refitting, new physical data
collection or successful calibration is claimed. The release requirement remains
successful calibration of every reference facility's claimed physical behavior.
Version 0.1.21 is staged, not releasable under the current evidence.

## Why an AC-hosted bench does not validate this predictor

AC supplies the SDL1 hardware/control workflow represented by the pinned source
profile. The AMPERE-2 response model is Dynamical's ordinary-least-squares fit to
a separate DTU measurement dataset; it is not an AC-supplied calibrated model.
Hardware provenance and correct source commands do not establish its predictive
accuracy. [Model origin and implementation](../dynamical/instruments/ac_oer.py#L1),
[historical dataset/protocol](../dynamical/bundle/reference-lab/calibration/ampere2-oer/frozen_protocol.json#L5).

The preserved report records MAE 0.3812627 V against a 0.05 V gate and Spearman
−0.187938 against 0.7. Both failed. Sigma 1.8073715 V makes the observed two-sigma
coverage of 1.0 uninformative about useful precision. These values must remain
visible even if a later model succeeds.
[Failed gates](../dynamical/bundle/reference-lab/calibration/ampere2-oer/calibration_report.json#L131),
[uncertainty erratum](../dynamical/bundle/reference-lab/calibration/ampere2-oer/ERRATA.md#L25).

The current evidence identifies several defects, without establishing how much
of the error each caused:

| Defect or competing explanation | Evidence | What would resolve it |
|---|---|---|
| Target/protocol mismatch | Historical fit uses 20/50 mA/cm²; SDL1 headline is corrected potential at 10 mA/cm² after its full protocol | Establish matched substrate, conditioning, activation, reference scale, iR correction and aggregation; do not transfer FastCat protocol or labels |
| Split leakage and unknown chronology | Raw-string condition keys split two identical conditions; file mtimes do not establish pre-outcome freezing | Canonical physical-condition groups and an independently verifiable freeze/reveal record |
| QC/compliance artifacts | Historical erratum identifies compliance-rail rows inflating sigma | Predeclare QC and instrument-compliance treatment before fitting/evaluation; retain excluded rows and reasons |
| Inadequate or unidentifiable representation | Rank 9 of 11; Mn/Cu not separately identified, while deposition conditions are absent from the fitted response inputs | Fit-only identifiability/ablation checks and comparison with simple baselines; no causal attribution from the old failed test |
| Domain declaration exceeds enforcement | The failed estimator is no longer admitted to either facility; direct calls enforce the two current points but not fit-composition membership | Implement and test exact supported-condition admission and explicit out-of-domain refusal |

Sources: [protocol domain/model](../dynamical/bundle/reference-lab/calibration/ampere2-oer/frozen_protocol.json#L11),
[SDL1 observable](../dynamical/bundle/reference-lab/protocols/sdl1-oer.json#L271),
[leakage/chronology/identifiability](../dynamical/bundle/reference-lab/calibration/ampere2-oer/ERRATA.md#L10),
[legacy admission and response](../dynamical/instruments/ac_oer.py#L59).
A corrected split may improve or worsen metrics; its result is unknown.

## Five work packages, in order

1. **Freeze the claim and decision rubric.** Specify the physical decisions the
   prediction must support, observable, protocol, substrate/electrode roles,
   conditions, units, reference, correction and aggregation. Decide whether the
   revision supplies SDL1's 10 mA/cm² headline or remains a separately bounded
   20/50 mA/cm² AMPERE estimator. The latter alone cannot satisfy full SDL1
   calibration. Declare accuracy, ordering, uncertainty coverage and useful
   interval-width criteria before any new evaluation. Keep the old thresholds
   and failed result intact; no thresholds are approved or weakened by this
   scope. Use meaningful simple prediction baselines and measured repeatability
   where available. Do not call workflow completion or twin argmin learning.

2. **Establish admissible evidence and independence.** Inventory permitted data
   provenance, exposure history, units, replicates, instrument metadata and
   protocol compatibility without accessing protected outcome roots. Normalize
   numerically equivalent condition values before grouping; all replicates,
   current steps and time points from one physical condition stay together.
   Predeclare QC, missingness, compliance rails and exclusions. Identify truly
   independent physical validation runs and preserve their separation from
   model selection. No untouched matching validation cohort is established by
   this scope. If none is available, stop for new authorized evidence; do not
   recycle inspected rows as held-out validation or use mtimes as a freeze proof.

3. **Revise only against fit/development evidence.** Implement the source-faithful
   target extractor and enforce the supported physical domain, with tests for
   reference/units, aggregation, condition equivalence and refusal. Check
   identifiability and compare a small predeclared set of candidate models with
   simple baselines. Features must be available at prediction time. Separate
   epistemic prediction limits from physical measurement uncertainty. Reject
   fabricated delivery, film composition, temperature or CV/EIS observations.
   The old fit's exact causal failure decomposition is not assumed.

4. **Freeze and evaluate once on independent evidence.** Bind code, data lineage,
   grouping, extractor, model, uncertainty procedure, thresholds and evaluation
   design in a verifiable pre-outcome receipt. Model development must not access
   the validation outcomes. Report every planned metric, interval precision,
   cohort size, single/replicated labels, exclusions and failures with artifact
   hashes. If a gate fails, preserve it and keep admission/release denied. Further
   development needs a new evaluation plan and genuinely independent evidence;
   renaming the split or rerunning the same outcomes does not provide it.

5. **Revise the report and admission only after evidence supports them.** Add a
   separately identified revision report, with an explicit comparison to the
   preserved failed report and a precise explanation of any changed target or
   cohort. Grant only the validated domain through existing admission mechanisms.
   Run full project checks and a closed canary against the actual revised wheel;
   separately judge execution, source fidelity, predictive validity and agent
   interpretation. Release remains blocked until every facility-level dependency
   below is met and Jarrod explicitly approves public release actions.

## Full-facility calibration dependencies

A successful AMPERE fit alone is insufficient for the user's release requirement.
Software IDs and command bookkeeping need verification; every claimed physical
response needs appropriate independent calibration evidence. The present bundle
explicitly lacks the following physical qualifications:

| Claimed behavior to qualify | Required evidence before a calibrated claim |
|---|---|
| Pipette delivery and rinse/drain | Recorded delivered quantities, repeatability, destination/stock identity, residuals and uncertainty over the admitted conditions |
| Temperature and timed ultrasound | Reference-linked temperature/readback and actual exposure/timing records; controller setpoint is not achieved temperature or acoustic dose |
| Deposition | Delivered current/charge and temperature, substrate/area, film state and run history; precursor ratios are not deposited stoichiometry |
| Cleaning between deposition and test | Film retention/change and carryover measurements under the specified sequence; preserving state history does not prove unchanged film chemistry |
| OER activation/CV/EIS/staircase | Protocol-matched measured responses, reference identity/scale, resistance extraction and iR-corrected headline, with repeatability and calibrated response validity |
| Geometry or embodied execution, if claimed | Measured placement/clearance or actual execution evidence; schematic poses and mesh provenance are insufficient |
| FastCat facility | Preserve its frozen table/constant interval and original 27-versus-pool 54 distinction. No untouched FastCat cohort remains; any stronger or full-facility claim needs separately admissible independent evidence, not reuse of exhausted outcomes |

Current boundaries are documented in the
[traceability guide](../dynamical/bundle/TRACEABILITY.md),
[SDL1 field table](../dynamical/bundle/reference-lab/field-traceability.json),
[FastCat field table](../dynamical/bundle/fastcat/field-traceability.json), and
[FastCat aggregate report](../dynamical/bundle/fastcat/calibration/fastcat-oer/calibration_report.json#L14).
This table specifies evidence requirements; it does not claim those measurements
exist or authorize their collection.

## Authority, resources and stopping conditions

This deliverable scopes the follow-on only. New fitting, independent-data access,
physical measurements, compute spending and implementation changes are not
performed here. The $10 authorization covered the completed closed canaries;
follow-on data/compute resources must be concretely budgeted before execution.
Protected outcome roots remain off limits. No schema change, broad skill rewrite,
CLI learning/scoring subsystem, value-estimation program or website work is added.

A valid negative or inconclusive calibration result can close an evaluation, but
cannot satisfy the successful-calibration release gate. Missing independent data,
unverified reference/protocol, unidentifiable required effects or unmet physical
calibration criteria keep that gate closed. Success must be earned by the new
validation; it cannot be guaranteed by a plan or a report revision.
