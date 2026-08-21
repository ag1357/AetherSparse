# AetherCore V13 agent vertical-slice qualification

Status: **LEVEL 4 EDGE-CANDIDATE VERTICAL SLICE — WORKING**

V13 converts the qualified V12 address and exact-reasoning substrate into a
working learned system. The `aethercore-server` executable accepts a session ID
and natural user text, runs the real V12 address machinery, persists bounded
conversation state, rolls the learned policy through exact typed operations,
requires the existing verifier, copies the accepted value from evidence, and
returns an answer, clarification, or explicit abstention.

The selected controller is intentionally small: a **918-parameter typed
legal-mask structured perceptron** over 27 compact state/action features and 34
exact micro-operations. It learns no world facts. Development alone fits the
weights; tuning selects the architecture and the sealed partitions remain
unused.

## Integrated result

| Measure | Result |
|---|---:|
| V12 real-corpus candidate completeness | 30/31 (96.77%) |
| Teacher next-action, development | 476/561 (84.85%) |
| Teacher next-action, tuning | 676/768 (88.02%) |
| Autonomous rollout, authenticated reproduced reachable | **93/260 (35.77%)** |
| Autonomous rollout, unseen tuning | **64/150 (42.67%)** |
| Autonomous success / all strict states | 93/695 (13.38%) |
| Integrated Semantic Address candidates | 3/3 (100%) |
| Integrated verified grounded answers | **3/3 (100%)** |
| Integrated user-visible dispositions | **7/7 (100%)** |
| Multi-turn direct + pronoun follow-up | **2/2** |
| Unsupported-answer rate | **0%** |
| Average / p95 answer-policy operations | 5 / 5 |
| Invalid actions / premature halts / runaways | 0 / 0 / 0 |

The integrated cases exercise a direct question, a pronoun follow-up, genuine
two-entity ambiguity, a clarification choice, unsupported knowledge, cancel,
and reset. The direct interaction is:

```text
User: Who was Alan Turing?
AetherCore: Alan Turing was an English mathematician and computer scientist.

User: Where was he born?
AetherCore: Alan Turing was born in Maida Vale, London.
```

The second turn contains no new entity address. It succeeds through the
persisted canonical Alan Turing binding and returns a different exact evidence
handle. Each accepted answer follows the learned sequence
`ENUMERATE_CLAIMS -> SELECT_CLAIM -> BUILD_DIRECT_PLAN -> VERIFY_PLAN -> ANSWER`.

The 3/3 and 7/7 figures are bounded integration-regression scores, not a claim
of broad benchmark accuracy. The policy score on authenticated real replay is
the 93/260 result above.

## Policy decision and measured repair

All 1,329 closed decision records bind session/query identity, semantic
candidates, evidence handles, unresolved state, workspace hashes, the legal
mask, selected operation and arguments, transition, verifier disposition,
trajectory identity, and split. There are 561 development decisions used for
fit and zero tuning, evaluation, or final-held decisions used for fit.

High teacher accuracy did not fully translate into autonomous accuracy. The
measured residual is narrow: all 167 failures selected the wrong answer among
verifier-grounded claims. There were no illegal actions or verifier bypasses.
One cheap, same-size averaged-perceptron repair was tested as a roll-in/error
correction control; tuning fell from 64/150 to 58/150, so the stronger baseline
was kept. That is evidence to improve claim contrast features next, not a reason
to multiply model size.

The published V12 aggregate certifies 572/695 reachable states, but the bulky
per-case V12 witness identities were not retained. V13 therefore evaluates the
260 authenticated per-case witnesses that can be reproduced exactly and does
not invent a 572-case policy denominator. The 572 result remains the certified
reachability ceiling, not the measured policy score.

## Conversation, grounding, and agent plane

- Structured conversation regressions pass 8/8: direct, follow-up, pronoun,
  `what about`, correction, genuine ambiguity, cancel, and reset.
- Session bounds are 12 recent utterances and 32 evidence handles, with atomic
  JSON persistence available.
- The verifier-gated copy realizer passes all eight required answer shapes and
  fails closed on unsupported values. No learned prose smoother was needed.
- The typed development plane exposes all requested tools and completed 5/5
  bounded repair/feature/parser/API/compilation tasks across 55 real operations.
- The tool plane never merges. `REQUEST_INTEGRATION` consumes explicit one-time
  authorization and still only reports an authorized request.
