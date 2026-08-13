# Mission 6 entity specialist qualification

## Decision

`UPSTREAM_REPRESENTATION_STILL_LIMITING`

Do not train the 0.25M–5M contextual entity specialist from the Mission 5
replay. The replay is sufficient to freeze the entity residual and fit a
nine-parameter case-relevance baseline, but it is not sufficient for honest
mention-conditioned specialist training. The correct candidate is missing for
most residual replicas, and the case-level gold entity list is not aligned to
individual mentions.

No evaluation or final-held label was used. No behavioral heuristic was added.

## Frozen hard negatives

`ENTITY_HARD_NEGATIVES_V11` contains exactly the 346 Mission 5
`ENTITY_BINDING_WRONG` development/tuning replicas, grouped into 175 unique
case IDs. Replica groups remain wholly inside their original partition.

| Partition | Unique cases | Tier replicas |
|---|---:|---:|
| development | 74 | 153 |
| tuning | 101 | 193 |
| total | 175 | 346 |

The 346 replica failures decompose only as far as retained state permits:

| Observable class | Replicas |
|---|---:|
| correct entity not generated | 153 |
| at least one required entity not generated | 100 |
| correct entity top-ranked but rejected | 54 |
| correct entity present but misranked | 21 |
| mention not detected | 18 |

Only 75/346 (21.6763%) replicas contain every required entity anywhere in the
retained candidate sets. At unique-case level, just 55/175 (31.4286%) have a
candidate-complete tier replica. Candidate-complete tuning coverage is
37/193 (19.1710%). A bounded neural scorer cannot recover an entity that its
input candidate set does not contain.

The corpus records query, mention surface/offsets, retained candidates,
selected IDs, method, title, current name/relation/context scores, confidence,
margin, ambiguity count, discourse references, and case-level correct IDs.
It explicitly records unavailable fields, including occurrence counts, anchor
prior, raw alias/redirect support, edit similarity, actual entity types,
pre-cap candidates, and per-mention correct-entity alignment.

### Integrity

- hard-negative gzip SHA-256:
  `b544edbb46570d09c6efc415bd77806f24331efa655f93682ebab28c40ec33ec`
- decompressed JSON SHA-256:
  `6626c50dcb4526c09a54a2fedecd466e5151a12f61f673612ecdb83c6a649f85`
- development case-ID SHA-256:
  `bdd649e8f128c85501b6fd2706a158898ae3dc2c42eebec04b1e35b6d5e104b0`
- tuning case-ID SHA-256:
  `749261f13868782cd46865ede635f5df0962c512f1ae2d1d2d5b76bfc7ff6a98`
- Mission 5 replay bundle SHA-256:
  `099cd28b5c2a090d1b08d1e07d39e59ae6583854971c539085741fbb369f0246`

## Supported baselines

The residual definition makes current strict binding accuracy 0/346: every
case is missing at least one selected required entity. Candidate-pool metrics
remain informative.

The only newly fitted baseline is a nine-parameter logistic candidate-relevance
scorer. It was fitted on development with every unique case assigned unit total
weight, so cases with two or three tier replicas do not dominate training.
It uses only retained name, relation, context, and resolution-method features.
It does not treat the replay's constant `type_score=1.0` as actual type evidence.

| Tuning metric | Current score | Linear reranker |
|---|---:|---:|
| top-1 candidate is any required entity | 67/193 (34.7150%) | 75/193 (38.8601%) |
| all required entities in top 2 | 22/193 | 24/193 |
| all required entities in top 4 | 36/193 | 37/193 |
| single-entity top-1 over all residuals | 8/122 (6.5574%) | 13/122 (10.6557%) |
| single-entity top-1 when candidate generated | 8/26 (30.7692%) | 13/26 (50.0000%) |
| candidate-label NLL | 3.3733 | 0.4607 |
| candidate-label Brier | 0.5245 | 0.1458 |
| candidate-label ECE-10 | 0.5908 | 0.1189 |

These are weak case-level multi-label candidate metrics, not product entity
accuracy. There is no lawful way to simulate full multi-mention selection
without inventing a mention-to-gold assignment. The statistical reranker is a
useful measured baseline and calibration warning, not a deployable repair.

The remaining requested baselines are gated by evidence:

- anchor prior and entropy: blocked until the external occurrence-level pack is
  supplied;
- actual type compatibility: unsupported because entity types are absent;
- relation compatibility: already present as the current binary relation score;
- discourse compatibility: not labelable without mention/antecedent alignment;
- GBDT: not justified before candidate-generation recall is repaired.

## Anchor occurrence audit and recovery

The v0.5 SQLite schema does retain every hyperlink occurrence. Each `anchors`
row has a unique anchor ID, source document, target title, normalized surface,
raw offsets, raw link text, and source hash. The 10k manifest reports 779,447
anchor occurrences. The flat binary `AnchorRecord` likewise represents one
occurrence, not a distinct mention-target pair.

The actual tier SQLite files are external and absent from this Work checkpoint,
so no occurrence count or prior was invented. The implemented targeted exporter
reads the canonical pack read-only, restricts work to hard-negative mention
surfaces, and computes smoothed `P(e|m)`, ambiguity entropy, distinct-document
support, title signals, redirect support, and alias types. The checked-in
handoff identifies the exact schema and command. No broad S600 corpus job is
needed.

## Contextual specialist gate

Neural training was intentionally not started:

1. candidate-complete tuning coverage is only 19.1710%;
2. 271/346 replicas lack at least one required candidate or the mention;
3. correct IDs are case-level rather than mention-aligned;
4. candidate context and occurrence statistics are absent;
5. a contextual candidate scorer cannot repair missing candidate generation.

Training four nominal parameter sizes under these conditions would measure a
weak-label shortcut on a small, biased survivor subset. The next valid step is
the targeted anchor export plus mention-level candidate-generation capture.
Only then should the deterministic/statistical ladder be rerun and a contextual
specialist considered if a meaningful residual remains.

## Reproduction

```bash
python scripts/droid/v11_entity_specialist.py freeze \
  --mission5-report reports/droid/v10/mission5-real-reachability.json.gz \
  --replay-bundle /path/to/controller-replay-3tier \
  --benchmark data/v050/benchmark/INDEPENDENT_NATURAL_QUERY_SET_V050_R1.json \
  --output-directory reports/droid/v11

python scripts/droid/v11_entity_specialist.py anchor-export \
  --pack /path/to/tier-pack.sqlite \
  --hard-negatives reports/droid/v11/ENTITY_HARD_NEGATIVES_V11.json.gz \
  --output /output/entity-anchor-statistics-TIER.json.gz \
  --alpha 1.0
```

The first command is deterministic and rejects any corpus that is not exactly
346 replicas / 175 unique cases. The second command validates SQLite schema
version 500 and never opens the pack writable.
