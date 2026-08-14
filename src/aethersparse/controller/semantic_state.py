"""Gold-independent Semantic Address Plane projection into controller state."""

from __future__ import annotations

from dataclasses import dataclass

from aethersparse.controller.micro_ops import MicroState
from aethersparse.controller.models import EntityCandidate, EntityMention, ResolutionMethod
from aethersparse.controller.semantic_address import (
    SemanticAddressDistribution,
    SemanticAddressPlane,
    normalize_mention,
)


@dataclass(frozen=True)
class SemanticStateResult:
    """A monotonic address projection and its bounded audit facts."""

    state: MicroState
    distributions: tuple[SemanticAddressDistribution, ...]
    added_entity_ids: tuple[str, ...]
    enriched_mentions: int
    candidate_capacity_exhausted: bool


def _candidate(hypothesis: object, mention_surface: str) -> EntityCandidate:
    # Kept separate so the controller-facing fields stay explicit.  Occurrence
    # probability is a prior, not contextual, relational, or type evidence.
    from aethersparse.controller.semantic_address import SemanticAddressHypothesis

    if not isinstance(hypothesis, SemanticAddressHypothesis):
        raise TypeError("expected a semantic address hypothesis")
    normalized_title = normalize_mention(hypothesis.target_title)
    name_score = 1.0 if normalized_title == normalize_mention(mention_surface) else 0.0
    return EntityCandidate(
        entity_id=hypothesis.entity_id,
        title=hypothesis.target_title,
        method=ResolutionMethod.ANCHOR,
        name_score=name_score,
        type_score=0.0,
        relation_score=0.0,
        context_score=0.0,
        confidence=hypothesis.mention_probability,
    )


def enrich_state_with_semantic_addresses(
    state: MicroState,
    plane: SemanticAddressPlane,
    *,
    max_addresses_per_mention: int = 8,
    max_frame_entity_ids: int = 64,
) -> SemanticStateResult:
    """Add bounded occurrence-backed alternatives without forcing a selection.

    The transformation consumes only the query frame and the frozen occurrence
    distribution.  It never receives accepted answers, required entity IDs, or
    partition labels.  Existing candidates and selected addresses remain first
    and are never removed.
    """

    if not 1 <= max_addresses_per_mention <= 32:
        raise ValueError("max_addresses_per_mention must be in [1,32]")
    if not 1 <= max_frame_entity_ids <= 256:
        raise ValueError("max_frame_entity_ids must be in [1,256]")
    raw_mentions = state.frame.get("entity_mentions", ())
    if not isinstance(raw_mentions, (list, tuple)):
        raise ValueError("controller frame entity_mentions must be a sequence")
    mentions = tuple(EntityMention.model_validate(item) for item in raw_mentions)
    distributions: list[SemanticAddressDistribution] = []
    enriched: list[EntityMention] = []
    added_ids: list[str] = []
    for mention in mentions:
        distribution = plane.distribution(
            mention.surface,
            retained_candidates=tuple(
                (candidate.entity_id, candidate.confidence) for candidate in mention.candidates
            ),
        )
        distributions.append(distribution)
        present = {candidate.entity_id for candidate in mention.candidates}
        additions: list[EntityCandidate] = []
        for hypothesis in distribution.hypotheses[:max_addresses_per_mention]:
            if hypothesis.entity_id in present:
                continue
            present.add(hypothesis.entity_id)
            additions.append(_candidate(hypothesis, mention.surface))
            added_ids.append(hypothesis.entity_id)
        enriched.append(
            mention.model_copy(update={"candidates": (*mention.candidates, *additions)})
        )

    original_frame_ids = tuple(
        str(item) for item in state.frame.get("candidate_entity_ids", ()) if str(item)
    )
    candidate_ids = tuple(
        dict.fromkeys(
            (
                *original_frame_ids,
                *(candidate.entity_id for mention in enriched for candidate in mention.candidates),
            )
        )
    )
    bounded_ids = candidate_ids[:max_frame_entity_ids]
    bounded_set = set(bounded_ids)
    bounded_mentions = tuple(
        mention.model_copy(
            update={
                "candidates": tuple(
                    candidate
                    for candidate in mention.candidates
                    if candidate.entity_id in bounded_set
                )
            }
        )
        for mention in enriched
    )
    frame = {
        **state.frame,
        "entity_mentions": tuple(
            mention.model_dump(mode="json") for mention in bounded_mentions
        ),
        "candidate_entity_ids": bounded_ids,
    }
    retained_added = tuple(
        entity_id
        for entity_id in dict.fromkeys(added_ids)
        if entity_id in bounded_set and entity_id not in original_frame_ids
    )
    return SemanticStateResult(
        state=state.model_copy(update={"frame": frame}),
        distributions=tuple(distributions),
        added_entity_ids=retained_added,
        enriched_mentions=sum(bool(distribution.hypotheses) for distribution in distributions),
        candidate_capacity_exhausted=len(candidate_ids) > len(bounded_ids),
    )
