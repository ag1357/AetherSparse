"""Validated cell routing with exact, VSA, and lexical signals."""

from __future__ import annotations

from collections import defaultdict

from aethersparse.cells.models import CellRoute, CognitiveCell
from aethersparse.cells.vsa import encode_terms, similarity
from aethersparse.traversal.corpus import TOKEN_RE, normalize_text


def _tokens(value: str) -> set[str]:
    return {token.casefold() for token in TOKEN_RE.findall(normalize_text(value)) if len(token) > 2}


def _token_sequence(value: str) -> tuple[str, ...]:
    return tuple(token.casefold() for token in TOKEN_RE.findall(normalize_text(value)))


def _contains_alias(query: tuple[str, ...], alias: str) -> bool:
    """Match an alias on token boundaries, including multi-token aliases."""
    target = _token_sequence(alias)
    if not target or len(target) > len(query):
        return False
    return any(
        query[index : index + len(target)] == target
        for index in range(len(query) - len(target) + 1)
    )


class CognitiveCellRouter:
    def __init__(self, cells: list[CognitiveCell], *, candidate_limit: int = 256):
        self.cells = {cell.cell_id: cell for cell in cells}
        self.candidate_limit = candidate_limit
        self.term_postings: dict[str, set[str]] = defaultdict(set)
        self.alias_postings: dict[str, set[str]] = defaultdict(set)
        self.vsa_bands: dict[tuple[int, int], set[str]] = defaultdict(set)
        for cell in cells:
            for term in cell.relation_terms:
                self.term_postings[term].add(cell.cell_id)
            for alias in cell.entity_aliases:
                for token in _tokens(alias):
                    self.alias_postings[token].add(cell.cell_id)
            signature = int.from_bytes(bytes.fromhex(cell.signature_hex))
            for band in range(8):
                key = (band, (signature >> (band * 8)) & 0xFF)
                self.vsa_bands[key].add(cell.cell_id)

    def candidate_ids(self, query: str, *, use_vsa: bool = True) -> tuple[str, ...]:
        query_tokens = _tokens(query)
        candidates: set[str] = set()
        for token in query_tokens:
            candidates.update(self.term_postings.get(token, ()))
            candidates.update(self.alias_postings.get(token, ()))
        if use_vsa and query_tokens:
            signature = encode_terms(query_tokens)
            for band in range(8):
                key = (band, (signature >> (band * 8)) & 0xFF)
                candidates.update(self.vsa_bands.get(key, ()))
        if not candidates:
            candidates.update(sorted(self.cells)[: self.candidate_limit])
        return tuple(sorted(candidates)[: self.candidate_limit])

    def route(
        self,
        query: str,
        *,
        limit: int = 8,
        predicted_cell_ids: tuple[str, ...] = (),
        use_vsa: bool = True,
    ) -> list[CellRoute]:
        query_tokens = _tokens(query)
        query_sequence = _token_sequence(query)
        query_signature = encode_terms(query_tokens) if query_tokens else 0
        routes: list[CellRoute] = []
        predicted = tuple(cell_id for cell_id in predicted_cell_ids if cell_id in self.cells)
        base = self.candidate_ids(query, use_vsa=use_vsa)
        candidate_ids = tuple(dict.fromkeys((*predicted, *base)))[: self.candidate_limit]
        for cell_id in candidate_ids:
            cell = self.cells[cell_id]
            valid_prediction = cell.cell_id in predicted_cell_ids
            exact = float(
                any(_contains_alias(query_sequence, alias) for alias in cell.entity_aliases)
            )
            lexical = len(query_tokens & set(cell.relation_terms)) / max(1, len(query_tokens))
            vector = (
                similarity(query_signature, int.from_bytes(bytes.fromhex(cell.signature_hex)))
                if use_vsa and query_tokens
                else 0.0
            )
            prediction_bonus = 0.08 if valid_prediction else 0.0
            score = 0.48 * exact + 0.30 * lexical + 0.22 * vector + prediction_bonus
            routes.append(
                CellRoute(
                    cell_id=cell.cell_id,
                    score=score,
                    exact_alias=exact,
                    lexical=lexical,
                    vsa_similarity=vector,
                    valid_registry_id=valid_prediction or cell.cell_id not in predicted_cell_ids,
                )
            )
        return sorted(routes, key=lambda item: (-item.score, item.cell_id))[:limit]

    def validate_predictions(self, predicted_cell_ids: tuple[str, ...]) -> tuple[str, ...]:
        """Invalid generated IDs never reach retrieval."""
        return tuple(cell_id for cell_id in predicted_cell_ids if cell_id in self.cells)
