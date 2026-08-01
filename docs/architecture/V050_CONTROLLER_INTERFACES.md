# v0.5 Structured Controller Interfaces

The controller performs bounded active cognition over a flat structured corpus. It does not build
or route through cognitive cells.

## Construction

```python
controller = StructuredController(linker)
```

`linker` implements `FrameLinker.link_frame(frame)`. `EntityRegistry` is the in-memory reference;
progressive corpus packs may provide a lazy SQLite implementation with the same method.

## Lazy corpus query

```python
result = controller.query(
    query_id="q:0001",
    query="When was Ada Lovelace born?",
    provider=flat_pack_adapter,
    prior_entity_ids=(),
    evidence_limit=64,
)
```

The provider implements:

```python
retrieve(frame, *, limit: int) -> tuple[EvidenceRecord, ...]
corpus_coverage(frame) -> bool
```

The controller always supplies a limit between 1 and 64. An adapter should perform bounded title,
redirect, alias, anchor, lexical, and structured-claim lookups and return exact source spans. It must
not load the complete corpus into memory. The provider owns workload measurement; the controller
does not disguise estimated I/O as measured I/O.

`StructuredController.answer(..., evidence=...)` remains available for compact fixtures and for an
already instrumented retriever.

## Result

`ControllerResult` contains:

- the complete `QueryFrame`;
- the bounded `EvidenceGraph` and its missing facets/contradictions;
- the deterministic `EvidenceRankTrace`;
- the `AnswerSelection`, including rejected alternative claim IDs;
- the exact `AnswerPlan`;
- exactly one seven-way `ControllerDisposition`;
- an optional pointer-copy `RealizedAnswer`;
- the exact deterministic `VerificationReport`;
- a short disposition reason.

An answer is removed from the result unless the disposition is `ANSWER`. Every factual surface in
an answer binds to an answer-plan claim, a structured claim, and an immutable source span.
