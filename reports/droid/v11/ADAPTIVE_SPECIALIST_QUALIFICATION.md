# Mission 6 adaptive specialist qualification

> **Upstream continuation (2026-08-13).** This report records the original
> Mission 6 checkpoint. The subsequently supplied targeted handoff has now
> been integrated and independently revalidated across all 695 states. Strict
> certified reachability is 324/695 (46.6187%); even the non-certified legacy
> carry-forward counterfactual is only 410/695 (58.9928%). The greater-than-60%
> policy gate therefore remains closed. See
> `UPSTREAM_SEMANTIC_ADDRESS_QUALIFICATION.md` for the current decision and
> decomposition.

## Architecture decision

`UPSTREAM_REPRESENTATION_STILL_LIMITING`

The selected Work checkpoint is the v10 exact controller plus a bounded,
zero-parameter typed value scan over retained exact source spans. No learned
entity specialist, learned value specialist, probabilistic fusion method, or
adaptive-depth gate is active. New certified training reachability is
306/695 (44.0288%), below the strict 50% gate. A global AetherCore policy sweep
was therefore prohibited and was not run.

The result supports the architectural truth boundary—probabilistic
interpretation may be useful later, while factual surfaces remain exact—but it
does not yet justify the requested adaptive learned workspace.

## Required leading results

| Item | Empirical result |
|---|---|
| 1. Entity-binding recovery | Strict selected binding remains 0/346. The dev-fitted 9-parameter relevance baseline improves tuning top-1-any-required 67→75/193, but only 37/193 tuning replicas contain every required candidate. It is inactive. |
| 2. Value decomposition/recovery | Gold-evidence diagnostic: 27 compiler, 30 runtime, 11 binding, 21 blocked replicas. Actual replay-span repair recovers 46/89; 43 remain. |
| 3. Certified reachability | 260/695 (37.4101%) → 306/695 (44.0288%), +46 cases / +6.6187 percentage points. |
| 4. Best semantic correctness | The product-like measured baseline remains 52.2656% canonical answer accuracy. The new result is a training-side search-oracle reachability ceiling, not deployed or held-out accuracy. |
| 5. Provenance correctness | 46/46 credited repairs pass the exact verifier with source-copied surfaces. Eleven additional verifier/canonical matches are rejected as wrong-entity semantic answers. |
| 6. Selected specialist architecture | Static typed exact value enumeration feeding the unchanged exact controller; no learned specialist is retained. |
| 7. Selected fusion | None. All five fusion implementations remain inactive because no correctness-labeled fusion comparison was lawful. |
| 8. Selected depth/gating | One bounded typed-scan pass on supported answer shapes, then the existing controller. No learned gate or recurrent depth is selected. |
| 9. Active parameters | Selected runtime mean/p95: 0/0 learned parameters. Experimental artifacts retain 9 fitted entity scalars, inactive and not quantized for deployment. |
| 10. Cognitive cycles | On the 435 unresolved-case qualification cohort, the static scan activates for 208 cases: mean 0.4782, p95 1 scan cycle. Credited controller paths are 5/5 median/p95 micro-operations. |
| 11. P4 analytical cost | Active scan at conservative 200 MHz: 0.0431 ms mean / 0.0659 ms p95. Nominal 300 MHz: 0.0224/0.0340 ms. These are analytical projections, not hardware measurements. |
| 12. Observer findings | 8/8 controlled causal attributions, deterministic route hashes, 100% failure/high-uncertainty sampling, zero production imports, and 1,393-byte mean compact record. No learned hidden-state conclusion exists because no neural model was qualified. |
| 13. Exact unresolved bottleneck | 346 entity-binding cases need candidate generation/mention alignment/anchor occurrence statistics; 43 value cases need targeted pre-pruning and exact rebinding state. |

## Entity lane

`ENTITY_HARD_NEGATIVES_V11` freezes 346 development/tuning replicas grouped
into 175 case IDs with no cross-partition replica leakage.

| Observable residual | Replicas |
|---|---:|
| Correct entity absent | 153 |
| At least one required entity absent | 100 |
| Correct entity top-ranked but rejected | 54 |
| Correct entity present but misranked | 21 |
| Mention absent | 18 |

Only 75/346 replicas contain every required entity; tuning completeness is
37/193. A candidate scorer cannot recover an entity outside the set it is
given. Case-level required entity IDs also are not aligned to mentions, so
training a contextual 0.25M–5M specialist would create weak-label shortcuts.

The development-fitted linear baseline uses nine retained features and gives
these tuning results:

