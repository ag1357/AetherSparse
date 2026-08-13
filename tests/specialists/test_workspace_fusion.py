from __future__ import annotations

import pytest
from pydantic import ValidationError

from aethersparse.specialists.fusion import (
    BeliefFusion,
    FusionMethod,
    LearnedFusionParameters,
    expert_disagreement,
)
from aethersparse.specialists.workspace import (
    BeliefSlot,
    CategoricalBelief,
    ComputeBudget,
    ExpertUpdate,
    SharedWorkspace,
)
from scripts.droid.v11_workspace_ablation import _candidate_updates


def _workspace(candidate_count: int = 3) -> SharedWorkspace:
    labels = tuple(f"entity:{index}" for index in range(candidate_count))
    return SharedWorkspace(
        entity_distribution=CategoricalBelief.normalized(
            labels, tuple(range(candidate_count, 0, -1))
        ),
        evidence_sufficiency=0.25,
        missing_facets=("relation",),
        compute_budget=ComputeBudget(
            active_macs_remaining=1_000_000,
            read_operations_remaining=4,
            cycles_remaining=6,
        ),
    )


def _updates(candidate_count: int = 3) -> tuple[ExpertUpdate, ...]:
    labels = tuple(f"entity:{index}" for index in range(candidate_count))
    return (
        ExpertUpdate(
            expert_id="entity.context",
            target=BeliefSlot.ENTITY,
            distribution=CategoricalBelief.normalized(labels, tuple(range(1, candidate_count + 1))),
            reliability_precision=2.0,
            gate_probability=0.8,
            latent_delta=(0.2, -0.1),
            active_parameters=250_000,
            active_macs=50_000,
        ),
        ExpertUpdate(
            expert_id="entity.relation",
            target=BeliefSlot.ENTITY,
            distribution=CategoricalBelief.normalized(labels, tuple(1.0 for _ in labels)),
            reliability_precision=0.5,
            gate_probability=0.6,
            latent_delta=(0.0, 0.3),
            active_parameters=50_000,
            active_macs=10_000,
        ),
    )


def test_belief_rejects_unnormalized_and_duplicate_labels() -> None:
    with pytest.raises(ValidationError, match="sum to one"):
        CategoricalBelief(labels=("a", "b"), probabilities=(0.2, 0.2))
    with pytest.raises(ValidationError, match="unique"):
        CategoricalBelief(labels=("a", "a"), probabilities=(0.5, 0.5))


@pytest.mark.parametrize("method", tuple(FusionMethod))
def test_all_fusion_families_preserve_symbolic_candidate_boundary(method: FusionMethod) -> None:
    workspace = _workspace()
    updates = _updates()
    outcome = BeliefFusion(
        method,
        learned=LearnedFusionParameters(
            reliability_weight=0.5,
            entropy_weight=-0.25,
            disagreement_weight=-0.5,
        ),
    ).fuse(workspace, BeliefSlot.ENTITY, updates)

    assert set(outcome.posterior.labels) <= set(workspace.entity_distribution.labels)  # type: ignore[union-attr]
    assert sum(outcome.posterior.probabilities) == pytest.approx(1.0)
    assert outcome.workspace.compute_budget.active_macs_remaining == 940_000
    assert outcome.workspace.latent_h == pytest.approx((0.16, 0.1))
    assert outcome.disagreement.aggregate > 0.0


def test_particle_fusion_retains_tail_as_uncertainty() -> None:
    outcome = BeliefFusion(FusionMethod.PARTICLE_TOP_K, particle_top_k=2).fuse(
        _workspace(5), BeliefSlot.ENTITY, _updates(5)
    )
    assert len(outcome.posterior.labels) == 3
    assert "__unresolved__" in outcome.posterior.labels
    assert outcome.posterior.probability("__unresolved__") > 0.0


def test_confidently_conflicting_experts_remain_detectable() -> None:
    labels = ("entity:right", "entity:wrong")
    updates = (
        ExpertUpdate(
            expert_id="expert.a",
            target=BeliefSlot.ENTITY,
            distribution=CategoricalBelief(labels=labels, probabilities=(0.99, 0.01)),
            reliability_precision=10.0,
            gate_probability=1.0,
        ),
        ExpertUpdate(
            expert_id="expert.b",
            target=BeliefSlot.ENTITY,
            distribution=CategoricalBelief(labels=labels, probabilities=(0.01, 0.99)),
            reliability_precision=10.0,
            gate_probability=1.0,
        ),
    )
    disagreement = expert_disagreement(updates)
    assert disagreement.top1_disagreement == 1.0
    assert disagreement.confidence_contradiction == pytest.approx(0.99)
    assert disagreement.aggregate >= 0.99


def test_compute_budget_is_a_hard_invariant() -> None:
    workspace = _workspace().model_copy(
        update={
            "compute_budget": ComputeBudget(
                active_macs_remaining=1,
                read_operations_remaining=0,
                cycles_remaining=1,
            )
        }
    )
    with pytest.raises(ValueError, match="exceeds"):
        BeliefFusion(FusionMethod.WEIGHTED_LOGIT).fuse(
            workspace, BeliefSlot.ENTITY, _updates()
        )


def test_replay_candidate_projection_uses_only_bounded_scores() -> None:
    prepared = _candidate_updates(
        {
            "selected_entity_id": "entity:a",
            "candidates": [
                {
                    "entity_id": "entity:a",
                    "confidence": 0.7,
                    "name_score": 0.8,
                    "type_score": 0.6,
                    "relation_score": 0.5,
                    "context_score": 0.9,
                },
                {
                    "entity_id": "entity:b",
                    "confidence": 0.3,
                    "name_score": 0.2,
                    "type_score": 0.4,
                    "relation_score": 0.5,
                    "context_score": 0.1,
                },
            ],
        }
    )
    assert prepared is not None
    prior, updates = prepared
    assert prior.top_label == "entity:a"
    assert tuple(item.expert_id for item in updates) == (
        "entity.name",
        "entity.type",
        "entity.relation",
        "entity.context",
    )
