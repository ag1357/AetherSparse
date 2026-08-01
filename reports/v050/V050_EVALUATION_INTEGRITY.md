# AetherSparse v0.5 evaluation integrity log

Status: `R2_ORDER_INVARIANT_CONTEXT_REPLAY_REQUIRED`

The frozen `INDEPENDENT_NATURAL_QUERY_SET_V050_R1` cases are serialized by
content-derived case ID, not by conversational chronology. Of 2,050 cases, 200
declare one parent through `prior_case_ids`; every declared parent is a direct
fact case and no parent has another parent. Ninety-nine children are serialized
before their declared parent.

The first qualification runner populated discourse state only after it reached
a parent in array order. This made those 99 conversational cases impossible by
construction. The defect affected evaluation context replay, not the frozen
benchmark bytes, source evidence, corpus packs, controller architecture, or
public v0.4.0 parent.

## Artifact disposition

- The completed 10k pre-fix R1 report is retained as diagnostic reconstruction
  evidence only. Report SHA-256:
  `62145307019fddc4fc580862f188bebf058140c0b37bbfe80e48e107e3027e8e`.
- Its complete outcome matrix is retained outside ordinary Git. SHA-256:
  `497ac05bd6b9ff4a5200fad07865df719b3153f4080c8a3f0f3f390b5c7bb3cf`.
- In-progress 10k and 50k R2 runs were terminated immediately after discovery.
  The runner writes atomically, so neither produced a final R2 report or outcome
  matrix.
- Final 10k/50k decisions may use only complete reports with qualification ID
  `AETHERSPARSE_V050_SQLITE_CONTROLLER_QUALIFICATION_R2`.

## Correction

Commit `2ffa9a234bb79e32c970c7eb255e56b873fda440` recursively replays only each
case's declared ancestry before measured evaluation. It rejects unknown parents
and dependency cycles. The resulting context is independent of benchmark array
order and does not perform broad history search or corpus traversal.

A regression test serializes a pronoun child before its direct parent and
asserts that the child receives the parent's canonical entity. The final
integrator independently rejects reports that lack the corrected R2 identity
and explicit order-invariant replay evidence.

## Corrected 10k R2

The complete corrected run evaluated 2,050 cases across all eight frozen
systems and verified parent pack SHA-256
`cb93db732eaf314806700b38ef7ec9d5cf85dea69f32deb51f97a3aa890023e5`.

- Report SHA-256:
  `ffc9c8fcb699f07689f2abe7fafb863ba12c81a0f01f04392ae60cbaaadba775`
- Outcome-matrix SHA-256:
  `dc19d376fefc480ded99e82a74b7cd6a7350a59beea85d77e0bc1f12f86a3244`
- Deterministic fusion article/evidence/exact: 59.06% / 48.52% / 31.09%
- Full controller article/evidence/exact: 64.77% / 54.30% / 35.08%
- Full follow-up/coreference exact accuracy: 47.00%
- Full silent wrong-entity rate: 23.31%
- Unsupported-claim rate: 0.00%

Context replay materially improved the pre-fix result but did not reproduce the
historical retained-baseline targets. Historical v0.4.1 and v0.5 absolute
results remain separate series and are not pooled.
