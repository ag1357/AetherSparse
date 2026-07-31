from __future__ import annotations

from aethersparse.autonomy.digital_twin import (
    BackendId,
    RecommendationDecision,
    WorkloadProfile,
    WorkloadSample,
    build_workload_profile,
    project_all_backends,
    recommend_backend,
)
from aethersparse.autonomy.systems import (
    AnswerDisposition,
    KnowledgeFact,
    MatchedCorpus,
    MatchedQuestion,
    QueryFrame,
    QuestionKind,
    build_matched_systems,
)


def _measured_result() -> tuple[MatchedCorpus, object]:
    fact = KnowledgeFact(
        fact_id="f1",
        subject_id="nova",
        relation_id="capital",
        object_value="Lumen",
        evidence_span_id="span_f1",
        evidence_text="Nova's capital is Lumen.",
        source_doc_id="doc1",
        source_family="family1",
        lineage_id="lineage1",
        aliases=("Nova",),
    )
    corpus = MatchedCorpus(
        corpus_id="digital_twin_fixture",
        facts=(fact,),
        domain_relations=("capital",),
        index_bytes=4096,
    )
    question = MatchedQuestion(
        question_id="q1",
        text="What is Nova's capital?",
        frame=QueryFrame(
            subject_surface="Nova",
            relation_id="capital",
            kind=QuestionKind.DIRECT_FACT,
        ),
        expected_disposition=AnswerDisposition.ANSWER,
        expected_value="Lumen",
    )
    return corpus, build_matched_systems(corpus)[1].execute(question)


def _profile(
    *,
    symbolic_ops: int,
    neural_macs: int,
    peak_ram: int = 4096,
    index_bytes: int = 4096,
    frozen: bool = False,
) -> WorkloadProfile:
    total = symbolic_ops + neural_macs
    sample = WorkloadSample(
        symbolic_ops=symbolic_ops,
        neural_macs=neural_macs,
        model_bytes=4096 if neural_macs else 0,
        index_bytes=index_bytes,
        peak_live_ram_bytes=peak_ram,
        storage_bytes=4096,
        storage_reads=2,
        sequential_reads=0,
        random_reads=2,
        scheduler_cycles=4,
        realization_ops=64,
        interface_bytes=512,
        deterministic_ops=total,
        total_ops=total,
    )
    return WorkloadProfile(
        samples=(sample,),
        corpus_bytes=1_000_000,
        architecture_frozen=frozen,
        p50_storage_bytes=4096,
        p95_storage_bytes=4096,
        p50_storage_reads=2,
        p95_storage_reads=2,
        peak_live_ram_bytes=peak_ram,
        model_bytes=sample.model_bytes,
        index_bytes=index_bytes,
        symbolic_ops=symbolic_ops,
        neural_macs=neural_macs,
        scheduler_cycles=4,
        realization_ops=64,
        interface_bytes=512,
        deterministic_share=1.0,
        symbolic_control_share=symbolic_ops / max(1, total),
    )


def test_profile_is_derived_from_structured_runtime_operations() -> None:
    corpus, raw_result = _measured_result()
    result = raw_result
    profile = build_workload_profile(
        (result,),  # type: ignore[arg-type]
        corpus_bytes=corpus.serialized_bytes,
        architecture_frozen=False,
    )

    assert profile.samples
    assert profile.symbolic_ops > 0
    assert profile.neural_macs == 0
    assert profile.p95_storage_reads > 0
    assert profile.p95_storage_bytes > 0
    assert profile.interface_bytes > 0


def test_projections_are_explicitly_unmeasured_and_conservative() -> None:
    projections = project_all_backends(
        _profile(symbolic_ops=100_000, neural_macs=10_000),
        latency_target_ms=1000.0,
    )

    assert {projection.backend_id for projection in projections} == set(BackendId)
    assert all(
        projection.evidence_class == "analytical_estimate_not_measured"
        for projection in projections
    )
    assert all(projection.p95_latency_ms >= projection.p50_latency_ms for projection in projections)
    assert all(
        any("uncertainty" in assumption for assumption in projection.assumptions)
        for projection in projections
    )


def test_accuracy_or_bounded_read_failure_prevents_purchase() -> None:
    recommendation = recommend_backend(
        _profile(symbolic_ops=1000, neural_macs=0),
        latency_target_ms=1000.0,
        accuracy_targets_met=False,
        bounded_reads_demonstrated=True,
    )

    assert recommendation.decision is RecommendationDecision.ARCHITECTURE_FAILED
    assert recommendation.winner is None
    assert "ACCURACY_TARGETS_NOT_MET" in recommendation.reason_codes


def test_p4_requires_symbolic_dominance_memory_latency_and_no_npu_gain() -> None:
    recommendation = recommend_backend(
        _profile(symbolic_ops=100_000, neural_macs=0),
        latency_target_ms=1000.0,
        accuracy_targets_met=True,
        bounded_reads_demonstrated=True,
    )

    assert recommendation.decision is RecommendationDecision.ESP32_P4_PICO
    assert recommendation.winner is not None
    assert recommendation.winner.backend_id is BackendId.ESP32_P4_PICO


def test_core1106_requires_validated_mapping_and_both_improvements() -> None:
    workload = _profile(symbolic_ops=1000, neural_macs=100_000_000)
    without_mapping = recommend_backend(
        workload,
        latency_target_ms=10_000.0,
        accuracy_targets_met=True,
        bounded_reads_demonstrated=True,
        neural_mapping_validated=False,
    )
    with_mapping = recommend_backend(
        workload,
        latency_target_ms=10_000.0,
        accuracy_targets_met=True,
        bounded_reads_demonstrated=True,
        neural_mapping_validated=True,
    )

    assert without_mapping.decision is not RecommendationDecision.CORE1106
    assert with_mapping.decision is RecommendationDecision.CORE1106
    assert "RKNN_MAPPING_OVER_90_PERCENT_VALIDATED" in with_mapping.reason_codes


def test_fpga_requires_frozen_repeatable_operation_set() -> None:
    unfrozen = recommend_backend(
        _profile(symbolic_ops=1_000_000_000, neural_macs=0, frozen=False),
        latency_target_ms=30_000.0,
        accuracy_targets_met=True,
        bounded_reads_demonstrated=True,
    )
    frozen = recommend_backend(
        _profile(symbolic_ops=1_000_000_000, neural_macs=0, frozen=True),
        latency_target_ms=30_000.0,
        accuracy_targets_met=True,
        bounded_reads_demonstrated=True,
    )

    assert unfrozen.decision is RecommendationDecision.NO_PURCHASE
    assert frozen.decision is RecommendationDecision.LOW_POWER_FPGA


def test_no_purchase_is_valid_when_no_backend_condition_is_met() -> None:
    recommendation = recommend_backend(
        _profile(symbolic_ops=1_000_000_000, neural_macs=0, frozen=False),
        latency_target_ms=1.0,
        accuracy_targets_met=True,
        bounded_reads_demonstrated=True,
    )

    assert recommendation.decision is RecommendationDecision.NO_PURCHASE
    assert recommendation.winner is None
