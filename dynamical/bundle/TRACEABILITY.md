# Reference bundle evidence boundaries

The two `field-traceability.json` sidecars map every terminal field in their
facility and registry documents, including empty collections, to an explicit
source, classification, derivation and claim limit. SDL1 has 2,192 fields;
FastCat has 559. A JSON Pointer identifies the field; `value_sha256` binds its
canonical JSON value. These sidecars do not change the facility/registry schema.

The source catalog records file hashes and exact line intervals, worksheet cells
or diagram regions. A source with a revision/URI refers to that external version,
not the current checkout. Other `dynamical/` paths refer to files in this package
or repository. External public metadata and export scripts are identified by
URI and recorded hash; their presence does not authorize access to outcome data.
Historical records retain their original uncertainty and limitations.

Classifications distinguish upstream protocol, recorded measurement, recorded
provenance, Dynamical convention and unknown physical property. A source command
is not measured delivery. Geometry extent is not a physical measurement of an
installed bench. IDs, display poses, scheduling intervals and admission policies
are software choices. Empty physical properties and unavailable response values
supply no quantitative physical evidence. Source-file identity alone establishes
neither calibration nor scientific validity.

Tests check exact field coverage, stale values, duplicate keys, source resolution,
source locations and current repository source hashes. They do not certify the
truth of a source or automatically verify external records. Factual review must
read the referenced source and assess the field's derivation and claim limit.
The full SDL1 measurement stages and analysis rules also carry individual pinned
upstream references in `reference-lab/protocols/sdl1-oer.json`.

SDL1 presently represents source commands and unavailable physical responses.
AMPERE-2 remains a failed simulator fit; its original numerical failure and
historical protocol remain visible alongside the errata. FastCat preserves its
frozen predictions and constant interval, with separate original-validation and
later-pool results. Neither bundle establishes full physical facility calibration.
The successful-calibration release requirement remains unmet; version 0.1.21 is
staged only. A revised report needs new admissible validation evidence, not a
renamed evidence class, removed failure, or changed threshold.
