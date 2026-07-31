"""Bounded multi-signal fusion, fixed-shape reranking, and facet traversal."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path

from aethersparse.selection.models import (
    FEATURE_NAMES,
    CandidateScore,
    QuantizedLinearModel,
    SelectionTrace,
)
from aethersparse.traversal.corpus import TOKEN_RE, CorpusStore, normalize_text

STOP = {
    "about", "after", "also", "and", "are", "article", "characterized", "discusses",
    "from", "identify", "into", "its", "related", "section", "starting", "that", "the",
    "their", "this", "through", "topic", "traverse", "what", "when", "where", "which",
    "whose", "with",
}
YEAR_RE = re.compile(r"\b(?:1[5-9]\d{2}|20\d{2}|2100)\b")
ENTITY_RE = re.compile(r"\b[A-Z0-9][\w'-]*(?:\s+[A-Z0-9][\w'-]*)*")
ATTRIBUTION_TERMS = {"said", "says", "stated", "wrote", "quoted", "according", "attributed"}


def _tokens(text: str) -> set[str]:
    return {
        token.casefold() for token in TOKEN_RE.findall(normalize_text(text))
        if len(token) > 2 and token.casefold() not in STOP
    }


def _ratio(left: set[str], right: set[str]) -> float:
    return len(left & right) / max(1, len(left))


def _sigmoid(value: float) -> float:
    if value >= 0:
        factor = math.exp(-value)
        return 1 / (1 + factor)
    factor = math.exp(value)
    return factor / (1 + factor)


DEFAULT_MODEL = QuantizedLinearModel(
    int8_weights=(64, 42, 20, 48, 30, 32, 34, 22, 12, 28, 56, 18, 52, 8),
    weight_scale=1 / 64,
    bias=-1.5,
    training_identity="untrained-deterministic-bootstrap",
)


class EvidenceSelector:
    """All selection operations are fixed-shape and corpus-ontology neutral."""

    def __init__(
        self,
        corpus_path: Path,
        model: QuantizedLinearModel | None = None,
        *,
        candidate_limit: int = 64,
        selected_limit: int = 8,
    ):
        self.store = CorpusStore(corpus_path)
        self.model = model or DEFAULT_MODEL
        self.candidate_limit = candidate_limit
        self.selected_limit = selected_limit
        self._category_cache: dict[str, set[str]] = {}
        self._alias_cache: dict[str, tuple[str, ...]] = {}
        self._target_cache: dict[tuple[str, ...], set[str]] = {}

    @classmethod
    def from_model_file(cls, corpus_path: Path, model_path: Path) -> EvidenceSelector:
        return cls(
            corpus_path,
            QuantizedLinearModel.model_validate_json(model_path.read_text(encoding="utf-8")),
        )

    def _anchor_documents(self, query: str) -> list[str]:
        candidates: list[str] = []
        phrases = ENTITY_RE.findall(query)
        for phrase in sorted(phrases, key=len, reverse=True)[:6]:
            if phrase.casefold() in {"in", "starting", "traverse"}:
                continue
            for row in self.store.title_search(phrase, 4):
                title = normalize_text(row["title"]).casefold()
                if title in normalize_text(query).casefold() or _ratio(
                    _tokens(title), _tokens(query)
                ) >= 0.8:
                    candidates.append(row["document_id"])
        return list(dict.fromkeys(candidates))[:6]

    def _query_categories(self, anchors: list[str]) -> set[str]:
        if not anchors:
            return set()
        marks = ",".join("?" for _ in anchors)
        rows = self.store.db.execute(
            f"SELECT category FROM categories WHERE document_id IN ({marks})", anchors
        )
        return {row[0].casefold() for row in rows}

    def _document_categories(self, document_id: str) -> set[str]:
        if document_id not in self._category_cache:
            self._category_cache[document_id] = {
                row[0].casefold()
                for row in self.store.db.execute(
                    "SELECT category FROM categories WHERE document_id=?",
                    (document_id,),
                )
            }
        return self._category_cache[document_id]

    def _document_aliases(self, document_id: str) -> tuple[str, ...]:
        if document_id not in self._alias_cache:
            self._alias_cache[document_id] = tuple(
                row[0]
                for row in self.store.db.execute(
                    "SELECT alias FROM aliases WHERE document_id=?",
                    (document_id,),
                )
                if len(row[0]) >= 3
            )
        return self._alias_cache[document_id]

    def _linked_distance(self, anchors: list[str], candidate_doc: str) -> float:
        if not anchors:
            return 0.0
        key = tuple(anchors)
        if key not in self._target_cache:
            marks = ",".join("?" for _ in anchors)
            self._target_cache[key] = {
                row[0]
                for row in self.store.db.execute(
                    f"""SELECT target_document_id FROM links
                        WHERE source_document_id IN ({marks})
                        AND target_document_id IS NOT NULL""",
                    anchors,
                )
            }
        return float(candidate_doc in self._target_cache[key])

    def _feature_vector(
        self,
        query: str,
        row: sqlite3.Row,
        lexical_position: int,
        anchors: list[str],
        query_categories: set[str],
    ) -> tuple[float, ...]:
        query_tokens = _tokens(query)
        body_tokens = _tokens(row["normalized_text"])
        title_tokens = _tokens(row["title"])
        section_tokens = _tokens(row["section_path"])
        entities = _tokens(" ".join(ENTITY_RE.findall(query)))
        query_years = set(YEAR_RE.findall(query))
        body_years = set(YEAR_RE.findall(row["normalized_text"]))
        candidate_categories = self._document_categories(row["document_id"])
        normalized_query = normalize_text(query).casefold()
        alias_fit = float(
            any(
                alias in normalized_query
                for alias in self._document_aliases(row["document_id"])
            )
        )
        semantic_query = {
            int.from_bytes(hashlib.blake2s(token.encode(), digest_size=2).digest())
            for token in query_tokens
        }
        semantic_body = {
            int.from_bytes(hashlib.blake2s(token.encode(), digest_size=2).digest())
            for token in body_tokens
        }
        semantic = len(semantic_query & semantic_body) / max(
            1, len(semantic_query | semantic_body)
        )
        directness = min(
            1.0,
            _ratio(query_tokens, body_tokens)
            * (1.0 if len(row["normalized_text"]) <= 480 else 0.75),
        )
        attribution_requested = bool(query_tokens & ATTRIBUTION_TERMS)
        attribution_fit = (
            float(
                bool(
                    body_tokens & ATTRIBUTION_TERMS
                    or '"' in row["normalized_text"]
                    or "\u201c" in row["raw_text"]
                )
            )
            if attribution_requested
            else 1.0
        )
        time_fit = (
            1.0 if not query_years
            else len(query_years & body_years) / max(1, len(query_years))
        )
        category_overlap = len(query_categories & candidate_categories) / max(
            1, len(query_categories)
        )
        answerability = min(
            1.0,
            0.55 * _ratio(query_tokens, body_tokens)
            + 0.25 * _ratio(entities, body_tokens | title_tokens)
            + 0.20 * float(bool(re.search(r"[.!?]", row["normalized_text"]))),
        )
        return (
            _ratio(query_tokens, body_tokens),
            _ratio(query_tokens, title_tokens),
            alias_fit,
            _ratio(entities, body_tokens | title_tokens),
            _ratio(query_tokens, section_tokens),
            1.0 / (1.0 + lexical_position),
            semantic,
            time_fit,
            category_overlap,
            self._linked_distance(anchors, row["document_id"]),
            directness,
            attribution_fit,
            answerability,
            1.0,
        )

    @staticmethod
    def _fusion(features: tuple[float, ...]) -> float:
        weights = (0.27, 0.12, 0.04, 0.12, 0.08, 0.09, 0.07, 0.05,
                   0.03, 0.04, 0.05, 0.02, 0.08, 0.01)
        return sum(weight * value for weight, value in zip(weights, features, strict=True))

    def candidates(self, query: str) -> list[CandidateScore]:
        anchors = self._anchor_documents(query)
        query_categories = self._query_categories(anchors)
        lexical_limit = min(48, self.candidate_limit)
        rows = self.store.search(query, lexical_limit)
        seen = {row["chunk_id"] for row in rows}
        supplemental: list[sqlite3.Row] = []
        if anchors:
            marks = ",".join("?" for _ in anchors)
            linked = list(
                self.store.db.execute(
                    f"""SELECT DISTINCT d.document_id, d.title
                        FROM links l JOIN documents d
                        ON d.document_id=l.target_document_id
                        WHERE l.source_document_id IN ({marks})
                        LIMIT 2048""",
                    anchors,
                )
            )
            query_tokens = _tokens(query)
            linked.sort(
                key=lambda row: (
                    -_ratio(query_tokens, _tokens(row["title"])),
                    row["document_id"],
                )
            )
            for linked_doc in linked[:24]:
                title_terms = _tokens(linked_doc["title"])
                if not title_terms:
                    continue
                match = " AND ".join(
                    f'"{term}"' for term in sorted(title_terms)[:5]
                )
                row = self.store.db.execute(
                    """SELECT c.*, d.title, d.revision, d.source_url, 0.0 AS rank
                       FROM chunks_fts f JOIN chunks c ON c.chunk_id=f.chunk_id
                       JOIN documents d ON d.document_id=c.document_id
                       WHERE chunks_fts MATCH ? AND c.document_id=?
                       ORDER BY c.block_index LIMIT 1""",
                    (match, linked_doc["document_id"]),
                ).fetchone()
                if row is not None:
                    supplemental.append(row)
        query_tokens = _tokens(query)
        supplemental.sort(
            key=lambda row: (
                -_ratio(
                    query_tokens,
                    _tokens(
                        f"{row['title']} {row['section_path']} "
                        f"{row['summary']}"
                    ),
                ),
                row["chunk_id"],
            )
        )
        for row in supplemental:
            if row["chunk_id"] not in seen:
                rows.append(row)
                seen.add(row["chunk_id"])
            if len(rows) >= self.candidate_limit:
                break
        scores: list[CandidateScore] = []
        for position, row in enumerate(rows):
            features = self._feature_vector(
                query, row, position, anchors, query_categories
            )
            deterministic = self._fusion(features)
            reranker = _sigmoid(self.model.score(features))
            scores.append(
                CandidateScore(
                    chunk_id=row["chunk_id"],
                    document_id=row["document_id"],
                    title=row["title"],
                    section_path=row["section_path"],
                    raw_text=row["raw_text"],
                    normalized_text=row["normalized_text"],
                    source_url=row["source_url"],
                    source_revision=row["revision"],
                    lexical_position=position,
                    features=features,
                    deterministic_score=deterministic,
                    reranker_score=reranker,
                    final_score=0.45 * deterministic + 0.55 * reranker,
                )
            )
        return scores

    def _missing_facets(self, query: str, ranked: list[CandidateScore]) -> tuple[str, ...]:
        if not ranked:
            return ("source_support",)
        facets: list[str] = []
        query_years = set(YEAR_RE.findall(query))
        if query_years and not any(
            query_years & set(YEAR_RE.findall(candidate.normalized_text))
            for candidate in ranked[: self.selected_limit]
        ):
            facets.append("temporal_fit")
        query_entities = _tokens(" ".join(ENTITY_RE.findall(query)))
        if query_entities and max(
            candidate.features[3] for candidate in ranked[: self.selected_limit]
        ) < 0.5:
            facets.append("entity_fit")
        if _tokens(query) & ATTRIBUTION_TERMS and max(
            candidate.features[11] for candidate in ranked[: self.selected_limit]
        ) < 1.0:
            facets.append("attribution_fit")
        if ranked[0].features[12] < 0.45:
            facets.append("answerability")
        return tuple(facets)

    def _targeted_candidates(
        self,
        query: str,
        ranked: list[CandidateScore],
        missing: tuple[str, ...],
    ) -> tuple[list[CandidateScore], str | None]:
        if not missing or not ranked:
            return [], None
        # One explicitly typed expansion from the strongest evidence document.
        source_doc = ranked[0].document_id
        linked_docs = self.store.linked_documents([source_doc], 12)
        rows = self.store.chunks_for_documents(linked_docs, 24)
        if not rows:
            return [], f"FILL_{missing[0].upper()}"
        anchors = [source_doc]
        query_categories = self._query_categories(anchors)
        candidates: list[CandidateScore] = []
        for position, row in enumerate(rows, start=self.candidate_limit):
            features = self._feature_vector(
                query, row, position, anchors, query_categories
            )
            deterministic = self._fusion(features)
            reranker = _sigmoid(self.model.score(features))
            candidates.append(
                CandidateScore(
                    chunk_id=row["chunk_id"], document_id=row["document_id"],
                    title=row["title"], section_path=row["section_path"],
                    raw_text=row["raw_text"], normalized_text=row["normalized_text"],
                    source_url=row["source_url"], source_revision=row["revision"],
                    lexical_position=position, features=features,
                    deterministic_score=deterministic, reranker_score=reranker,
                    final_score=0.45 * deterministic + 0.55 * reranker,
                )
            )
        return candidates, f"FILL_{missing[0].upper()}"

    def select(
        self,
        query: str,
        *,
        stage: str = "reranker",
        permit_targeted_traversal: bool = False,
        initial_candidates: list[CandidateScore] | None = None,
    ) -> SelectionTrace:
        started = time.perf_counter_ns()
        initial = initial_candidates if initial_candidates is not None else self.candidates(query)
        if stage == "lexical":
            ranked = list(initial)
        elif stage == "fusion":
            ranked = sorted(initial, key=lambda item: (-item.deterministic_score, item.chunk_id))
        else:
            ranked = sorted(initial, key=lambda item: (-item.final_score, item.chunk_id))
        missing = self._missing_facets(query, ranked)
        targeted: list[CandidateScore] = []
        operation = None
        if permit_targeted_traversal and missing:
            targeted, operation = self._targeted_candidates(query, ranked, missing)
            if targeted:
                ranked = sorted(
                    [*ranked, *targeted],
                    key=lambda item: (-item.final_score, item.chunk_id),
                )
                missing = self._missing_facets(query, ranked)
        selected_ids = {candidate.chunk_id for candidate in ranked[: self.selected_limit]}
        marked = [
            candidate.model_copy(update={"selected": candidate.chunk_id in selected_ids})
            for candidate in ranked
        ]
        selected = [candidate for candidate in marked if candidate.selected]
        source_bytes = sum(len(candidate.raw_text.encode()) for candidate in selected)
        elapsed = (time.perf_counter_ns() - started) / 1_000_000
        return SelectionTrace(
            query=query,
            initial_candidates=tuple(initial),
            reranked_candidates=tuple(marked),
            selected_evidence=tuple(selected),
            missing_facets=missing,
            traversal_activated=bool(targeted),
            traversal_operation=operation,
            traversal_depth=1 if targeted else 0,
            marginal_recall_gain=0.0,
            stop_reason="SUPPORTED" if selected and not missing else "EVIDENCE_GAP",
            source_bytes=source_bytes,
            model_macs=len(initial) * len(FEATURE_NAMES),
            latency_ms=round(elapsed, 3),
        )

    def dump_model(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model.model_dump_json(indent=2) + "\n", encoding="utf-8")


def model_identity(weights: Iterable[float], training_manifest: dict[str, object]) -> str:
    payload = json.dumps(
        {"weights": list(weights), "manifest": training_manifest},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()
