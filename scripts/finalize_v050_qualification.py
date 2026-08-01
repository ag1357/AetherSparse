#!/usr/bin/env python3
# ruff: noqa: E501
"""Integrate matched 10k/50k evidence into the frozen v0.5 final decision."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from aethersparse.controller.evaluation import (
    AblationSystem,
    EvaluationOutcome,
    FrozenBenchmark,
    NaturalQueryCase,
    Partition,
)
from aethersparse.controller.models import ControllerDisposition
from aethersparse.v050.gates import (
    ArchitectureDecision,
    HardwareDecision,
    MetricSnapshot,
    evaluate_gates,
    select_architecture,
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--report-10k", type=Path, required=True)
    parser.add_argument("--outcomes-10k", type=Path, required=True)
    parser.add_argument("--report-50k", type=Path, required=True)
    parser.add_argument("--outcomes-50k", type=Path, required=True)
    parser.add_argument("--hard-negative", type=Path, required=True)
    parser.add_argument("--edge", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    return parser.parse_args()


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _normalized(value: str | None) -> str:
    return " ".join((value or "").casefold().split())


def _exact(case: NaturalQueryCase, outcome: EvaluationOutcome) -> bool:
    if outcome.disposition is not case.accepted_disposition:
        return False
    if case.accepted_disposition is not ControllerDisposition.ANSWER:
        return True
    answer = _normalized(outcome.answer_text)
    return (
        any(_normalized(value) == answer for value in case.accepted_answers)
        and outcome.unsupported_surface_count == 0
    )


def _partition_metrics(
    benchmark: FrozenBenchmark,
    outcomes: tuple[EvaluationOutcome, ...],
    *,
    system: AblationSystem,
    partition: Partition,
) -> dict[str, float | int]:
    cases = {
        case.case_id: case for case in benchmark.cases if case.partition is partition
    }
    rows = [row for row in outcomes if row.system is system and row.case_id in cases]
    answer_rows = [
        row
        for row in rows
        if cases[row.case_id].accepted_disposition is ControllerDisposition.ANSWER
    ]
    exact = sum(_exact(cases[row.case_id], row) for row in answer_rows)
    article = sum(
        bool(
            {span.document_id for span in cases[row.case_id].gold_evidence}
            & set(row.retrieved_document_ids)
        )
        for row in answer_rows
    )
    evidence = sum(
        bool(
            {span.span_id for span in cases[row.case_id].gold_evidence}
            & set(row.retrieved_span_ids)
        )
        for row in answer_rows
    )
    emitted = [row for row in rows if row.disposition is ControllerDisposition.ANSWER]
    wrong_entity = sum(
        bool(cases[row.case_id].required_entity_ids)
        and not set(cases[row.case_id].required_entity_ids).issubset(row.linked_entity_ids)
        for row in emitted
    )
    factual = sum(row.factual_surface_count for row in rows)
    unsupported = sum(row.unsupported_surface_count for row in rows)
    disposition_correct = sum(
        row.disposition is cases[row.case_id].accepted_disposition for row in rows
    )
    denominator = len(answer_rows)
    return {
        "case_count": len(rows),
        "answerable_case_count": denominator,
        "exact_supported_answer_accuracy": exact / denominator if denominator else 0.0,
        "article_recall_at_8": article / denominator if denominator else 0.0,
        "evidence_recall_at_8": evidence / denominator if denominator else 0.0,
        "silent_wrong_entity_rate": wrong_entity / len(emitted) if emitted else 0.0,
        "unsupported_claim_rate": unsupported / factual if factual else 0.0,
        "disposition_accuracy": disposition_correct / len(rows) if rows else 0.0,
    }


def _failure_taxonomy(
    benchmark: FrozenBenchmark,
    outcomes: tuple[EvaluationOutcome, ...],
    *,
    system: AblationSystem,
) -> dict[str, int]:
    cases = {case.case_id: case for case in benchmark.cases}
    failures: Counter[str] = Counter()
    for row in outcomes:
        if row.system is not system:
            continue
        case = cases[row.case_id]
        if _exact(case, row):
            continue
        if case.accepted_disposition is not ControllerDisposition.ANSWER:
            failures[f"disposition:{case.accepted_disposition.value}->{row.disposition.value}"] += 1
            continue
        gold_documents = {span.document_id for span in case.gold_evidence}
        gold_spans = {span.span_id for span in case.gold_evidence}
        if not gold_documents.intersection(row.retrieved_document_ids):
            failures["answerable:article_miss"] += 1
        elif not gold_spans.intersection(row.retrieved_span_ids):
            failures["answerable:evidence_span_miss"] += 1
        elif row.disposition is ControllerDisposition.VERIFICATION_FAILURE:
            failures["answerable:verification_withheld"] += 1
        elif row.disposition is not ControllerDisposition.ANSWER:
            failures[f"answerable:withheld_{row.disposition.value}"] += 1
        elif case.required_entity_ids and not set(case.required_entity_ids).issubset(
            row.linked_entity_ids
        ):
            failures["answerable:silent_wrong_entity"] += 1
        else:
            failures["answerable:exact_span_selection_mismatch"] += 1
    return dict(sorted(failures.items()))


def _as_float(metrics: dict[str, Any], name: str) -> float:
    return float(metrics[name])


def _percent(value: int | float) -> str:
    return f"{100 * float(value):.2f}%"


def main() -> int:
    args = _args()
    benchmark = FrozenBenchmark.model_validate_json(args.benchmark.read_text(encoding="utf-8"))
    report10 = _load(args.report_10k)
    report50 = _load(args.report_50k)
    if not report10["qualification_complete"] or not report50["qualification_complete"]:
        raise SystemExit("both progressive qualifications must be complete")
    expected_qualification_id = "AETHERSPARSE_V050_SQLITE_CONTROLLER_QUALIFICATION_R2"
    for report in (report10, report50):
        if report.get("qualification_id") != expected_qualification_id:
            raise SystemExit("qualification did not use the corrected R2 evaluator")
        context_note = str(report.get("measurement_notes", {}).get("conversational_context", ""))
        if "invariant" not in context_note or "serialization order" not in context_note:
            raise SystemExit("qualification lacks order-invariant conversational replay evidence")
    expected_hash = benchmark.content_sha256
    for report in (report10, report50):
        if report["benchmark"]["content_sha256"] != expected_hash:
            raise SystemExit("qualification benchmark hash mismatch")
        if not report["pack"]["pack_sha256_verified"]:
            raise SystemExit("qualification pack hash was not verified")
    outcomes10 = tuple(
        EvaluationOutcome.model_validate(row) for row in _load(args.outcomes_10k)
    )
    outcomes50 = tuple(
        EvaluationOutcome.model_validate(row) for row in _load(args.outcomes_50k)
    )
    expected_rows = len(benchmark.cases) * len(AblationSystem)
    if len(outcomes10) != expected_rows or len(outcomes50) != expected_rows:
        raise SystemExit("outcome matrix is incomplete")

    system = AblationSystem.FULL_EXTRACTIVE_CONTROLLER
    systems10 = report10["ablation"]["systems"]
    systems50 = report50["ablation"]["systems"]
    full10 = systems10[system.value]
    full50 = systems50[system.value]
    stability_deltas = {
        name: _as_float(full50, name) - _as_float(full10, name)
        for name in (
            "article_recall_at_8",
            "evidence_recall_at_8",
            "exact_supported_answer_accuracy",
        )
    }
    stable = min(stability_deltas.values()) >= -0.03
    edge = _load(args.edge)
    board_measurements = bool(edge.get("board_measurements_present", False))
    operation_counter_instrumented = bool(
        edge.get("operation_counter_instrumented", False)
    )
    credible_edge = board_measurements and operation_counter_instrumented
    metrics = MetricSnapshot(
        article_recall_at_8=_as_float(full50, "article_recall_at_8"),
        evidence_recall_at_8=_as_float(full50, "evidence_recall_at_8"),
        exact_answerable_accuracy=_as_float(full50, "exact_supported_answer_accuracy"),
        unsupported_claim_rate=_as_float(full50, "unsupported_claim_rate"),
        entity_link_accuracy=_as_float(full50, "entity_accuracy"),
        silent_wrong_entity_rate=_as_float(full50, "silent_wrong_entity_rate"),
        answer_shape_accuracy=_as_float(full50, "answer_shape_accuracy"),
        required_facet_accuracy=_as_float(full50, "required_facet_accuracy"),
        unknown_copy_fidelity=_as_float(full50, "unknown_copy_fidelity"),
        multi_source_accuracy=_as_float(full50, "multi_source_accuracy"),
        comparison_accuracy=_as_float(full50, "comparison_accuracy"),
        followup_coreference_accuracy=_as_float(full50, "follow_up_coreference_accuracy"),
        clarification_precision=_as_float(full50, "clarification_precision"),
        abstention_precision=_as_float(full50, "abstention_precision"),
        exact_binding_reproducible=(
            report10["pack"]["pack_sha256_verified"]
            and report50["pack"]["pack_sha256_verified"]
            and _as_float(full50, "unsupported_claim_rate") == 0.0
        ),
        stable_10k_to_50k=stable,
        credible_edge_backend=credible_edge,
        verified_rag_exact_accuracy=None,
    )
    gates = evaluate_gates(metrics)
    architecture = select_architecture(metrics)
    hardware = (
        HardwareDecision(edge["hardware_outcome"]["decision"])
        if architecture
        in {ArchitectureDecision.EDGE_AI, ArchitectureDecision.HYBRID}
        else HardwareDecision.NO_PURCHASE
    )
    hard_negative = _load(args.hard_negative)
    final_held = _partition_metrics(
        benchmark,
        outcomes50,
        system=system,
        partition=Partition.FINAL_HELD,
    )
    ablation_summary = {
        name: {
            key: value
            for key, value in metrics_by_system.items()
            if key
            in {
                "article_recall_at_8",
                "evidence_recall_at_8",
                "entity_accuracy",
                "answer_shape_accuracy",
                "required_facet_accuracy",
                "exact_supported_answer_accuracy",
                "unsupported_claim_rate",
                "silent_wrong_entity_rate",
                "comparison_accuracy",
                "multi_source_accuracy",
                "follow_up_coreference_accuracy",
                "clarification_precision",
                "abstention_precision",
                "unknown_copy_fidelity",
                "mean_bytes_read",
                "mean_blocks_read",
                "p50_latency_ms",
                "p95_latency_ms",
                "peak_ram_bytes",
                "model_bytes",
                "mean_macs",
            }
        }
        for name, metrics_by_system in systems50.items()
    }
    baseline10 = systems10[AblationSystem.DETERMINISTIC_FEATURE_FUSION.value]
    baseline_recovered = (
        _as_float(baseline10, "article_recall_at_8") >= 0.84
        and _as_float(baseline10, "evidence_recall_at_8") >= 0.79
        and _as_float(baseline10, "exact_supported_answer_accuracy") >= 0.49
        and _as_float(baseline10, "unsupported_claim_rate") == 0.0
    )
    payload = {
        "qualification_id": "AETHERSPARSE_V050_FINAL_QUALIFICATION_R1",
        "benchmark_identity": benchmark.benchmark_identity,
        "benchmark_content_sha256": benchmark.content_sha256,
        "lead_metrics": {
            "natural_query_exact_supported_answer_accuracy_50k": metrics.exact_answerable_accuracy,
            "final_held_exact_supported_answer_accuracy_50k": final_held[
                "exact_supported_answer_accuracy"
            ],
            "silent_wrong_entity_rate_50k": metrics.silent_wrong_entity_rate,
            "unsupported_claim_rate_50k": metrics.unsupported_claim_rate,
            "multi_source_accuracy_50k": metrics.multi_source_accuracy,
            "followup_coreference_accuracy_50k": metrics.followup_coreference_accuracy,
        },
        "scaling": {
            "10k": full10,
            "50k": full50,
            "50k_minus_10k": stability_deltas,
            "stable_with_three_point_degradation_bound": stable,
        },
        "final_held": final_held,
        "gates": gates.model_dump(mode="json"),
        "metric_snapshot": metrics.model_dump(mode="json"),
        "architecture_decision": architecture.value,
        "hardware_decision": hardware.value,
        "ablation_50k": ablation_summary,
        "retained_baseline_reconstruction_10k": {
            "metrics": baseline10,
            "gate_recovered": baseline_recovered,
            "historical_absolute_results_mixed": False,
            "note": (
                "The v0.5 benchmark/corpus are a new series; historical v0.4.1 values are "
                "comparison targets only and are never pooled with these results."
            ),
        },
        "failure_taxonomy_50k": _failure_taxonomy(
            benchmark, outcomes50, system=system
        ),
        "hard_negative_ablation": hard_negative,
        "adversarial_verifier_10k": report10["adversarial_verifier"],
        "adversarial_verifier_50k": report50["adversarial_verifier"],
        "edge_profile": edge,
        "artifact_hashes": {
            str(path): _sha256(path)
            for path in (
                args.benchmark,
                args.report_10k,
                args.outcomes_10k,
                args.report_50k,
                args.outcomes_50k,
                args.hard_negative,
                args.edge,
            )
        },
        "limitations": [
            "The verified-RAG comparator was not configured and failed closed; no simulated score is reported.",
            "The frozen benchmark contains exact-source but sometimes under-specified selected-passage date, quantity, and quotation questions; failures remain counted.",
            "The ablation runner changes retrieval/frame inputs while retaining a common exact planning and verification path, so equal downstream scores do not isolate every internal module.",
            "Host cold-cache advice is not an edge-board measurement, and deterministic CPU operations were not instrumented; hardware purchase therefore remains fail-closed.",
        ],
    }
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(serialized, encoding="utf-8")

    markdown = f"""# AetherSparse v0.5.0 final qualification

