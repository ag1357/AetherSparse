from __future__ import annotations

import pytest

from aethersparse.specialists.evaluation import (
    CaseQualification,
    DatasetUse,
    evaluate_configuration,
    matched_ablation,
)


def _record(
    case_id: str,
    *,
    configuration: str = "baseline",
    partition: str = "development",
    correct: bool = True,
    confidence: float = 0.8,
    training_eligible: bool = True,
) -> CaseQualification:
    return CaseQualification(
        case_id=case_id,
        partition=partition,
        corpus_tier="10k",
        training_eligible=training_eligible,
        configuration_id=configuration,
        architecture_sha256=f"sha256:{configuration}",
        exact_correct=correct,
        canonical_correct=correct,
        entity_top1_correct=correct,
        entity_topk_recalled=True,
        value_enumerated=True,
        confidence=confidence,
        selected_claim_count=1,
        unsupported_claim_count=0,
        active_experts=1,
        active_parameters=250_000,
        cognitive_cycles=1,
        active_macs=100_000,
        projected_p4_latency_ms=3.0,
        route_signature="C0:entity\nHALT",
    )


def test_summary_separates_semantic_provenance_calibration_and_compute() -> None:
    records = (
        _record("case:1", confidence=0.8),
        _record("case:2", correct=False, confidence=0.6),
    )
    summary = evaluate_configuration(records, DatasetUse.FIT)
    assert summary.canonical_accuracy == 0.5
    assert summary.provenance_perfect_case_fraction == 1.0
    assert summary.brier_score == pytest.approx(0.2)
    assert summary.expected_calibration_error == pytest.approx(0.4)
    assert summary.mean_active_parameters == 250_000
    assert summary.mean_projected_p4_latency_ms == 3.0


@pytest.mark.parametrize(
    ("dataset_use", "partition", "training", "frozen"),
    (
        (DatasetUse.FIT, "tuning", True, False),
        (DatasetUse.CALIBRATE_SELECT, "development", True, False),
        (DatasetUse.FROZEN_HOLDOUT, "evaluation", False, False),
        (DatasetUse.FROZEN_HOLDOUT, "evaluation", True, True),
    ),
)
def test_split_guards_reject_illegal_dataset_use(
    dataset_use: DatasetUse, partition: str, training: bool, frozen: bool
) -> None:
    record = _record(
        "case:1", partition=partition, training_eligible=training
    )
    with pytest.raises(ValueError):
        evaluate_configuration((record,), dataset_use, architecture_frozen=frozen)


def test_frozen_holdout_accepts_only_nontraining_evaluation_and_final() -> None:
    records = (
        _record(
            "case:1",
            partition="evaluation",
            training_eligible=False,
        ),
        _record(
            "case:2",
            partition="final_held",
            training_eligible=False,
        ),
    )
    summary = evaluate_configuration(
        records, DatasetUse.FROZEN_HOLDOUT, architecture_frozen=True
    )
    assert summary.case_count == 2


def test_matched_ablation_requires_identical_case_tier_keys() -> None:
    baseline = (_record("case:1"), _record("case:2", correct=False))
    candidate = tuple(
        item.model_copy(
            update={
                "configuration_id": "candidate",
                "architecture_sha256": "sha256:candidate",
                "canonical_correct": True,
                "exact_correct": True,
                "active_parameters": 500_000,
            }
        )
        for item in baseline
    )
    delta = matched_ablation(baseline, candidate, DatasetUse.FIT)
    assert delta.canonical_accuracy_delta == 0.5
    assert delta.mean_active_parameters_delta == 250_000
    with pytest.raises(ValueError, match="identical"):
        matched_ablation(baseline, candidate[:1], DatasetUse.FIT)


def test_tier_replicas_may_not_cross_partitions() -> None:
    records = (
        _record("case:1"),
        _record("case:1").model_copy(
            update={"partition": "tuning", "corpus_tier": "25k"}
        ),
    )
    with pytest.raises(ValueError):
        evaluate_configuration(records, DatasetUse.FIT)
