# V15 architecture-debt ledger

Source checkpoint: `c3aa2ef61e6ae77a12063e47221c6e4decae3762`.

This ledger reconciles V11–V14 before V15 cognition work. It distinguishes a
failed architecture from an experiment that was never lawful because upstream
state or causal supervision was absent. Evaluation and final-held labels remain
sealed. The machine-readable ledger contains the complete reason, data, result,
V14 reassessment, evidence path, and V15 disposition for every row.

## Selected and frozen

| Proposal | Status | Evidence-based disposition |
|---|---|---|
| Semantic Address v2 | `IMPLEMENTED_AND_SELECTED` | 30/31 tuning completeness; 107/107 physical queries exact. Freeze semantics. |
| Exact FST + fuzzy channels | `IMPLEMENTED_AND_SELECTED` | Retained inputs to the qualified address union; neither is a complete replacement alone. |
| V14 feed-forward COG controller | `IMPLEMENTED_AND_SELECTED` | 1,292 int8 parameters; 242/260; physical 1,329/1,329 decisions exact. |
| COG | `IMPLEMENTED_AND_SELECTED` | Same-cohort autonomous gain 93/260 to 242/260 with zero illegal/bypass/runaway. |
| 5C | `IMPLEMENTED_AND_SELECTED` | Immutable verifier/permission/provenance/rollback boundary. |
| Quantization | `IMPLEMENTED_AND_SELECTED` | Int8 retained/improved float rollout and passed physical parity. |
| Knowledge packs + native runtime | `IMPLEMENTED_AND_SELECTED` | Physical exactness qualified; current bottleneck is media I/O, not cognition. |

## Retained negative results

| Proposal | Status | Exact result |
|---|---|---|
| DAgger / roll-in | `TESTED_REJECTED` | 243 distinct certified states around 81 development divergences reached **231/260**, versus selected **242/260**. Do not repeat. |
| V11 learned entity head | `SUPERSEDED` | Tuning any-required top-1 67→75/193 but strict binding 0/193 under candidate absence; Semantic Address v2 fixed upstream state. |
| V11 probabilistic fusion | `IMPLEMENTED_NOT_PRODUCTIONIZED` | Label-free entropy changed; semantic correctness was not lawfully measured and top choice changed only 0.1587%. |
| Learned semantic addressing | `TESTED_REJECTED` | Deterministic address v2 exceeded 90%; sole residual entity was absent from the canonical registry. |
| Broad nonlinear controller | `TESTED_REJECTED` | No selected matched post-COG gain or positive capability/byte curve; the measured residual is missing local context. |

## Never misreport these as rejections

| Proposal | Status | Why no performance conclusion exists |
|---|---|---|
| Shared recurrent core, 1/2/4/8 cycles | `UNTESTED` | No qualified learned implementation or causal cycle dataset. |
| Learned adaptive depth | `DEFERRED_BECAUSE_UPSTREAM_STATE_WAS_BAD` | V11 had zero halt-now/+1/+2 counterfactual fields; V14 explicit COG shows no temporal-loss signal. |
| Deeper unique layers | `UNTESTED` | Not tested after COG; scaling was unjustified after 93.08%. |
| Factorized/bilinear policy | `UNTESTED` | No matched post-COG implementation/result. |
| Cognitive lookup memory | `UNTESTED` | No leakage-safe learned lookup experiment; controller facts must remain external. |

## Reopened for the smallest V15 experiment

The 18 V14 residual errors are 11 dates, six quotations, and one
definition/misspelling. They are grounded alternatives inside the same passage.
Therefore V15 reopens only a shared passage-context specialist spanning relation,
temporal, attribution, and evidence roles. The frozen V14 policy still chooses
the operation; the specialist may rank only legal `SELECT_CLAIM` arguments.

Sparse branches and exact operational telemetry are also reopened. Quantity and
composition learned heads remain contract-only because neither appears in the
measured residual. Semantic Address, dense depth, recurrence, and mandatory
cross-source reads remain closed.

### V15 experiment disposition

The economical 54-parameter int8 context head was tested on the exact cohort.
Its best development-only fit reached 110/110 development but only 129/150
tuning and 239/260 overall, versus the frozen V14 controller's 138/150 tuning
and 242/260 overall. It is `TESTED_REJECTED`, its artifact is archived inactive,
and V14 remains selected. This is evidence against replacing the existing claim
score with this small local-context view; it is not evidence for a larger dense
controller, recurrence, or Semantic Address redesign.

## Operational debt owned outside this lane

EPHEMERAL/SHORT_TERM/WORKING/LONG_TERM are not yet one authoritative tier
manager. Conversation state, sandbox/source agent, specialist sharing,
Tactility transport, and probabilistic workspace are implemented but not fully
productionized. Lane A owns native/memory/storage hardening; Lane C owns
conversation/user-memory/agent/service/Tactility integration. This lane does
not duplicate them.
