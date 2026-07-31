"""Fail-closed sealed-query qualification for the deterministic runtime."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from aethersparse.gate0.metrics import read_gold_partitions
from aethersparse.gate0.models import (
    GoldPartition,
    QueryReviewStatus,
    SealedQuerySet,
)
from aethersparse.gate0.sources import stable_json
from aethersparse.models import Disposition, QueryRequest
from aethersparse.runtime import AetherSparseRuntime


def read_query_set(path: Path) -> SealedQuerySet:
    return SealedQuerySet.model_validate_json(path.read_text(encoding="utf-8"))


def _evaluate_strategy(
    query_set: SealedQuerySet,
    strategy: Literal["top1_template", "compiled_program"],
) -> dict[str, Any]:
    runtime = AetherSparseRuntime()
    results: list[dict[str, Any]] = []
    for case in query_set.cases:
        response = runtime.query(
            QueryRequest(
                request_id=f"gate0:{strategy}:{case.case_id}",
                session_id="gate0-sealed",
                text=case.question,
            ),
            strategy=strategy,
        )
        rendered = response.sentence or response.reason or ""
        content_ok = all(
            expected.casefold() in rendered.casefold() for expected in case.expected_contains
        ) and not any(
            forbidden.casefold() in rendered.casefold() for forbidden in case.forbidden_contains
        )
        cited_sources = {citation.source_doc_id for citation in response.citations}
        evidence_ok = response.disposition is not Disposition.ANSWER or set(
            case.evidence_source_ids
        ).issubset(cited_sources)
        passed = response.disposition is case.expected_disposition and content_ok and evidence_ok
        results.append(
            {
                "case_id": case.case_id,
                "passed": passed,
                "hard_subset": case.hard_subset,
                "categories": list(case.categories),
                "disposition": response.disposition,
                "content_ok": content_ok,
                "evidence_ok": evidence_ok,
                "pack_manifest_hash": response.pack_manifest_hash,
            }
        )
    hard = [result for result in results if result["hard_subset"]]
    return {
        "strategy": strategy,
        "case_count": len(results),
        "passed": sum(bool(result["passed"]) for result in results),
        "accuracy": (
            sum(bool(result["passed"]) for result in results) / len(results) if results else 0.0
        ),
        "hard_subset_count": len(hard),
        "hard_subset_passed": sum(bool(result["passed"]) for result in hard),
        "hard_subset_accuracy": (
            sum(bool(result["passed"]) for result in hard) / len(hard) if hard else 0.0
        ),
        "failures": [result["case_id"] for result in results if not bool(result["passed"])],
        "results": results,
    }


def evaluate_sealed_queries(
    *,
    query_path: Path,
    freeze_lock_path: Path,
    gold_root: Path,
) -> dict[str, Any]:
    """Run only after rules, gold, and query review prerequisites are satisfied."""

    query_set = read_query_set(query_path)
    gold = read_gold_partitions(gold_root)
    sealed_gold = tuple(record for record in gold if record.partition is GoldPartition.SEALED_GATE0)
    freeze_lock = (
        json.loads(freeze_lock_path.read_text(encoding="utf-8"))
        if freeze_lock_path.exists()
        else {}
    )
    blockers: list[str] = []
    if freeze_lock.get("sealed_evaluation_permitted") is not True:
        blockers.append("extractor/validator lock does not permit sealed evaluation")
    if len(sealed_gold) != 150:
        blockers.append(f"sealed human-reviewed packet count is {len(sealed_gold)}, not 150")
    pending = sum(
        case.review_status is not QueryReviewStatus.HUMAN_REVIEWED for case in query_set.cases
    )
    if pending:
        blockers.append(f"{pending} query cases lack human review")
    gold_candidate_ids = {record.candidate_id for record in sealed_gold}
    missing_evidence = [
        case.case_id
        for case in query_set.cases
        if case.expected_disposition is Disposition.ANSWER
        and (
            not case.evidence_candidate_ids
            or not set(case.evidence_candidate_ids).issubset(gold_candidate_ids)
        )
    ]
    if missing_evidence:
        blockers.append(
            f"{len(missing_evidence)} answer cases lack reviewed sealed evidence bindings"
        )
    conflicting_cases = [
        case for case in query_set.cases if "conflicting_source" in case.categories
    ]
    incomplete_conflicts = [
        case.case_id
        for case in conflicting_cases
        if len(case.evidence_candidate_ids) < 2
        or len(case.evidence_source_ids) < 2
    ]
    if incomplete_conflicts:
        blockers.append(
            f"{len(incomplete_conflicts)} conflicting-source cases lack two "
            "independently reviewed evidence bindings"
        )

    query_set_hash = (
        f"sha256:{hashlib.sha256(stable_json(query_set.model_dump(mode='json'))).hexdigest()}"
    )
    report: dict[str, Any] = {
        "query_set_id": query_set.query_set_id,
        "query_set_hash": query_set_hash,
        "case_count": len(query_set.cases),
        "human_reviewed_case_count": len(query_set.cases) - pending,
        "status": "BLOCKED" if blockers else "READY",
        "blockers": blockers,
        "top1_matched_baseline": None,
        "compiled_program": None,
        "evidence_arbitration": {
            "status": "DISABLED",
            "reason": ("May be enabled only after it beats top-1 on the designated hard subset."),
        },
    }
    if blockers:
        return report

    top1 = _evaluate_strategy(query_set, "top1_template")
    compiled = _evaluate_strategy(query_set, "compiled_program")
    report["top1_matched_baseline"] = top1
    report["compiled_program"] = compiled
    report["status"] = "PASS" if compiled["accuracy"] == 1.0 else "FAIL"
    report["evidence_arbitration"]["eligible"] = (
        compiled["hard_subset_accuracy"] > top1["hard_subset_accuracy"]
    )
    return report


def write_query_report(
    report: dict[str, Any],
    json_path: Path,
    markdown_path: Path,
) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Gate 0 sealed-query evaluation",
        "",
        f"**Status:** {report['status']}",
        "",
        f"- Candidate questions: {report['case_count']}",
        f"- Human-reviewed questions: {report['human_reviewed_case_count']}",
        "- Top-1 retrieval: retained as the matched baseline",
        "- Evidence arbitration: disabled",
    ]
    if report["blockers"]:
        lines.extend(["", "## BLOCKED", ""])
        lines.extend(f"- {blocker}" for blocker in report["blockers"])
    else:
        lines.extend(
            [
                "",
                "## Results",
                "",
                f"- Top-1 accuracy: {report['top1_matched_baseline']['accuracy']:.3f}",
                f"- Compiled-program accuracy: {report['compiled_program']['accuracy']:.3f}",
            ]
        )
    lines.extend(
        [
            "",
            "The sealed set is never executed while any freeze, review, partition, "
            "or evidence-binding prerequisite is incomplete.",
            "",
        ]
    )
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
