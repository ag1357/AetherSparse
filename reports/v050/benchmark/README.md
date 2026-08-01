# v0.5 benchmark audit

`INDEPENDENT_NATURAL_QUERY_SET_V050_R1_PROVENANCE_AUDIT.json` was produced by
the isolated provenance-auditor process against the checksum-pinned 10k SQLite
pack. It re-sliced all 1,770 evidence spans from immutable page text, recomputed
document and span SHA-256 values, verified answer-surface bindings, checked role
separation, checked all 19 categories, and confirmed zero tuning/evaluation
article overlap.

The audit is evidence about the new v0.5 R1 series only. It does not reconstruct
or rename the unavailable v0.4.1 benchmark.
