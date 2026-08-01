"""Exact span selection, pointer-copy planning, and bounded realization."""

from __future__ import annotations

from typing import Literal

from aethersparse.controller.evidence import compare_quantities
from aethersparse.controller.models import (
    AnswerPlan,
    AnswerSelection,
    AnswerShape,
    EvidenceGraph,
    PlannedClaim,
    QueryFrame,
    RealizedAnswer,
    StructuredClaim,
    SurfaceBinding,
)


def _claim_fit(frame: QueryFrame, claim: StructuredClaim) -> float:
    entity_fit = (
        1.0
        if not frame.candidate_entity_ids
        or claim.subject_entity_id in frame.candidate_entity_ids
        or claim.object_entity_id in frame.candidate_entity_ids
        else 0.0
    )
    relation_fit = (
        1.0
        if not frame.requested_relation_families
        or claim.relation_family in frame.requested_relation_families
        else 0.0
    )
    shape_fit = (
        1.0
        if frame.answer_shape is AnswerShape.UNKNOWN or claim.answer_shape is frame.answer_shape
        else 0.2
        if {claim.answer_shape, frame.answer_shape}
        <= {AnswerShape.ENTITY, AnswerShape.DEFINITION, AnswerShape.LIST}
        else 0.0
    )
    temporal_fit = (
        1.0
        if not frame.temporal_constraints
        else float(
            any(
                value in (claim.occurred_at or claim.object_value)
                for value in frame.temporal_constraints
            )
        )
    )
    attribution_fit = (
        1.0
        if not frame.attribution_constraints
        else float(bool(claim.speaker_entity_id or claim.quotation))
    )
    return (
        0.32 * entity_fit
        + 0.27 * relation_fit
        + 0.21 * shape_fit
        + 0.08 * temporal_fit
        + 0.07 * attribution_fit
        + 0.05 * claim.confidence
    )


def select_answer(frame: QueryFrame, graph: EvidenceGraph) -> AnswerSelection | None:
    if not graph.claims or graph.contradictions:
        return None
    scored = sorted(
        ((_claim_fit(frame, claim), claim) for claim in graph.claims),
        key=lambda item: (-item[0], item[1].claim_id),
    )
    if not scored or scored[0][0] < 0.72:
        return None

    if frame.answer_shape is AnswerShape.COMPARISON:
        targets = set(frame.candidate_entity_ids)
        compatible = [
            (score, claim)
            for score, claim in scored
            if not targets or claim.subject_entity_id in targets
        ]
        if len(frame.candidate_entity_ids) >= 2:
            target_order = {
                entity_id: index
                for index, entity_id in enumerate(frame.candidate_entity_ids)
            }
            compatible.sort(
                key=lambda item: (
                    target_order.get(item[1].subject_entity_id, len(target_order)),
                    -item[0],
                    item[1].claim_id,
                )
            )
        for index, (left_score, left) in enumerate(compatible):
            for right_score, right in compatible[index + 1 :]:
                if left.subject_entity_id == right.subject_entity_id:
                    continue
                if left.relation_family != right.relation_family:
                    continue
                comparison = compare_quantities(left, right)
                if comparison is None:
                    continue
                symbol = "=" if comparison == 0 else ">" if comparison > 0 else "<"
                text = f"{left.object_value} {symbol} {right.object_value}"
                return AnswerSelection(
                    answer_text=text,
                    answer_shape=AnswerShape.COMPARISON,
                    selected_claim_ids=(left.claim_id, right.claim_id),
                    selected_source_span_ids=tuple(
                        dict.fromkeys((*left.source_span_ids, *right.source_span_ids))
                    ),
                    confidence=min(left_score, right_score),
                    rejected_claim_ids=tuple(
                        claim.claim_id for _, claim in scored if claim not in {left, right}
                    ),
                )
        return None

    if frame.answer_shape is AnswerShape.LIST:
        list_targets = tuple(dict.fromkeys(frame.candidate_entity_ids))
        selected_rows: list[tuple[float, StructuredClaim]] = []
        if list_targets:
            for target in list_targets:
                matching = [
                    item for item in scored if item[1].subject_entity_id == target
                ]
                if not matching:
                    return None
                selected_rows.append(matching[0])
        else:
            seen_subjects: set[str] = set()
            for item in scored:
                if item[1].subject_entity_id in seen_subjects:
                    continue
                selected_rows.append(item)
                seen_subjects.add(item[1].subject_entity_id)
                if len(selected_rows) >= 6:
                    break
        if not selected_rows:
            return None
        selected_claims = tuple(item[1] for item in selected_rows)
        selected_ids = {claim.claim_id for claim in selected_claims}
        return AnswerSelection(
            answer_text="; ".join(claim.object_value for claim in selected_claims),
            answer_shape=AnswerShape.LIST,
            selected_claim_ids=tuple(claim.claim_id for claim in selected_claims),
            selected_source_span_ids=tuple(
                dict.fromkeys(
                    span_id
                    for claim in selected_claims
                    for span_id in claim.source_span_ids
                )
            ),
            confidence=min(score for score, _claim in selected_rows),
            rejected_claim_ids=tuple(
                claim.claim_id for _, claim in scored if claim.claim_id not in selected_ids
            ),
        )

    best_score, best = scored[0]
    value = (
        best.quotation
        if frame.answer_shape is AnswerShape.QUOTATION and best.quotation
        else best.object_value
    )
    if not value:
        return None
    return AnswerSelection(
        answer_text=value,
        answer_shape=best.answer_shape,
        selected_claim_ids=(best.claim_id,),
        selected_source_span_ids=best.source_span_ids,
        confidence=best_score,
        rejected_claim_ids=tuple(claim.claim_id for _, claim in scored[1:]),
    )