| Metric | Current | Linear baseline |
|---|---:|---:|
| Top-1 candidate is any required entity | 67/193 | 75/193 |
| NLL | 3.3733 | 0.4607 |
| Brier | 0.5245 | 0.1458 |
| ECE-10 | 0.5908 | 0.1189 |
| Strict selected binding | 0/193 | 0/193 |

At confidence threshold 0.4, its tuning coverage is 38.86% and selective risk
is still 50.67%. Calibration improvement is not semantic qualification. The
baseline remains inactive. Occurrence-level anchor rows exist in the v0.5
schema, but the tier SQLite packs were absent from Work; no anchor prior was
invented.

## Value lane

The 89 training residual replicas group into 34 unique cases. Diagnostic scans
over benchmark gold evidence enumerate 31/34 and retain 31/34 at late cap 64,
or 80/89 replicas. That result proves compiler potential, not replay-state
availability.

The integrated repair accepts no answer or correctness field. It scans only
source spans retained in replay, copies exact date/quantity/comparison/
quotation surfaces, preserves competing subject/relation hypotheses, keeps
existing claims first, and adds at most 64 total claims. All 435 previously
unresolved Mission 5 training states were rerun through both bounded best-first
and beam search at depth 12, 5,000 expansions, beam width 64, and argument cap
64.

| Reachability result | Count |
|---|---:|
| Previously certified | 260 |
| Newly recovered `VALUE_NOT_ENUMERATED` | 46 |
| Wrong-entity grounded/canonical matches rejected | 11 |
| Entity residual | 346 |
| Value residual | 43 |
| New total | 306/695 (44.0288%) |

Every credited new trajectory is the same five-operation exact path:
`ENUMERATE_CLAIMS`, `SELECT_CLAIM`, `BUILD_DIRECT_PLAN`, `VERIFY_PLAN`,
`ANSWER`.

No neural span model was trained. There are only 116 direct uniquely located
development spans, and the remaining typed-scan failure cluster consists of
tuning-only quotation fragments without a failing development example.

## Workspace and fusion

The shared workspace carries exact candidate pointers outside its bounded
latent and retains entity, relation, shape, and value distributions,
evidence-sufficiency belief, missing facets, disagreement, verifier state,
cycle count, and remaining compute. All updates enforce the compute budget and
cannot invent a candidate label.

Weighted-logit, temperature/product, precision-residual, neutral learned, and
top-k particle fusion were run label-free over all 2,055 eligible tier replicas
and 630 real multi-candidate mentions. No evaluation/final-held labels or
outcomes were consumed.

The weighted/product family reduces mean normalized entropy from 0.9969 to
0.4614 while changing top choice in only 0.1587%. Mean disagreement is 0.5766,
and controlled confident conflict is detected in only 68.89%. Precision
residual remains less confident under conflict, but there is no semantic label
to prove it correct. Lower entropy is therefore not a selection result. No
fusion method is retained.

## Gating and adaptive depth

The bounded router supports 1–6 cycles, at most three parallel specialists per
cycle, dependencies between sequential groups, expected-gain ranking, hard
MAC/read/cycle budgets, and deterministic route hashes. The analytical cost
model supports 200/300/400 MHz without sleeps or host throttling.

Mission 5 contains 695 eligible final failure rows but zero per-cycle workspace
snapshots or `halt now / +1 / +2` counterfactual outcomes. A learned expected
value-of-computation target cannot be reconstructed from final search results.
The gate/depth family remains inactive; recurrent processing and always-on
versus adaptive routing were not claimed as ablations.

## Analytical edge cost

Scope is the exact typed repair over all 435 old unresolved training replicas.
It activates on 208 cases.

| Metric | Result |
|---|---:|
| Stored/active learned parameters | 0 / 0 |
| Mean/p95 integer operations | 482.7 / 1,373.7 |
| Mean/p95 MACs | 0 / 0 |
| Mean/p95 source bytes | 363.6 / 1,036.7 |
| Peak estimated workspace RAM | 14,515 bytes |
| Model bytes | 0 |
| Work-host median/p95 per unresolved case | 0.2623 / 0.2859 ms |

| P4 scenario | Active mean | Active p95 |
|---|---:|---:|
| Conservative: 200 MHz, 20 MB/s PSRAM | 0.0431 ms | 0.0659 ms |
| Nominal: 300 MHz, 40 MB/s PSRAM | 0.0224 ms | 0.0340 ms |
| Optimistic plausible: 400 MHz, 80 MB/s PSRAM | 0.0120 ms | 0.0180 ms |

Projection assumes one scalar integer operation per cycle, sequential resident
PSRAM spans, no accelerator/SIMD credit, zero flash reads, and 256 bytes of
scratch per bounded candidate. Actual P4 measurements remain future work.

## Observer and registry

