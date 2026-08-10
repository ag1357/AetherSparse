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


_DURATION_UNITS = frozenset(
    {
        "year", "years", "month", "months", "week", "weeks", "day", "days",
        "hour", "hours", "minute", "minutes", "second", "seconds",
        "decade", "decades", "century", "centuries",
    }
)


def _claim_value_kind(claim: StructuredClaim) -> str:
    """Typed value kind of a claim: date | duration | percent | count | text."""
    surface = (claim.quantity_value or claim.object_value or "").casefold()
    if claim.occurred_at:
        return "date"
    if "%" in surface or "percent" in surface or "per cent" in surface:
        return "percent"
    unit = (claim.quantity_unit or "").casefold().strip()
    if unit in _DURATION_UNITS:
        return "duration"
    import re

    if re.fullmatch(r"\d{4}(-\d{1,2}(-\d{1,2})?)?", surface.strip()):
        return "date"
    if re.search(r"\d", surface) and (
        claim.quantity_value or re.fullmatch(r"[\d,.]+", surface.strip())
    ):
        return "count"
    return "text"


def _demanded_value_kind(frame: QueryFrame) -> str | None:
    """Value kind demanded by the question's shape and quantity cues."""
    if frame.answer_shape is AnswerShape.DATE:
        return "date"
    if frame.answer_shape not in (AnswerShape.QUANTITY, AnswerShape.COMPARISON):
        return None
    query = frame.normalized_query.casefold()
    if any(
        cue in query
        for cue in ("how long", "how old", "duration", "how many years",
                    "how many days", "how many months", "how many hours")
    ):
        return "duration"
    if (
        "percentage" in query
        or "percent" in query
        or "what proportion" in query
        or "%" in query
    ):
        return "percent"
    if "how many" in query or "how much" in query:
        return "count"
    return None


def _value_fit(frame: QueryFrame, claim: StructuredClaim) -> float:
    """Typed-binding tiebreak: does the claim's value kind match the demand?"""
    demanded = _demanded_value_kind(frame)
    if demanded is None:
        return 0.5
    kind = _claim_value_kind(claim)
    if kind == demanded:
        return 1.0
    if kind == "text":
        return 0.2
    return 0.0


_SLOT_SHAPE_BY_RELATION: dict[str, AnswerShape] = {
    "definition": AnswerShape.DEFINITION,
    "date": AnswerShape.DATE,
    "birth": AnswerShape.DATE,
    "death": AnswerShape.DATE,
    "quantity": AnswerShape.QUANTITY,
    "comparison": AnswerShape.QUANTITY,
    "quotation": AnswerShape.QUOTATION,
}


def _slot_shape(frame: QueryFrame) -> AnswerShape | None:
    """Per-slot shape demand for LIST containers (Phase 4.1).

    A LIST frame is a container; each slot carries the question's underlying
    per-item demand.  'Using both sources, what are X and Y?' requests the
    definition relation, so each slot wants a DEFINITION-shaped claim — not
    a LIST-shaped one.  Measured @10k (taxonomy list:wrong_parts): without
    slot shapes, LIST-shaped extraction residue ('{{reflist}}', infobox
    tails) outranks the per-entity gloss claims (shape_fit 1.0 vs 0.2) even
    though the glosses are present in the graph with higher confidence.
    """

    requested = set(frame.requested_relation_families)
    shapes = {_SLOT_SHAPE_BY_RELATION.get(relation) for relation in requested}
    shapes.discard(None)
    if len(shapes) == 1:
        return shapes.pop()
    return None


def _compare_pair_selection(
    frame: QueryFrame,
    scored: list[tuple[float, StructuredClaim]],
    compatible: list[tuple[float, StructuredClaim]],
    *,
    lenient: bool,
) -> AnswerSelection | None:
    """One comparison pairing pass over the compatible pool."""

    for index, (left_score, left) in enumerate(compatible):
        for right_score, right in compatible[index + 1 :]:
            if left.subject_entity_id == right.subject_entity_id:
                continue
            if left.relation_family != right.relation_family:
                continue
            comparison = compare_quantities(
                left, right, surface_percent_compat=lenient
            )
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


def select_answer(frame: QueryFrame, graph: EvidenceGraph) -> AnswerSelection | None:
    if not graph.claims or graph.contradictions:
        return None
    # Phase 3.2: tie-breaks after _claim_fit — typed value-kind binding first,
    # then span salience (claims bound to earlier-ranked evidence spans win);
    # claim_id stays last so ordering remains deterministic.
    span_index = {span.span_id: rank for rank, span in enumerate(graph.source_spans)}

    def _salience(claim: StructuredClaim) -> int:
        return min(
            (span_index[sid] for sid in claim.source_span_ids if sid in span_index),
            default=len(span_index),
        )

    scored = sorted(
        ((_claim_fit(frame, claim), claim) for claim in graph.claims),
        key=lambda item: (
            -item[0],
            -_value_fit(frame, item[1]),
            _salience(item[1]),
            item[1].claim_id,
        ),
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
        # Phase 4.2: pair claims of the demanded value kind only.  Without
        # the filter, 'Compare the stated % values ...' paired year- or
        # count-valued claims (taxonomy comparison:value_mismatch).  Fall
        # back to the unfiltered pool when the kind is under-populated.
        demanded = _demanded_value_kind(frame)
        if demanded is not None:
            typed = [
                item
                for item in compatible
                if _claim_value_kind(item[1]) == demanded
            ]
            # Apply only when a typed pair across two subjects can exist;
            # otherwise keep the unfiltered pool (answering with a
            # cross-kind pair beats abstaining on an answer-case).
            if len({item[1].subject_entity_id for item in typed}) >= 2:
                compatible = typed
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
        # Strict unit equality first; a lenient percent-surface pass only
        # when no strict pair exists (preserves strict pair ordering).
        for lenient in (False, True):
            selection = _compare_pair_selection(
                frame, scored, compatible, lenient=lenient
            )
            if selection is not None:
                return selection
        return None

    if frame.answer_shape is AnswerShape.LIST:
        list_targets = tuple(dict.fromkeys(frame.candidate_entity_ids))
        slot = _slot_shape(frame)

        def _slot_key(item: tuple[float, StructuredClaim]) -> tuple[object, ...]:
            score, claim = item
            compatible = {AnswerShape.ENTITY, AnswerShape.DEFINITION, AnswerShape.LIST}
            if claim.answer_shape is AnswerShape.LIST:
                container_fit = 1.0
            elif {claim.answer_shape, AnswerShape.LIST} <= compatible:
                container_fit = 0.2
            else:
                container_fit = 0.0
            if slot is None or claim.answer_shape is slot:
                slot_fit = 1.0
            elif {claim.answer_shape, slot} <= compatible:
                slot_fit = 0.2
            else:
                slot_fit = 0.0
            # Recompute the shape_fit term against the slot shape instead of
            # the LIST container (0.21 is the shape_fit weight in _claim_fit).
            return (
                -(score - 0.21 * container_fit + 0.21 * slot_fit),
                -claim.confidence,
                _salience(claim),
                claim.claim_id,
            )

        ordered = sorted(scored, key=_slot_key) if slot is not None else scored
        selected_rows: list[tuple[float, StructuredClaim]] = []
        if list_targets:
            for target in list_targets:
                matching = [
                    item for item in ordered if item[1].subject_entity_id == target
                ]
                if not matching:
                    return None
                selected_rows.append(matching[0])
        else:
            seen_subjects: set[str] = set()
            for item in ordered:
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
