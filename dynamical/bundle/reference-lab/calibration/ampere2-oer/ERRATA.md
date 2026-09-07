# AMPERE-2 OER calibration — errata

The scientific method, thresholds, and numeric pipeline outputs remain unchanged.
The public metadata keys and prose were normalized after the freeze to use the
public evidence-class vocabulary; the v0.1.6 tag preserves the prior byte-level
records. This errata records defects found in independent review after the freeze.
**None of them change the outcome: the
held-out gates failed and calibrated-twin admission is denied.** These defects do not supply evidence for reversing that decision.

1. **Condition-split leakage does not change admission.** The condition key hashes
   raw metadata strings, so upstream formatting differences (`0` vs `0.0`,
   `60` vs `60.0`) split two physically identical conditions across the
   fit/held-out boundary (uids 291–295 vs 316–318; 325 vs 323–324). This compromises the independence of the reported holdout. The direction
   and magnitude of a corrected evaluation are unknown without a separately
   specified evaluation; neither improvement nor worsening follows from this
   defect alone. The existing failed result remains the admission record.

2. **`validation_design` corrected to `cross_condition`.** The evidence record
   originally declared `independent_facility_runs`. The data is a
   cross-condition holdout within a *single* AMPERE-2 campaign (one lab, one
   instrument, contiguous days), which is `cross_condition`. The manifest now
   declares `cross_condition`. This record does not support calibrated-twin
   admission because it does not use independent physical facility runs.

3. **`uncertainty-coverage-2sigma` passes vacuously.** Declared sigma
   (1.8074 V) is the fit-residual standard deviation, inflated by four rows at
   the potentiostat compliance rail (uids 169 and 223, corrected potential
   ~10.63 V). At that sigma, 2σ coverage of 1.0 is trivial and is not evidence
   of calibrated uncertainty. It is reported for completeness; it does not
   support a calibrated-twin claim, and the two substantive gates (held-out MAE,
   candidate-order Spearman) both failed.

4. **Extraction column name.** `frozen_protocol.json` names the target column
   `Corrected Voltage (V)`; the raw CP CSVs actually carry
   `Corrected Working Electrode Voltage [V]`. `pipeline.py` used the correct
   column (verified by re-derivation); the protocol string is a typo. The
   pipeline, not the protocol prose, is authoritative for the column used.

5. **Freeze chronology is not independently established.**
   `frozen_protocol.json` records `frozen_at_utc: 2026-08-10T19:30:00Z`.
   The earlier audit cited file mtimes near 19:13 UTC for the protocol and
   19:17 UTC for outputs. File mtimes can change and do not establish that
   the protocol preceded access to outcomes. An independently verifiable
   pre-outcome freeze receipt is not supplied by this bundle; chronology
   remains unverified. The recorded timestamp is preserved, not certified.

6. **Model identifiability (rank 9 of 11).** No fit condition separates Mn
   from Cu, so `x_Mn` and `x_Cu` are identical across all 90 fit rows and the
   design matrix is rank-deficient. The published coefficients are a valid
   least-norm least-squares solution, but the individual Mn and Cu terms are
   not separately identified from this fit. This is another reason the model is
   simulator evidence only.

7. **Spearman value.** The candidate-order Spearman in `calibration_report.json`
   (-0.18794) differs from a naive reimplementation (~-0.1750) only in
   floating-point tie-breaking of analytically tied per-current-density
   centered means. Both are far below the 0.7 threshold; the gate fails under
   either.
