# Cognitive Cell v0.4 status

**State:** `IMPLEMENTED_NOT_REAL_CORPUS_QUALIFIED`

The architectural realignment is implemented as a comparative gate. It restores
HKC-like cells without erasing exact grounding or the failed flat-index baseline.

Validated locally:

- four deterministic topology constructors;
- bounded cell membership;
- canonical cell-ID validation and invalid-ID rejection;
- binary VSA determinism, reversible XOR binding, bundling, permutation, and
  Hamming similarity;
- exact evidence ledger remains authoritative;
- seven adversarial mutation types are rejected by exact verification;
- mobile route inspection occurs through the external accessory API.

Not yet evidenced:

- cell recall at real 1k/10k/50k scale;
- article recall within selected cells;
- whether hybrid cells reduce the 7.33-point 1k-to-50k degradation;
- useful VSA gains over non-VSA cell routing;
- generative-address accuracy or update economics;
- adversarial learned-verifier accuracy.

The preserved release available in this workspace does not contain the large
SQLite corpus packs, so fabricating those measurements would be invalid. The
qualification CLI and exact commands are present for the retained packs.
