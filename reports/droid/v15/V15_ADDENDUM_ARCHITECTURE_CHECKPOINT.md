# V15 addendum architecture checkpoint

**Status:** SOFTWARE CHECKPOINT COMPLETE, PHYSICAL USB QUALIFICATION PENDING

**Date:** 2026-09-08

**Branch:** `work/aethercore-v15-final-integration`

This checkpoint implements the architecture addendum without declaring
`V15_FULL_CORPUS_USB_INTERACTIVE_QUALIFIED`.

## Implemented

### Typed request semantics

- Provider-mode relation and answer shape are inferred before retrieval.
- A host-safe `RequestFrame` carries relation family, answer shape, required
  support obligations, and lexical constraints.
- The full request frame is retained through clarification, pronoun follow-up,
  and `what about` continuation.
- `when` and `where` determine date versus location intent. `born` and `died`
  identify event family but do not independently force temporal intent.

### Bounded competing evidence

- `KnowledgeProvider::FetchRecords()` now accepts the typed request and bounded
  fetch options.
- PackProvider returns a deterministic shortlist of up to eight records instead
  of one preselected quotation.
- The controller workspace retains direct and background competitors.
- The exact verifier independently requires subject, relation, answer shape,
  requested obligation coverage, and exact evidence-copy support.

### Full, partial, and unsupported outcomes

- `SupportLevel` is typed as `FULL`, `PARTIAL`, or `NONE`.
- Empty complete retrieval is unsupported (`NONE`).
- Related evidence is returned as grounded `PARTIAL` background with evidence
  handles, never as a complete answer.
- Provider I/O/corruption is distinct from unsupported knowledge.
- Evidence summaries no longer call partial background an accepted plan.

### Retrieval behavior

- Self-article rows keep the corpus-structural occurrence flag.
- The `+6` self-article prior applies only to general descriptions.
- The source is described as an encyclopedia self-article, not a primary source.
- Negative usable scores are retained with an explicit unusable sentinel.
- Scans have occurrence and byte budgets, resumable validated cursors,
  cancellation probes, explicit completion/error status, and deterministic
  merge ordering.
- Selected occurrences can be reread to a separate bounded passage-expansion
  limit while preserving exact evidence text.

### Direct tests

Native provider/service tests cover:

1. general descriptions remain full grounded answers;
2. where-born requests location, not date;
3. background-only evidence becomes partial;
4. direct evidence beats higher-confidence background;
5. shape mismatch cannot answer;
6. the complete request frame survives follow-up;
7. constraints must be covered;
8. unsupported knowledge differs from provider failure;
9. resumable cursors are forwarded;
10. numeric clarification selection resolves;
11. synthetic Pack-v2 date and location extraction;
12. conditional self-article prior;
13. negative usable retrieval scores;
14. resumed scan equivalence;
15. cancellation and corruption status;
16. bounded selected-passage expansion.

The canonical 16-query native/Python static-fixture parity remains exact.

### UART reconciliation

- The hosted-absent `pack_io` guard from `e836e93` was already present.
- Tactility already contains the proven 8 KiB UART pump stack from `83b08e3`.
- Device C now flushes the UART RX FIFO on malformed boot-noise framing.
- USB CDC remains the selected production transport; UART remains fallback.

## Learned experiment

The addendum’s capacity ranges were implemented as a shadow-only experiment:

| Component | Parameters | Authority |
| --- | ---: | --- |
| Shared hashed-ngram question/evidence encoder, 192-1536-192 | 2,164,416 | Shadow |
| Encoder + 19-field COG advisor, 512-288-5 | 257,733 | Shadow |
| Combined | 2,422,149 | Shadow |

Development was used for fitting and tuning for evaluation. Evaluation and
final-held cases were not used for metrics or selection.

The encoder did **not** qualify:

- learned tuning Recall@1: `0.1124`;
- static hash baseline Recall@1: `0.3760`;
- float/int8 top-1 agreement: `0.9729`.

The COG advisor reached tuning macro-F1 `0.9702`, with int8 macro-F1 `0.9749`
and float/int8 disposition agreement `0.9976`. It remains non-authoritative
because the available committed data does not support honest training of a
larger 34-operation controller.

Artifact:

- external int8 model:
  `/media/cloud/2982-E16B/work/v15-p4-deployment/models/v15-shadow-shared-encoder-cog-int8.npz`;
- SHA-256:
  `0e15f3825e258791cfc5984d18985ff29a47763257d01a2d50a20a97e336e8cd`;
- qualification report:
  `reports/droid/v15/experimental-shared-encoder-qualification.json`.

The deterministic request frame, legal mask, exact verifier, and support gate
remain authoritative.

## Validation

- Host CMake build: pass.
- Provider contract CTest: pass.
- Direct PackProvider CTest: pass.
- Static native/Python parity: `16/16` exact.
- Broader `tests/agent`, `tests/controller`, and `tests/experiments`: pass.
- Learned experiment tests and Ruff: pass.
- AetherCore ESP32-P4 build: pass, 54% app partition free.
- Tactility Device A build: pass, 11% app partition free.
- Production-source entity literal audit: no acceptance entity names found.
- Query/entity equality-branch audit: no topic-specific branches found.

## Remaining before final classification

1. publish this architecture checkpoint;
2. flash and directly requalify Device C with the new provider contract;
3. connect Device A OTG host through the powered hub to Device C USB CDC;
4. observe HEALTH, CAPABILITIES, and AccessoryLink READY end to end;
5. qualify broad/random AetherChat interaction, continuation, clarification,
   cancel/reset, and persistent natural memory;
6. run one authoritative full Pack-v2 verification;
7. publish the final 22-point report and V16 backlog.

Full source-article expansion beyond bytes retained in Pack-v2 cannot be
invented by firmware. This checkpoint expands only the complete selected
occurrence up to the explicit bound. A separately indexed source-passage region
is recorded for V16 rather than falsely claiming access to article bytes that
the current pack does not contain.
