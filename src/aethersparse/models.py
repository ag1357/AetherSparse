"""Typed contracts shared by the compiler, runtime, service, and tests."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    """Base model that rejects accidental schema expansion."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class PacketType(StrEnum):
    PROPOSITION = "PROPOSITION"
    EVENT = "EVENT"
    QUOTATION = "QUOTATION"
    SOURCE_SPAN = "SOURCE_SPAN"


class PacketStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    CANONICAL = "CANONICAL"
    QUARANTINE = "QUARANTINE"
    REJECTED = "REJECTED"


class KeyClass(StrEnum):
    K0 = "K0"
    K1 = "K1"
    K2 = "K2"
    K3 = "K3"


class Disposition(StrEnum):
    ANSWER = "answer"
    CLARIFY = "clarify"
    ABSTAIN = "abstain"
    OUT_OF_DOMAIN = "out_of_domain"


class FailureCode(StrEnum):
    OUT_OF_DOMAIN = "OUT_OF_DOMAIN"
    PARSE_FAILURE = "PARSE_FAILURE"
    ENTITY_AMBIGUITY = "ENTITY_AMBIGUITY"
    RETRIEVAL_FAILURE = "RETRIEVAL_FAILURE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    OUT_OF_ONTOLOGY = "OUT_OF_ONTOLOGY"
    TEMPORAL_AMBIGUITY = "TEMPORAL_AMBIGUITY"
    PERSPECTIVE_AMBIGUITY = "PERSPECTIVE_AMBIGUITY"
    SAFETY_BLOCK = "SAFETY_BLOCK"
    PRIVACY_BLOCK = "PRIVACY_BLOCK"
    VERIFICATION_FAILURE = "VERIFICATION_FAILURE"
    REALIZATION_FAILURE = "REALIZATION_FAILURE"


class Intent(StrEnum):
    FACT_LOOKUP = "FACT_LOOKUP"
    TEMPORAL_WHEN = "TEMPORAL_WHEN"
    QUOTE_WHO_SAID = "QUOTE_WHO_SAID"
    UNKNOWN = "UNKNOWN"


class OperationCategory(StrEnum):
    SYMBOLIC = "symbolic"
    STORAGE = "storage"
    CONTROL = "control"
    VERIFICATION = "verification"
    REALIZATION = "realization"
    SECURITY = "security"


class SourceDocument(StrictModel):
    source_doc_id: str
    title: str
    source_revision: str
    source_url: str
    license: Literal["public_domain", "compatible_open_license"]
    source_group: str
    text: str


class SourceSpan(StrictModel):
    source_span_id: str
    source_doc_id: str
    source_title: str
    source_revision: str
    source_url: str
    source_group: str
    license: str
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    text_hash: str
    text: str

    @model_validator(mode="after")
    def end_follows_start(self) -> SourceSpan:
        if self.char_end <= self.char_start:
            raise ValueError("char_end must be greater than char_start")
        return self


class AtomicClaim(StrictModel):
    claim_unit_id: str
    subject_id: str
    relation_id: str
    object_value: str
    aligned_span_ids: tuple[str, ...] = Field(min_length=1)
    alignment_score: float = Field(ge=0.0, le=1.0)
    alignment_method: Literal["rule", "neural", "teacher", "human"]


class PacketHeader(StrictModel):
    packet_id: str
    packet_type: PacketType
    schema_version: str = "0.3.0"
    status: PacketStatus
    tier: Literal[1, 2, 3]
    namespace: str = "canonical"
    primary_subject: str
    primary_relation: str
    primary_object: str
    concept_ids: tuple[str, ...]
    bucket_id: str
    valid_from: date | None = None
    valid_to: date | None = None
    recorded_at: date | None = None
    source_span_ids: tuple[str, ...] = Field(min_length=1)
    support_packet_ids: tuple[str, ...] = ()
    derivation: Literal["manual", "rule", "teacher_candidate"]
    perspective: str = "asserted_fact"
    polarity: Literal["positive", "negative"] = "positive"
    modality: Literal["asserted", "quoted", "estimated"] = "asserted"
    packet_quality: float = Field(ge=0.0, le=1.0)
    privacy: Literal["public", "personal"] = "public"
    license: str
    checksum: str
    key_class: KeyClass
    logical_header_bytes: Literal[128] = 128


class PropositionPayload(StrictModel):
    subject_label: str
    predicate_label: str
    object_label: str
    normalized_value: str | None = None
    answer_kind: Literal["entity", "date", "text", "location", "vehicle"]