- Free-form source generation is not disguised as controller capability. A
  future bounded `CODE_SYNTHESIS` specialist remains the minimal addition for
  novel patches beyond known deterministic transformations.
- Four source types, immutable provenance-bound pack identity, region hashes,
  update lineage, and atomic add/update/remove are qualified without rebuilding
  the 397k corpus.
- All 11 Tactility events use bounded length-prefixed JSON over an injected
  transport. The display device contains no cognitive business logic.

## Portable runtime and paged storage

The hot runtime is an allocation-free portable C++17 implementation behind the
stable `aethercore.runtime.c-abi.v1`. Frozen vectors cover candidate union beyond
K=32, legal-mask int8 inference, exact typed transitions/verifier, and 836-byte
CRC-protected session serialization. Python/C++ parity is bit exact with zero
numeric tolerance.

| Runtime item | Measured/contract value |
|---|---:|
| Host GCC load text + data + bss | 10,198 B |
| Host shared object file | 20,904 B |
| Compact workspace | 648 B |
| Session struct / serialized wire | 872 B / 836 B |
| Resident surface + postings directories | **1,735,620 B** |
| Page-aligned cold V12 address index | **32,284,672 B** |
| Page size | 4,096 B |

The index remains on storage rather than resident in PSRAM. On the deterministic
1,000-query 10k proxy trace, a 256 KiB cache retained 100% candidate completeness
at 48,537.6 bytes and 11.85 missed pages/query with a 49.80% hit rate. A 1 MiB
cache reached a 99.20% hit rate and 778.24 cold bytes/0.19 pages per query. This
is a layout/cache sensitivity trace, not a physical 397k P4 measurement.

An ESP-IDF component target and exact build instructions are present. ESP-IDF
was unavailable in the Work environment, so only the host build is measured.
The selected 918-weight float-compatible Python policy is not yet quantized and
bound into the generic C ABI weight table; the ABI inference path itself has
exact parity.

## Accessory hardware requirement contract

The contract applies to the separate AetherCore accessory, never the Waveshare
display/Tactility P4.

| Envelope | CPU | RAM | Knowledge storage | Storage service |
|---|---|---|---|---|
| Minimum | 32-bit integer MCU, >=200 MHz, int8/int16 multiply | 4 MiB external + 384 KiB fast internal | >=32 GB removable/page-addressable | >=5 MB/s sequential; <=100 us cached-page target |
| Recommended | integer MCU or small CPU, >=300 MHz, efficient int8 MAC | 8 MiB external + 512 KiB fast internal | **256 GB removable solid state** | >=10 MB/s and >=4k random IOPS or effective cache |
| Comfortable | 400 MHz+ integer core(s) | >=16 MiB | 256 GB+ replaceable | >=20 MB/s and >=10k random IOPS |

Linux is optional: neither required nor rejected. The module should remain
compact, have no permanent RJ45-height requirement, and expose USB and/or local
IP to Tactility. Power is deliberately not asserted until trace-equivalent
hardware measurements exist.

The reused P4 analytical address projections remain p50/p95 116.05/236.61 ms at
200 MHz, 63.65/129.94 ms at 300 MHz, and 37.45/76.67 ms at 400 MHz. They are not
board measurements.

## Remaining bottleneck and exact next action

The remaining learned bottleneck is **claim ranking among exact grounded
alternatives**, represented by the 167 wrong-grounded rollouts. The next policy
action is a bounded DAgger-style collection on those states plus generic
relation/entity/claim contrast features at the same parameter scale. A larger
model is not yet justified.

In parallel, quantize and bind the selected weights to the C ABI, build the
existing ESP-IDF component on the accessory P4, and capture physical 4 KiB page
latency/cache traces. Those two measurements determine whether the replacement
accessory benefits most from more RAM/cache, storage IOPS, or compute.

## Reproduction

```bash
PYTHONPATH=src python scripts/droid/v13_policy_qualify.py \
  --bundle /external/controller-replay-3tier \
  --benchmark data/v050/benchmark/INDEPENDENT_NATURAL_QUERY_SET_V050_R1.json \
  --mission5-report reports/droid/v10/mission5-real-reachability.json.gz \
  --output /tmp/policy-qualification.json

PYTHONPATH=src python scripts/droid/v13_vertical_qualify.py
make -C native/aethercore_runtime clean test
```

The authoritative combined metrics are in
`reports/droid/v13/aethercore-agent-vertical-slice-qualification.json`.
