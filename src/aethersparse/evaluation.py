"""Small public evaluation harness and falsification-oriented baseline comparison."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any, Literal

from aethersparse.ir import estimates_as_json
from aethersparse.models import Disposition, QueryRequest
from aethersparse.runtime import AetherSparseRuntime

ROOT = Path(__file__).resolve().parents[2]
EVAL_FILE = ROOT / "data" / "eval_public" / "questions.json"


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    passed: bool
    disposition_ok: bool
    content_ok: bool
    is_answer: bool
    grounded: bool
    latency_us: int
    operation_count: int


def _case_pass(
    sentence: str | None,
    expected_contains: list[str],
    forbidden_contains: list[str],
) -> bool:
    actual = (sentence or "").casefold()
    return all(item.casefold() in actual for item in expected_contains) and not any(
        item.casefold() in actual for item in forbidden_contains
    )


def evaluate_strategy(
    runtime: AetherSparseRuntime,
    cases: list[dict[str, Any]],
    strategy: Literal["top1_template", "compiled_program"],
) -> dict[str, Any]:
    results: list[CaseResult] = []
    total_bytes = runtime.store.pack.manifest.logical_query_pack_bytes
    query_bytes: list[int] = []
    representative_cost = None

    for case in cases:
        response = runtime.query(
            QueryRequest(
                request_id=f"eval:{strategy}:{case['case_id']}",
                session_id="eval-public",
                text=case["question"],
                trace=True,
            ),
            strategy=strategy,
        )
        expected_disposition = Disposition(case["expected_disposition"])
        disposition_ok = response.disposition is expected_disposition
        content_ok = _case_pass(
            response.sentence or response.reason,
            case.get("expected_contains", []),
            case.get("forbidden_contains", []),
        )
        grounded = (
            response.disposition is not Disposition.ANSWER
            or bool(response.bindings and response.citations)
        )
        results.append(
            CaseResult(
                case_id=case["case_id"],
                passed=disposition_ok and content_ok and grounded,
                disposition_ok=disposition_ok,
                content_ok=content_ok,
                is_answer=response.disposition is Disposition.ANSWER,
                grounded=grounded,
                latency_us=response.cost.measured_host_latency_us,
                operation_count=response.cost.operation_count,
            )
        )
        query_bytes.append(response.cost.bytes_read)
        if response.disposition is Disposition.ANSWER and (
            representative_cost is None
            or response.cost.bytes_read > representative_cost.bytes_read
        ):
            representative_cost = response.cost

    count = len(results)
    answered = [item for item in results if item.is_answer]
    grounded_answers = [item for item in answered if item.grounded]
    report: dict[str, Any] = {
        "strategy": strategy,
        "case_count": count,
        "passed": sum(item.passed for item in results),
        "accuracy": sum(item.passed for item in results) / count if count else 0.0,
        "disposition_accuracy": (
            sum(item.disposition_ok for item in results) / count if count else 0.0
        ),
        "answer_count": len(answered),
        "grounded_answer_rate": (
            len(grounded_answers) / len(answered) if answered else 1.0
        ),
        "unsupported_answer_rate": (
            sum(not item.grounded for item in results) / count if count else 0.0
        ),
        "median_measured_host_pipeline_us": median(
            [item.latency_us for item in results]
        )
        if results
        else 0,
        "median_operation_count": median(
            [item.operation_count for item in results]
        )
        if results
        else 0,
        "max_query_bytes_read": max(query_bytes, default=0),
        "max_fraction_of_logical_pack_read": (
            max(query_bytes, default=0) / total_bytes if total_bytes else 0.0
        ),
        "failures": [item.case_id for item in results if not item.passed],
    }
    if strategy == "compiled_program" and representative_cost is not None:
        report["hardware_estimates_for_representative_answer"] = estimates_as_json(
            representative_cost
        )
    return report


def run_evaluation(eval_file: Path = EVAL_FILE) -> dict[str, Any]:
    cases = json.loads(eval_file.read_text(encoding="utf-8"))["cases"]
    runtime = AetherSparseRuntime()
    baselines = [
        evaluate_strategy(runtime, cases, "top1_template"),
        evaluate_strategy(runtime, cases, "compiled_program"),
    ]
    return {
        "evaluation_set": "apollo_smoke_public_v0.1",
        "scope_warning": (
            "Tiny hand-authored vertical-slice smoke evaluation; not Gate 0 or parser gate."
        ),
        "pack_manifest": runtime.store.pack.manifest.model_dump(mode="json"),
        "baselines": baselines,
    }
