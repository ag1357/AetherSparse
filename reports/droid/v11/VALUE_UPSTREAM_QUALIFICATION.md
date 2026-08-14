# Mission 6 upstream value qualification

## Pre-repair diagnosis

`VALUE_AVAILABILITY_IS_PRIMARILY_A_SELECTED_CHUNK_PROBLEM`

The supplied targeted pack capture resolves the former ten blocked rows and
replaces the old four-class summary with an exact first-loss taxonomy over all
43 remaining development/tuning replicas.  No evaluation or final-held case,
answer, threshold, or feature was read.

| First observed loss | Replicas |
|---|---:|
| exact value atoms already present; address/controller binding unresolved | 11 |
| at least one exact target span absent from the selected top-eight chunks | 29 |
| exact target present in selected chunk, missed by both compiler and runtime | 3 |
| answer-shape error | 0 |
| region pruning | 0 |
| deduplication | 0 |
| per-chunk value cap | 0 |
| exact document rebinding | 0 |

The 32 genuine enumeration rows all retrieve every required source document
somewhere in the ranked evidence.  The failure is finer-grained: 29 do not
select the chunk containing every exact target.  The three target-present
extraction failures are replicas of one tuning-only quotation case.  They do
not lawfully support a new quotation feature or model.

The remaining 11 rows must not be counted as value-extraction failures.  Their
unaddressed replay candidate list already contains every accepted atomic value.
The capture does not carry a gold relation-vs-subject label for that list, so it
cannot lawfully split those rows between wrong relation and wrong subject.  They
are handed to the Semantic Address Plane/controller assembly qualification
instead of being assigned a guessed cause.

## Pack-side boundary facts

| Capture fact | Count |
|---|---:|
| replicas | 43 |
| unique cases | 16 |
| development / tuning | 16 / 27 |
| 10k / 25k / 397k | 16 / 16 / 11 |
| selected chunk replicas | 344 |
| unique selected chunks within tier | 329 |
| missing selected chunks | 0 |
| compiler source documents | 75 |
| missing compiler documents | 0 |
| scored runtime regions | 2,493 |
| matches before top-eight region pruning | 83 |
| matches after top-eight region pruning | 79 |
| exact source-surface matches | 83/83 |
| successful exact document rebindings | 83/83 |

The full-page compiler trace independently shows target-to-source alignment
missing before type caps in 21 of the 32 value-missing replicas.  This is a
non-exclusive diagnostic: 18 of those rows also lack the target selected chunk,
and the other three are the tuning-only dual extraction case.  Three replicas
lose a target at the type cap and three more at the page cap.  These overlaps
are preserved rather than inflated into additional recoveries.

## Generic deterministic correction

The source-bound repair no longer treats a local claim address as proof that
all other frame-supported entity/relation addresses are impossible.  It now
retains the stable local-first union of local and frame hypotheses.  Every
hypothesis still points to the same exact source span, document, surface hash,
and bounded typed lattice; no factual value is generated.

The certified goal precheck now canonicalizes percent typography identically
inside atomic and compound values.  The atomic numeric path already emitted
`number %`, while compound comparison strings retained `number%`; exact
components therefore failed the substring guard even when the typed value was
present.  Normalizing a percent sign immediately after a digit is a generic
representation correction, not a case, entity, relation, or answer rule.

Candidate enumeration now visits the primary address for every source region
before visiting alternate addresses.  Competing addresses remain source-bound
and ordered, but an ambiguous first region can no longer consume the global
claim cap before later source documents contribute a primary candidate.

No quotation rule was added.  The only target-present extractor residual is
tuning-only, and using it for feature design would violate the partition
boundary.  No neural value specialist was trained because the target-present
development extraction residual is zero.

## Targeted residual rerun

The repaired state was rerun against exactly the same 43 development/tuning
replay rows with the existing bounded best-first and beam configurations:
maximum depth 14, 4,096 expansions, beam width 32, and argument cap 64.  This
was not a broad or held-out battery.

| Targeted result | Count |
|---|---:|
| semantic entity binding valid | 43/43 |
| exact goal possible | 34/43 |
| certified reachable | 32/43 |
| development reachable | 14/16 |
| tuning reachable | 18/27 |
| first success by best-first | 32 |

The representation correction raises the exact-goal precondition from 2 to 34
rows; bounded best-first proves 32 of those 34.  The remaining 11 rows comprise
three tuning-only compiler/runtime extraction misses, six tuning rows whose
source chunks do not contain all target atoms, and two development rows whose
goals are present but remain outside the bounded controller search.  The
machine artifact retains `SOURCE_CHUNK_ABSENT=8` and
`TOOLSET_CONTROLLER_SEARCH=2`; the latter is an intentional overlapping
refinement of two of those eight rows, not an additional failure count.

All 11 formerly unaddressed replay-candidate rows become reachable.  Their
earlier relation-vs-subject ambiguity therefore requires no guessed label or
case-specific address rule.  The targeted result identifies the surviving
upstream limitation as value availability (nine tuning rows) plus bounded
toolset/controller search (two development rows).  A full Mission 5 protocol
rerun is still required before making a new corpus-level certified-reachability
claim.

## Integrity and reproduction

The qualification verifies the compressed and uncompressed value-capture
identities before reading rows:

- capture SHA-256:
  `d73de7b357ff9ec82aa23d9b40496b66cccb33405b76bb2a74a42ef19300145c`;
- capture manifest SHA-256:
  `2cf5bbd5cea6de63ebdea2f971a99687d03dc52df7a16ff17e9d859463966d23`;
- uncompressed SHA-256:
  `c5ca03d61e6557219658ec6932fde27cfd8e8ad5ea5287a31efd820cc4ddc176`;
- replay logical SHA-256:
  `099cd28b5c2a090d1b08d1e07d39e59ae6583854971c539085741fbb369f0246`;
- benchmark SHA-256:
  `1e8b89427898df3c3e5efef55135192cf6d48240f0f568c95eb1570470ead113`.

The machine-readable aggregate is
`reports/droid/v11/value-upstream-qualification.json`; the bounded residual
result is `reports/droid/v11/value-targeted-residual-rerun.json`.  Neither
artifact contains row-level query, label, chunk text, or source document
content.
