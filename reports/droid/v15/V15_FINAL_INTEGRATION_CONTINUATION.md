# AetherCore V15 final-integration continuation

**Status:** INCOMPLETE, safe published checkpoint

**Date:** 2026-09-08

**Mission source:** `/media/cloud/2982-E16B/AetherSparse/v15 continuation`

**AetherSparse branch:** `work/aethercore-v15-final-integration`

**Published checkpoint before this report:** `3b77ce51224e5c78667f87908b08a45d82f9bc43`

**Published checkpoint tree:** `703a45242d10baaa04e787155fbfa82eb330f1a2`

**Tactility branch:** `ag1357/Tactility:work/aethercore-v15-final-integration`

**Tactility checkpoint:** `ce41498309299177a4a24b0246d236e7f1ee8db9`

**Tactility tree:** `6e0577a58169afb778c20d3862b4045d4e92c3fe`

This report records the current state for continuation. It does not classify
`V15_FULL_CORPUS_USB_INTERACTIVE_QUALIFIED`.

## Current physical state

- Device A and Device C are attached separately to the Raspberry Pi by their
  programming/debug USB connections.
- Device A and Device C are **not yet connected to each other** through Device
  A's OTG host and the powered hub.
- Device A is the Waveshare ESP32-P4-WIFI6-Touch-LCD-3.5 terminal. A separate
  recovery session restored and hardware-validated the custom Tactility port,
  display, touch, Audio Player, AetherChat, and an AccessoryLink idle-backoff
  fix.
- Device C is the Waveshare ESP32-P4-Pico now being used as the removable
  AetherCore compute target. It runs the P4 AetherCore firmware and reads the
  staged Kingston 128 GB pack.
- The two currently visible programming ports are stable by-id devices:
  `usb-1a86_USB_Single_Serial_5B91055305-if00` and
  `usb-1a86_USB_Single_Serial_5B91042354-if00`.
- P1/OTG VBUS qualification has not occurred. Do not claim CHECKPOINT B until
  the physical hub link, HEALTH, CAPABILITIES, and AccessoryLink READY are
  observed end to end.

## Published work completed

The final-integration branch currently contains:

- Device-B ESP32-P4 build and native USB CDC device bring-up.
- Device-A USB CDC-ACM host backend under `AccessoryLinkService`.
- Production `KnowledgeProvider` backed by Semantic Address and Pack-v2.
- Removal of the V13 fixture initializer from the production firmware tree.
- Bounded query/candidate/evidence/controller/verifier telemetry, including the
  controller operation chain.
- A generic build-time self-article lead supplement for the backlink-only
  occurrence corpus.
- A corpus-structural self-article occurrence flag and ranking prior.
- Parenthetical title-qualifier removal for generated self-lead mention
  surfaces.

The pack-backed path was exercised directly on Device C before this report.
Representative observed responses included definitional evidence for Mars,
Saturn, Marie Curie, photosynthesis, oxygen, Western Roman Empire, Rome, and a
Jupiter continuation. The verifier accepted the exact Pack-v2 evidence handles.
This demonstrates real external-corpus access, but it does not complete the
mission because USB AetherLink, natural memory recall, cancellation, final
randomized acceptance, and the one authoritative full-pack verification remain
open.

## Branch reconciliation required

The active final-integration and UART physical-acceptance branches diverge from
the common base `8ea5898fe8e534fc43540372772f15b9d1f5c8be`.

The UART branch has two commits absent from final integration:

1. `e836e93` — Device-A UART backend under `AccessoryLinkService` plus the
   hosted-absent `pack_io` guard.
2. `83b08e3` — physical UART link and first real Device-A/Device-B bench
   interaction.

Do not replace the final-integration branch with the older UART branch. Reconcile
the still-applicable UART fixes into the active continuation after reviewing
their interaction with the USB backend and current Device C hardware. Preserve
the proven transport behavior while retaining the newer corpus integration.

## Model/retrieval source-review recommendation

The following is a source-review recommendation supplied after inspection of
`3b77ce5`. It has **not** been physically benchmarked by that reviewer. Treat it
as the required architecture-review agenda, not as a completed qualification or
permission to add query/topic special cases.

The review confirms that the full-corpus connection is implemented and that the
ranked address adapter does not merely use `first_entity_idx`. It recommends
preserving both the `KnowledgeProvider` and the self-article supplement. Its
central finding is:

> Retrieval currently chooses the answer before the learned controller receives
> the evidence.

Recommended repair order:

### 1. Preserve request meaning through retrieval

`InitWithProvider()` leaves static records empty, while `RelationOf()` still
searches static records. Provider-backed continued/resolved requests therefore
fall back to `describe`. Retrieved records also currently declare
`relation="describe"`, `answer_kind="QUOTATION"`, and `confidence=1.0`.