def make_answer_plan(
    selection: AnswerSelection,
    graph: EvidenceGraph,
) -> AnswerPlan:
    claims = {claim.claim_id: claim for claim in graph.claims}
    selected = tuple(claims[claim_id] for claim_id in selection.selected_claim_ids)
    if selection.answer_shape is AnswerShape.COMPARISON:
        if len(selected) != 2:
            raise ValueError("comparison requires exactly two selected claims")
        surfaces: tuple[str, ...] = (selected[0].object_value, selected[1].object_value)
        operators: tuple[Literal["<", "=", ">"], ...] = ("<", "=", ">")
        operator = next(
            (item for item in operators if f" {item} " in selection.answer_text),
            None,
        )
        if operator is None:
            raise ValueError("comparison selection lacks an exact operator")
        construction: Literal["direct_extraction", "pointer_copy", "deterministic_grammar"] = (
            "deterministic_grammar"
        )
    elif selection.answer_shape is AnswerShape.LIST:
        surfaces = tuple(claim.object_value for claim in selected)
        operator = None
        construction = "deterministic_grammar"
    else:
        surfaces = (selection.answer_text,)
        operator = None
        construction = "direct_extraction"
    planned = tuple(
        PlannedClaim(
            plan_claim_id=f"plan:{index}:{claim.claim_id}",
            surface=surface,
            structured_claim_ids=(claim.claim_id,),
            source_span_ids=claim.source_span_ids,
        )
        for index, (claim, surface) in enumerate(zip(selected, surfaces, strict=True))
    )
    return AnswerPlan(
        answer_shape=selection.answer_shape,
        planned_claims=planned,
        construction=construction,
        comparison_operator=operator,
        confidence=selection.confidence,
    )


def realize_plan(plan: AnswerPlan) -> RealizedAnswer:
    if plan.answer_shape is AnswerShape.COMPARISON:
        if len(plan.planned_claims) != 2:
            raise ValueError("comparison plan requires two copied values")
        left, right = plan.planned_claims
        if plan.comparison_operator is None:
            raise ValueError("comparison plan lacks an operator")
        # The operator is deterministic glue; each compared factual value is copied.
        text = f"{left.surface} {plan.comparison_operator} {right.surface}."
    elif plan.answer_shape is AnswerShape.LIST:
        text = "; ".join(claim.surface for claim in plan.planned_claims)
    else:
        text = plan.planned_claims[0].surface
    bindings: list[SurfaceBinding] = []
    cursor = 0
    for claim in plan.planned_claims:
        start = text.find(claim.surface, cursor)
        if start < 0:
            raise ValueError("planned copied surface is absent from realization")
        bindings.append(
            SurfaceBinding(
                plan_claim_id=claim.plan_claim_id,
                start=start,
                end=start + len(claim.surface),
                surface=claim.surface,
                structured_claim_ids=claim.structured_claim_ids,
                source_span_ids=claim.source_span_ids,
            )
        )
        cursor = start + len(claim.surface)
    return RealizedAnswer(text=text, bindings=tuple(bindings))
