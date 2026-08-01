"""Frozen v0.5 qualification gates and deterministic decision policy."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ArchitectureDecision(StrEnum):
    EDGE_AI = "STRUCTURED_CONTROLLER_EDGE_AI_VIABLE"
    HYBRID = "STRUCTURED_CONTROLLER_HYBRID_VIABLE"
    VERIFIED_RAG = "VERIFIED_RAG_PREFERRED"
    RETRIEVAL_ONLY = "RETRIEVAL_APPLIANCE_ONLY"
    FALSIFIED = "ARCHITECTURE_FALSIFIED"


class HardwareDecision(StrEnum):
    P4_REFERENCE = "P4_PICO_REFERENCE_PURCHASE_JUSTIFIED"
    P4_FINAL = "P4_PICO_FINAL_TARGET_JUSTIFIED"
    CORE1106 = "CORE1106_PURCHASE_JUSTIFIED"
    RT700 = "RT700_CLASS_TARGET_JUSTIFIED"
    FPGA = "FPGA_EXPERIMENT_JUSTIFIED"
    NO_PURCHASE = "NO_HARDWARE_PURCHASE_JUSTIFIED"


class MetricSnapshot(FrozenModel):
    """One frozen evaluation snapshot; all rates are fractions in [0, 1]."""

    article_recall_at_8: float = Field(ge=0.0, le=1.0)
    evidence_recall_at_8: float = Field(ge=0.0, le=1.0)
    exact_answerable_accuracy: float = Field(ge=0.0, le=1.0)
    unsupported_claim_rate: float = Field(ge=0.0, le=1.0)
    entity_link_accuracy: float = Field(ge=0.0, le=1.0)
    silent_wrong_entity_rate: float = Field(ge=0.0, le=1.0)
    answer_shape_accuracy: float = Field(ge=0.0, le=1.0)
    required_facet_accuracy: float = Field(ge=0.0, le=1.0)
    unknown_copy_fidelity: float = Field(ge=0.0, le=1.0)
    multi_source_accuracy: float = Field(ge=0.0, le=1.0)
    comparison_accuracy: float = Field(ge=0.0, le=1.0)
    followup_coreference_accuracy: float = Field(ge=0.0, le=1.0)
    clarification_precision: float = Field(ge=0.0, le=1.0)
    abstention_precision: float = Field(ge=0.0, le=1.0)
    exact_binding_reproducible: bool
    stable_10k_to_50k: bool
    credible_edge_backend: bool
    verified_rag_exact_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    retained_baseline_article_recall_at_8: float | None = Field(
        default=None, ge=0.0, le=1.0
    )
    retained_baseline_evidence_recall_at_8: float | None = Field(
        default=None, ge=0.0, le=1.0
    )
    retained_baseline_exact_answerable_accuracy: float | None = Field(
        default=None, ge=0.0, le=1.0
    )
    retained_baseline_unsupported_claim_rate: float | None = Field(
        default=None, ge=0.0, le=1.0
    )


class GateEvaluation(FrozenModel):
    retained_baseline: bool
    entity_and_query: bool
    cognitive_answering: bool
    full_qualification: bool
    failures: tuple[str, ...]


def evaluate_gates(metrics: MetricSnapshot) -> GateEvaluation:
    """Evaluate the published R/E/C/Q gates without discretionary adjustment."""

    baseline_article = (
        metrics.retained_baseline_article_recall_at_8
        if metrics.retained_baseline_article_recall_at_8 is not None
        else metrics.article_recall_at_8
    )
    baseline_evidence = (
        metrics.retained_baseline_evidence_recall_at_8
        if metrics.retained_baseline_evidence_recall_at_8 is not None
        else metrics.evidence_recall_at_8
    )
    baseline_exact = (
        metrics.retained_baseline_exact_answerable_accuracy
        if metrics.retained_baseline_exact_answerable_accuracy is not None
        else metrics.exact_answerable_accuracy
    )
    baseline_unsupported = (
        metrics.retained_baseline_unsupported_claim_rate
        if metrics.retained_baseline_unsupported_claim_rate is not None
        else metrics.unsupported_claim_rate
    )
    checks = {
        "R_ARTICLE_RECALL": baseline_article >= 0.84,
        "R_EVIDENCE_RECALL": baseline_evidence >= 0.79,
        "R_EXACT_ANSWER": baseline_exact >= 0.49,
        "R_UNSUPPORTED_ZERO": baseline_unsupported == 0.0,
        "R_SOURCE_BINDING": metrics.exact_binding_reproducible,
        "E_ENTITY_LINK": metrics.entity_link_accuracy >= 0.95,
        "E_WRONG_ENTITY": metrics.silent_wrong_entity_rate < 0.01,
        "E_ANSWER_SHAPE": metrics.answer_shape_accuracy >= 0.95,
        "E_REQUIRED_FACET": metrics.required_facet_accuracy >= 0.90,
        "E_UNKNOWN_COPY": metrics.unknown_copy_fidelity >= 0.99,
        "C_EXACT_ANSWER": metrics.exact_answerable_accuracy >= 0.70,
        "C_MULTI_SOURCE": metrics.multi_source_accuracy >= 0.50,
        "C_COMPARISON": metrics.comparison_accuracy >= 0.50,
        "C_FOLLOWUP": metrics.followup_coreference_accuracy >= 0.60,
        "C_CLARIFICATION": metrics.clarification_precision >= 0.80,
        "C_ABSTENTION": metrics.abstention_precision >= 0.80,
        "C_UNSUPPORTED": metrics.unsupported_claim_rate < 0.01,
        "Q_EVIDENCE": metrics.evidence_recall_at_8 >= 0.90,
        "Q_EXACT_ANSWER": metrics.exact_answerable_accuracy >= 0.85,
        "Q_MULTI_SOURCE": metrics.multi_source_accuracy >= 0.70,
        "Q_COMPARISON": metrics.comparison_accuracy >= 0.70,
        "Q_WRONG_ENTITY": metrics.silent_wrong_entity_rate < 0.01,
        "Q_UNSUPPORTED": metrics.unsupported_claim_rate < 0.01,
        "Q_SCALING": metrics.stable_10k_to_50k,
        "Q_EDGE": metrics.credible_edge_backend,
    }
    retained = all(value for key, value in checks.items() if key.startswith("R_"))
    entity = all(value for key, value in checks.items() if key.startswith("E_"))
    cognitive = all(value for key, value in checks.items() if key.startswith("C_"))
    full = all(value for key, value in checks.items() if key.startswith("Q_"))
    return GateEvaluation(
        retained_baseline=retained,
        entity_and_query=entity,
        cognitive_answering=cognitive,
        full_qualification=full,
        failures=tuple(key for key, value in checks.items() if not value),
    )


def select_architecture(metrics: MetricSnapshot) -> ArchitectureDecision:
    """Issue exactly one architecture token from frozen evidence."""

    gates = evaluate_gates(metrics)
    comparator = metrics.verified_rag_exact_accuracy
    if (
        gates.retained_baseline
        and gates.entity_and_query
        and gates.cognitive_answering
        and gates.full_qualification
    ):
        return ArchitectureDecision.EDGE_AI
    if (
        gates.retained_baseline
        and gates.entity_and_query
        and gates.cognitive_answering
        and metrics.stable_10k_to_50k
        and not metrics.credible_edge_backend
    ):
        return ArchitectureDecision.HYBRID
    if (
        comparator is not None
        and comparator >= 0.85
        and comparator > metrics.exact_answerable_accuracy
        and metrics.unsupported_claim_rate < 0.01
    ):
        return ArchitectureDecision.VERIFIED_RAG
    if gates.retained_baseline:
        return ArchitectureDecision.RETRIEVAL_ONLY
    return ArchitectureDecision.FALSIFIED
