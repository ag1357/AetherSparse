"""Immutable contracts for the v0.5 bounded structured controller.

These records describe active per-query cognition over a flat corpus.  They are
not corpus partitions and they never authorize a learned component to invent a
source, claim, or address.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AnswerShape(StrEnum):
    ENTITY = "entity"
    DATE = "date"
    QUANTITY = "quantity"
    QUOTATION = "quotation"
    DEFINITION = "definition"
    EVENT = "event"
    PROCESS = "process"
    LIST = "list"
    COMPARISON = "comparison"
    EXPLANATION = "explanation"
    VERIFICATION = "verification"
    UNKNOWN = "unknown"


class RequiredFacet(StrEnum):
    SUBJECT = "subject"
    RELATION = "relation"
    OBJECT = "object"
    TIME = "time"
    LOCATION = "location"
    SPEAKER = "speaker"
    QUOTATION = "quotation"
    QUANTITY = "quantity"
    COMPARISON_A = "comparison_side_a"
    COMPARISON_B = "comparison_side_b"
    REASON = "reason"
    SOURCE = "source"


class ControllerDisposition(StrEnum):
    ANSWER = "ANSWER"
    CLARIFY = "CLARIFY"
    ABSTAIN = "ABSTAIN"
    INCORRECT_PREMISE = "INCORRECT_PREMISE"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    OUT_OF_CORPUS = "OUT_OF_CORPUS"
    VERIFICATION_FAILURE = "VERIFICATION_FAILURE"


class ResolutionMethod(StrEnum):
    EXACT_TITLE = "exact_title"
    REDIRECT = "redirect"
    ALIAS = "alias"
    ANCHOR = "anchor"
    FUZZY = "fuzzy"
    UNKNOWN = "unknown"


class CanonicalEntity(FrozenModel):
    entity_id: str
    title: str
    entity_types: tuple[str, ...] = ()
    redirects: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    anchors: tuple[str, ...] = ()
    relation_families: tuple[str, ...] = ()


class EntityCandidate(FrozenModel):
    entity_id: str
    title: str
    method: ResolutionMethod
    name_score: float = Field(ge=0.0, le=1.0)
    type_score: float = Field(ge=0.0, le=1.0)
    relation_score: float = Field(ge=0.0, le=1.0)
    context_score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)


class EntityMention(FrozenModel):
    surface: str
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    candidates: tuple[EntityCandidate, ...] = ()
    selected_entity_id: str | None = None
    selected_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    resolution_method: ResolutionMethod = ResolutionMethod.UNKNOWN
    copy_status: Literal["linked", "unknown_but_copyable", "ambiguous"] = "unknown_but_copyable"

    @model_validator(mode="after")
    def offsets_match_surface(self) -> EntityMention:
        if self.char_end - self.char_start != len(self.surface):
            raise ValueError("mention offsets must cover the copied surface exactly")
        if self.copy_status == "linked" and self.selected_entity_id is None:
            raise ValueError("linked mention requires a selected entity")
        return self


class DiscourseReference(FrozenModel):
    surface: str
    antecedent_entity_ids: tuple[str, ...] = ()
    confidence: float = Field(ge=0.0, le=1.0)


class QueryFrame(FrozenModel):
    normalized_query: str
    entity_mentions: tuple[EntityMention, ...]
    candidate_entity_ids: tuple[str, ...]
    requested_relation_families: tuple[str, ...]
    answer_shape: AnswerShape
    required_facets: tuple[RequiredFacet, ...]
    temporal_constraints: tuple[str, ...] = ()
    location_constraints: tuple[str, ...] = ()
    attribution_constraints: tuple[str, ...] = ()
    comparison_targets: tuple[str, ...] = ()
    premise_claims: tuple[str, ...] = ()
    discourse_references: tuple[DiscourseReference, ...] = ()
    uncertainty: float = Field(ge=0.0, le=1.0)
    clarification_need: bool


# Entity ID bands (schema reservation, Mission 4 — landed as schema, not
# capability).  Corpus compiles mint "as:v050:entity:{sha256[:24]}" (see
# sqlite_provider._entity_id).  The reserved high band
# "as:user:entity:{sha256[:24]}" belongs to user-defined entities created by
# a later mission's persistent conversational memory.  Both bands share the
# binder's ID space; resolvers MUST NOT mint user-band IDs from corpus
# content, nor corpus-band IDs from conversation content.
CORPUS_ENTITY_ID_PREFIX = "as:v050:entity:"
USER_ENTITY_ID_PREFIX = "as:user:entity:"


class ExactSourceSpan(FrozenModel):
    span_id: str
    document_id: str
    source_title: str
    source_revision: str
    source_url: str
    source_family: str
    # Schema reservation (Mission 4): CORPUS spans are document+revision+span;
    # CONVERSATION spans (a later mission) will be conversation+turn+span,
    # carried in the same fields (document_id=conversation id,
    # source_revision=turn id, char bounds=span within the turn).
    source_class: Literal["CORPUS", "CONVERSATION"] = "CORPUS"
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    text: str
    text_hash: str

    @model_validator(mode="after")
    def valid_bounds(self) -> ExactSourceSpan:
        if self.char_end <= self.char_start:
            raise ValueError("source span end must follow start")
        return self


class StructuredClaim(FrozenModel):
    claim_id: str
    subject_entity_id: str
    relation_family: str
    object_value: str
    answer_shape: AnswerShape
    source_span_ids: tuple[str, ...] = Field(min_length=1)
    # Schema reservation (Mission 4): CORPUS_GROUNDED claims are extracted
    # from corpus evidence; USER_ASSERTED claims originate from user
    # statements in conversation.  USER_ASSERTED claims are ineligible to
    # satisfy any factual verification path (verification.py treats
    # grounding as a hard gate once conversation sources exist).
    grounding: Literal["CORPUS_GROUNDED", "USER_ASSERTED"] = "CORPUS_GROUNDED"
    polarity: Literal["positive", "negative"] = "positive"
    object_entity_id: str | None = None
    occurred_at: str | None = None
    location_entity_id: str | None = None
    speaker_entity_id: str | None = None
    quotation: str | None = None
    quantity_value: str | None = None
    quantity_unit: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class EvidenceRecord(FrozenModel):
    claim: StructuredClaim
    source_spans: tuple[ExactSourceSpan, ...] = Field(min_length=1)
    entity_fit: float = Field(ge=0.0, le=1.0)
    relation_fit: float = Field(ge=0.0, le=1.0)
    answerability: float = Field(ge=0.0, le=1.0)
    answer_shape_fit: float = Field(ge=0.0, le=1.0)
    temporal_fit: float = Field(ge=0.0, le=1.0)
    attribution_fit: float = Field(ge=0.0, le=1.0)
    source_quality: float = Field(ge=0.0, le=1.0)
    facet_coverage: tuple[RequiredFacet, ...] = ()

    @model_validator(mode="after")
    def spans_match_claim(self) -> EvidenceRecord:
        present = {span.span_id for span in self.source_spans}
        if not set(self.claim.source_span_ids).issubset(present):
            raise ValueError("evidence record lacks a source span named by its claim")
        return self


class EvidenceGraph(FrozenModel):
    query_id: str
    entities: tuple[str, ...]
    claims: tuple[StructuredClaim, ...]
    source_spans: tuple[ExactSourceSpan, ...]
    source_families: tuple[str, ...]
    contradictions: tuple[tuple[str, str], ...]
    required_facets: tuple[RequiredFacet, ...]
    missing_facets: tuple[RequiredFacet, ...]
    hypotheses: tuple[str, ...] = ()


class EvidenceRankEntry(FrozenModel):
    rank: int = Field(ge=1)
    claim_id: str
    score: float = Field(ge=0.0, le=1.0)
    source_families: tuple[str, ...]
    facet_coverage: tuple[RequiredFacet, ...]
    selected_for_graph: bool


class EvidenceRankTrace(FrozenModel):
    candidate_count: int = Field(ge=0)
    bounded_candidate_limit: int = Field(ge=1, le=64)
    entries: tuple[EvidenceRankEntry, ...]
    selected_claim_ids: tuple[str, ...]


class AnswerSelection(FrozenModel):
    answer_text: str
    answer_shape: AnswerShape
    selected_claim_ids: tuple[str, ...] = Field(min_length=1)
    selected_source_span_ids: tuple[str, ...] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    rejected_claim_ids: tuple[str, ...] = ()


class PlannedClaim(FrozenModel):
    plan_claim_id: str
    surface: str
    structured_claim_ids: tuple[str, ...] = Field(min_length=1)
    source_span_ids: tuple[str, ...] = Field(min_length=1)


class AnswerPlan(FrozenModel):
    answer_shape: AnswerShape
    planned_claims: tuple[PlannedClaim, ...] = Field(min_length=1)
    construction: Literal["direct_extraction", "pointer_copy", "deterministic_grammar"]
    comparison_operator: Literal["<", "=", ">"] | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class SurfaceBinding(FrozenModel):
    plan_claim_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    surface: str
    structured_claim_ids: tuple[str, ...] = Field(min_length=1)
    source_span_ids: tuple[str, ...] = Field(min_length=1)


class RealizedAnswer(FrozenModel):
    text: str
    bindings: tuple[SurfaceBinding, ...] = Field(min_length=1)


class VerificationFinding(FrozenModel):
    code: str
    passed: bool
    detail: str


class VerificationReport(FrozenModel):
    passed: bool
    findings: tuple[VerificationFinding, ...]
    bound_surface_count: int = Field(ge=0)


class ControllerResult(FrozenModel):
    frame: QueryFrame
    graph: EvidenceGraph
    evidence_trace: EvidenceRankTrace
    selection: AnswerSelection | None = None
    plan: AnswerPlan | None = None
    disposition: ControllerDisposition
    answer: RealizedAnswer | None = None
    verification: VerificationReport | None = None
    reason: str