class EventPayload(StrictModel):
    event_label: str
    participants: tuple[str, ...]
    location: str | None = None
    vehicle: str | None = None
    occurred_on: date | None = None


class QuotationPayload(StrictModel):
    speaker_id: str
    speaker_label: str
    quotation: str


PacketPayload = PropositionPayload | EventPayload | QuotationPayload


class KnowledgePacket(StrictModel):
    header: PacketHeader
    payload: PacketPayload
    atomic_claims: tuple[AtomicClaim, ...] = Field(min_length=1)


class PackManifest(StrictModel):
    pack_id: str
    ontology_version: str
    compiler_version: str
    source_manifest_hash: str
    extraction_config_hash: str
    validator_config_hash: str
    packet_count: int = Field(ge=0)
    span_count: int = Field(ge=0)
    normalized_source_bytes: int = Field(ge=0)
    logical_query_pack_bytes: int = Field(ge=0)
    logical_compiled_bytes_per_source_byte: float = Field(ge=0)
    manifest_hash: str
    signature: Literal["UNSIGNED_HOST_EMULATOR"]


class CompiledPack(StrictModel):
    manifest: PackManifest
    source_spans: tuple[SourceSpan, ...]
    packets: tuple[KnowledgePacket, ...]


class UnknownSpan(StrictModel):
    surface: str
    status: Literal["unknown_but_copyable"] = "unknown_but_copyable"
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    candidate_entity_ids: tuple[str, ...] = ()


class ParseFrame(StrictModel):
    intent: Intent
    entity_id: str | None = None
    relation_id: str | None = None
    answer_slot: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    unknown_spans: tuple[UnknownSpan, ...] = ()
    ambiguity_flags: tuple[str, ...] = ()


class Budget(StrictModel):
    deadline_ms: int = Field(default=5000, ge=1, le=60_000)
    energy_budget_mj: float = Field(default=15_000.0, gt=0)


class QueryRequest(StrictModel):
    request_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=2048)
    trace: bool = True
    budget: Budget = Field(default_factory=Budget)


class Citation(StrictModel):
    citation_id: str
    source_span_id: str
    source_doc_id: str
    source_title: str
    source_url: str
    source_revision: str
    quoted_text: str
    char_start: int
    char_end: int


class ClaimBinding(StrictModel):
    surface: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    claim_unit_id: str
    packet_id: str
    source_span_ids: tuple[str, ...] = Field(min_length=1)


class ConfidenceDimensions(StrictModel):
    factual_support: float = Field(ge=0.0, le=1.0)
    query_relevance: float = Field(ge=0.0, le=1.0)
    temporal_validity: float = Field(ge=0.0, le=1.0)
    source_reliability: float = Field(ge=0.0, le=1.0)
    source_independence: float = Field(ge=0.0, le=1.0)
    interpretation: float = Field(ge=0.0, le=1.0)
    realization_fidelity: float = Field(ge=0.0, le=1.0)
    safety_clearance: float = Field(ge=0.0, le=1.0)


class TraceEntry(StrictModel):
    cycle: int = Field(ge=0)
    operation: str
    category: OperationCategory
    input_count: int = Field(ge=0)
    output_count: int = Field(ge=0)
    bytes_read: int = Field(ge=0)
    storage_reads: int = Field(ge=0)
    integer_ops: int = Field(ge=0)
    working_ram_bytes: int = Field(ge=0)
    host_latency_us: int = Field(ge=0)
    measurement: Literal["measured_host", "estimated"]


class CostSummary(StrictModel):
    operation_count: int = Field(ge=0)
    bytes_read: int = Field(ge=0)
    storage_reads: int = Field(ge=0)
    integer_ops: int = Field(ge=0)
    peak_working_ram_bytes: int = Field(ge=0)
    measured_host_latency_us: int = Field(ge=0)


class CapsuleDelta(StrictModel):
    ontology_version: str
    active_entity_ids: tuple[str, ...] = ()
    supported_claim_ids: tuple[str, ...] = ()
    unresolved_goals: tuple[str, ...] = ()


class QueryResponse(StrictModel):
    request_id: str
    session_id: str
    disposition: Disposition
    reason_code: FailureCode | None = None
    reason: str | None = None
    sentence: str | None = None
    citations: tuple[Citation, ...] = ()
    bindings: tuple[ClaimBinding, ...] = ()
    confidence: ConfidenceDimensions | None = None
    trace: tuple[TraceEntry, ...] = ()
    cost: CostSummary
    capsule_delta: CapsuleDelta
    pack_manifest_hash: str
