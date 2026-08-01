from __future__ import annotations

import hashlib

import pytest

from aethersparse.controller.evaluation import (
    AblationSystem,
    EvaluationOutcome,
    GoldEvidence,
    NaturalQueryCase,
    Partition,
    RoleIdentity,
    audit_benchmark,
    evaluate_ablation,
    freeze_benchmark,
)
from aethersparse.controller.models import (
    AnswerShape,
    ControllerDisposition,
    RequiredFacet,
)


def _role(identity: str, role: str) -> RoleIdentity:
    return RoleIdentity(identity=identity, role=role, process_identity=f"process:{identity}")


def _fixture() -> tuple[object, dict[str, str]]:
    source = "Ada Lovelace was born in 1815."
    answer = "1815"
    start = source.index(answer)
    evidence = GoldEvidence(
        span_id="span:birth",
        document_id="doc:ada",
        document_hash=hashlib.sha256(source.encode()).hexdigest(),
        source_revision="1",
        source_url="https://example.test/ada",
        char_start=start,
        char_end=start + len(answer),
        exact_text=answer,
        exact_text_hash=hashlib.sha256(answer.encode()).hexdigest(),
    )
    case = NaturalQueryCase(
        case_id="q1",
        partition=Partition.EVALUATION,
        question="When was Ada Lovelace born?",
        categories=("direct_fact", "date"),
        author_identity="author-a",
        adjudicator_identity="adjudicator",
        accepted_disposition=ControllerDisposition.ANSWER,
        accepted_answers=(answer,),
        required_entity_ids=("entity:ada",),
        required_answer_shape=AnswerShape.DATE,
        required_facets=(RequiredFacet.SUBJECT, RequiredFacet.RELATION, RequiredFacet.TIME),
        gold_claim_ids=("claim:birth",),
        gold_evidence=(evidence,),
    )
    benchmark = freeze_benchmark(
        (case,),
        author_roles=(_role("author-a", "author"),),
        adjudicator_role=_role("adjudicator", "adjudicator"),
        evaluator_role=_role("evaluator", "evaluator"),
        auditor_role=_role("auditor", "auditor"),
        require_full=False,
    )
    return benchmark, {"doc:ada": source}


def test_benchmark_freeze_and_independent_provenance_audit() -> None:
    benchmark, sources = _fixture()
    report = audit_benchmark(benchmark, sources, require_full=False)  # type: ignore[arg-type]
    assert report.passed
    assert report.evidence_span_count == 1
    assert report.tuning_evaluation_article_overlap == ()


def test_role_collision_and_too_small_full_set_are_rejected() -> None:
    benchmark, _ = _fixture()
    with pytest.raises(ValueError, match="2,000"):
        freeze_benchmark(
            benchmark.cases,  # type: ignore[union-attr]
            author_roles=benchmark.author_roles,  # type: ignore[union-attr]
            adjudicator_role=benchmark.adjudicator_role,  # type: ignore[union-attr]
            evaluator_role=benchmark.evaluator_role,  # type: ignore[union-attr]
            auditor_role=benchmark.auditor_role,  # type: ignore[union-attr]
        )


def test_ablation_metrics_include_accuracy_safety_and_workload() -> None:
    benchmark, _ = _fixture()
    outcome = EvaluationOutcome(
        case_id="q1",
        system=AblationSystem.FULL_EXTRACTIVE_CONTROLLER,
        disposition=ControllerDisposition.ANSWER,
        answer_text="1815",
        retrieved_document_ids=("doc:ada",),
        retrieved_span_ids=("span:birth",),
        linked_entity_ids=("entity:ada",),
        unknown_input_spans=("Qorvax-7",),
        copied_unknown_spans=("Qorvax-7",),
        answer_shape=AnswerShape.DATE,
        predicted_facets=(RequiredFacet.SUBJECT, RequiredFacet.RELATION, RequiredFacet.TIME),
        factual_surface_count=1,
        unsupported_surface_count=0,
        bytes_read=512,
        blocks_read=2,
        latency_ms=3.0,
        peak_ram_bytes=2048,
        model_bytes=0,
        macs=0,
    )
    report = evaluate_ablation(
        benchmark,
        (outcome,),
        require_complete=False,  # type: ignore[arg-type]
    )
    metrics = report["systems"][AblationSystem.FULL_EXTRACTIVE_CONTROLLER.value]  # type: ignore[index]
    assert metrics["exact_supported_answer_accuracy"] == 1.0
    assert metrics["unsupported_claim_rate"] == 0.0
    assert metrics["silent_wrong_entity_rate"] == 0.0
    assert metrics["unknown_copy_fidelity"] == 1.0
    assert metrics["mean_bytes_read"] == 512


def test_full_ablation_rejects_missing_system_rows() -> None:
    benchmark, _ = _fixture()
    with pytest.raises(ValueError, match="incomplete ablation"):
        evaluate_ablation(benchmark, ())  # type: ignore[arg-type]
