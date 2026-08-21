from __future__ import annotations

from aethersparse.controller.adaptive_policy import (
    ADAPTIVE_FEATURE_NAMES,
    adaptive_action_features,
    fit_adaptive_policy,
    quantize_adaptive_policy,
)
from aethersparse.controller.micro_ops import MicroAction, MicroState, execute_action, legal_actions


def _state(*, renamed_spans: bool = False) -> MicroState:
    good_span = "opaque:evidence:a" if renamed_spans else "span:first"
    bad_span = "opaque:evidence:b" if renamed_spans else "span:second"
    return MicroState(
        case_id="case:adaptive",
        frame={
            "answer_shape": "definition",
            "candidate_entity_ids": [],
            "entity_mentions": [
                {
                    "selected_entity_id": "entity:good",
                    "selected_confidence": 0.95,
                    "candidates": [
                        {"entity_id": "entity:good", "confidence": 0.95},
                        {"entity_id": "entity:other", "confidence": 0.20},
                    ],
                }
            ],
            "requested_relation_families": ["definition"],
            "required_facets": ["subject", "relation", "object", "source"],
        },
        claims=(
            {
                "claim_id": "claim:bad",
                "subject_entity_id": "entity:other",
                "relation_family": "definition",
                "answer_shape": "definition",
                "object_value": "wrong value",
                "source_span_ids": [bad_span],
                "confidence": 0.99,
            },
            {
                "claim_id": "claim:good",
                "subject_entity_id": "entity:good",
                "relation_family": "definition",
                "answer_shape": "definition",
                "object_value": "grounded value",
                "source_span_ids": [good_span],
                "confidence": 0.80,
            },
        ),
        source_spans=(
            {"span_id": good_span, "text": "An entity is a grounded value with context."},
            {"span_id": bad_span, "text": "wrong value"},
        ),
    )


def test_ambiguous_entity_hypotheses_are_not_treated_as_match_all() -> None:
    state = execute_action(_state(), MicroAction(operation_id=32))
    good = MicroAction(operation_id=43, arguments={"claim_id": "claim:good"})
    bad = MicroAction(operation_id=43, arguments={"claim_id": "claim:bad"})
    good_features = dict(
        zip(ADAPTIVE_FEATURE_NAMES, adaptive_action_features(state, good), strict=True)
    )
    bad_features = dict(
        zip(ADAPTIVE_FEATURE_NAMES, adaptive_action_features(state, bad), strict=True)
    )
    assert good_features["claim_subject_matches_hypothesis"] == 1.0
    assert good_features["claim_subject_hypothesis_confidence"] == 0.95
    assert bad_features["claim_subject_conflicts_hypothesis"] == 0.0
    assert bad_features["claim_subject_hypothesis_confidence"] == 0.20


def test_span_identifier_text_cannot_change_features() -> None:
    original = execute_action(_state(), MicroAction(operation_id=32))
    renamed = execute_action(_state(renamed_spans=True), MicroAction(operation_id=32))
    action = MicroAction(operation_id=43, arguments={"claim_id": "claim:good"})
    assert adaptive_action_features(original, action) == adaptive_action_features(renamed, action)


def test_same_scale_policy_quantizes_to_int8_and_stays_inside_legal_mask() -> None:
    initial = _state()
    enumerate_claims = MicroAction(operation_id=32)
    active = execute_action(initial, enumerate_claims)
    select_good = MicroAction(operation_id=43, arguments={"claim_id": "claim:good"})
    policy = fit_adaptive_policy(
        [(initial, enumerate_claims), (active, select_good)], epochs=12
    )
    quantized = quantize_adaptive_policy(policy)
    assert policy.parameter_count == 1_292
    assert quantized.parameter_bytes == 1_292
    assert all(-127 <= value <= 127 for row in quantized.weights_int8 for value in row)
    assert quantized.select(initial) == enumerate_claims
    assert quantized.select(active) == select_good
    assert quantized.select(active) in legal_actions(active)
