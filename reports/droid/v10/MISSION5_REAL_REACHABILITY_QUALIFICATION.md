# Mission 5 real reachability and AetherCore policy qualification

## Decision

`MICRO_OP_TOOLSET_EXTENSION_REQUIRED`

The authenticated 10k/25k/397k replay gate recovered 260 of 695
training-eligible `D_CONTROLLER_FAILED` cases (37.4101%). This is below the
strict >60% `AETHERCORE_POLICY_FEASIBLE` threshold. No trajectory corpus,
learned policy, architecture sweep, quantization run, or final S600 battery was
started.

The one permitted toolset-extension analysis found no lawful generic operation
to add. The 435 unreached training failures are 346 `ENTITY_BINDING_WRONG` and
89 `VALUE_NOT_ENUMERATED`; both require compiler/retrieval knowledge repair,
not a policy micro-operation. Adding an operation that rewrites an entity or
invents a missing value would violate the provenance contract. The exact
qualification was therefore rerun unchanged. Both reports are byte-identical
with SHA-256:

`37a4e5cc075a676d6321271a14419ef540aa66f5a65e2a326c0e3d508bdb1c66`

The committed deterministic gzip has SHA-256:

`280b314b313b69c72583702898bf135b614d725405587725d4d5f047601327cd`

## Artifact and split integrity

- outer replay archive SHA-256:
  `572c4e3c4d210e058d9384571618e7fa4abcea7c91b9775e47f7451847ebc1ad`
- replay cases SHA-256:
  `1254196c179a8d87b9ce6c8301d4873fe1ddf836364a8e03e5b75b9b10c113aa`
- logical replay bundle SHA-256:
  `099cd28b5c2a090d1b08d1e07d39e59ae6583854971c539085741fbb369f0246`
- 6,150 cases, 54,477 decisions, 0 incomplete records
- 2,050 cases per tier; 813 development, 1,242 tuning, 3,057 evaluation,
  1,038 final-held
- all 2,055 development/tuning replicas are training-eligible
- all evaluation/final-held replicas are non-training
- no replay mapping contains an accepted answer or answer-label key

The replay was exported with candidate, ranking, and evidence oracles. It
contains oracle-injected evidence claims, including `span:oracle:*` claims on
held-out cases. The held-out experiment is therefore answer-label-blind
controller isolation over oracle evidence, not a fully gold-data-blind product
evaluation. Held-out results were excluded from the architecture gate.

## Real reachability result

| Metric | Result |
|---|---:|
| Current deterministic exact case accuracy | 64.1301% |
| Current deterministic canonical answer accuracy | 52.2656% |
| Training-eligible controller failures | 695 |
| Search-oracle reachable | 260 (37.4101%) |
| Search-oracle unresolved | 435 (62.5899%) |
| Search-oracle canonical ceiling over answer cases | 59.0365% |
| Held-out controller failures | 1,351 |
| Frozen gold-independent selection correct | 232 (17.1725%) |
| Held-out generated candidate-set oracle | 543 (40.1925%) |
| Policy-recoverable residual, all failure replicas | 1,058/2,046 (51.7107%) |

The gap between held-out frozen selection (232) and the generated candidate set
(543) is evidence that selecting among multiple verifier-supported distractors
remains difficult. A verifier proves grounding; it does not identify which
supported claim answers the benchmark question.

### By corpus tier

| Tier | Exact case baseline | Canonical answer baseline | Train oracle | Held-out blind | Held-out candidate set |
|---|---:|---:|---:|---:|---:|
| 10k | 69.9512% | 60.7031% | 78/196 (39.7959%) | 71/371 (19.1375%) | 157/371 (42.3181%) |
| 25k | 64.3902% | 52.4219% | 83/234 (35.4701%) | 77/444 (17.3423%) | 175/444 (39.4144%) |
| 397k | 58.0488% | 43.6719% | 99/265 (37.3585%) | 84/536 (15.6716%) | 211/536 (39.3657%) |

The three tiers agree on the 30–60% gate band and show no unexplained
discontinuity. A 100k replay/cache build is not justified.

### By answer shape

| Shape | Train oracle | Held-out blind | Held-out candidate set |
|---|---:|---:|---:|
| comparison | 0/69 | 16/203 | 41/203 |
| date | 80/119 | 92/193 | 146/193 |
| definition | 110/222 | 39/413 | 225/413 |
| list | 7/122 | 4/252 | 9/252 |
| quantity | 18/86 | 36/160 | 48/160 |
| quotation | 45/77 | 45/130 | 74/130 |

