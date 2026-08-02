"""Typed contracts for evidence selection and score inspection."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

FEATURE_NAMES = (
    "lexical_overlap",
    "title_overlap",
    "alias_fit",
    "entity_fit",
    "section_overlap",
    "lexical_rank",
    "bm25_score",
    "time_compatibility",
    "category_overlap",
    "hyperlink_proximity",
    "directness",
    "attribution_fit",
    "answerability",
    "char3gram_fit",
)


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CandidateScore(FrozenModel):
    chunk_id: str
    document_id: str
    title: str
    section_path: str
    raw_text: str
    normalized_text: str
    source_url: str
    source_revision: str
    lexical_position: int = Field(ge=0)
    features: tuple[float, ...] = Field(
        min_length=len(FEATURE_NAMES), max_length=len(FEATURE_NAMES)
    )
    deterministic_score: float
    reranker_score: float
    final_score: float
    selected: bool = False


class SelectionTrace(FrozenModel):
    query: str
    initial_candidates: tuple[CandidateScore, ...]
    reranked_candidates: tuple[CandidateScore, ...]
    selected_evidence: tuple[CandidateScore, ...]
    missing_facets: tuple[str, ...]
    traversal_activated: bool
    traversal_operation: str | None
    traversal_depth: int
    marginal_recall_gain: float
    stop_reason: str
    source_bytes: int
    model_macs: int
    latency_ms: float


class QuantizedLinearModel(FrozenModel):
    schema_version: str = "1.0"
    feature_names: tuple[str, ...] = FEATURE_NAMES
    int8_weights: tuple[int, ...] = Field(
        min_length=len(FEATURE_NAMES), max_length=len(FEATURE_NAMES)
    )
    weight_scale: float = Field(gt=0)
    bias: float
    parameter_count: int = len(FEATURE_NAMES) + 1
    int8_model_bytes: int = len(FEATURE_NAMES) + 4
    macs_per_candidate: int = len(FEATURE_NAMES)
    training_identity: str

    def score(self, features: tuple[float, ...]) -> float:
        return (
            sum(weight * self.weight_scale * value for weight, value in zip(
                self.int8_weights, features, strict=True
            ))
            + self.bias
        )
