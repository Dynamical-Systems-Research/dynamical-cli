---
name: dynamical-instrument
description: Assess source evidence and create the smallest supported pending Dynamical integration for a computational model, experimental dataset, instrument, or facility interface. Use when an agent must map partial or complete source material to simulator, archived-replay, calibrated-twin, or physical-proposal contracts, or when a campaign HOLD identifies a missing capability. Keep evidence limits explicit; do not grant provider admission, facility approval, or physical authority.
---

# Dynamical Instrument

For a first proposal, start with the [provider-onboarding example](https://github.com/Dynamical-Systems-Research/dynamical-cli/tree/main/examples/provider-onboarding).

Match the requested scope. For an assessment, inventory the evidence, select the
supported route, and report gaps without editing files. For implementation,
create the smallest candidate integration that the supplied evidence supports.
The candidate can define and test an adapter. It cannot admit itself.

If a scientific objective, requirement, `HOLD` receipt, or study report is
supplied, use it to identify the needed capability and evidence gap. It does not
prove that the source can provide that capability.

## Inspect the existing contract

For implementation, work in a disposable branch or worktree. Preserve unrelated changes. Inspect the public interface first, then read only the contract files needed for this instrument:

- `dynamical capabilities --json` and `dynamical compose --schema` for the installed public interface.
- `dynamical/instruments/` for `InstrumentRequest`, `InstrumentResult`, registration, and one similar adapter.
- The relevant models in `dynamical/schema.py` and `dynamical/sources.py`.
- One relevant registry, manifest, and conformance test.
- The package allowlist in `pyproject.toml`.

Use these contracts. Do not create a second registry, plugin system, provider ABI, onboarding wizard, or restricted tool wrapper.

## Establish the evidence boundary

Inventory each supplied item before implementation:

- Source URI, revision, digest, owner, and license evidence.
- API, driver, protocol, or simulator interface and its test endpoint.
- Typed inputs, outputs, units, state, limits, failures, cost, and duration.
- Calibration dataset, frozen split and thresholds, covered variables, and operating range.
- Facility endpoint, safety limits, approvals, availability, and physical authority.
- CAD source, derivation, license, and tolerance when geometry is relevant.

Separate source facts from assumptions and missing evidence. Do not infer calibration from a manual, infer a license from availability, or turn an HTTP success response into scientific validity. Do not invent a type, unit, limit, uncertainty, failure mode, endpoint, or authority record.

## Select the onboarding route

Choose the smallest route that the supplied evidence supports:

| Supplied evidence | Candidate route | Boundary |
| --- | --- | --- |
| Executable computational model | `simulator` provider proposal | Repeated calls share model assumptions and are not independent evidence. |
| Historical experiments | Archived replay | Reproduce realized evidence only; do not invent unseen outcomes. |
| Model plus independent held-out validation | `calibrated_twin` candidate | Require uncertainty checks and a declared validity envelope. |
| Hardware API or driver | Physical provider proposal | Keep admission, safety review, approval, and execution authority with the facility. |

All routes are proposals until the installed authority accepts their complete
records. Historical data alone does not support a counterfactual twin, and a
working hardware API does not grant physical authority.

## Author the candidate

Author only the layers that the inventory supports. Omit unsupported layers and list their missing evidence. Do not write a simulator to replace a missing interface or test endpoint.

1. Define one provider-independent `Capability` for each supported scientific operation. Give every port and parameter an explicit type and unit. Record required conditions and possible failures.
2. Implement the smallest adapter only when the supplied evidence defines an executable interface or simulator endpoint. Return typed observations, uncertainty when supported, cost, duration, failure reasons, and sample-state changes. Refuse inputs outside the documented envelope; do not clamp them.
3. Add a `CapabilityProvider` proposal with `admission.status: pending` only when the evidence supports every required provider field. Otherwise omit the provider and report the missing fields.
4. Add facility records only when the evidence identifies the endpoint and bindings. Authority-bearing facility records remain proposals until an independent installed authority accepts them.
5. Bind each external artifact by digest and license. Mark unresolved assets `pending` or `unlicensed`. Do not generate substitute CAD or claim source geometry from a URI alone.
6. Add one minimal example. Exercise the adapter only when it exists; otherwise demonstrate the proposal reaching structured `HOLD`. Test observable types, limits, failures, sample-state effects, trace-compatible observations, and HOLD. Do not manufacture an admitted runtime trace, or test source text or fixed campaign order.

Use a supplied simulator only as candidate execution evidence. A simulator developed with the adapter is not independent verification of that adapter. Never operate physical hardware without separate facility authority.

## Validate without self-admission

Run the repository's targeted formatting, tests, package checks, and the relevant CLI path. Demonstrate only what the evidence supports:

- Candidate records pass the relevant schema and package checks.
- An implemented adapter accepts and returns the declared types and units, and documented limits fail closed.
- The proposal returns a structured `HOLD` when no admitted provider exists. If the CLI writes a composition artifact, it passes `dynamical validate`; otherwise preserve the direct HOLD receipt.
- A proposed provider cannot survive as admitted unless its complete authority-bearing record matches the installed authority bundle.
- Missing calibration, verification, licensing, facility approval, or physical authority remains explicit.

Do not route the example through an existing admitted provider to obtain a passing trace for the new candidate.

Do not change admission, safety, validation, or physical-routing code to make a candidate pass. If the current provider-independent contract cannot express a required, source-backed behavior, preserve the failing case and identify the smallest contract gap for human review.

## Return the contribution

Report:

- Candidate files, if any, and the source evidence each uses.
- Scientific questions the candidate can and cannot answer within its declared envelope.
- The supplied campaign gap it may address and the smallest bounded campaign that could test it.
- Capability operations and, when supported, the adapter entry point, provider ID, evidence class, and declared operating range. Mark omitted layers as absent.
- Exact example and conformance commands with results.
- Admission status: `pending` or `HOLD`.
- Missing independent verification, calibration gates, license evidence, facility approval, safety review, or physical authority.
- Claims supported by simulation, and claims that remain unproven about the physical instrument.

Do not call the contribution admitted, calibrated, physical, or safe unless the independent authority and evidence already exist in the installed Dynamical contracts.

## Naming

Identity is orthogonal to lineage. Name so that recalibration never renames.

- Operation: device-independent `verb-noun` (`measure-oer`, `deposit-chemical-bath`).
- Adapter module: `{family}_{device_or_process}.py` (`ac_oer_twin.py`). Never a dataset name.
- Provider: `{family}-{device_or_process}-{evidence_role}`; evidence-role suffixes: `-simulator` (simulator evidence), `-twin` (calibrated-twin evidence), physical providers end `-pending` until admitted.
- Model binding: `{provider}-model`.
- Calibration evidence: `{dataset}-{output}` (`fastcat-oer`); the ONLY place dataset names appear.
- Authority: `{org}-{authority_kind}-{evidence_scope}-{date}`.
- Registry id: `{org}-{family}-{contents}-{date}`, date bumped on every admission change.
