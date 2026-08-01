"""Bounded evidence ranking, active graphs, and exact composition operations."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation

from aethersparse.controller.models import (
    AnswerShape,
    EvidenceGraph,
    EvidenceRankEntry,
    EvidenceRankTrace,
    EvidenceRecord,
    ExactSourceSpan,
    QueryFrame,
    RequiredFacet,
    StructuredClaim,
)

DATE_RE = re.compile(r"\b(\d{4})(?:-(\d{2})-(\d{2}))?\b")
QUANTITY_RE = re.compile(r"[-+]?\d+(?:[,.]\d+)?")
PREMISE_DESCRIPTION_RE = re.compile(
    r"\b(?:described|identified|classified|defined|known)\s+as\s+(.+?)(?:\?|$)",
    re.IGNORECASE,
)
PREMISE_TOKEN_RE = re.compile(r"[\w'-]+", re.UNICODE)
PREMISE_STOP = frozenset(
    {"a", "an", "the", "accurately", "actually", "really", "source", "article"}
)


def evidence_score(record: EvidenceRecord, seen_source_families: set[str] | None = None) -> float:
    """Score exact evidence with an explicit lineage-diversity component."""

    families = {span.source_family for span in record.source_spans}
    diversity = 1.0 if not seen_source_families or not families <= seen_source_families else 0.0
    weights = (0.22, 0.18, 0.17, 0.13, 0.08, 0.08, 0.08, 0.06)
    values = (
        record.entity_fit,
        record.relation_fit,
        record.answerability,
        record.answer_shape_fit,
        record.temporal_fit,
        record.attribution_fit,
        record.source_quality,
        diversity,
    )
    return sum(weight * value for weight, value in zip(weights, values, strict=True))


def rank_evidence(
    records: tuple[EvidenceRecord, ...], limit: int = 16
) -> tuple[EvidenceRecord, ...]:
    if limit < 1 or limit > 64:
        raise ValueError("evidence limit must remain between one and 64")
    # First order by intrinsic score; then greedily reward new source lineages.
    pending = list(records)
    selected: list[EvidenceRecord] = []
    families: set[str] = set()
    while pending and len(selected) < limit:
        pending.sort(
            key=lambda item: (
                -evidence_score(item, families),
                item.claim.claim_id,
            )
        )
        current = pending.pop(0)
        selected.append(current)
        families.update(span.source_family for span in current.source_spans)
    return tuple(selected)


def make_evidence_rank_trace(
    records: tuple[EvidenceRecord, ...],
    graph: EvidenceGraph,
    *,
    limit: int = 64,
) -> EvidenceRankTrace:
    ranked = rank_evidence(records, limit=min(limit, len(records) or 1)) if records else ()
    selected = {claim.claim_id for claim in graph.claims}
    seen_families: set[str] = set()
    entries: list[EvidenceRankEntry] = []
    for index, record in enumerate(ranked, start=1):
        families = tuple(sorted({span.source_family for span in record.source_spans}))
        entries.append(
            EvidenceRankEntry(
                rank=index,
                claim_id=record.claim.claim_id,
                score=evidence_score(record, seen_families),
                source_families=families,
                facet_coverage=record.facet_coverage,
                selected_for_graph=record.claim.claim_id in selected,
            )
        )
        seen_families.update(families)
    return EvidenceRankTrace(
        candidate_count=len(records),
        bounded_candidate_limit=limit,
        entries=tuple(entries),
        selected_claim_ids=tuple(claim.claim_id for claim in graph.claims),
    )


def _covered(records: tuple[EvidenceRecord, ...]) -> set[RequiredFacet]:
    covered: set[RequiredFacet] = set()
    for record in records:
        covered.update(record.facet_coverage)
    return covered


def build_evidence_graph(
    query_id: str,
    frame: QueryFrame,
    records: tuple[EvidenceRecord, ...],
    *,
    max_claims: int = 32,
    max_spans: int = 48,
    max_entities: int = 24,
) -> EvidenceGraph:
    """Compile a bounded disposable graph from already selected exact evidence."""

    if not (1 <= max_claims <= 64 and 1 <= max_spans <= 96 and 1 <= max_entities <= 48):
        raise ValueError("active graph bounds exceed the controller contract")
    ranked = rank_evidence(records, limit=min(max_claims, len(records) or 1)) if records else ()
    claims: list[StructuredClaim] = []
    spans: dict[str, ExactSourceSpan] = {}
    entities: dict[str, None] = {}
    for record in ranked:
        if len(claims) >= max_claims:
            break
        claim = record.claim
        claim_spans = [
            span for span in record.source_spans if span.span_id in claim.source_span_ids
        ]
        new_span_count = sum(span.span_id not in spans for span in claim_spans)
        claim_entities = {
            value
            for value in (
                claim.subject_entity_id,
                claim.object_entity_id,
                claim.location_entity_id,
                claim.speaker_entity_id,
            )
            if value
        }
        new_entity_count = sum(value not in entities for value in claim_entities)
        if (
            len(spans) + new_span_count > max_spans
            or len(entities) + new_entity_count > max_entities
        ):
            continue
        claims.append(claim)
        for span in claim_spans:
            spans[span.span_id] = span
        for entity in sorted(claim_entities):
            entities[entity] = None

    contradictions = detect_contradictions(tuple(claims), spans)
    kept_records = tuple(record for record in ranked if record.claim in claims)
    covered = _covered(kept_records)
    missing = tuple(facet for facet in frame.required_facets if facet not in covered)
    return EvidenceGraph(
        query_id=query_id,
        entities=tuple(entities),
        claims=tuple(claims),
        source_spans=tuple(spans.values()),
        source_families=tuple(sorted({span.source_family for span in spans.values()})),
        contradictions=contradictions,
        required_facets=frame.required_facets,
        missing_facets=missing,
    )


def join_claims_by_entity(
    graph: EvidenceGraph,
    entity_id: str,
    relation_families: tuple[str, ...] = (),
) -> tuple[StructuredClaim, ...]:
    relations = set(relation_families)
    return tuple(
        claim
        for claim in graph.claims
        if entity_id
        in {
            claim.subject_entity_id,
            claim.object_entity_id,
            claim.location_entity_id,
            claim.speaker_entity_id,
        }
        and (not relations or claim.relation_family in relations)
    )


def _date_key(value: str | None) -> tuple[int, int, int]:
    match = DATE_RE.search(value or "")
    if not match:
        return (date.max.year, 12, 31)
    return (int(match.group(1)), int(match.group(2) or 1), int(match.group(3) or 1))


def temporal_order(claims: tuple[StructuredClaim, ...]) -> tuple[StructuredClaim, ...]:
    return tuple(sorted(claims, key=lambda claim: (_date_key(claim.occurred_at), claim.claim_id)))


def _decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    match = QUANTITY_RE.search(value.replace(",", ""))
    if not match:
        return None
    try:
        return Decimal(match.group(0))
    except InvalidOperation:
        return None


def compare_quantities(
    left: StructuredClaim,
    right: StructuredClaim,
) -> int | None:
    """Return -1/0/1 only when exact units are compatible."""

    left_value = _decimal(left.quantity_value or left.object_value)
    right_value = _decimal(right.quantity_value or right.object_value)
    if left_value is None or right_value is None:
        return None
    left_unit = (left.quantity_unit or "").casefold()
    right_unit = (right.quantity_unit or "").casefold()
    if left_unit != right_unit:
        return None
    return (left_value > right_value) - (left_value < right_value)


def resolve_attribution(
    graph: EvidenceGraph,
    quotation: str,
) -> tuple[StructuredClaim, ...]:
    key = " ".join(quotation.casefold().split())
    return tuple(
        claim
        for claim in graph.claims
        if claim.speaker_entity_id
        and claim.quotation
        and key in " ".join(claim.quotation.casefold().split())
    )


def group_by_source_family(graph: EvidenceGraph) -> dict[str, tuple[str, ...]]:
    span_families = {span.span_id: span.source_family for span in graph.source_spans}
    grouped: dict[str, list[str]] = defaultdict(list)
    for claim in graph.claims:
        for family in sorted(
            {span_families[span] for span in claim.source_span_ids if span in span_families}
        ):
            grouped[family].append(claim.claim_id)
    return {family: tuple(dict.fromkeys(ids)) for family, ids in sorted(grouped.items())}


def evaluate_premise(
    graph: EvidenceGraph,
    *,
    subject_entity_id: str,
    relation_family: str,
    object_value: str,
) -> str:
    matching = [
        claim
        for claim in graph.claims
        if claim.subject_entity_id == subject_entity_id and claim.relation_family == relation_family
    ]
    if not matching:
        return "UNKNOWN"
    normalized = " ".join(object_value.casefold().split())
    supporting = [
        claim for claim in matching if " ".join(claim.object_value.casefold().split()) == normalized
    ]
    if supporting and all(claim.polarity == "positive" for claim in supporting):
        return "SUPPORTED"
    if supporting and any(claim.polarity == "negative" for claim in supporting):
        return "REFUTED"
    return "REFUTED" if any(claim.polarity == "positive" for claim in matching) else "UNKNOWN"


def evaluate_frame_premise(frame: QueryFrame, graph: EvidenceGraph) -> str:
    """Conservatively compare a descriptive premise with selected exact claims.

    This operates on the runtime frame and evidence only. It never reads a
    benchmark label. Unsupported premise forms remain UNKNOWN.
    """

    if frame.answer_shape is not AnswerShape.VERIFICATION or not frame.premise_claims:
        return "UNKNOWN"
    match = PREMISE_DESCRIPTION_RE.search(frame.premise_claims[0])
    if match is None:
        return "UNKNOWN"
    premise_terms = {
        token.casefold()
        for token in PREMISE_TOKEN_RE.findall(match.group(1))
        if token.casefold() not in PREMISE_STOP
    }
    if not premise_terms:
        return "UNKNOWN"
    matching_claims = tuple(
        claim
        for claim in graph.claims
        if not frame.candidate_entity_ids
        or claim.subject_entity_id in frame.candidate_entity_ids
    )
    if not matching_claims:
        return "UNKNOWN"
    for claim in matching_claims:
        claim_terms = {
            token.casefold() for token in PREMISE_TOKEN_RE.findall(claim.object_value)
        }
        if premise_terms <= claim_terms:
            return "SUPPORTED"
    # Exact evidence about the linked subject exists, but none supports the
    # concrete descriptive object. This is a bounded refutation, not a guess.
    return "REFUTED"


def detect_contradictions(
    claims: tuple[StructuredClaim, ...],
    spans: Mapping[str, ExactSourceSpan] | None = None,
) -> tuple[tuple[str, str], ...]:
    """Detect incompatible objects/polarities for the same directed relation."""

    by_key: dict[tuple[str, str], list[StructuredClaim]] = defaultdict(list)
    for claim in claims:
        by_key[(claim.subject_entity_id, claim.relation_family)].append(claim)
    span_families: dict[str, str] = {}
    if spans:
        span_families = {
            span_id: str(getattr(span, "source_family", "")) for span_id, span in spans.items()
        }
    contradictions: list[tuple[str, str]] = []
    for siblings in by_key.values():
        for index, left in enumerate(siblings):
            for right in siblings[index + 1 :]:
                incompatible = (
                    left.object_value.casefold() != right.object_value.casefold()
                    or left.polarity != right.polarity
                )
                if not incompatible:
                    continue
                left_families = {span_families.get(item, "") for item in left.source_span_ids}
                right_families = {span_families.get(item, "") for item in right.source_span_ids}
                if span_families and left_families == right_families:
                    continue
                pair = sorted((left.claim_id, right.claim_id))
                contradictions.append((pair[0], pair[1]))
    return tuple(sorted(set(contradictions)))


def specific_missing_facet_request(graph: EvidenceGraph) -> tuple[str, str] | None:
    """Name one typed retrieval need; never return a broad frontier expansion."""

    if not graph.missing_facets or not graph.entities:
        return None
    return (graph.entities[0], graph.missing_facets[0].value)


def make_hard_negatives(
    positive: EvidenceRecord,
    pool: tuple[EvidenceRecord, ...],
    limit: int = 6,
) -> tuple[tuple[str, str], ...]:
    """Label bounded error modes for selector training without changing evidence."""

    negatives: list[tuple[str, str]] = []
    claim = positive.claim
    positive_families = {span.source_family for span in positive.source_spans}
    for candidate in pool:
        other = candidate.claim
        label: str | None = None
        if (
            other.subject_entity_id == claim.subject_entity_id
            and other.relation_family != claim.relation_family
        ):
            label = "correct_entity_wrong_relation"
        elif (
            other.relation_family == claim.relation_family
            and other.subject_entity_id != claim.subject_entity_id
        ):
            label = "correct_relation_wrong_entity"
        elif claim.occurred_at and other.occurred_at and claim.occurred_at != other.occurred_at:
            label = "right_terms_wrong_date"
        elif (
            claim.quotation
            and other.quotation
            and claim.speaker_entity_id != other.speaker_entity_id
        ):
            label = "quotation_wrong_speaker"
        elif (
            claim.answer_shape is AnswerShape.COMPARISON
            and other.subject_entity_id != claim.subject_entity_id
        ):
            label = "wrong_comparison_side"
        elif {span.source_family for span in candidate.source_spans} <= positive_families:
            label = "duplicate_source_family"
        elif candidate.answerability < 0.25:
            label = "related_without_answer"
        if label:
            negatives.append((other.claim_id, label))
        if len(negatives) >= limit:
            break
    return tuple(negatives)
