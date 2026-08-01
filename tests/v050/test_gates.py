from __future__ import annotations

from aethersparse.v050.gates import (
    ArchitectureDecision,
    MetricSnapshot,
    evaluate_gates,
    select_architecture,
)


def _snapshot(**updates: object) -> MetricSnapshot:
    values: dict[str, object] = {
        "article_recall_at_8": 0.92,
        "evidence_recall_at_8": 0.91,
        "exact_answerable_accuracy": 0.86,
        "unsupported_claim_rate": 0.0,
        "entity_link_accuracy": 0.97,
        "silent_wrong_entity_rate": 0.005,
        "answer_shape_accuracy": 0.96,
        "required_facet_accuracy": 0.93,
        "unknown_copy_fidelity": 1.0,
        "multi_source_accuracy": 0.72,
        "comparison_accuracy": 0.71,
        "followup_coreference_accuracy": 0.68,
        "clarification_precision": 0.86,
        "abstention_precision": 0.88,
        "exact_binding_reproducible": True,
        "stable_10k_to_50k": True,
        "credible_edge_backend": True,
    }
    values.update(updates)
    return MetricSnapshot.model_validate(values)


def test_full_gate_selects_edge_controller() -> None:
    metrics = _snapshot()
    assert evaluate_gates(metrics).full_qualification
    assert select_architecture(metrics) is ArchitectureDecision.EDGE_AI


def test_baseline_only_selects_retrieval_appliance() -> None:
    metrics = _snapshot(
        evidence_recall_at_8=0.80,
        exact_answerable_accuracy=0.50,
        entity_link_accuracy=0.80,
        silent_wrong_entity_rate=0.10,
        answer_shape_accuracy=0.70,
        required_facet_accuracy=0.60,
        multi_source_accuracy=0.10,
        comparison_accuracy=0.10,
        followup_coreference_accuracy=0.10,
        clarification_precision=0.20,
        abstention_precision=0.30,
        credible_edge_backend=False,
    )
    assert evaluate_gates(metrics).retained_baseline
    assert select_architecture(metrics) is ArchitectureDecision.RETRIEVAL_ONLY


def test_verified_rag_can_win_only_with_qualified_exact_accuracy() -> None:
    metrics = _snapshot(
        evidence_recall_at_8=0.82,
        exact_answerable_accuracy=0.72,
        multi_source_accuracy=0.55,
        comparison_accuracy=0.53,
        stable_10k_to_50k=False,
        credible_edge_backend=False,
        verified_rag_exact_accuracy=0.88,
    )
    assert select_architecture(metrics) is ArchitectureDecision.VERIFIED_RAG


def test_failed_retained_baseline_is_falsified() -> None:
    metrics = _snapshot(
        article_recall_at_8=0.60,
        evidence_recall_at_8=0.50,
        exact_answerable_accuracy=0.30,
        unsupported_claim_rate=0.02,
        exact_binding_reproducible=False,
        credible_edge_backend=False,
    )
    assert select_architecture(metrics) is ArchitectureDecision.FALSIFIED


def test_downstream_controller_cannot_mask_failed_retained_baseline() -> None:
    metrics = _snapshot(
        retained_baseline_article_recall_at_8=0.60,
        retained_baseline_evidence_recall_at_8=0.50,
        retained_baseline_exact_answerable_accuracy=0.30,
        retained_baseline_unsupported_claim_rate=0.0,
    )
    gates = evaluate_gates(metrics)
    assert gates.full_qualification
    assert gates.retained_baseline is False
    assert select_architecture(metrics) is ArchitectureDecision.FALSIFIED
