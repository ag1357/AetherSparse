from __future__ import annotations

import hashlib

from aethersparse.controller.models import (
    AnswerShape,
    EvidenceRecord,
    ExactSourceSpan,
    RequiredFacet,
    StructuredClaim,
)
from aethersparse.controller.nonlinear_ranker import (
    RankerExample,
    ranker_features,
    rerank_records,
    train_tiny_evidence_mlp,
)


def _record(identity: str, *, entity_fit: float, relevant: bool) -> EvidenceRecord:
    text = "supported" if relevant else "distractor"
    span = ExactSourceSpan(
        span_id=f"span:{identity}",
        document_id=f"doc:{identity}",
        source_title=identity,
        source_revision="1",
        source_url=f"https://example.test/{identity}",
        source_family=f"source:{identity}",
        char_start=0,
        char_end=len(text),
        text=text,
        text_hash=f"sha256:{hashlib.sha256(text.encode()).hexdigest()}",
    )
    return EvidenceRecord(
        claim=StructuredClaim(
            claim_id=f"claim:{identity}",
            subject_entity_id=f"entity:{identity}",
            relation_family="definition",
            object_value=text,
            answer_shape=AnswerShape.DEFINITION,
            source_span_ids=(span.span_id,),
            confidence=1.0 if relevant else 0.4,
        ),
        source_spans=(span,),
        entity_fit=entity_fit,
        relation_fit=1.0,
        answerability=1.0,
        answer_shape_fit=1.0,
        temporal_fit=1.0,
        attribution_fit=1.0,
        source_quality=1.0,
        facet_coverage=(RequiredFacet.SUBJECT, RequiredFacet.OBJECT),
    )


def test_tiny_nonlinear_ranker_learns_frozen_hard_negatives() -> None:
    positive = _record("positive", entity_fit=1.0, relevant=True)
    negative = _record("negative", entity_fit=0.0, relevant=False)
    examples = tuple(
        RankerExample(
            query_id=f"q:{index}",
            features=ranker_features(record),
            relevant=relevant,
        )
        for index in range(12)
        for record, relevant in ((positive, True), (negative, False))
    )
    model = train_tiny_evidence_mlp(examples)

    assert rerank_records(model, (negative, positive))[0] is positive
    assert model.int8_model_bytes < 128
    assert model.macs_per_record == 60
