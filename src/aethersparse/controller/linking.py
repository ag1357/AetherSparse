"""Contextual canonical entity linking with explicit fail-closed thresholds."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

from aethersparse.controller.models import (
    CanonicalEntity,
    EntityCandidate,
    EntityMention,
    QueryFrame,
    ResolutionMethod,
)


def _key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"[\w'-]+", normalized))


def _edit_similarity(left: str, right: str) -> float:
    if left == right:
        return 1.0
    if not left or not right:
        return 0.0
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_char != right_char),
                )
            )
        previous = current
    return 1.0 - previous[-1] / max(len(left), len(right))


class EntityRegistry:
    """Deterministic registry indexes; candidates never become facts."""

    def __init__(self, entities: tuple[CanonicalEntity, ...]) -> None:
        self.entities = {entity.entity_id: entity for entity in entities}
        self.title: dict[str, set[str]] = defaultdict(set)
        self.redirect: dict[str, set[str]] = defaultdict(set)
        self.alias: dict[str, set[str]] = defaultdict(set)
        self.anchor: dict[str, set[str]] = defaultdict(set)
        for entity in entities:
            self.title[_key(entity.title)].add(entity.entity_id)
            for value in entity.redirects:
                self.redirect[_key(value)].add(entity.entity_id)
            for value in entity.aliases:
                self.alias[_key(value)].add(entity.entity_id)
            for value in entity.anchors:
                self.anchor[_key(value)].add(entity.entity_id)

    def _named_candidates(self, surface: str) -> dict[str, tuple[ResolutionMethod, float]]:
        key = _key(surface)
        found: dict[str, tuple[ResolutionMethod, float]] = {}
        cascade = (
            (self.title, ResolutionMethod.EXACT_TITLE, 1.0),
            (self.redirect, ResolutionMethod.REDIRECT, 0.99),
            (self.alias, ResolutionMethod.ALIAS, 0.97),
            (self.anchor, ResolutionMethod.ANCHOR, 0.93),
        )
        for index, method, score in cascade:
            for entity_id in index.get(key, set()):
                found.setdefault(entity_id, (method, score))
        if found:
            return found
        # Bounded misspelling search. Only plausible length-neighbours can enter.
        if len(key) >= 4:
            for entity_id, entity in self.entities.items():
                names = (entity.title, *entity.aliases, *entity.redirects)
                similarity = max(
                    (_edit_similarity(key, _key(name)) for name in names),
                    default=0.0,
                )
                if similarity >= 0.78:
                    found[entity_id] = (ResolutionMethod.FUZZY, 0.88 * similarity)
        return found

    def candidates(
        self,
        mention: EntityMention,
        *,
        query: str,
        requested_relations: tuple[str, ...],
        expected_types: tuple[str, ...] = (),
        limit: int = 8,
    ) -> tuple[EntityCandidate, ...]:
        query_terms = set(_key(query).split())
        ranked: list[EntityCandidate] = []
        for entity_id, (method, name_score) in self._named_candidates(mention.surface).items():
            entity = self.entities[entity_id]
            type_score = (
                1.0
                if not expected_types
                else float(bool(set(expected_types) & set(entity.entity_types)))
            )
            relation_score = (
                1.0
                if not requested_relations
                else len(set(requested_relations) & set(entity.relation_families))
                / len(set(requested_relations))
            )
            context_terms = set(_key(" ".join((entity.title, *entity.aliases))).split())
            context_score = len(query_terms & context_terms) / max(1, len(context_terms))
            confidence = (
                0.62 * name_score + 0.12 * type_score + 0.16 * relation_score + 0.10 * context_score
            )
            ranked.append(
                EntityCandidate(
                    entity_id=entity_id,
                    title=entity.title,
                    method=method,
                    name_score=name_score,
                    type_score=type_score,
                    relation_score=relation_score,
                    context_score=context_score,
                    confidence=min(1.0, confidence),
                )
            )
        return tuple(sorted(ranked, key=lambda item: (-item.confidence, item.entity_id))[:limit])

    def resolve_mention(
        self,
        mention: EntityMention,
        *,
        query: str,
        requested_relations: tuple[str, ...],
        expected_types: tuple[str, ...] = (),
        threshold: float = 0.82,
        margin: float = 0.08,
    ) -> EntityMention:
        candidates = self.candidates(
            mention,
            query=query,
            requested_relations=requested_relations,
            expected_types=expected_types,
        )
        if not candidates:
            return mention.model_copy(
                update={"candidates": (), "copy_status": "unknown_but_copyable"}
            )
        top = candidates[0]
        runner_up = candidates[1].confidence if len(candidates) > 1 else 0.0
        if top.confidence < threshold or top.confidence - runner_up < margin:
            return mention.model_copy(
                update={
                    "candidates": candidates,
                    "selected_confidence": top.confidence,
                    "resolution_method": top.method,
                    "copy_status": "ambiguous",
                }
            )
        return mention.model_copy(
            update={
                "candidates": candidates,
                "selected_entity_id": top.entity_id,
                "selected_confidence": top.confidence,
                "resolution_method": top.method,
                "copy_status": "linked",
            }
        )

    def link_frame(self, frame: QueryFrame) -> QueryFrame:
        mentions = tuple(
            self.resolve_mention(
                mention,
                query=frame.normalized_query,
                requested_relations=frame.requested_relation_families,
            )
            for mention in frame.entity_mentions
        )
        selected = tuple(
            dict.fromkeys(
                (
                    *frame.candidate_entity_ids,
                    *(item.selected_entity_id for item in mentions if item.selected_entity_id),
                )
            )
        )
        ambiguous = any(item.copy_status == "ambiguous" for item in mentions)
        uncertainty = max(
            frame.uncertainty,
            max((1.0 - item.selected_confidence for item in mentions), default=0.0),
        )
        return frame.model_copy(
            update={
                "entity_mentions": mentions,
                "candidate_entity_ids": selected,
                "uncertainty": uncertainty,
                "clarification_need": frame.clarification_need or ambiguous,
            }
        )

    @staticmethod
    def verify_unknown_copy(query: str, mention: EntityMention) -> bool:
        return query[mention.char_start : mention.char_end] == mention.surface
