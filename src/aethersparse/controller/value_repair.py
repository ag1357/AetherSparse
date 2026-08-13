"""Gold-blind upstream state repair from exact replay source spans.

This module adds bounded typed value hypotheses to a ``MicroState``. It accepts
no benchmark answer and can only copy surfaces from already retained immutable
source spans. Existing claims remain first so previously reachable controller
trajectories are not displaced by bounded search argument coverage.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from pydantic import Field

from aethersparse.controller.micro_ops import MicroState
from aethersparse.controller.models import AnswerShape, FrozenModel
from aethersparse.controller.value_lattice import (
    SourceValueRegion,
    TypedValueCandidate,
    scan_typed_value_region,
)


class ValueRepairResult(FrozenModel):
    state: MicroState
    scanned_source_spans: int = Field(ge=0)
    proposed_hypotheses: int = Field(ge=0)
    added_claims: int = Field(ge=0)
    added_source_spans: int = Field(ge=0)
    candidate_capacity_exhausted: bool


def _strings(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, list | tuple):
        return tuple(str(item) for item in value if str(item))
    return ()


def _shape(value: object) -> AnswerShape | None:
    try:
        shape = AnswerShape(str(value))
    except ValueError:
        return None
    return (
        shape
        if shape
        in {
            AnswerShape.DATE,
            AnswerShape.QUANTITY,
            AnswerShape.COMPARISON,
            AnswerShape.QUOTATION,
        }
        else None
    )


def _region(raw: dict[str, object]) -> SourceValueRegion | None:
    required = (
        "document_id",
        "source_title",
        "source_revision",
        "source_url",
        "source_family",
        "text",
    )
    if any(not isinstance(raw.get(key), str) for key in required):
        return None
    return SourceValueRegion(
        document_id=str(raw["document_id"]),
        source_title=str(raw["source_title"]),
        source_revision=str(raw["source_revision"]),
        source_url=str(raw["source_url"]),
        source_family=str(raw["source_family"]),
        char_start=int(str(raw.get("char_start", 0))),
        text=str(raw["text"]),
    )


def _hypotheses(
    state: MicroState,
    source_span_id: str,
) -> tuple[tuple[str | None, str | None], ...]:
    frame_entities = _strings(state.frame.get("candidate_entity_ids"))
    frame_relations = _strings(state.frame.get("requested_relation_families"))
    local_entities: list[str] = []
    local_relations: list[str] = []
    for claim in state.claims:
        if source_span_id not in _strings(claim.get("source_span_ids")):
            continue
        entity = claim.get("subject_entity_id")
        relation = claim.get("relation_family")
        if isinstance(entity, str) and entity and entity not in local_entities:
            local_entities.append(entity)
        if isinstance(relation, str) and relation and relation not in local_relations:
            local_relations.append(relation)
    entities: tuple[str | None, ...] = tuple(local_entities) or frame_entities or (None,)
    relations: tuple[str | None, ...] = tuple(local_relations) or frame_relations or (None,)
    return tuple((entity, relation) for entity in entities for relation in relations)


def _claim(candidate: TypedValueCandidate, answer_shape: AnswerShape) -> dict[str, object]:
    identity = "\x1f".join(
        (
            candidate.source_span.span_id,
            candidate.subject_entity_hypothesis or "",
            candidate.relation_hypothesis or "",
            answer_shape.value,
        )
    )
    claim_id = f"claim:value-v11:{hashlib.sha256(identity.encode()).hexdigest()[:24]}"
    value_type = candidate.value_type.value
    return {
        "claim_id": claim_id,
        "subject_entity_id": candidate.subject_entity_hypothesis or "",
        "relation_family": candidate.relation_hypothesis or "",
        "object_value": candidate.raw_surface,
        "answer_shape": answer_shape.value,
        "source_span_ids": [candidate.source_span.span_id],
        "grounding": "CORPUS_GROUNDED",
        "polarity": "positive",
        "object_entity_id": None,
        "occurred_at": candidate.raw_surface if value_type == "date" else None,
        "location_entity_id": None,
        "speaker_entity_id": candidate.speaker_attribution,
        "quotation": candidate.raw_surface if value_type == "quotation" else None,
        "quantity_value": candidate.raw_surface if value_type == "quantity" else None,
        "quantity_unit": candidate.unit,
        "confidence": candidate.confidence,
    }


def _candidate_key(candidate: TypedValueCandidate) -> tuple[str, str, str, str]:
    return (
        candidate.source_span.span_id,
        candidate.value_type.value,
        candidate.subject_entity_hypothesis or "",
        candidate.relation_hypothesis or "",
    )


def _iter_candidates(
    state: MicroState,
    answer_shape: AnswerShape,
    *,
    scan_capacity_per_hypothesis: int,
) -> Iterable[TypedValueCandidate]:
    for raw_span in state.source_spans:
        region = _region(raw_span)
        if region is None:
            continue
        source_span_id = str(raw_span.get("span_id", ""))
        for entity, relation in _hypotheses(state, source_span_id):
            lattice = scan_typed_value_region(
                region,
                answer_shape=answer_shape,
                subject_entity_id=entity,
                relation=relation,
                capacity=scan_capacity_per_hypothesis,
            )
            yield from lattice.candidates


def repair_state_with_typed_values(
    state: MicroState,
    *,
    total_claim_capacity: int = 64,
    scan_capacity_per_hypothesis: int = 64,
) -> ValueRepairResult:
    """Add exact typed hypotheses without consulting correctness or benchmark gold."""

    if total_claim_capacity < 1 or total_claim_capacity > 256:
        raise ValueError("total_claim_capacity must be in [1,256]")
    answer_shape = _shape(state.frame.get("answer_shape"))
    if answer_shape is None or len(state.claims) >= total_claim_capacity:
        return ValueRepairResult(
            state=state,
            scanned_source_spans=0,
            proposed_hypotheses=0,
            added_claims=0,
            added_source_spans=0,
            candidate_capacity_exhausted=len(state.claims) >= total_claim_capacity,
        )
    candidates: list[TypedValueCandidate] = []
    seen_candidates: set[tuple[str, str, str, str]] = set()
    for candidate in _iter_candidates(
        state,
        answer_shape,
        scan_capacity_per_hypothesis=scan_capacity_per_hypothesis,
    ):
        key = _candidate_key(candidate)
        if key not in seen_candidates:
            seen_candidates.add(key)
            candidates.append(candidate)
    existing_claim_ids = {str(item.get("claim_id", "")) for item in state.claims}
    available = total_claim_capacity - len(state.claims)
    new_claims: list[dict[str, object]] = []
    new_spans: dict[str, dict[str, object]] = {}
    for candidate in candidates:
        claim = _claim(candidate, answer_shape)
        if str(claim["claim_id"]) in existing_claim_ids:
            continue
        if len(new_claims) >= available:
            break
        existing_claim_ids.add(str(claim["claim_id"]))
        new_claims.append(claim)
        new_spans[candidate.source_span.span_id] = candidate.source_span.model_dump(mode="json")
    existing_span_ids = {str(item.get("span_id", "")) for item in state.source_spans}
    added_spans = tuple(
        span for span_id, span in new_spans.items() if span_id not in existing_span_ids
    )
    repaired = state.model_copy(
        update={
            "claims": (*state.claims, *new_claims),
            "source_spans": (*state.source_spans, *added_spans),
        }
    )
    return ValueRepairResult(
        state=repaired,
        scanned_source_spans=len(state.source_spans),
        proposed_hypotheses=len(candidates),
        added_claims=len(new_claims),
        added_source_spans=len(added_spans),
        candidate_capacity_exhausted=len(new_claims) < len(candidates),
    )
