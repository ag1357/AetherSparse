from __future__ import annotations

from aethersparse.controller.micro_ops import MicroState
from aethersparse.controller.semantic_address import SemanticAddressPlane
from aethersparse.controller.semantic_state import enrich_state_with_semantic_addresses


def _plane() -> SemanticAddressPlane:
    return SemanticAddressPlane.from_document(
        {
            "schema_version": "aethersparse.entity-anchor-statistics.v11",
            "alpha": 1.0,
            "requested_mention_count": 1,
            "covered_mention_count": 1,
            "source_pack_sha256": "a" * 64,
            "statistics": [
                {
                    "mention": "mercury",
                    "target_title": "mercury planet",
                    "target_entity_id": "as:v050:entity:8ae75df8d290a1074d25e2eb",
                    "occurrence_count": 3,
                    "total_mention_occurrences": 4,
                    "probability": 2 / 3,
                    "ambiguity_count": 2,
                    "entropy_nats": 0.6365141682948128,
                    "source_document_count": 3,
                    "title_indicator": False,
                    "title_prior": 0.0,
                    "redirect_indicator": False,
                    "redirect_support_count": 0,
                    "redirect_prior": 0.0,
                    "alias_types": ["anchor"],
                },
                {
                    "mention": "mercury",
                    "target_title": "mercury element",
                    "target_entity_id": None,
                    "occurrence_count": 1,
                    "total_mention_occurrences": 4,
                    "probability": 1 / 3,
                    "ambiguity_count": 2,
                    "entropy_nats": 0.6365141682948128,
                    "source_document_count": 1,
                    "title_indicator": False,
                    "title_prior": 0.0,
                    "redirect_indicator": False,
                    "redirect_support_count": 0,
                    "redirect_prior": 0.0,
                    "alias_types": ["anchor"],
                },
            ],
        }
    )


def _state() -> MicroState:
    return MicroState(
        case_id="case:semantic-state",
        frame={
            "normalized_query": "When was Mercury discovered?",
            "entity_mentions": [
                {
                    "surface": "Mercury",
                    "char_start": 9,
                    "char_end": 16,
                    "candidates": [
                        {
                            "entity_id": "as:v050:entity:existing",
                            "title": "Mercury",
                            "method": "exact_title",
                            "name_score": 1.0,
                            "type_score": 1.0,
                            "relation_score": 1.0,
                            "context_score": 1.0,
                            "confidence": 1.0,
                        }
                    ],
                    "selected_entity_id": "as:v050:entity:existing",
                    "selected_confidence": 1.0,
                    "resolution_method": "exact_title",
                    "copy_status": "linked",
                }
            ],
            "candidate_entity_ids": ["as:v050:entity:existing"],
            "requested_relation_families": ["discovery date"],
            "answer_shape": "date",
            "required_facets": ["subject", "relation", "source", "date"],
            "temporal_constraints": [],
            "location_constraints": [],
            "attribution_constraints": [],
            "comparison_targets": [],
            "premise_claims": [],
            "discourse_references": [],
            "uncertainty": 0.4,
            "clarification_need": False,
        },
        claims=(),
        source_spans=(),
    )


def test_semantic_state_preserves_selection_and_adds_uncertain_address() -> None:
    result = enrich_state_with_semantic_addresses(_state(), _plane())

    assert result.added_entity_ids == ("as:v050:entity:8ae75df8d290a1074d25e2eb",)
    assert result.enriched_mentions == 1
    assert result.candidate_capacity_exhausted is False
    assert result.distributions[0].resolved_probability_mass == 2 / 3
    assert result.distributions[0].unresolved_probability_mass == 1 / 3
    assert result.state.frame["candidate_entity_ids"] == (
        "as:v050:entity:existing",
        "as:v050:entity:8ae75df8d290a1074d25e2eb",
    )
    mention = result.state.frame["entity_mentions"][0]
    assert mention["selected_entity_id"] == "as:v050:entity:existing"
    assert len(mention["candidates"]) == 2


def test_semantic_state_is_bounded_without_dropping_original_address() -> None:
    result = enrich_state_with_semantic_addresses(
        _state(), _plane(), max_frame_entity_ids=1
    )

    assert result.candidate_capacity_exhausted is True
    assert result.added_entity_ids == ()
    assert result.state.frame["candidate_entity_ids"] == (
        "as:v050:entity:existing",
    )
