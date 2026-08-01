from __future__ import annotations

import hashlib

from aethersparse.controller.answering import make_answer_plan, realize_plan, select_answer
from aethersparse.controller.evidence import (
    build_evidence_graph,
    compare_quantities,
    detect_contradictions,
    evaluate_frame_premise,
    evaluate_premise,
    join_claims_by_entity,
    make_hard_negatives,
    specific_missing_facet_request,
    temporal_order,
)
from aethersparse.controller.framing import QueryFramer
from aethersparse.controller.linking import EntityRegistry
from aethersparse.controller.models import (
    AnswerShape,
    CanonicalEntity,
    ControllerDisposition,
    EvidenceRecord,
    ExactSourceSpan,
    QueryFrame,
    RequiredFacet,
    StructuredClaim,
)
from aethersparse.controller.pipeline import StructuredController
from aethersparse.controller.verification import adversarial_mutations, verify_realization


def _span(span_id: str, text: str, family: str = "source-a") -> ExactSourceSpan:
    return ExactSourceSpan(
        span_id=span_id,
        document_id=f"doc:{span_id}",
        source_title="Fixture",
        source_revision="1",
        source_url=f"https://example.test/{span_id}",
        source_family=family,
        char_start=0,
        char_end=len(text),
        text=text,
        text_hash=f"sha256:{hashlib.sha256(text.encode()).hexdigest()}",
    )


def _record(
    claim: StructuredClaim,
    span: ExactSourceSpan,
    facets: tuple[RequiredFacet, ...],
) -> EvidenceRecord:
    return EvidenceRecord(
        claim=claim,
        source_spans=(span,),
        entity_fit=1.0,
        relation_fit=1.0,
        answerability=1.0,
        answer_shape_fit=1.0,
        temporal_fit=1.0,
        attribution_fit=1.0,
        source_quality=1.0,
        facet_coverage=facets,
    )


def _birth_record() -> EvidenceRecord:
    span = _span("span:ada-birth", "Ada Lovelace was born on 1815-12-10.")
    claim = StructuredClaim(
        claim_id="claim:ada-birth",
        subject_entity_id="entity:ada",
        relation_family="birth",
        object_value="1815-12-10",
        occurred_at="1815-12-10",
        answer_shape=AnswerShape.DATE,
        source_span_ids=(span.span_id,),
    )
    return _record(
        claim,
        span,
        (
            RequiredFacet.SUBJECT,
            RequiredFacet.RELATION,
            RequiredFacet.TIME,
            RequiredFacet.SOURCE,
        ),
    )


def test_controller_answers_only_after_exact_plan_verification() -> None:
    registry = EntityRegistry(
        (
            CanonicalEntity(
                entity_id="entity:ada",
                title="Ada Lovelace",
                relation_families=("birth",),
            ),
        )
    )
    result = StructuredController(registry).answer(
        "q:ada",
        "When was Ada Lovelace born?",
        (_birth_record(),),
    )

    assert result.disposition is ControllerDisposition.ANSWER
    assert result.answer is not None
    assert result.answer.text == "1815-12-10"
    assert result.verification is not None and result.verification.passed
    assert result.verification.bound_surface_count == 1


def test_lazy_provider_boundary_is_bounded() -> None:
    registry = EntityRegistry(
        (
            CanonicalEntity(
                entity_id="entity:ada",
                title="Ada Lovelace",
                relation_families=("birth",),
            ),
        )
    )

    class Provider:
        observed_limit = 0

        def retrieve(self, frame: QueryFrame, *, limit: int) -> tuple[EvidenceRecord, ...]:
            assert frame.candidate_entity_ids == ("entity:ada",)
            self.observed_limit = limit
            return (_birth_record(),)

        def corpus_coverage(self, frame: QueryFrame) -> bool:
            return bool(frame.candidate_entity_ids)

    provider = Provider()
    result = StructuredController(registry).query(
        "q:lazy",
        "When was Ada Lovelace born?",
        provider,
        evidence_limit=8,
    )
    assert result.disposition is ControllerDisposition.ANSWER
    assert provider.observed_limit == 8


