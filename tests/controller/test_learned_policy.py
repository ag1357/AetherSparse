from __future__ import annotations

from aethersparse.controller.learned_policy import (
    FEATURE_NAMES,
    action_features,
    finite_weights,
    fit_masked_linear_policy,
)
from aethersparse.controller.micro_ops import MicroAction, MicroState, execute_action, legal_actions


def _state() -> MicroState:
    return MicroState(
        case_id="case:policy",
        frame={
            "answer_shape": "definition",
            "candidate_entity_ids": ["entity:good"],
            "requested_relation_families": ["definition"],
        },
        claims=(
            {
                "claim_id": "claim:bad",
                "subject_entity_id": "entity:other",
                "relation_family": "definition",
                "object_value": "wrong",
                "source_span_ids": ["span:bad"],
            },
            {
                "claim_id": "claim:good",
                "subject_entity_id": "entity:good",
                "relation_family": "definition",
                "object_value": "grounded value",
                "source_span_ids": ["span:good"],
                "confidence": 0.9,
            },
        ),
        source_spans=(
            {"span_id": "span:bad", "text": "wrong"},
            {"span_id": "span:good", "text": "grounded value"},
        ),
    )


def test_feature_schema_and_parameter_bound() -> None:
    state = _state()
    action = MicroAction(operation_id=32)
    assert len(action_features(state, action)) == len(FEATURE_NAMES)
    policy = fit_masked_linear_policy([(state, action)], epochs=2)
    assert policy.parameter_count < 250_000
    assert finite_weights(policy)
    assert policy.select(state) == action


def test_policy_can_learn_claim_argument_and_never_escape_legal_mask() -> None:
    initial = _state()
    enumerate_claims = MicroAction(operation_id=32)
    active = execute_action(initial, enumerate_claims)
    select_good = MicroAction(operation_id=43, arguments={"claim_id": "claim:good"})
    policy = fit_masked_linear_policy(
        [(initial, enumerate_claims), (active, select_good)], epochs=16
    )
    chosen_initial = policy.select(initial)
    chosen_active = policy.select(active)
    assert chosen_initial in legal_actions(initial)
    assert chosen_active in legal_actions(active)
    assert chosen_initial == enumerate_claims
    assert chosen_active == select_good
