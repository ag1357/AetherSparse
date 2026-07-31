"""Typed contracts for the v0.4 cognitive-cell substrate."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CellKind(StrEnum):
    CATEGORY = "category"
    ENTITY_COMMUNITY = "entity_community"
    SEMANTIC_BUCKET = "semantic_bucket"
    HYBRID = "hybrid"


class CognitiveCell(FrozenModel):
    cell_id: str
    kind: CellKind
    label: str
    document_ids: tuple[str, ...]
    entity_aliases: tuple[str, ...] = ()
    relation_terms: tuple[str, ...] = ()
    signature_hex: str
    source_bytes: int = Field(ge=0)


class CellRoute(FrozenModel):
    cell_id: str
    score: float
    exact_alias: float
    lexical: float
    vsa_similarity: float
    valid_registry_id: bool


class ExactEvidenceNode(FrozenModel):
    claim_id: str
    entity_id: str
    relation_id: str
    source_span_id: str
    polarity: int = Field(ge=-1, le=1)
    temporal_value: str | None = None


class DualWorkingState(FrozenModel):
    exact_nodes: tuple[ExactEvidenceNode, ...]
    associative_signature_hex: str
    unresolved_facets: tuple[str, ...]
    exact_graph_is_authoritative: bool = True
