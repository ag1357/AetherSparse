# Mission 6 upstream semantic-address qualification

Status: **complete**.  Decision:
`UPSTREAM_LIMIT_REMAINS_POLICY_SWEEP_BLOCKED`.

The selected Work checkpoint is the v10 exact controller plus Semantic Address Plane
v1 and the bounded exact value lattice.  The contextual entity/value specialists,
probabilistic fusion, learned adaptive depth, and AetherCore policy sweep remain
inactive because independently revalidated reachability is 46.6187%, below the strict
greater-than-60% gate.

## Handoff acceptance

`scripts/droid/v11_targeted_handoff_audit.py` verified all supplied compressed and
uncompressed payload identities, the exact source benchmark and Mission 5 report, and
the strict 6,150-case replay identity.  Entity rows contain only development/tuning
(153/193 replicas); value rows contain only development/tuning (16/27 replicas).
No evaluation or final-held label entered fitting, calibration, feature design,
architecture selection, or threshold choice.

The value handoff is complete for its 43-row target.  The entity handoff is authentic
but partial: it contains only the surviving raw 10k occurrence corpus, covers 126/152
raw mention surfaces, and still has no mention-level correct-entity alignment or
pre-cap candidate-generation state.  The detailed acceptance record is
`TARGETED_HANDOFF_AUDIT.md` and `targeted-handoff-audit.json`.

## Semantic Address Plane v1

The plane is a generic, immutable occurrence-backed address substrate.  It verifies
the external gzip/manifest, preserves canonical `as:v050:entity:*` IDs, exposes exact
alpha-smoothed `P(entity|mention)`, occurrence and distinct-source support, ambiguity
entropy, source diversity, title/redirect priors, alias channels, and retained
candidate rank/confidence annotations.  Unresolved probability mass is retained and
never renormalized into a false entity choice.

Runtime projection is bounded to eight addresses per mention and 64 frame entity IDs.
Existing selections are never removed, and no alternative is forced.  In the 695-state
rerun the maximum new addresses in one state is six; no address cap exhausts.

Measured data scope:

- 345 occurrence rows, 6,112 occurrences, 126 covered normalized surfaces;
- 133 canonical rows plus 212 unresolved target rows;
- 76 ambiguous mentions; mean resolved probability mass 0.703537;
- 108/126 covered mentions have at least one canonical address;
- candidate-complete entity residuals rise only 75→77 of 346, both additions in
  tuning 397k.

The plane deliberately cannot emit “outside cap” versus “never generated”: the
supplied post-cap replay makes those states observationally indistinguishable.

## Value plane

Trace attribution over all 43 residual replicas finds:

- 29 selected-source-chunk absences;
- 3 tuning-only dual compiler/runtime quotation extraction misses;
- 11 already-enumerated value sets requiring semantic address/controller assembly;
- 0 answer-shape, region-pruning, deduplication, value-cap, or exact-rebinding losses.

Generic corrections preserve local and frame entity/relation hypotheses together,
enumerate the primary address across every exact source region before competing
addresses consume the global cap, and normalize percent typography consistently in
atomic and compound canonical forms.  No gold value or source document enters the
constructor.

The targeted 43-row rerun makes 34 goals representable and certifies 32 trajectories:
14/16 development and 18/27 tuning.  The residual is three tuning extraction gaps,
six tuning source-span gaps, and two development bounded-search failures.  Because the
only target-present extraction residual is tuning-only, a neural value specialist is
not trained.

## Certified reachability

Every one of the same 695 development/tuning Mission 5 controller-failure states is
loaded from the authenticated replay, regenerated, and searched.  The original state
branch is retained as a deterministic alternative to the monotonic upstream-enriched
branch; search-oracle gold is used only after construction for training-only search and
posthoc decomposition.

| qualification | reachable | fraction |
|---|---:|---:|
| Mission 5 published | 260/695 | 37.4101% |
| Mission 6 published (46 new, 260 carried) | 306/695 | 44.0288% |
| New strict all-state revalidation | 324/695 | 46.6187% |
| Non-certified legacy carry-forward counterfactual | 410/695 | 58.9928% |

The strict rerun newly certifies 150 trajectories relative to the original Mission 5
per-case labels: 72 formerly `ENTITY_BINDING_WRONG` and 78 formerly
`VALUE_NOT_ENUMERATED`.

It also rejects 86 legacy `TOOLSET_REACHABLE` rows: their search found a canonical
value, but their regenerated frame lacks at least one required authoritative entity
address.  Mission 6 had carried those rows without independently revalidating semantic
binding.  The 410/695 counterfactual shows that even granting every legacy row would
still remain below the required 418/695.

The remaining 371 strict residuals decompose as:

| limiting plane | replicas |
|---|---:|
| `SEMANTIC_ADDRESS_GENERATION` | 355 |
| `EVIDENCE_RETRIEVAL` | 8 |
| `VALUE_AVAILABILITY` | 7 |
| `TOOLSET_CONTROLLER` | 1 |
| `RELATION_ADDRESSING` | 0 |
| `STATE_REPRESENTATION` | 0 |

The dominant limitation is therefore **SEMANTIC_ADDRESS_GENERATION**, not controller
architecture.  The next lawful data improvement is explicit mention alignment plus
pre-cap generation provenance and real 25k/397k occurrence packs.  Another policy,
fusion, or adaptive-depth sweep is not authorized by the measured gate.

## Specialist readiness

The split-safe readiness gate returns
`BLOCK_CONTEXTUAL_ENTITY_SUCCESSIVE_HALVING`.  Explicit mention alignment is 0/528,
pre-cap state is absent, occurrence coverage is 10k-only, and tuning
candidate-complete recall is 39/193 (20.2073%) versus the frozen 90% readiness
threshold.  The 0.25M/1M/3M/5M sweep is `NOT_STARTED`; no contextual model, fusion
weights, depth gate, or policy parameters were trained.

## Analytical P4 projection

The existing analytical digital twin was run at full Work speed over all 695 states;
no sleep-based emulation was used.  The exact value scan has zero learned parameters
and zero MACs.  Mean/p95 integer operations are 728.83/2,376; mean/p95 sequential
source bytes are 390.07/1,087.5; peak projected workspace is 16,866 bytes.  Nominal
300 MHz projected mean/p95 active latency is 0.02412/0.03766 ms.  These are analytical
projections, not board measurements.  The 136,164-byte decoded 10k semantic table and
bounded address-record storage are recorded in the architecture registry; semantic
map-lookup latency is not included in the value-scan projection.

## Reproduction

```bash
PYTHONPATH=src python scripts/droid/v11_targeted_handoff_audit.py ...
PYTHONPATH=src python scripts/droid/v11_semantic_address_qualify.py ...
PYTHONPATH=src python scripts/droid/v11_value_upstream_qualify.py ...
PYTHONPATH=src python scripts/droid/v11_value_residual_rerun.py ...
PYTHONPATH=src python scripts/droid/v11_specialist_readiness.py ...
PYTHONPATH=src python scripts/droid/v11_upstream_reachability.py ...
PYTHONPATH=src python scripts/droid/v11_p4_qualification.py ...
PYTHONPATH=src python scripts/droid/v11_architecture_registry.py \
  --repository . \
  --output config/architecture/aethercore-v11-integrated.registry.json
```

Private row-level gzip/tar/SQLite payloads remain outside Git.  Only source, tests,
compact reports/manifests, the sealed registry, and reproduction documentation belong
in the checkpoint.  `LICENSE` and `NOTICE` remain unchanged.
