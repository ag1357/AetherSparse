# AetherSparse v0.5 flat binary pack status

Status: `CANONICAL_256_DOCUMENT_FIXTURE_VERIFIED`

The eligible compact flat structured fixture is
`flat-structured-256-final-r1.aeth`. It was rebuilt from the checksum-verified
canonical 10k SQLite pack and contains no cognitive-cell topology.

- Canonical series:
  `simplewiki_real_corpus_v050_20260701_e7a60c622d86dd01`
- Parent SQLite SHA-256:
  `cb93db732eaf314806700b38ef7ec9d5cf85dea69f32deb51f97a3aa890023e5`
- Documents: 256
- Claims: 2,187
- Exact source bindings: 9,333
- Binary bytes: 64,031,839
- Binary SHA-256:
  `adcd68ea0bb11bc41d588b41f2688ac55c49868492a982fad2bbeedd803d36b2`
- Root-manifest SHA-256:
  `bb459d2c57ea839e9df78ac8e18dccd4f7328dc844a7016b5949fd4ab2b7bda6`
- Sections: 324 across 32 deterministic shards
- Full verification: all 324 sections and the complete 64,031,839 bytes passed

The external artifact `flat-structured-256-r1.aeth` is preserved but ineligible.
Its embedded series identity predates the final deterministic corpus series, so
it must not be used as v0.5 release or edge evidence. No existing artifact was
deleted.

The eligible binary bytes remain outside ordinary Git. Git tracks the complete
section/checksum manifest and deterministic build recipe.
