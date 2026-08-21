# AetherCore V14 COG adaptive-controller qualification

Status: **READY_FOR_FACTORY_P4**

V14 qualifies the Cognitive Obligation Graph, a same-scale adaptive int8
controller, immutable 5C constraints, sparse specialist contracts, and an
argument-aware native policy path. The physically separate accessory ESP32-P4
is now a justified measurement target. This decision never applies to the
Waveshare Tactility/display appliance.

## Checkpoint and scope

| Item | Value |
|---|---|
| Exact published V13 parent | `7ddce4152f85eff78ba8d14a73d59e1d53ecc4ee` |
| Qualified V14 source commit | `125232d7d50a264ccf225b8870092c0018ef535f` |
| Publication commit | Branch HEAD reported after this self-referential report is committed |
| Semantic Address | V2 retained unchanged |
| Authenticated policy cohort | 260 per-case reproducible witnesses |
| V12 reachable ceiling | 572/695; no missing witness identities inferred |

## COG v1 and interpreter

The authoritative state is `C_t = (G,O,I,H,E,U,F,S)`: goals, obligations,
invariants, hypotheses, evidence, unresolved variables, exploration frontier,
and externally observed state. Bounds are G8/O48/I16/H16/E64/U32/F32/S16.

| Representation | Size |
|---|---:|
| Representative canonical Python JSON | 3,871–5,213 B |
| Compact controller view | 19 `uint16` fields / 38 B |
| Native aligned COG struct | 48 B |
| Native COG+5C+progress+specialist wire snapshot | 180 B |

`HALT_SUCCESS` fails closed while mandatory obligations remain open, an
invariant is violated, or required verifier acceptance is absent. Evidence is
append-only. Obligation dependencies, reopening, frontier expansion, invariant
verification, progress accounting, and bounded stagnation recovery are typed
operations.

The interpreter supports `NATURAL_LANGUAGE` and
`STRUCTURED_EXTERNAL_EVENT`. Direct QA, competing Mercury hypotheses,
pronoun follow-up, negation/premise interpretation, generic external events,
actuator anomaly, and missing observation regressions pass. Sensor values retain
`OBSERVATION` provenance; fault hypotheses retain separate `INFERENCE`
provenance.

## Controller result

The selected policy is a **COG-derived typed legal-mask structured
perceptron** with 38 generic obligation/claim-contrast features and 34 exact
operations: **1,292 int8 parameters / 1,292 bytes**. Activations are fixed-point
integers scaled by 256. It contains no answer text, target identities, span-name
signals, benchmark-specific lexical rules, or learned world facts.

| Measure | V13 | V14 selected int8 |
|---|---:|---:|
| Teacher action, development | 476/561 (84.85%) | **549/561 (97.86%)** |
| Teacher action, tuning | 676/768 (88.02%) | **743/768 (96.74%)** |
| Autonomous development | 29/110 (26.36%) | **104/110 (94.55%)** |
| Autonomous unseen tuning | 64/150 (42.67%) | **138/150 (92.00%)** |
| Autonomous reproduced reachable | 93/260 (35.77%) | **242/260 (93.08%)** |
| Wrong grounded claim | 167 | **18** |
| Invalid / verifier bypass / premature halt / runaway | 0 / 0 / 0 / 0 | **0 / 0 / 0 / 0** |
| Mean / p95 operations | 5.11 / 5 | **5.11 / 5** |

The same-scale float structural controller reached 239/260. Global int8
quantization retained 138/150 unseen tuning successes and improved total success
to 242/260. The exact-certified DAgger experiment added 243 distinct roll-in
states around 81 development divergences, but reached only 231/260, so it was
correctly not selected. No capacity ladder above approximately 1K parameters
was justified.

The improvement comes primarily from representing unresolved subject
hypotheses and candidate-specific obligation contrast. V13 could treat an
unknown subject as matching every grounded claim; V14 distinguishes unknown,
matching, and conflicting hypotheses.

## Structural stress and coding architecture

- Small HLE-style structural set: **9/9**. It covers multi-obligation state,
  competing interpretations, two-hop discourse composition, comparison,
  temporal constraints, clarification, negated premise, missing premise, and
  unavailable/unsafe state. This is not an HLE benchmark claim.
- Multi-file coding obligation tasks: **3/3**, with 100% affected-object recall,
  successful build/tests, zero invariant violations, zero redundant cycles,
  and zero stagnation detections.
- Retained V13 typed tool plane: **5/5** bounded tasks and 55 operations, with no
  automatic integration. V14 still does not claim unrestricted source synthesis.

The generic cognitive operations include `DISCOVER_DEPENDENTS`,
`DISCOVER_REFERENCES`, `ADD_OBLIGATION`, `SATISFY_OBLIGATION`,
`VERIFY_INVARIANT`, `REOPEN_OBLIGATION`, and `EXPAND_SCOPE`.

## 5C and specialists