The observer is optional and has zero imports from production inference. It
retains all failures, high-uncertainty cases, and novel routes while allowing a
configurable confident-success sample; full activations are disabled by
default. Its controlled attribution battery identifies gate, expert, fusion,
insufficient/excessive depth, upstream state, missing evidence, and verifier
failures in 8/8 scenarios.

`config/architecture/aethercore-v11-integrated.registry.json` is sealed as
`aethercore.architecture-registry.v1`. Active modules are only the exact
controller and exact typed scan. Entity, fusion, and depth modules are
inactive; the observer is training-only. The deployed runtime cannot rewrite
code, weights, or this registry.

## Matched component qualification

| Configuration | Empirical outcome | Retained? |
|---|---|---|
| v10 baseline | 52.2656% canonical; 37.4101% training reachability | Yes |
| + anchor entity prior | Pack data absent; targeted exporter implemented | Not evaluated |
| + linear entity baseline | Better weak-label tuning calibration; strict binding 0 | No |
| + learned entity specialist | Candidate recall and mention labels insufficient | No |
| + typed value enumeration | 46 semantic/exact recoveries; reachability 44.0288% | Yes |
| + learned value specialist | Development spans insufficient | No |
| + probabilistic fusion | Label-free uncertainty behavior only | No |
| + adaptive gating/depth | Counterfactual cycle labels absent | No |
| Selected integrated Work system | v10 exact controller + static exact value scan | Yes, upstream-limited |

No evaluation/final-held result was run after selection because there is no
qualified learned architecture to freeze. Existing held-out replay also uses
oracle-injected evidence and would be controller isolation, not product
evaluation. No full S600 battery is justified.

## Required negative results

- Anchor priors: not falsified; occurrence packs were unavailable.
- Learned entity specialist: not justified and not trained.
- Neural value specialist: not justified and not trained.
- Precision fusion: not selected; no lawful semantic comparison exists.
- Adaptive depth and specialist gating: not trained because causal cycle labels
  are absent.
- Recurrent processing: not run because the reachability gate failed.
- Expert routing collapse: not measurable without trained experts.
- Observer activation analysis: no learned hidden-state conclusion; the causal
  and operational observer functions pass.
- Behavioral heuristics added: zero. The typed scan is a measured
  corpus-independent exact extraction baseline.

## Data and archival boundary

The private 44 KB entity hard-negative rows and 158 KB value diagnostic rows
are kept out of GitHub. Their manifests and SHA-256 identities are committed.
The connected repository guard independently rejected publishing private
row-level replay/corpus derivatives; no bypass was attempted.

The only justified external continuation is the minimal training-side capture
in `docs/reproduction/V11_TARGETED_DATA_HANDOFF.md`: occurrence-level anchor
statistics and mention alignment for the entity residual, plus pre-pruning
state for the 43 remaining value replicas. It requests no 100k rebuild, broad
retrieval run, evaluation/final labels, or final product battery.

## Reproduction and identities

```bash
python scripts/droid/v11_reachability_rerun.py \
  --bundle /path/to/controller-replay-3tier \
  --benchmark data/v050/benchmark/INDEPENDENT_NATURAL_QUERY_SET_V050_R1.json \
  --mission5-report reports/droid/v10/mission5-real-reachability.json.gz \
  --output reports/droid/v11/reachability-rerun.json \
  --max-depth 12 --max-expansions 5000 --beam-width 64

python scripts/droid/v11_p4_qualification.py \
  --bundle /path/to/controller-replay-3tier \
  --reachability-report reports/droid/v11/reachability-rerun.json \
  --output reports/droid/v11/p4-cost-qualification.json

python scripts/droid/v11_architecture_registry.py \
  --repository . \
  --output config/architecture/aethercore-v11-integrated.registry.json
```

- replay bundle SHA-256:
  `099cd28b5c2a090d1b08d1e07d39e59ae6583854971c539085741fbb369f0246`
- Mission 5 report SHA-256:
  `280b314b313b69c72583702898bf135b614d725405587725d4d5f047601327cd`
- reachability rerun SHA-256:
  `98e4149e415a75241d416d93733d2e8e2de9c932ea800b3669cde905f8768098`
- P4 cost report SHA-256:
  `00d419e0810c785936d0d84a91bf69cdf555422ed7b3d9877c20791235c73dff`
- observer qualification SHA-256:
  `99d552280f16a650fb713e64fc26701fc9aea7963634bf6a29b2559011448325`
- value diagnostic manifest SHA-256:
  `71457cf469d3726571626aedcc8f6cc2cb301072abb5333e2b221835f958726a`

The global policy sweep, quantization sweep, final-held evaluation, and final
S600 product battery were not run because the upstream representation gate
failed.
