# Changelog

## 0.1.21 (staged; not released)

Correct the public reference labs against pinned source evidence: SDL1 is a
source-verified virtual workflow; FastCat is a separate bounded empirical
predictor. Physical bench calibration is outside this release. AMPERE-2 and
the FastCat interval revision retain their failed results.

- Separate SDL1 and DTU FastCat facilities and registry IDs; SDL1 keeps only
  `ot2-liquid-handling`, FastCat uses `fastcat-process`. Remove the fictional
  transfer and cell-loading routes, echem-cell assets and geometry adapter.
- Compose selects the installed facility from the requirement's declared source
  workstation; `--facility` overrides selection. Registry defaults follow that
  selection. HOLD and capability errors explain facility discovery and recovery.
- Correct the SDL1 display name, Ni-tape working electrode and carried tool roles.
  Remove the old workstation/asset IDs and label schematic geometry assumptions.
- Replace ultrasound power percentage with Arduino temperature and timed-relay
  commands. Admit the explicit 30 s / 35 C well-conditioning recipe and
  cathodic −0.002827 A / 60 s deposition with Arduino temperature setup.
- Replace cleaning volume/time parameters with `use_acid` and `acid_dwell_s`;
  preserve deposit history. Pipettes dispense; pumps rinse/drain. Electrolyte
  volume retains its requested precision; mixture aliquots truncate to integer µL.
- Rename source-command output ports: ultrasound/temperature commands, nominal
  pipette volume and `commanded_charge_c`. Unknown physical delivery, temperature,
  deposited mass/thickness and residual liquid remain null. Source-command
  parameters have `applied: null`; stationary sample provenance remains recorded
  without inventing a sample-state write or quantity.
- Replace SDL1 `measure-oer`'s free current parameter with a fixed `protocol_id`.
  Bind setup, thirteen stages, reference/correction/aggregation and missing responses.
  Account for 550 s of known CP dwell as a campaign-time lower bound; additional
  CV/EIS and handling time remain unaccounted, and actual duration is unknown.
- Remove AMPERE `estimate-oer` from SDL1 admission and its misleading `measure-oer`
  dispatch alias. Historical fit coefficients and failed metrics stay intact;
  direct estimator calls now refuse unsupported currents without predictions.
- Update provider policy tags and command envelopes. Receipts include enum bounds
  when declared. Compiled action/channel enums remain facility-specific under
  the existing schema; the schema format and open-vocabulary semantics are unchanged.
  Both old pure-SDL1 and mixed-platform requirements may HOLD under corrected contracts.
- Correct FastCat accuracy/attribution: original 27-composition MAE 22.2 mV;
  later 54-composition MAE 48.4 mV with 43 single-run labels. Publish an aggregate
  receipt with study revisions and digest. Frozen table and ±104.969 mV width are unchanged.
- Date the AMPERE errata amendment and explicitly supersede the historical,
  unverified pre-outcome-freeze assertion without rewriting frozen records.
- Correct example routing and stale skill instructions; remove the unsupported
  reporting additions. Keep the public prompt and nine candidates unchanged.
- Separate substantive field evidence from software/unknown declarations. Remove
  circular citations and private paths from the public canary index; report
  execution, source fidelity, predictive validity and agent interpretation separately.

## 0.1.20

Closes gaps found by an audit of agent campaigns run with Dynamical (fatigue
qualification, water electrolysis, additive-alloy, critical-mineral and magnet
studies, 2026-08 to 2026-09). The tool stays a boundary: it records and checks,
the agent decides.

- `CampaignRequirement.prospective_ref` (optional): a digest and label of the
  agent's prospective record for the arm (the prediction it tests, the result
  that would change the decision). The compose receipt repeats it beside
  `composition_sha256`, so the prediction made before the arm ran joins to the
  composed artifact and, through it, to the trace. The composed artifact and
  its hash do not change. Dynamical never evaluates the record.
- Campaign skill, one sentence at the `HOLD` guidance: do not resubmit the same
  requirement unchanged; a repeated `HOLD` means the requirement must change
  or the campaign must stop.
- FastCat example and README: the next physical measurement is the one whose
  result would most change the decision, with the prediction it tests stated;
  the lowest point estimate is the virtual lead, not the decision.
- Study-report template in the campaign skill: `next_physical_experiment` may
  be a request or `none`; when it is `none`, `no_request_reason` is required.
  The OpenAI agent card and the FastCat example README say the same.

No command, exit code, schema version, trace schema, admission rule or evidence
class changed. Requirements without `prospective_ref` validate as before, compose to
byte-identical artifacts, and produce the same receipts. A calibration summary in the observation
record was considered and deferred: the trace's uncertainty object forbids
extra keys, so it belongs with the next schema revision.

### Study runs and the Dynamical version they used

| study | version | note |
|---|---|---|
| water electrolysis, 144 counted trajectories | wheel `0.1.2+fastcat2` (study build on commit e0cc858) | the calibrated OER twin first shipped in 0.1.3; the study build carried its own copy |
| additive-alloy fatigue value program (Sept 2026) | `0.1.19+amb20260902` runtime image | agent-authored providers admitted study-side |
| additive-alloy, critical-mineral, magnet portfolio cases | not recorded in this repository | see each study's own provenance |

## 0.1.19 and earlier

See the GitHub releases.
