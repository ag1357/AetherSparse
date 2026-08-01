"""Bounded flat retrieval with transparent deterministic feature fusion."""

from __future__ import annotations

from collections.abc import Iterable
from itertools import pairwise

from aethersparse.substrate.builder import normalize_surface, tokenize
from aethersparse.substrate.models import (
    AliasKind,
    FlatStructuredPack,
    FusionFeatures,
    Posting,
    RetrievalRequest,
    RetrievalResult,
    RetrievedEvidence,
)


def _posting_map(postings: tuple[Posting, ...]) -> dict[str, Posting]:
    return {posting.key: posting for posting in postings}


class FlatHybridRetriever:
    """Retained lexical/structured baseline; no cell topology or learned ranker."""

    def __init__(self, pack: FlatStructuredPack) -> None:
        self.pack = pack
        self.documents = {document.document_id: document for document in pack.documents}
        self.chunks = {chunk.chunk_id: chunk for chunk in pack.chunks}
        self.bindings = {binding.binding_id: binding for binding in pack.source_bindings}
        self.claims = {claim.claim_id: claim for claim in pack.claims}
        self.lexical = _posting_map(pack.indexes.lexical)
        self.title = _posting_map(pack.indexes.title)
        self.heading = _posting_map(pack.indexes.heading)
        self.phrase = _posting_map(pack.indexes.phrase)
        self.relation = _posting_map(pack.indexes.relation)
        self.entity = _posting_map(pack.indexes.entity)
        mutable_chunks_by_document: dict[str, list[str]] = {}
        for chunk in pack.chunks:
            mutable_chunks_by_document.setdefault(chunk.document_id, []).append(chunk.chunk_id)
        self.chunks_by_document = {
            document_id: tuple(
                sorted(chunk_ids, key=lambda chunk_id: self.chunks[chunk_id].ordinal)
            )
            for document_id, chunk_ids in mutable_chunks_by_document.items()
        }
        self.aliases_by_entity: dict[str, list[tuple[str, AliasKind]]] = {}
        for alias in pack.aliases:
            self.aliases_by_entity.setdefault(alias.entity_id, []).append(
                (alias.normalized_surface, alias.kind)
            )
        self.claims_by_chunk: dict[str, set[str]] = {}
        chunk_bindings_by_document: dict[str, list[tuple[str, int, int]]] = {}
        for chunk in pack.chunks:
            binding = self.bindings[chunk.binding_id]
            chunk_bindings_by_document.setdefault(chunk.document_id, []).append(
                (chunk.chunk_id, binding.char_start, binding.char_end)
            )
        for claim in pack.claims:
            for binding_id in claim.source_binding_ids:
                binding = self.bindings[binding_id]
                for chunk_id, chunk_start, chunk_end in chunk_bindings_by_document.get(
                    binding.document_id, []
                ):
                    if chunk_start <= binding.char_start and chunk_end >= binding.char_end:
                        self.claims_by_chunk.setdefault(chunk_id, set()).add(claim.claim_id)

    @staticmethod
    def _add_chunks(
        target: list[str],
        seen: set[str],
        chunk_ids: Iterable[str],
        limit: int,
    ) -> None:
        for chunk_id in sorted(chunk_ids):
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            target.append(chunk_id)
            if len(target) >= limit:
                return

    def _add_documents(
        self,
        target: list[str],
        seen: set[str],
        document_ids: Iterable[str],
        limit: int,
    ) -> None:
        first_chunks = (
            self.chunks_by_document[document_id][0]
            for document_id in sorted(document_ids)
            if self.chunks_by_document.get(document_id)
        )
        self._add_chunks(target, seen, first_chunks, limit)

    @staticmethod
    def _proximity(query_terms: tuple[str, ...], chunk_terms: tuple[str, ...]) -> int:
        unique_query = tuple(dict.fromkeys(query_terms))
        if len(unique_query) < 2:
            return 0
        positions: list[int] = []
        for query_term in unique_query:
            try:
                positions.append(chunk_terms.index(query_term))
            except ValueError:
                return 0
        return int(max(positions) - min(positions) <= len(unique_query) + 4)

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        query_terms = tokenize(request.text)
        query_term_set = set(query_terms)
        query_phrases = tuple(
            dict.fromkeys(f"{left} {right}" for left, right in pairwise(query_terms))
        )
        candidate_ids: list[str] = []
        seen: set[str] = set()

        # Structured constraints and high-precision fields receive candidate-pool priority.
        for relation_family in request.relation_families:
            posting = self.relation.get(normalize_surface(relation_family))
            if posting is not None:
                self._add_chunks(
                    candidate_ids, seen, posting.chunk_ids, request.max_candidates
                )
                if len(candidate_ids) < request.max_candidates:
                    self._add_documents(
                        candidate_ids, seen, posting.document_ids, request.max_candidates
                    )
        for entity_id in request.entity_ids:
            posting = self.entity.get(entity_id)
            if posting is not None and len(candidate_ids) < request.max_candidates:
                self._add_chunks(
                    candidate_ids, seen, posting.chunk_ids, request.max_candidates
                )
                if len(candidate_ids) < request.max_candidates:
                    self._add_documents(
                        candidate_ids, seen, posting.document_ids, request.max_candidates
                    )
        for phrase in query_phrases:
            posting = self.phrase.get(phrase)
            if posting is not None and len(candidate_ids) < request.max_candidates:
                self._add_chunks(
                    candidate_ids, seen, posting.chunk_ids, request.max_candidates
                )
        for term in tuple(dict.fromkeys(query_terms)):
            posting = self.title.get(term)
            if posting is not None and len(candidate_ids) < request.max_candidates:
                self._add_documents(
                    candidate_ids, seen, posting.document_ids, request.max_candidates
                )
            posting = self.heading.get(term)
            if posting is not None and len(candidate_ids) < request.max_candidates:
                self._add_documents(
                    candidate_ids, seen, posting.document_ids, request.max_candidates
                )
            posting = self.lexical.get(term)
            if posting is not None and len(candidate_ids) < request.max_candidates:
                self._add_chunks(
                    candidate_ids, seen, posting.chunk_ids, request.max_candidates
                )

        was_truncated = len(candidate_ids) >= request.max_candidates
        normalized_query = normalize_surface(request.text)
        scored: list[tuple[int, str, FusionFeatures, tuple[str, ...]]] = []
        requested_relations = {
            normalize_surface(relation) for relation in request.relation_families
        }
        requested_entities = set(request.entity_ids)
        for chunk_id in candidate_ids[: request.max_candidates]:
            chunk = self.chunks[chunk_id]
            document = self.documents[chunk.document_id]
            chunk_terms = tokenize(chunk.text)
            chunk_term_set = set(chunk_terms)
            title_terms = set(tokenize(document.title))
            heading_terms = set(tokenize(chunk.heading or ""))
            lexical_hits = len(query_term_set & chunk_term_set)
            title_hits = len(query_term_set & title_terms)
            heading_hits = len(query_term_set & heading_terms)
            phrase_hits = sum(
                1 for query_phrase in query_phrases if query_phrase in normalize_surface(chunk.text)
            )
            document_entity_id = document.canonical_entity_id
            alias_fit = 0
            redirect_fit = 0
            anchor_fit = 0
            if document_entity_id is not None:
                for surface, kind in self.aliases_by_entity.get(document_entity_id, []):
                    if surface and surface in normalized_query:
                        alias_fit = 1
                        redirect_fit = max(redirect_fit, int(kind is AliasKind.REDIRECT))
                        anchor_fit = max(anchor_fit, int(kind is AliasKind.ANCHOR))
            entity_fit = int(
                bool(document_entity_id and document_entity_id in requested_entities)
            )
            candidate_claim_ids = tuple(sorted(self.claims_by_chunk.get(chunk_id, ())))
            relation_fit = int(
                any(
                    self.claims[claim_id].relation_family in requested_relations
                    for claim_id in candidate_claim_ids
                )
            )
            answer_type_fit = int(
                request.answer_kind is not None
                and any(
                    self.claims[claim_id].object_kind is request.answer_kind
                    for claim_id in candidate_claim_ids
                )
            )
            temporal_fit = int(
                request.temporal_constraint is not None
                and (
                    request.temporal_constraint.casefold() in chunk.text.casefold()
                    or any(
                        request.temporal_constraint.casefold()
                        in self.claims[claim_id].object_value.casefold()
                        for claim_id in candidate_claim_ids
                    )
                )
            )
            features = FusionFeatures(
                lexical_hits=lexical_hits,
                title_hits=title_hits,
                heading_hits=heading_hits,
                phrase_hits=phrase_hits,
                proximity=self._proximity(query_terms, chunk_terms),
                alias_fit=alias_fit,
                redirect_fit=redirect_fit,
                anchor_fit=anchor_fit,
                entity_fit=entity_fit,
                relation_fit=relation_fit,
                answer_type_fit=answer_type_fit,
                temporal_fit=temporal_fit,
            )
            # Integer weights are an inspectable deterministic fusion policy, not a model.
            score = (
                lexical_hits * 90
                + title_hits * 260
                + heading_hits * 170
                + phrase_hits * 220
                + features.proximity * 120
                + alias_fit * 180
                + redirect_fit * 90
                + anchor_fit * 60
                + entity_fit * 700
                + relation_fit * 650
                + answer_type_fit * 320
                + temporal_fit * 300
            )
            scored.append((score, chunk_id, features, candidate_claim_ids))

        scored.sort(key=lambda item: (-item[0], item[1]))
        evidence = tuple(
            RetrievedEvidence(
                rank=rank,
                score=score,
                document_id=self.chunks[chunk_id].document_id,
                chunk_id=chunk_id,
                binding_id=self.chunks[chunk_id].binding_id,
                matched_claim_ids=claim_ids,
                features=features,
            )
            for rank, (score, chunk_id, features, claim_ids) in enumerate(
                scored[: request.top_k], start=1
            )
        )
        return RetrievalResult(
            request=request,
            evidence=evidence,
            considered_candidates=len(scored),
            truncated=was_truncated,
        )
