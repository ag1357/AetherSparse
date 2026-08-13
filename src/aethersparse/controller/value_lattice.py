"""Exact, bounded typed value candidates for probabilistic interpretation.

The lattice may retain competing interpretations, but every factual surface is
an exact pointer into immutable evidence.  Learned specialists may assign or
update confidence; they may not create a surface that is absent from a supplied
source region.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import Field, model_validator

from aethersparse.controller.models import AnswerShape, ExactSourceSpan, FrozenModel

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from aethersparse.controller.models import EvidenceRecord


class ValueType(StrEnum):
    """Coarse source-bound value kinds carried by the workspace."""

    DATE = "date"
    QUANTITY = "quantity"
    QUOTATION = "quotation"
    ENTITY = "entity"
    DEFINITION = "definition"
    TEXT = "text"


class SourceValueRegion(FrozenModel):
    """One bounded immutable source region supplied to value enumeration."""

    document_id: str
    source_title: str
    source_revision: str
    source_url: str
    source_family: str
    char_start: int = Field(ge=0)
    text: str
    section: str | None = None


class TypedValueCandidate(FrozenModel):
    """One interpretation hypothesis whose factual text is exact evidence."""

    source_span: ExactSourceSpan
    raw_surface: str
    canonical_representation: str
    value_type: ValueType
    subject_entity_hypothesis: str | None = None
    relation_hypothesis: str | None = None
    time_scope: str | None = None
    unit: str | None = None
    speaker_attribution: str | None = None
    section: str | None = None
    source_document_id: str
    confidence: float = Field(ge=0.0, le=1.0)
    provenance: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def exact_source_contract(self) -> TypedValueCandidate:
        if self.raw_surface != self.source_span.text:
            raise ValueError("typed value surface must equal its exact source span")
        if self.source_document_id != self.source_span.document_id:
            raise ValueError("typed value document must equal its source span document")
        expected = f"sha256:{hashlib.sha256(self.raw_surface.encode()).hexdigest()}"
        if self.source_span.text_hash != expected:
            raise ValueError("typed value source hash does not match its exact surface")
        if self.source_span.span_id not in self.provenance:
            raise ValueError("typed value provenance must name its exact source span")
        if not self.canonical_representation:
            raise ValueError("typed value canonical representation must not be empty")
        return self


class TypedValueLattice(FrozenModel):
    """A bounded set of competing exact value hypotheses."""

    candidates: tuple[TypedValueCandidate, ...]
    capacity: int = Field(default=64, ge=1, le=256)
    dropped_candidates: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def bounded_and_unique(self) -> TypedValueLattice:
        if len(self.candidates) > self.capacity:
            raise ValueError("typed value lattice exceeds its declared capacity")
        keys = {
            (
                item.source_span.span_id,
                item.value_type,
                item.subject_entity_hypothesis,
                item.relation_hypothesis,
            )
            for item in self.candidates
        }
        if len(keys) != len(self.candidates):
            raise ValueError("typed value lattice contains duplicate hypotheses")
        return self


_MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|"
    "November|December"
)
TYPED_DATE_RE = re.compile(
    rf"\b(?:{_MONTHS})\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?\b"
    rf"|\b\d{{1,2}}\s+(?:{_MONTHS})(?:\s+\d{{4}})?\b"
    r"|\b(?:1[0-9]{3}|20[0-9]{2}|2100)-\d{2}-\d{2}\b"
    r"|\b(?:1[0-9]{3}|20[0-9]{2}|2100)\b",
    re.IGNORECASE,
)
_QUANTITY_UNIT = (
    r"km|m|cm|mm|kilomet(?:er|re)s?|miles?|met(?:er|re)s?|feet|ft|kg|kilograms?|"
    r"grams?|tonnes?|tons?|lit(?:er|re)s?|percent|people|inhabitants?|degrees?|"
    r"°\s*[CF]?|days?|years?|months?|hours?|minutes?|seconds?|million|billion"
)
_QUANTITY_SUFFIX = rf"(?:%|(?:{_QUANTITY_UNIT})(?!\w))"
TYPED_QUANTITY_RE = re.compile(
    rf"(?<!\w)[-+]?(?:\d+(?:[.,]\d+)*)\s*{_QUANTITY_SUFFIX}",
    re.IGNORECASE,
)
TYPED_QUOTATION_RE = re.compile(r'["“]([^"”\n]{3,300})["”]')


def canonicalize_value(value: str) -> str:
    """Return a comparison form while preserving the exact raw surface separately."""

    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _value_type(shape: AnswerShape) -> ValueType:
    if shape is AnswerShape.DATE:
        return ValueType.DATE
    if shape in {AnswerShape.QUANTITY, AnswerShape.COMPARISON}:
        return ValueType.QUANTITY
    if shape is AnswerShape.QUOTATION:
        return ValueType.QUOTATION
    if shape is AnswerShape.ENTITY:
        return ValueType.ENTITY
    if shape is AnswerShape.DEFINITION:
        return ValueType.DEFINITION
    return ValueType.TEXT


def _unit(surface: str) -> str | None:
    match = re.search(r"(?:[A-Za-z]+|%|°\s*[CF]?)\s*$", surface, re.IGNORECASE)
    return match.group(0).strip() if match else None


def _candidate(
    region: SourceValueRegion,
    *,
    start: int,
    end: int,
    value_type: ValueType,
    subject_entity_id: str | None,
    relation: str | None,
    confidence: float,
) -> TypedValueCandidate:
    surface = region.text[start:end]
    absolute_start = region.char_start + start
    absolute_end = region.char_start + end
    digest = hashlib.sha256(surface.encode()).hexdigest()
    identity = f"{region.document_id}:{absolute_start}:{absolute_end}"
    span_id = f"span:value-v11:{hashlib.sha256(identity.encode()).hexdigest()[:24]}"
    span = ExactSourceSpan(
        span_id=span_id,
        document_id=region.document_id,
        source_title=region.source_title,
        source_revision=region.source_revision,
        source_url=region.source_url,
        source_family=region.source_family,
        char_start=absolute_start,
        char_end=absolute_end,
        text=surface,
        text_hash=f"sha256:{digest}",
    )
    return TypedValueCandidate(
        source_span=span,
        raw_surface=surface,
        canonical_representation=canonicalize_value(surface),
        value_type=value_type,
        subject_entity_hypothesis=subject_entity_id,
        relation_hypothesis=relation,
        time_scope=surface if value_type is ValueType.DATE else None,
        unit=_unit(surface) if value_type is ValueType.QUANTITY else None,
        section=region.section,
        source_document_id=region.document_id,
        confidence=confidence,
        provenance=(span_id,),
    )


def _quantity_spans(match: re.Match[str]) -> tuple[tuple[int, int], ...]:
    """Retain bounded lexical alternatives instead of committing prematurely.

    Signed/unsigned and grouped/dotted numeric surfaces are genuinely ambiguous
    at extraction time.  The alternatives remain exact substrings and are left
    for subject/relation-aware ranking rather than rewritten into new facts.
    """

    start, end = match.span(0)
    surface = match.group(0)
    spans = [(start, end)]
    numeric_end = len(surface)
    unit = re.search(rf"\s*{_QUANTITY_SUFFIX}\s*$", surface, re.IGNORECASE)
    if unit is not None:
        numeric_end = unit.start()
    numeric = surface[:numeric_end]
    prefix = 1 if numeric.startswith(("+", "-")) else 0
    if prefix:
        spans.append((start + prefix, end))
    for separator in (",", "."):
        positions = [index for index, char in enumerate(numeric) if char == separator]
        for position in positions[-2:]:
            suffix_start = position + 1
            if suffix_start < len(numeric) and numeric[suffix_start].isdigit():
                spans.append((start + suffix_start, end))
    return tuple(dict.fromkeys(spans))


def scan_typed_value_region(
    region: SourceValueRegion,
    *,
    answer_shape: AnswerShape,
    subject_entity_id: str | None = None,
    relation: str | None = None,
    confidence: float = 0.65,
    capacity: int = 64,
) -> TypedValueLattice:
    """Enumerate bounded exact typed candidates before sentence pruning."""

    if capacity < 1 or capacity > 256:
        raise ValueError("capacity must be in [1,256]")
    matches: list[tuple[int, int, ValueType]] = []
    if answer_shape is AnswerShape.DATE:
        matches.extend(
            (*match.span(0), ValueType.DATE) for match in TYPED_DATE_RE.finditer(region.text)
        )
    elif answer_shape in {AnswerShape.QUANTITY, AnswerShape.COMPARISON}:
        for match in TYPED_QUANTITY_RE.finditer(region.text):
            matches.extend((*span, ValueType.QUANTITY) for span in _quantity_spans(match))
    elif answer_shape is AnswerShape.QUOTATION:
        matches.extend(
            (*match.span(1), ValueType.QUOTATION)
            for match in TYPED_QUOTATION_RE.finditer(region.text)
        )

    candidates: list[TypedValueCandidate] = []
    seen: set[tuple[int, int, ValueType]] = set()
    for start, end, value_type in matches:
        key = (start, end, value_type)
        if key in seen or start == end:
            continue
        seen.add(key)
        candidates.append(
            _candidate(
                region,
                start=start,
                end=end,
                value_type=value_type,
                subject_entity_id=subject_entity_id,
                relation=relation,
                confidence=confidence,
            )
        )
    dropped = max(0, len(candidates) - capacity)
    return TypedValueLattice(
        candidates=tuple(candidates[:capacity]),
        capacity=capacity,
        dropped_candidates=dropped,
    )


def lattice_from_evidence(
    records: Sequence[EvidenceRecord], *, capacity: int = 64
) -> TypedValueLattice:
    """Lift exact current evidence records into competing typed hypotheses."""

    candidates: list[TypedValueCandidate] = []
    for record in records:
        shape = record.claim.answer_shape
        value_type = _value_type(shape)
        for span in record.source_spans:
            if span.span_id not in record.claim.source_span_ids:
                continue
            candidates.append(
                TypedValueCandidate(
                    source_span=span,
                    raw_surface=span.text,
                    canonical_representation=canonicalize_value(span.text),
                    value_type=value_type,
                    subject_entity_hypothesis=record.claim.subject_entity_id,
                    relation_hypothesis=record.claim.relation_family,
                    time_scope=record.claim.occurred_at,
                    unit=record.claim.quantity_unit,
                    speaker_attribution=record.claim.speaker_entity_id,
                    section=None,
                    source_document_id=span.document_id,
                    confidence=record.claim.confidence,
                    provenance=(span.span_id, record.claim.claim_id),
                )
            )
    return merge_value_lattices(
        (TypedValueLattice(candidates=tuple(candidates)),), capacity=capacity
    )


def merge_value_lattices(
    lattices: Iterable[TypedValueLattice], *, capacity: int = 64
) -> TypedValueLattice:
    """Stable confidence-ranked merge with exact-pointer deduplication."""

    if capacity < 1 or capacity > 256:
        raise ValueError("capacity must be in [1,256]")
    candidates = [candidate for lattice in lattices for candidate in lattice.candidates]
    candidates.sort(
        key=lambda item: (
            -item.confidence,
            item.source_document_id,
            item.source_span.char_start,
            item.source_span.char_end,
            item.value_type.value,
            item.subject_entity_hypothesis or "",
            item.relation_hypothesis or "",
        )
    )
    unique: list[TypedValueCandidate] = []
    seen: set[tuple[str, ValueType, str | None, str | None]] = set()
    for candidate in candidates:
        key = (
            candidate.source_span.span_id,
            candidate.value_type,
            candidate.subject_entity_hypothesis,
            candidate.relation_hypothesis,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return TypedValueLattice(
        candidates=tuple(unique[:capacity]),
        capacity=capacity,
        dropped_candidates=max(0, len(unique) - capacity),
    )
