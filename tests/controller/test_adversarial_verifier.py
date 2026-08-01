from __future__ import annotations

import hashlib

from aethersparse.controller.adversarial import run_adversarial_verifier_experiment
from aethersparse.controller.linking import EntityRegistry
from aethersparse.controller.models import (
    AnswerShape,
    CanonicalEntity,
    ControllerResult,
    EvidenceRecord,
    ExactSourceSpan,
    RequiredFacet,
    StructuredClaim,
)
from aethersparse.controller.pipeline import StructuredController


def _answer(query_id: str) -> ControllerResult:
    text = "Ada Lovelace was born on 1815-12-10."
    span = ExactSourceSpan(
        span_id=f"span:{query_id}",
        document_id="doc:ada",
        source_title="Ada Lovelace",
        source_revision="1",
        source_url="https://example.test/ada",
        source_family="source:ada",
        char_start=0,
        char_end=len(text),
        text=text,
        text_hash=f"sha256:{hashlib.sha256(text.encode()).hexdigest()}",
    )
    claim = StructuredClaim(
        claim_id=f"claim:{query_id}",
        subject_entity_id="entity:ada",
        relation_family="birth",
        object_value="1815-12-10",
        occurred_at="1815-12-10",
        answer_shape=AnswerShape.DATE,
        source_span_ids=(span.span_id,),
    )
    record = EvidenceRecord(
        claim=claim,
        source_spans=(span,),
        entity_fit=1.0,
        relation_fit=1.0,
        answerability=1.0,
        answer_shape_fit=1.0,
        temporal_fit=1.0,
        attribution_fit=1.0,
        source_quality=1.0,
        facet_coverage=(
            RequiredFacet.SUBJECT,
            RequiredFacet.RELATION,
            RequiredFacet.TIME,
            RequiredFacet.SOURCE,
        ),
    )
    registry = EntityRegistry(
        (
            CanonicalEntity(
                entity_id="entity:ada",
                title="Ada Lovelace",
                relation_families=("birth",),
            ),
        )
    )
    return StructuredController(registry).answer(
        query_id,
        "When was Ada Lovelace born?",
        (record,),
    )


def test_learned_adversarial_supplement_never_weakens_exact_verification() -> None:
    report = run_adversarial_verifier_experiment(
        (f"q:{index}", _answer(f"q:{index}")) for index in range(20)
    )

    assert report.source_answer_count == 20
    assert report.evaluation_mutation_count > 0
    assert report.deterministic_mutation_rejection_rate == 1.0
    assert report.incremental_mutations_rejected == 0
    assert report.retained_in_primary_runtime is False
    assert report.decision == "SUPPLEMENT_NO_INCREMENTAL_VALUE"
    assert report.model_bytes == 52
