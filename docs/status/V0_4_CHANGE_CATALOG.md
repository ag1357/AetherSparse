# AetherSparse v0.4 change catalog

## Baseline retained

- Phase 0 manifests, source snapshots, exact bindings, sentinels, external API,
  traces, and expected outputs remain unchanged.
- `REAL_CORPUS_ARCHITECTURE_FAILED` remains the frozen result for the conventional
  flat retrieval architecture: 84.10% article recall and 83.30% evidence-span
  recall for the compact reranker on the 10k/2,000-question run.
- The unpublished-metric concern is now resolved by the retained machine report
  `reports/EVIDENCE_SELECTION_10K_FULL.json`, which records 15.42% unsupported
  and 14.45% silent wrong-entity rates for the failed constrained answer path.
  These numbers describe that experiment only; the browser runtime fails closed.

## Added

| Path | Purpose |
|---|---|
| `src/aethersparse/cells/models.py` | Typed cells, routes, exact nodes, and dual working state |
| `src/aethersparse/cells/vsa.py` | Deterministic 1,024-bit binary VSA operations |
| `src/aethersparse/cells/topology.py` | Four bounded comparative topology builders |
| `src/aethersparse/cells/router.py` | Alias + lexical + VSA routing and generated-ID validation |
| `src/aethersparse/cells/qualification.py` | Cell recall, size, overlap, and byte metrics |
| `src/aethersparse/cells/adversarial.py` | Seven mutation families and exact-ledger verifier |
| `src/aethersparse/cells/pack.py` | Content-addressed blocks, root manifest, verification, and deltas |
| `src/aethersparse/cells/retrieval.py` | Bounded cell→article→chunk retrieval with VSA ablation |
| `src/aethersparse/cells/address.py` | Registry-validated generative-address experiment gate |
| `tests/cells/` | Contract, fail-closed, VSA, topology, and mutation tests |
| `/v3/cells/route` | Browser-accessible external-service routing trace |
| `aethersparse cells build/pack/qualify` | Reproducible topology, pack, and comparison commands |

## Explicitly not added

- no generative corpus index or assumption that 1–5M parameters memorize Wikipedia;
- no replacement of exact claim/provenance graphs by an HRR vector;
- no predictive prefetch model;
- no broad hyperlink traversal;
- no hardware port, accelerator selection, or new LRVM operation;
- no claim that the tiny-corpus tests qualify real-corpus cell topology.

## Research constraints

Pradeep et al. evaluated generative retrieval up to 8.8M passages and 11B
parameters and still characterized million-scale retrieval as unsolved. DSI++
identifies update cost and forgetting as core differentiable-index problems.
Accordingly, the generative-address interface is a registry-validated hint only.

## Remaining gate

Restore the frozen corpus databases and run the same question subsets against all
four topologies at 1k, 10k, and 50k. Measure cell recall@1/4/8, article recall
inside selected cells, maximum cell size, overlap, cell bytes, and scaling. No
topology is accepted until it reduces degradation without producing giant cells.
