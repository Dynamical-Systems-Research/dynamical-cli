# AMPERE-2 OER calibration — errata

The frozen protocol and pipeline outputs in this directory are preserved
byte-for-byte (their SHA-256 digests are cited in
`manifests/ac-electrodeposition-cell.yaml`). This errata records defects found
in independent review after the freeze. **None of them change the outcome: the
held-out gates FAILED and W2 is CLOSED.** Each correction below can only
reinforce that decision — none could reopen W2.

1. **Condition-split leakage (does not rescue W2).** The condition key hashes
   raw metadata strings, so upstream formatting differences (`0` vs `0.0`,
   `60` vs `60.0`) split two physically identical conditions across the
   fit/held-out boundary (uids 291–295 vs 316–318; 325 vs 323–324). A clean
   condition-level split would remove those rows from the held-out set. Split
   leakage biases held-out performance *optimistically*, so a correct split
   can only make the already-failed MAE and rank gates worse. The W2-closed
   decision is conservative under this defect.

2. **`validation_design` corrected to `cross_condition`.** The evidence record
   originally declared `independent_facility_runs`. The data is a
   cross-condition holdout within a *single* AMPERE-2 campaign (one lab, one
   instrument, contiguous days), which is `cross_condition`. The manifest now
   declares `cross_condition`, and `evaluate_calibration` closes W2 for that
   reason *in addition to* the failed gates. Bounded W2 requires independent
   physical facility runs, which this evidence is not.

3. **`uncertainty-coverage-2sigma` passes vacuously.** Declared sigma
   (1.8074 V) is the fit-residual standard deviation, inflated by four rows at
   the potentiostat compliance rail (uids 169 and 223, corrected potential
   ~10.63 V). At that sigma, 2σ coverage of 1.0 is trivial and is not evidence
   of calibrated uncertainty. It is reported for completeness; it does not
   support any W2 claim, and the two substantive gates (held-out MAE,
   candidate-order Spearman) both failed.

4. **Extraction column name.** `frozen_protocol.json` names the target column
   `Corrected Voltage (V)`; the raw CP CSVs actually carry
   `Corrected Working Electrode Voltage [V]`. `pipeline.py` used the correct
   column (verified by re-derivation); the protocol string is a typo. The
   pipeline, not the protocol prose, is authoritative for the column used.

5. **Freeze timestamp.** `frozen_protocol.json` records
   `frozen_at_utc: 2026-08-10T19:30:00Z`. The file's own mtime is ~19:13 UTC,
   before every pipeline output (~19:17 UTC), so the protocol was authored
   before any outcome existed — the ordering the freeze requires holds and is
   verifiable by mtime. The `19:30` string is an imprecise round; the file
   mtimes are the authoritative record of ordering.

6. **Model identifiability (rank 9 of 11).** No fit condition separates Mn
   from Cu, so `x_Mn` and `x_Cu` are identical across all 90 fit rows and the
   design matrix is rank-deficient. The published coefficients are a valid
   least-norm least-squares solution, but the individual Mn and Cu terms are
   not separately identified from this fit. This is another reason the model is
   W1 only.

7. **Spearman value.** The candidate-order Spearman in `calibration_report.json`
   (-0.18794) differs from a naive reimplementation (~-0.1750) only in
   floating-point tie-breaking of analytically tied per-current-density
   centered means. Both are far below the 0.7 threshold; the gate fails under
   either.
