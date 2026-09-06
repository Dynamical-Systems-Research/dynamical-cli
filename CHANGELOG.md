# Changelog

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
