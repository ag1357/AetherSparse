"""Bounded two-level cell-to-article-to-chunk retrieval."""

from __future__ import annotations

import time

from pydantic import BaseModel, ConfigDict, Field

from aethersparse.cells.models import CellRoute, CognitiveCell
from aethersparse.cells.router import CognitiveCellRouter
from aethersparse.traversal.corpus import TOKEN_RE, CorpusStore, normalize_text


def _tokens(value: str) -> set[str]:
    return {token.casefold() for token in TOKEN_RE.findall(normalize_text(value)) if len(token) > 2}


class CellEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    chunk_id: str
    document_id: str
    title: str
    section_path: str
    normalized_text: str
    score: float
    source_bytes: int = Field(ge=0)


class CellRetrievalTrace(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    query: str
    cell_routes: tuple[CellRoute, ...]
    routed_cell_candidates: int
    candidate_documents: int
    candidate_chunks: int
    selected_evidence: tuple[CellEvidence, ...]
    source_bytes: int
    latency_ms: float
    vsa_enabled: bool
    generated_address_is_hint_only: bool = True
    broad_frontier_expansion: bool = False


class TwoLevelCellRetriever:
    def __init__(
        self,
        store: CorpusStore,
        cells: list[CognitiveCell],
        *,
        cell_limit: int = 8,
        document_limit: int = 512,
        chunk_limit: int = 128,
        evidence_limit: int = 8,
    ):
        self.store = store
        self.router = CognitiveCellRouter(cells)
        self.cells = {cell.cell_id: cell for cell in cells}
        self.cell_limit = cell_limit
        self.document_limit = document_limit
        self.chunk_limit = chunk_limit
        self.evidence_limit = evidence_limit

    def retrieve(
        self,
        query: str,
        *,
        predicted_cell_ids: tuple[str, ...] = (),
        use_vsa: bool = True,
    ) -> CellRetrievalTrace:
        started = time.perf_counter_ns()
        valid = self.router.validate_predictions(predicted_cell_ids)
        routed_candidates = self.router.candidate_ids(query, use_vsa=use_vsa)
        routes = self.router.route(
            query,
            limit=self.cell_limit,
            predicted_cell_ids=valid,
            use_vsa=use_vsa,
        )
        document_ids = list(
            dict.fromkeys(
                document_id
                for route in routes
                for document_id in self.cells[route.cell_id].document_ids
            )
        )[: self.document_limit]
        rows = self.store.chunks_for_documents(document_ids, self.chunk_limit)
        query_terms = _tokens(query)
        evidence: list[CellEvidence] = []
        for row in rows:
            body_terms = _tokens(f"{row['title']} {row['section_path']} {row['normalized_text']}")
            overlap = len(query_terms & body_terms) / max(1, len(query_terms))
            directness = len(query_terms & _tokens(row["normalized_text"])) / max(
                1, len(query_terms)
            )
            score = 0.65 * overlap + 0.35 * min(1.0, directness)
            evidence.append(
                CellEvidence(
                    chunk_id=row["chunk_id"],
                    document_id=row["document_id"],
                    title=row["title"],
                    section_path=row["section_path"],
                    normalized_text=row["normalized_text"],
                    score=score,
                    source_bytes=len(row["raw_text"].encode()),
                )
            )
        selected = tuple(
            sorted(evidence, key=lambda item: (-item.score, item.chunk_id))[: self.evidence_limit]
        )
        return CellRetrievalTrace(
            query=query,
            cell_routes=tuple(routes),
            routed_cell_candidates=len(routed_candidates),
            candidate_documents=len(document_ids),
            candidate_chunks=len(rows),
            selected_evidence=selected,
            source_bytes=sum(item.source_bytes for item in selected),
            latency_ms=(time.perf_counter_ns() - started) / 1_000_000,
            vsa_enabled=use_vsa,
        )
