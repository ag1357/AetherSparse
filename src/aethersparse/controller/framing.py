"""Schema-flexible query framing with broad linguistic features.

The rules classify slots and constraints; they do not enumerate complete
questions or directly choose evidence.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping

from aethersparse.controller.models import (
    AnswerShape,
    DiscourseReference,
    EntityMention,
    QueryFrame,
    RequiredFacet,
)

YEAR_RE = re.compile(r"\b(?:1[0-9]{3}|20[0-9]{2}|2100)\b")
CAPITALIZED_RE = re.compile(
    r"(?<![\w'-])(?:[A-Z][\w'-]*(?:\s+(?:of|the|and|de|van)\s+|\s+)){0,5}[A-Z][\w'-]*"
)
QUOTED_RE = re.compile(r"[\"“]([^\"”]{2,200})[\"”]")
PRONOUN_RE = re.compile(r"\b(it|its|they|them|their|he|him|his|she|her|hers|that one)\b", re.I)

DEFAULT_RELATION_CUES: dict[str, tuple[str, ...]] = {
    "birth": ("born", "birth", "birthplace"),
    "death": ("died", "death"),
    "date": ("when", "date", "year"),
    "location": ("where", "located", "place", "capital"),
    "quantity": ("how many", "how much", "population", "distance", "height"),
    "quotation": ("who said", "quote", "quotation", "stated", "wrote"),
    "definition": ("what is", "what are", "define", "meaning"),
    "comparison": ("compare", "difference", "larger", "smaller", "older", "newer"),
    "cause": ("why", "reason", "cause", "because"),
    "membership": ("member", "part of", "belongs", "included"),
    "event": ("happened", "occurred", "event"),
}

QUESTION_OPENERS = {
    "who",
    "what",
    "when",
    "where",
    "why",
    "which",
    "how",
    "is",
    "are",
    "was",
    "were",
    "did",
    "does",
    "do",
    "can",
    "could",
    "would",
    "name",
    "list",
    "compare",
}


def normalize_query(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).strip().split())


def infer_answer_shape(query: str) -> AnswerShape:
    folded = query.casefold()
    if "refer to here" in folded:
        # The missing local referent is the point of the question; guessing a
        # definition would turn ambiguity into a silent entity selection.
        return AnswerShape.UNKNOWN
    if "redirect to" in folded or "official biography of" in folded:
        return AnswerShape.DEFINITION
    if any(
        cue in folded
        for cue in (
            "compare",
            "difference",
            "which is larger",
            "which was older",
            " larger ",
            " smaller ",
            " older ",
            " newer ",
        )
    ):
        return AnswerShape.COMPARISON
    if folded.startswith("who said"):
        return AnswerShape.ENTITY
    if "quotation" in folded or " quote" in folded or ("what did" in folded and "say" in folded):
        return AnswerShape.QUOTATION
    if folded.startswith("who") or folded.startswith("which person"):
        return AnswerShape.ENTITY
    if (
        folded.startswith("when")
        or "what date" in folded
        or "which date" in folded
        or "which year" in folded
    ):
        return AnswerShape.DATE
    if (
        folded.startswith("how many")
        or folded.startswith("how much")
        or "which quantity" in folded
        or "which numeric value" in folded
    ):
        return AnswerShape.QUANTITY
    if folded.startswith(("is ", "are ", "was ", "were ", "did ", "does ", "do ")):
        return AnswerShape.VERIFICATION
    if folded.startswith("why") or "what caused" in folded:
        return AnswerShape.EXPLANATION
    if "define" in folded or "refer to" in folded or "what is it" in folded:
        return AnswerShape.DEFINITION
    if folded.startswith("how"):
        return AnswerShape.PROCESS
    if (
        folded.startswith("list")
        or "name all" in folded
        or "using both sources" in folded
        or "for each of" in folded
    ):
        return AnswerShape.LIST
    if folded.startswith("what happened") or "what event" in folded:
        return AnswerShape.EVENT
    if folded.startswith(("what is", "what are", "define")):
        return AnswerShape.DEFINITION
    if folded.startswith(("where", "which")):
        return AnswerShape.ENTITY
    return AnswerShape.UNKNOWN


def facets_for_shape(shape: AnswerShape) -> tuple[RequiredFacet, ...]:
    common = (RequiredFacet.SUBJECT, RequiredFacet.RELATION, RequiredFacet.SOURCE)
    shape_facets: dict[AnswerShape, tuple[RequiredFacet, ...]] = {
        AnswerShape.DATE: (RequiredFacet.TIME,),
        AnswerShape.QUANTITY: (RequiredFacet.QUANTITY,),
        AnswerShape.QUOTATION: (RequiredFacet.SPEAKER, RequiredFacet.QUOTATION),
        AnswerShape.COMPARISON: (
            RequiredFacet.COMPARISON_A,
            RequiredFacet.COMPARISON_B,
            RequiredFacet.QUANTITY,
        ),
        AnswerShape.EXPLANATION: (RequiredFacet.REASON,),
        AnswerShape.ENTITY: (RequiredFacet.OBJECT,),
        AnswerShape.DEFINITION: (RequiredFacet.OBJECT,),
        AnswerShape.EVENT: (RequiredFacet.OBJECT, RequiredFacet.TIME),
        AnswerShape.PROCESS: (RequiredFacet.OBJECT,),
        AnswerShape.LIST: (RequiredFacet.OBJECT,),
        AnswerShape.VERIFICATION: (RequiredFacet.OBJECT,),
        AnswerShape.UNKNOWN: (RequiredFacet.OBJECT,),
    }
    return common + shape_facets[shape]


class QueryFramer:
    """Build a conservative frame before entity resolution and retrieval."""

    def __init__(self, relation_cues: Mapping[str, tuple[str, ...]] | None = None) -> None:
        self.relation_cues = dict(relation_cues or DEFAULT_RELATION_CUES)

    @staticmethod
    def mention_spans(query: str) -> tuple[EntityMention, ...]:
        mentions: list[EntityMention] = []
        occupied: list[tuple[int, int]] = []
        for match in CAPITALIZED_RE.finditer(query):
            surface = match.group(0)
            if surface.casefold() in QUESTION_OPENERS:
                continue
            start, end = match.span()
            mentions.append(EntityMention(surface=surface, char_start=start, char_end=end))
            occupied.append((start, end))
        # Quoted unknown names remain exact copyable spans, unless already inside a name.
        for match in QUOTED_RE.finditer(query):
            start, end = match.span(1)
            if any(left <= start and end <= right for left, right in occupied):
                continue
            mentions.append(EntityMention(surface=match.group(1), char_start=start, char_end=end))
        return tuple(sorted(mentions, key=lambda item: (item.char_start, item.char_end)))

    def frame(
        self,
        text: str,
        *,
        prior_entity_ids: tuple[str, ...] = (),
    ) -> QueryFrame:
        query = normalize_query(text)
        folded = query.casefold()
        shape = infer_answer_shape(query)
        relations = tuple(
            relation
            for relation, cues in self.relation_cues.items()
            if any(cue in folded for cue in cues)
        )
        mentions = self.mention_spans(query)
        discourse = tuple(
            DiscourseReference(
                surface=match.group(0),
                antecedent_entity_ids=prior_entity_ids[-2:],
                confidence=0.9 if len(prior_entity_ids) == 1 else 0.45,
            )
            for match in PRONOUN_RE.finditer(query)
        )
        locations = tuple(
            match.group(1)
            for match in re.finditer(
                r"\b(?:in|at|near|from)\s+([A-Z][\w'-]*(?:\s+[A-Z][\w'-]*){0,4})",
                query,
            )
        )
        attributions = tuple(match.group(1) for match in QUOTED_RE.finditer(query))
        comparison_targets = (
            tuple(item.surface for item in mentions[:2]) if shape is AnswerShape.COMPARISON else ()
        )
        premise = (query.rstrip("?"),) if shape is AnswerShape.VERIFICATION else ()
        incomplete = (
            len(query.split()) < 3
            or query.endswith((" of", " about", " between"))
            or "what about it" in folded
        )
        unresolved_discourse = any(not item.antecedent_entity_ids for item in discourse)
        uncertainty = 0.15
        if shape is AnswerShape.UNKNOWN:
            uncertainty += 0.35
        if not relations:
            uncertainty += 0.2
        if unresolved_discourse:
            uncertainty += 0.25
        if incomplete:
            uncertainty += 0.35
        uncertainty = min(1.0, uncertainty)
        required_facets = facets_for_shape(shape)
        if shape is AnswerShape.ENTITY and "quotation" in relations:
            required_facets = (
                RequiredFacet.SUBJECT,
                RequiredFacet.RELATION,
                RequiredFacet.SPEAKER,
                RequiredFacet.QUOTATION,
                RequiredFacet.SOURCE,
            )
        return QueryFrame(
            normalized_query=query,
            entity_mentions=mentions,
            candidate_entity_ids=prior_entity_ids if discourse else (),
            requested_relation_families=relations,
            answer_shape=shape,
            required_facets=required_facets,
            temporal_constraints=tuple(YEAR_RE.findall(query)),
            location_constraints=locations,
            attribution_constraints=attributions,
            comparison_targets=comparison_targets,
            premise_claims=premise,
            discourse_references=discourse,
            uncertainty=uncertainty,
            clarification_need=incomplete or unresolved_discourse,
        )