### Search and edge profile

- both bounded best-first and beam search were run at depth 12, 5,000 maximum
  expansions, beam width 64, and full 32-claim argument coverage
- best-first training oracle: 260; beam training oracle: 259
- median/p95 expanded states: 5/428
- median/p95 successful trajectory length: 5/8 actions
- 81,794 exact-verifier attempts; 2,713 rejected (3.3169%)
- median/p95 estimated P4 relative operations: 82/107
- median/p95 read-bearing actions: 0/0; the retained replay graph is already
  resident and the selected paths use compute-only/free micro-operations
- 188 selected trajectories repeat an operation; 292 repeated action instances,
  primarily multi-claim `SELECT_CLAIM`/`BIND_LIST_SLOT`
- operations 96/97 remain absent and illegal

## Residual classification

All 2,046 failed-answer replicas after bounded search:

| Class | Count | Policy interpretation |
|---|---:|---|
| `TOOLSET_REACHABLE` | 803 | at least one generated exact-verifier-certified canonical trajectory |
| `ENTITY_BINDING_WRONG` | 988 | compiler/linker defect; not a learned-policy failure |
| `VALUE_NOT_ENUMERATED` | 248 | missing value/claim; no micro-op may invent it |
| `COMPOSITION_OPERATOR_MISSING` | 7 | held-out-only diagnostic residue; no training support for a new primitive |

Training-only residual: 260 reachable, 346 entity-binding defects, and 89
non-enumerated values. No training failure supported adding a new generic
composition operation.

## 397k post-cap candidate diagnostic

Integrity passed for 2,050 unique cases and 158,816 candidate rows:

- diagnostic gzip SHA-256:
  `8dfb6c9a723a66d9dfd7d24a102a719a87b590a457fc1bab505cced771d57158`
- compressed/decompressed bytes: 12,632,762 / 55,002,948
- runtime correct: 999/2,050 (48.7317%)
- candidate pool min/median/p95/max: 48/83/88/96; mean 77.4712
- final top-eight distinct documents: median 5, mean 5.142
- 79 cases select from one document only; 249 select from at most two
- final top-one came from lexical top-one in 10.2439%, lexical top-eight in
  37.1220%; its lexical position median/p95 is 14/54
- final/lexical top-eight overlap mean 2.100; final/deterministic-score overlap
  5.9576; final/reranker overlap 6.1273
- 68/2,050 cases hit the exact 96-candidate cap, all misspellings; 5/68 are
  correct. This is a saturation risk, not proof that the cap caused failure.

All candidate-level `selected` booleans are false and are inconsistent with the
case-level selection. `selected_chunk_ids` is authoritative and equals
`ranking_order[:8]` in every case.

The artifact lacks pre-cap candidates, channel provenance, alias/redirect
resolution, semantic candidates, relevance labels, query text, canonical
answers, and evidence text. It cannot measure recall@k, MRR, alias generation,
semantic-union value, or counterfactual reranking accuracy.

Retrieval work remains secondary. If later policy/compiler work justifies a
focused capture, request pre-cap/channel/alias/redirect/semantic provenance and
relevance only for the 240 alias+redirect+misspelling cases (208 failures, 32
controls, including all 68 cap-saturated cases). The newline-terminated sorted
case-ID list has SHA-256
`0dad3dbbc54b19faad1ab9472597b0d390cd410e1b5a27d57aef0d1b5d2aceaf`.
Keep the 79 one-document top-eight cases as a separate concentration audit.

## Reproduction

```bash
aethersparse controller verify-replay \
  --bundle /path/to/controller-replay-3tier

aethersparse controller qualify-reachability \
  --bundle /path/to/controller-replay-3tier \
  --benchmark data/v050/benchmark/INDEPENDENT_NATURAL_QUERY_SET_V050_R1.json \
  --output reports/droid/v10/mission5-real-reachability.json \
  --max-depth 12 \
  --max-expansions 5000 \
  --beam-width 64
```

The committed `mission5-real-reachability.json.gz` report contains per-case
frozen selection hashes, search statistics, partition/tier/shape/category/
source-mode breakdowns, residual labels, verifier statistics, operation
distributions, and P4 estimates. Decompress it with `gzip -dk` for inspection.

## Final handoff

Mission 5 stops at the reachability gate. There is no learned AetherCore model
to freeze and no final S600 397k battery command to issue. The next justified
work is upstream entity-binding and missing-value/compiler repair under a new
mission; it must not be disguised as a policy micro-operation.
