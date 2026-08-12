# AetherCore v10 Work qualification checkpoint

Base: `8d27af28b3036a07b796aaa70a036a1324112464`
Branch: `work/aethercore-v10`

## Mission-leading results

1. **Certified reachability:** real four-tier result pending the retained S600
   cache export. The bounded search implementation is fixture-qualified; no
   real percentage is claimed.
2. **Best held-out canonical accuracy:** pending. No policy was trained.
3. **Controller residual recovered:** pending. The checked-in 10k legacy
   taxonomy contains 613 failures, of which 375 are classified as recoverable
   by an existing policy/tool path and 238 require a generic composition
   primitive. This is taxonomy, not demonstrated reachability.
4. **Minimum viable parameter count:** not authorized before the reachability
   gate.
5. **Active compute:** deterministic micro-operation search only; learned
   parameters and MACs/action are zero at this checkpoint.
6. **Retrieval scale result:** no new result. An isolated
   `work/aethercore-retrieval-v10` worktree exists at the v09 base; retrieval
   changes are not allowed to block the controller gate.
7. **ESP32-P4 feasibility:** unqualified until trajectory lengths, active
   operations, and the policy frontier are measured on the real replay bundle.

## Completed in Work

- Added `aethersparse controller export-replay` and `verify-replay`.
- Added deterministic `aethercore.controller-replay.v1` bundles with canonical
  JSON, deterministic gzip, input hashes, cases hash, bundle hash, schema and
  integrity checks, counts, and forced non-training flags for `evaluation` and
  `final_held`.
- Expanded the diagnostic-only v09 trace projection to retain full structured
  query frames, linked candidates, discourse state, ranked metadata, exact
  claims, required source spans, selection, plan, verification, and
  disposition. Runtime controller behavior is unchanged.
- Added a separate stable registry of 34 exact typed micro-operations. Existing
  v09 high-level operator IDs remain unchanged. Personal-memory actions 96/97
  are reserved but disabled.
- Added exact execution with provenance-bound claim/source selection,
  deterministic filtering, list/comparison/count/quantity composition,
  planning, verification, and terminal actions. Unknown IDs and invented
  claims/sources are rejected.
- Added bounded best-first and beam search. Evaluation/final-held search is
  gold-blind; canonical gold is applied only by a post-hoc scorer. Development
  and tuning may use gold in the search objective.
- Added `qualify-reachability`, implementing the 30%/60% control gates and
  reporting reachable ceiling, trajectory length, search requirements,
  branching factor, actions, and split leakage checks.
- Added the exact one-command S600 cache-replay/export handoff. It uses existing
  trace caches and does not rerun candidate retrieval.

## Evidence available now

The checked-in 10k v09 taxonomy was normalized without changing its source
records:

| Failure class | Cases |
|---|---:|
| COMPOSITION_OPERATOR_MISSING | 238 |
| VALUE_MISRANKED | 128 |
| DISPOSITION_WRONG | 106 |
| VALUE_NOT_ENUMERATED | 74 |
| TEMPORAL_SCOPE_WRONG | 57 |
| REALIZATION_ONLY | 10 |

The fixture qualification proves mechanics only: both search methods can find
an exact, verifier-passing trajectory through a distractor claim; an evaluation
case uses no gold during search; protected records are non-training;
deterministic exports are byte-identical; tampering is detected; and an
invented claim ID is rejected.

## Gate state

`REAL_REACHABILITY_PENDING`

This is not one of the Mission 5 final control decisions because the required
real replay evidence is absent from the Work checkout. Therefore no recurrent
policy, parameter sweep, learned side memory, sparse heads, or quantization was
started. The next permissible action is the S600 replay export documented in
`docs/reproduction/V10_S600_REPLAY_HANDOFF.md`, followed by Work-side
reachability qualification.