def test_bounded_graph_and_exact_operations() -> None:
    first = _birth_record()
    second_span = _span("span:death", "Ada Lovelace died on 1852-11-27.", "source-b")
    second_claim = StructuredClaim(
        claim_id="claim:ada-death",
        subject_entity_id="entity:ada",
        relation_family="death",
        object_value="1852-11-27",
        occurred_at="1852-11-27",
        answer_shape=AnswerShape.DATE,
        source_span_ids=(second_span.span_id,),
    )
    second = _record(
        second_claim,
        second_span,
        (RequiredFacet.SUBJECT, RequiredFacet.RELATION, RequiredFacet.TIME),
    )
    frame = QueryFramer().frame("When was Ada Lovelace born?")
    graph = build_evidence_graph("q", frame, (first, second), max_claims=2)

    assert len(join_claims_by_entity(graph, "entity:ada")) == 2
    assert [claim.claim_id for claim in temporal_order(graph.claims)] == [
        "claim:ada-birth",
        "claim:ada-death",
    ]
    assert (
        evaluate_premise(
            graph,
            subject_entity_id="entity:ada",
            relation_family="birth",
            object_value="1815-12-10",
        )
        == "SUPPORTED"
    )
    assert specific_missing_facet_request(graph) is None
    location_frame = frame.model_copy(
        update={"required_facets": (*frame.required_facets, RequiredFacet.LOCATION)}
    )
    location_graph = build_evidence_graph("q:location", location_frame, (first, second))
    assert specific_missing_facet_request(location_graph) == ("entity:ada", "location")


def test_runtime_descriptive_premise_is_refuted_without_gold_labels() -> None:
    record = _birth_record()
    frame = QueryFramer().frame(
        "Is Ada Lovelace accurately described as an ocean on Mars?"
    ).model_copy(update={"candidate_entity_ids": ("entity:ada",)})
    graph = build_evidence_graph("q:premise", frame, (record,))

    assert evaluate_frame_premise(frame, graph) == "REFUTED"


def test_quantity_comparison_requires_compatible_units() -> None:
    common = dict(
        relation_family="height",
        answer_shape=AnswerShape.QUANTITY,
        source_span_ids=("span",),
    )
    left = StructuredClaim(
        claim_id="left",
        subject_entity_id="a",
        object_value="10 m",
        quantity_value="10",
        quantity_unit="m",
        **common,
    )
    right = StructuredClaim(
        claim_id="right",
        subject_entity_id="b",
        object_value="9 m",
        quantity_value="9",
        quantity_unit="m",
        **common,
    )
    incompatible = right.model_copy(update={"quantity_unit": "ft"})
    assert compare_quantities(left, right) == 1
    assert compare_quantities(left, incompatible) is None


def test_comparison_plan_preserves_and_reverifies_direction() -> None:
    left_span = _span("span:left", "Tower A is 10 m tall.", "source-a")
    right_span = _span("span:right", "Tower B is 9 m tall.", "source-b")
    left_claim = StructuredClaim(
        claim_id="claim:left",
        subject_entity_id="entity:a",
        relation_family="height",
        object_value="10 m",
        quantity_value="10",
        quantity_unit="m",
        answer_shape=AnswerShape.QUANTITY,
        source_span_ids=(left_span.span_id,),
    )
    right_claim = StructuredClaim(
        claim_id="claim:right",
        subject_entity_id="entity:b",
        relation_family="height",
        object_value="9 m",
        quantity_value="9",
        quantity_unit="m",
        answer_shape=AnswerShape.QUANTITY,
        source_span_ids=(right_span.span_id,),
    )
    facets = (
        RequiredFacet.SUBJECT,
        RequiredFacet.RELATION,
        RequiredFacet.QUANTITY,
        RequiredFacet.COMPARISON_A,
        RequiredFacet.COMPARISON_B,
        RequiredFacet.SOURCE,
    )
    frame = QueryFrame(
        normalized_query="Compare Tower A and Tower B height.",
        entity_mentions=(),
        candidate_entity_ids=("entity:a", "entity:b"),
        requested_relation_families=("height",),
        answer_shape=AnswerShape.COMPARISON,
        required_facets=facets,
        comparison_targets=("Tower A", "Tower B"),
        uncertainty=0.0,
        clarification_need=False,
    )
    graph = build_evidence_graph(
        "q:comparison",
        frame,
        (_record(left_claim, left_span, facets), _record(right_claim, right_span, facets)),
    )
    selection = select_answer(frame, graph)
    assert selection is not None and selection.answer_text == "10 m > 9 m"
    plan = make_answer_plan(selection, graph)
    answer = realize_plan(plan)
    assert answer.text == "10 m > 9 m."
    assert verify_realization(frame, graph, plan, answer).passed
    reversed_answer = answer.model_copy(update={"text": "10 m < 9 m."})
    assert not verify_realization(frame, graph, plan, reversed_answer).passed

    reversed_graph = graph.model_copy(update={"claims": tuple(reversed(graph.claims))})
    stable_selection = select_answer(frame, reversed_graph)
    assert stable_selection is not None
    assert stable_selection.answer_text == "10 m > 9 m"