5C implements nine immutable root classes: invariants, capabilities,
permissions, verifier integrity, resources, physical hard limits,
self-modification, rollback, and fail-closed behavior. The learned controller
cannot rewrite evidence, bypass the verifier, rewrite/prune root state, or
self-integrate generated components. Activation requires external authorization,
signature, sandbox, tests, and rollback. Contextual social/ethical policy is an
advisory layer and cannot override root denial.

Specialists expose deterministic/learned/shared-learned/sensor/actuator/tool/
hybrid kinds, COLD/WARM/HOT activation, schemas, resource/latency cost,
permissions, provenance behavior, and 5C constraints. The synthetic 12-instance
test shares one parameter family with per-instance calibration; deterministic
hard limits clamp learned residual output.

## Integrated service

`aethercore-server` now serves `/v14/query` using the real V12 address index,
selected V14 int8 policy, exact micro-operations, verifier, evidence-copy
realizer, persistent conversation state, and COG completion gate.

| Integration regression | Result |
|---|---:|
| Semantic-address candidates | 3/3 |
| Verifier- and COG-accepted grounded answers | 3/3 |
| User-visible dispositions | 7/7 |
| Direct + pronoun follow-up | 2/2 |
| Unsupported-answer rate | 0% |
| Mean / p95 controller steps | 5 / 5 |

These are bounded integration regressions; 242/260 is the broad authenticated
policy result.

## Native parity and resources

The portable allocation-free C++17 runtime retains the stable C ABI and adds
compact COG, 5C, progress, specialist, and 64-action int8 contracts. An
argument-aware candidate scorer closes the V13 ABI gap: every legal claim/action
may supply its own 38-feature contrast vector. The exact selected 1,292-byte
artifact passes Python/C++ parity with zero tolerance.

| Native measurement | Result |
|---|---:|
| Host ELF load text+data+bss | 16,488 B |
| Host shared object | 25,912 B |
| Maximum host static stack estimate | 976 B |
| Candidate-policy scorer/select stack | 128 B |
| Full operation-table MACs | 1,292 |
| Average argument-aware MACs/trajectory | 5,937.94 |
| p95 / maximum MACs/trajectory | 7,638 / 7,752 |

ESP-IDF was unavailable, so these are host measurements. Physical P4 timing,
linker memory, stack high-water, storage behavior, and CPU utilization remain
unmeasured.

## Edge memory and storage projection

The V13 paged address layout is retained: 1,735,620 B resident directories,
32,284,672 B cold page-aligned index, and 4,096 B pages.

| Page cache | Combined resident | 4 MiB headroom | Proxy page reads/query |
|---:|---:|---:|---:|
| 256 KiB | 2,010,888 B | 2,183,416 B | 11.85 |
| 1 MiB | **2,797,320 B** | **1,396,984 B** | **0.19** |
| 2 MiB | 3,845,896 B | 348,408 B | 0.19 |

The 1 MiB row is the Factory reference. Page-read figures are retained proxy
cache traces, not physical 397k P4 measurements. Knowledge remains on removable
page-addressable storage; the temporary 128 GB microSD does not replace the
long-term 256 GB deployment class.

Analytical P4 policy lower bounds are 13.255/8.837/6.628 microseconds at
200/300/400 MHz. Retained address projections are p50/p95
116.05/236.61 ms, 63.65/129.94 ms, and 37.45/76.67 ms respectively. None are
board claims.

## Factory prediction-versus-actual contract

| Measurement | V14 prediction | Physical P4 actual |
|---|---:|---:|
| Resident bytes, 1 MiB cache | 2,797,320 B | Pending Factory trace |
| Page reads/query | 0.19 proxy misses | Pending Factory trace |
| Policy operations | 1,292 MACs/decision | Pending Factory trace |
| Address latency | 63.652 ms p50 at 300 MHz analytical | Pending Factory trace |

The machine-consumable handoff is
`reports/droid/v14/factory-p4-handoff.json`. It explicitly targets the SECOND /
ACCESSORY ESP32-P4 and the temporary 128 GB microSD.

## Remaining bottleneck and next action

The residual is narrow: 18/260 wrong-grounded selections—11 date, six
quotation, and one definition/misspelling case—distributed across 10k, 25k, and
397k tiers. The remaining representation need is finer bounded local
passage-context-to-relation contrast, not another semantic-address redesign or
a larger controller.

The exact next justified action is the Factory physical deployment: install
ESP-IDF, compile/flash this frozen native ABI and selected int8 policy onto the
separate accessory P4, place the paged pack on the temporary 128 GB microSD,
run trace-equivalent queries, and fill the four predicted-versus-actual fields.
That measurement will distinguish compute, cache/RAM, and storage-I/O limits.

## Validation

- Full repository suite: **449 passed, one skipped** (450 collected).
- Modified-path Ruff: pass.
- Modified-path strict mypy: pass.
- Native host build and exact Python/C++ parity: pass.
- JSON manifests and Factory handoff: pass.
- `LICENSE` and `NOTICE`: unchanged from the exact V13 parent.

The authoritative machine-readable metrics are in
`reports/droid/v14/aethercore-cog-adaptive-controller-qualification.json`.
