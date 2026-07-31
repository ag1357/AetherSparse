"""Typed Gate 0 source, candidate, validation, and review contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from aethersparse.models import Disposition, KeyClass, PacketType, StrictModel


class AlignmentMethod(StrEnum):
    EXACT_RAW = "exact_raw"
    NORMALIZED_EQUIVALENT = "normalized_equivalent"


class CandidateState(StrEnum):
    CANDIDATE = "CANDIDATE"
    ACCEPTED = "ACCEPTED"
    EDITED = "EDITED"
    QUARANTINED = "QUARANTINED"
    REJECTED = "REJECTED"
    MERGED_DUPLICATE = "MERGED_DUPLICATE"


class ValidationDecision(StrEnum):
    PASS = "PASS"
    REVIEW = "REVIEW"
    FAIL = "FAIL"


class CheckStatus(StrEnum):
    PASS = "PASS"
    REVIEW = "REVIEW"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ReviewAction(StrEnum):
    ACCEPT = "ACCEPT"
    EDIT = "EDIT"
    QUARANTINE = "QUARANTINE"
    REJECT = "REJECT"
    MERGE_DUPLICATE = "MERGE_DUPLICATE"


class ReviewReason(StrEnum):
    ALIGNMENT_FIX = "ALIGNMENT_FIX"
    ENTITY_FIX = "ENTITY_FIX"
    RELATION_FIX = "RELATION_FIX"
    NEGATION_FIX = "NEGATION_FIX"
    TEMPORAL_FIX = "TEMPORAL_FIX"
    QUANTITY_UNIT_FIX = "QUANTITY_UNIT_FIX"
    ATTRIBUTION_FIX = "ATTRIBUTION_FIX"
    TYPE_FIX = "TYPE_FIX"
    DUPLICATE = "DUPLICATE"
    CONTRADICTION = "CONTRADICTION"
    UNSUPPORTED = "UNSUPPORTED"
    LICENSE_POLICY = "LICENSE_POLICY"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    OTHER = "OTHER"


class ReviewerKind(StrEnum):
    HUMAN = "human"
    AGENT = "agent"
    TEST = "test"


class GoldPartition(StrEnum):
    CALIBRATION = "calibration"
    DEVELOPMENT = "compiler_development"
    SEALED_GATE0 = "sealed_gate0"


class MetricStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    INFORMATIONAL = "INFORMATIONAL"


class QueryReviewStatus(StrEnum):
    PENDING_HUMAN_REVIEW = "PENDING_HUMAN_REVIEW"
    HUMAN_REVIEWED = "HUMAN_REVIEWED"


class FrozenSourceSnapshot(StrictModel):
    source_doc_id: str
    title: str
    source_url: str
    source_revision: str
    retrieved_at: datetime
    license: Literal["public_domain", "compatible_open_license"]
    source_group: str
    raw_text: str
    raw_content_hash: str
    raw_byte_length: int = Field(ge=0)
    raw_char_length: int = Field(ge=0)
    normalization_version: str
    normalized_text: str
    normalized_content_hash: str

    @model_validator(mode="after")
    def lengths_are_consistent(self) -> FrozenSourceSnapshot:
        if len(self.raw_text) != self.raw_char_length:
            raise ValueError("raw_char_length does not match raw_text")
        if len(self.raw_text.encode("utf-8")) != self.raw_byte_length:
            raise ValueError("raw_byte_length does not match UTF-8 raw_text")
        return self


class SourceAlignment(StrictModel):
    source_doc_id: str
    source_revision: str
    source_content_hash: str
    raw_char_start: int = Field(ge=0)
    raw_char_end: int = Field(gt=0)
    raw_byte_start: int = Field(ge=0)
    raw_byte_end: int = Field(gt=0)
    raw_text: str
    raw_text_hash: str
    normalized_char_start: int = Field(ge=0)
    normalized_char_end: int = Field(gt=0)
    normalized_text: str
    normalized_text_hash: str
    alignment_method: AlignmentMethod
    direct_quotation: bool = False


class ExtractorProvenance(StrictModel):
    extractor_identity: str
    extractor_version: str
    configuration_hash: str
    prompt_or_rule_version: str
    source_revision: str
    source_content_hash: str
    deterministic_cache_identity: str
    teacher_model: str | None = None
    teacher_tokens: int = Field(default=0, ge=0)
    estimated_rule_operations: int = Field(default=0, ge=0)


class QuantityValue(StrictModel):
    surface: str
    normalized_value: float
    normalized_unit: str
    owner_entity_id: str | None = None


class CandidateClaimUnit(StrictModel):
    claim_unit_id: str
    subject_id: str
    relation_id: str
    object_value: str
    evidence_surface: str
    alignment: SourceAlignment
    extractor_confidence: float = Field(ge=0.0, le=1.0)


class CandidatePacket(StrictModel):
    candidate_id: str
    schema_version: str = "0.3.0"
    packet_type: PacketType
    state: Literal[CandidateState.CANDIDATE] = CandidateState.CANDIDATE
    tier: Literal[1] = 1
    key_class: KeyClass
    source_doc_id: str
    source_revision: str
    source_content_hash: str
    primary_subject: str
    primary_relation: str
    primary_object: str
    entity_ids: tuple[str, ...]
    temporal_values: tuple[str, ...] = ()
    quantities: tuple[QuantityValue, ...] = ()
    polarity: Literal["positive", "negative"] = "positive"
    attribution: str | None = None
    payload: dict[str, Any]
    atomic_claims: tuple[CandidateClaimUnit, ...] = Field(min_length=1)
    extractor_confidence: float = Field(ge=0.0, le=1.0)
    extractor: ExtractorProvenance


class ExtractionRun(StrictModel):
    run_id: str
    started_at: datetime
    completed_at: datetime
    source_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    cache_hits: int = Field(ge=0)
    wall_clock_ms: float = Field(ge=0.0)
    teacher_tokens: int = Field(ge=0)
    teacher_cost_usd: float = Field(ge=0.0)
    estimated_rule_operations: int = Field(ge=0)
    configuration_hash: str
    candidate_set_hash: str


class ValidatorCheck(StrictModel):
    check_id: str
    status: CheckStatus
    detail: str


class ValidatorResult(StrictModel):
    candidate_id: str
    validator_identity: str
    validator_version: str
    independent_from_extractor: bool
    decision: ValidationDecision
    checks: tuple[ValidatorCheck, ...]
    duplicate_candidate_ids: tuple[str, ...] = ()
    near_duplicate_candidate_ids: tuple[str, ...] = ()
    contradiction_candidate_ids: tuple[str, ...] = ()
    result_hash: str


class ValidationRun(StrictModel):
    run_id: str
    started_at: datetime
    completed_at: datetime
    candidate_count: int = Field(ge=0)
    pass_count: int = Field(ge=0)
    review_count: int = Field(ge=0)
    fail_count: int = Field(ge=0)
    wall_clock_ms: float = Field(ge=0.0)
    configuration_hash: str
    result_set_hash: str


class ReviewRequest(StrictModel):
    candidate_id: str
    action: ReviewAction
    reviewer_id: str = Field(min_length=1, max_length=128)
    reviewer_kind: ReviewerKind
    reason_code: ReviewReason | None = None
    reason_detail: str | None = Field(default=None, max_length=2000)
    edited_candidate: CandidatePacket | None = None
    merge_target_candidate_id: str | None = None

    @model_validator(mode="after")
    def action_requirements(self) -> ReviewRequest:
        reason_required = {
            ReviewAction.EDIT,
            ReviewAction.QUARANTINE,
            ReviewAction.REJECT,
            ReviewAction.MERGE_DUPLICATE,
        }
        if self.action in reason_required and self.reason_code is None:
            raise ValueError(f"{self.action} requires a structured reason_code")
        if self.action is ReviewAction.EDIT and self.edited_candidate is None:
            raise ValueError("EDIT requires edited_candidate")
        if self.action is ReviewAction.MERGE_DUPLICATE and not self.merge_target_candidate_id:
            raise ValueError("MERGE_DUPLICATE requires merge_target_candidate_id")
        if self.edited_candidate is not None and (
            self.edited_candidate.candidate_id != self.candidate_id
        ):
            raise ValueError("edited_candidate identity may not change")
        return self


class ReviewJournalEntry(StrictModel):
    sequence: int = Field(ge=1)
    occurred_at: datetime
    candidate_id: str
    action: ReviewAction
    reviewer_id: str
    reviewer_kind: ReviewerKind
    reason_code: ReviewReason | None = None
    reason_detail: str | None = None
    edited_candidate: CandidatePacket | None = None
    merge_target_candidate_id: str | None = None
    previous_entry_hash: str
    entry_hash: str


class ReviewedGoldRecord(StrictModel):
    candidate_id: str
    partition: GoldPartition
    review_entry_hash: str
    reviewer_id: str
    reviewer_kind: ReviewerKind
    packet: CandidatePacket


class MetricResult(StrictModel):
    metric_id: str
    status: MetricStatus
    value: float | int | str | None = None
    threshold: str | None = None
    evidence: str


class Gate0Report(StrictModel):
    generated_at: datetime
    source_manifest_hash: str
    extractor_configuration_hash: str
    validator_configuration_hash: str
    review_journal_hash: str
    partition_counts: dict[str, int]
    metrics: tuple[MetricResult, ...]
    overall_status: MetricStatus
    blockers: tuple[str, ...]


class SealedQueryCase(StrictModel):
    case_id: str
    question: str
    categories: tuple[str, ...] = Field(min_length=1)
    hard_subset: bool
    expected_disposition: Disposition
    expected_contains: tuple[str, ...] = ()
    forbidden_contains: tuple[str, ...] = ()
    reviewed_frame: dict[str, str | None]
    evidence_source_ids: tuple[str, ...] = ()
    evidence_candidate_ids: tuple[str, ...] = ()
    author_identity: str
    authoring_process: str
    review_status: QueryReviewStatus
    reviewer_id: str | None = None
    review_entry_hash: str | None = None

    @model_validator(mode="after")
    def reviewed_status_requires_identity(self) -> SealedQueryCase:
        if self.review_status is QueryReviewStatus.HUMAN_REVIEWED and (
            not self.reviewer_id or not self.review_entry_hash
        ):
            raise ValueError("HUMAN_REVIEWED query cases require reviewer_id and review_entry_hash")
        return self


class SealedQuerySet(StrictModel):
    query_set_id: str
    schema_version: str
    independence_statement: str
    cases: tuple[SealedQueryCase, ...] = Field(min_length=50, max_length=100)


def utc_now() -> datetime:
    return datetime.now(UTC)