def test_multi_source_list_composes_each_exact_definition_once() -> None:
    left_span = _span("span:list-left", "Alpha is the first description.", "source-a")
    right_span = _span("span:list-right", "Beta is the second description.", "source-b")
    left_claim = StructuredClaim(
        claim_id="claim:list-left",
        subject_entity_id="entity:alpha",
        relation_family="definition",
        object_value="the first description",
        answer_shape=AnswerShape.DEFINITION,
        source_span_ids=(left_span.span_id,),
    )
    right_claim = StructuredClaim(
        claim_id="claim:list-right",
        subject_entity_id="entity:beta",
        relation_family="definition",
        object_value="the second description",
        answer_shape=AnswerShape.DEFINITION,
        source_span_ids=(right_span.span_id,),
    )
    facets = (
        RequiredFacet.SUBJECT,
        RequiredFacet.RELATION,
        RequiredFacet.OBJECT,
        RequiredFacet.SOURCE,
    )
    frame = QueryFrame(
        normalized_query="Using both sources, what are Alpha and Beta?",
        entity_mentions=(),
        candidate_entity_ids=("entity:alpha", "entity:beta"),
        requested_relation_families=("definition",),
        answer_shape=AnswerShape.LIST,
        required_facets=facets,
        uncertainty=0.0,
        clarification_need=False,
    )
    graph = build_evidence_graph(
        "q:list",
        frame,
        (
            _record(left_claim, left_span, facets),
            _record(right_claim, right_span, facets),
        ),
    )

    selection = select_answer(frame, graph)
    assert selection is not None
    assert selection.answer_text == "the first description; the second description"
    plan = make_answer_plan(selection, graph)
    answer = realize_plan(plan)
    assert answer.text == selection.answer_text
    assert len(answer.bindings) == 2
    assert verify_realization(frame, graph, plan, answer).passed


def test_contradiction_requires_distinct_lineage() -> None:
    positive = _birth_record()
    other_span = _span("span:other", "Ada was born in 1816.", "source-b")
    other_claim = positive.claim.model_copy(
        update={
            "claim_id": "claim:other",
            "object_value": "1816",
            "source_span_ids": (other_span.span_id,),
        }
    )
    spans = {
        positive.source_spans[0].span_id: positive.source_spans[0],
        other_span.span_id: other_span,
    }
    assert detect_contradictions((positive.claim, other_claim), spans) == (
        ("claim:ada-birth", "claim:other"),
    )


def test_hard_negative_labels_cover_entity_relation_and_lineage() -> None:
    positive = _birth_record()
    wrong_relation = positive.model_copy(
        update={
            "claim": positive.claim.model_copy(
                update={"claim_id": "wrong-rel", "relation_family": "death"}
            )
        }
    )
    wrong_entity = positive.model_copy(
        update={
            "claim": positive.claim.model_copy(
                update={"claim_id": "wrong-ent", "subject_entity_id": "entity:bob"}
            )
        }
    )
    labels = dict(make_hard_negatives(positive, (wrong_relation, wrong_entity)))
    assert labels == {
        "wrong-rel": "correct_entity_wrong_relation",
        "wrong-ent": "correct_relation_wrong_entity",
    }


def test_adversarial_surface_mutation_fails_exact_verification() -> None:
    record = _birth_record()
    frame = QueryFrame(
        normalized_query="When was Ada Lovelace born?",
        entity_mentions=(),
        candidate_entity_ids=("entity:ada",),
        requested_relation_families=("birth",),
        answer_shape=AnswerShape.DATE,
        required_facets=(RequiredFacet.SUBJECT, RequiredFacet.RELATION, RequiredFacet.TIME),
        uncertainty=0.0,
        clarification_need=False,
    )
    graph = build_evidence_graph("q", frame, (record,))
    selection = select_answer(frame, graph)
    assert selection is not None
    plan = make_answer_plan(selection, graph)
    answer = realize_plan(plan)
    mutations = adversarial_mutations(answer)
    assert mutations
    assert all(not verify_realization(frame, graph, plan, item).passed for item in mutations)
    addition = answer.model_copy(update={"text": f"{answer.text} Unsupported addition."})
    assert not verify_realization(frame, graph, plan, addition).passed