Natural real-source exact supported-answer accuracy was **{_percent(metrics.exact_answerable_accuracy)}** at 50k; the frozen final-held partition was **{_percent(final_held['exact_supported_answer_accuracy'])}**. Silent wrong-entity answers were **{_percent(metrics.silent_wrong_entity_rate)}** and unsupported claims were **{_percent(metrics.unsupported_claim_rate)}**.

Multi-source exact accuracy was **{_percent(metrics.multi_source_accuracy)}** and follow-up/coreference exact accuracy was **{_percent(metrics.followup_coreference_accuracy)}**. Article/evidence recall@8 changed from **{_percent(full10['article_recall_at_8'])}/{_percent(full10['evidence_recall_at_8'])}** at 10k to **{_percent(full50['article_recall_at_8'])}/{_percent(full50['evidence_recall_at_8'])}** at 50k.

Architecture decision: `{architecture.value}`

Hardware decision: `{hardware.value}`

## Gate result

- Retained baseline: `{gates.retained_baseline}`
- Entity and query cognition: `{gates.entity_and_query}`
- Cognitive answering: `{gates.cognitive_answering}`
- Full qualification: `{gates.full_qualification}`
- Failed checks: `{', '.join(gates.failures) or 'none'}`

## Qualification boundaries

- Corpus: official Simple English Wikipedia 2026-07-01, checksum-pinned independent 10k and reproducible 50k packs.
- Benchmark: `{benchmark.benchmark_identity}`, {len(benchmark.cases):,} cases, isolated author/adjudicator/evaluator/auditor roles.
- Primary system: `{system.value}` with deterministic realization and exact fail-closed verification.
- Verified-RAG: not configured; comparator abstained rather than receiving a simulated result.
- Edge: host workload measurements and analytical projections only; no board result and no purchase inference from advertised TOPS.

## Measured limitations

""" + "".join(f"- {item}\n" for item in payload["limitations"])
    args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output_markdown.write_text(markdown, encoding="utf-8")
    print(f"json={args.output_json}")
    print(f"json_sha256={hashlib.sha256(serialized.encode()).hexdigest()}")
    print(f"markdown={args.output_markdown}")
    print(f"markdown_sha256={hashlib.sha256(markdown.encode()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
