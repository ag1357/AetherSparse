"""Validated cell routing with exact, VSA, and lexical signals."""

from __future__ import annotations

from aethersparse.cells.models import CellRoute, CognitiveCell
from aethersparse.cells.vsa import encode_terms, similarity
from aethersparse.traversal.corpus import TOKEN_RE, normalize_text


def _tokens(value: str) -> set[str]:
    return {token.casefold() for token in TOKEN_RE.findall(normalize_text(value)) if len(token) > 2}


class CognitiveCellRouter:
    def __init__(self, cells: list[CognitiveCell]):
        self.cells = {cell.cell_id: cell for cell in cells}

    def route(
        self, query: str, *, limit: int = 8, predicted_cell_ids: tuple[str, ...] = ()
    ) -> list[CellRoute]:
        query_tokens = _tokens(query)
        query_signature = encode_terms(query_tokens)
        normalized = normalize_text(query).casefold()
        routes: list[CellRoute] = []
        for cell in self.cells.values():
            valid_prediction = cell.cell_id in predicted_cell_ids
            exact = float(
                any(alias in normalized for alias in cell.entity_aliases if len(alias) >= 3)
            )
            lexical = len(query_tokens & set(cell.relation_terms)) / max(1, len(query_tokens))
            vector = similarity(query_signature, int.from_bytes(bytes.fromhex(cell.signature_hex)))
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