Derive the requested relation, answer type, and constraints independently from
the input. Pass that typed request into the provider, controller, and verifier.
Dates, locations, causes, comparisons, and descriptions must remain distinct
obligations.

### 2. Expose competing evidence to the controller

`FetchRecords()` scans occurrences but retains only one best context per entity.
The controller therefore receives an already-selected snippet. Return a bounded
shortlist, initially 4–8 distinct evidence candidates, with relevance features,
source identities, and unresolved obligations. Retain the current heuristic as
an inexpensive first-stage baseline rather than the final answer selector.

### 3. Distinguish support from related background

When query-specific scoring finds no usable result, the provider retries with an
empty informative-token set. That may return a generic description even when
the requested fact is missing. Do not label such a result as a fully satisfied
answer with confidence `1.0`.

Preserve useful partial responses, but make the distinction explicit, for
example: background information was found, but the requested date was not.
Completion requires satisfaction of the original typed obligation.

### 4. Make time and source priors conditional

`IsWhenQuery()` currently treats `born` and `died` themselves as time markers,
so a location question such as “Where was X born?” can activate date scoring.
Repair intent/relation typing rather than adding phrase-specific exceptions.

The self-article `+6` prior can be useful for descriptions, but should not
automatically dominate evidence for a specific event, relationship, or later
development. Keep it bounded and query-dependent. An entity's own encyclopedia
article is not a primary source in the bibliographic sense.

### 5. Drive retrieval by missing information

The current provider performs a full occurrence-blob scan plus the self-article
supplement. This preserves coverage but scales with mention count and is not a
learned recursive reasoning mechanism.

Each additional retrieval action should target an unresolved obligation and
report whether it found new relevant evidence. Add resumable scan budgets and
cancellation checks. Do not truncate to the first records because self-article
records are appended. Distinguish corpus-index access from source-article
content access: current occurrence windows retain at most 1,536 context bytes
and realize at most 360 bytes. Add a targeted source-passage expansion
operation for evidence outside those windows.

### 6. Add learned understanding at the heuristic boundary

Initial experimental ranges proposed by the review:

| Component | Initial experimental range |
| --- | ---: |
| Shared question/evidence encoder with relation, relevance, and span-selection heads | 2–4M |
| COG update and action model | 0.25–1M |
| Existing exact execution, provenance, and verification | Retain |

Combined int8 weights are estimated at roughly 2.15–4.77 MiB, excluding
metadata and working buffers. Validate this against measured post-load P4
memory and inference costs.

Training should include question/passage contrasts for correct entity but wrong
relation, birth versus death, when versus where, negation, and popular but
irrelevant articles. Enlarging the existing 38-feature ranker alone is not
expected to supply the missing understanding. Add recurrence only after the
inputs preserve the required information; its role should be revising
hypotheses and completing obligations.

### 7. Qualify the production provider directly

Keep existing parity tests and add native provider tests measuring separately:

- correct evidence retrieval;
- correct answer with supporting evidence supplied directly;
- satisfaction of the original question;
- wrong-relation answers;
- explicit partial answers and abstentions;
- pages, controller steps, and physical p50/p95 latency.

`ANSWER` plus `verifier_accepted=true` is not sufficient to establish answer
correctness under the current quotation contract.

## Continuation order

1. Review and reconcile `e836e93` and `83b08e3` without regressing USB support.
2. Connect Device A OTG host to the powered hub and Device C USB device path,
   following the mission's VBUS/dual-source precautions.
3. Require hub enumeration, Device-C CDC enumeration, HEALTH, CAPABILITIES, and
   AccessoryLink READY before debugging cognition through Device A.
4. Run the architecture review above before treating the self-article prior or
   one-snippet selection as final.
5. Preserve typed request semantics and expose bounded competing evidence.
6. Add direct provider correctness tests before new learned capacity.
7. Qualify AetherChat interaction, pronoun/continuation, real clarification,
   reset, cancel, and natural user-memory recall across reboot.
8. Draw five random corpus entities only at acceptance time.
9. Disable development verification skip and run the approximately 820-second
   authoritative Pack-v2 verification once.
10. Repeat one unrelated query, one follow-up, and one persisted memory recall;
    then produce `V16_BACKLOG.md` and the final 22-point report.

## Explicit non-completion statement

At this checkpoint:

- The deployed Device-C model demonstrably uses the external Pack-v2 corpus in
  direct Device-C interaction rather than the removed V13 fixture.
- The user **cannot yet be said to converse with it through Device A over USB
  AetherLink**, because the physical Device-A-to-Device-C hub link and protocol
  negotiation have not yet been performed.
- The final success classification remains unissued.
