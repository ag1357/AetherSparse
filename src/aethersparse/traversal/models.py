"""Typed, ontology-neutral contracts for query-time knowledge traversal."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AnswerGoal(StrEnum):
    IDENTIFY = "identify"
    DESCRIBE = "describe"
    EXPLAIN = "explain"
    COMPARE = "compare"
    LOCATE = "locate"
    ATTRIBUTE = "attribute"
    ENUMERATE = "enumerate"
    CALCULATE = "calculate"
    VERIFY = "verify"


class TraversalOperation(StrEnum):
    LOOKUP_ENTITY = "LOOKUP_ENTITY"
    SEARCH_TITLE_ALIAS = "SEARCH_TITLE_ALIAS"
    SEARCH_LEXICAL = "SEARCH_LEXICAL"
    SEARCH_SEMANTIC = "SEARCH_SEMANTIC"
    FETCH_ARTICLE = "FETCH_ARTICLE"
    FETCH_SECTION = "FETCH_SECTION"
    FOLLOW_HYPERLINK = "FOLLOW_HYPERLINK"
    FOLLOW_CITATION = "FOLLOW_CITATION"
    EXPAND_ENTITY = "EXPAND_ENTITY"
    EXPAND_CATEGORY = "EXPAND_CATEGORY"
    EXPAND_TEMPORAL_CONTEXT = "EXPAND_TEMPORAL_CONTEXT"
    RESOLVE_REFERENCE = "RESOLVE_REFERENCE"
    EXTRACT_TEMPORARY_CLAIMS = "EXTRACT_TEMPORARY_CLAIMS"
    ADD_EVIDENCE_NODE = "ADD_EVIDENCE_NODE"
    COMPARE_EVIDENCE = "COMPARE_EVIDENCE"
    GROUP_SOURCE_LINEAGE = "GROUP_SOURCE_LINEAGE"
    CHECK_REQUIRED_FACETS = "CHECK_REQUIRED_FACETS"
    VERIFY_SOURCE_SUPPORT = "VERIFY_SOURCE_SUPPORT"
    REQUEST_CLARIFICATION = "REQUEST_CLARIFICATION"
    ABSTAIN = "ABSTAIN"


class QueryState(FrozenModel):
    query: str
    requested_information_type: str
    answer_goal: AnswerGoal
    entities: tuple[str, ...] = ()
    unresolved_references: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    time_context: tuple[str, ...] = ()
    location_context: tuple[str, ...] = ()
    expected_answer_shape: str = "extractive_text"
    required_evidence_facets: tuple[str, ...] = ("source_support",)
    candidate_interpretations: tuple[str, ...] = ()
    unknown_spans: tuple[str, ...] = ()
    discourse_context: tuple[str, ...] = ()


class OperationTrace(FrozenModel):
    step: int
    operation: TraversalOperation
    input_ids: tuple[str, ...] = ()
    output_ids: tuple[str, ...] = ()
    bytes_read: int = Field(ge=0)
    elapsed_us: int = Field(ge=0)
    marginal_evidence_gain: float = Field(ge=0.0, le=1.0)
    unresolved_facets: tuple[str, ...] = ()


class EvidenceNode(FrozenModel):
    chunk_id: str
    document_id: str
    title: str
    section_path: str
    source_revision: str
    source_url: str
    raw_start: int
    raw_end: int
    raw_text: str
    normalized_text: str
    score: float
    temporary_claims: tuple[str, ...] = ()
    verified: bool = False


class TraversalBudget(FrozenModel):
    max_steps: int = Field(default=10, ge=1, le=30)
    max_articles: int = Field(default=12, ge=1, le=64)
    max_chunks: int = Field(default=32, ge=1, le=256)
    max_bytes: int = Field(default=262_144, ge=1024, le=8_388_608)


class TraversalResult(FrozenModel):
    query_state: QueryState
    disposition: str
    answer: str | None
    failure_reason: str | None
    citations: tuple[EvidenceNode, ...]
    retrieved_chunks: tuple[EvidenceNode, ...]
    operations: tuple[OperationTrace, ...]
    unresolved_facets: tuple[str, ...]
    contradictions: tuple[str, ...]
    stop_reason: str
    retrieval_depth: int
    unique_articles_visited: int
    unique_sections_visited: int
    source_families: int
    bytes_read: int
    measured_latency_ms: float
