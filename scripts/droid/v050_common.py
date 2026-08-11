"""Shared helpers for the droid retrieval-accuracy mission (v06).

Mission harness: evaluate the EvidenceSelector against the frozen
INDEPENDENT_NATURAL_QUERY_SET_V050_R1 benchmark without modifying it.

Gold document IDs have the form ``simplewiki:{pageid}:{revid}`` while the
selector pack uses ``mw:{pageid}:{revid}:{hash}``.  Matching is at the pageid
component only (mission Phase 0 mitigation); exact IDs never match across
pack schemas and byte-offset spans need not align across pack builders.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import fmean
from typing import Any

from aethersparse.controller.evaluation import FrozenBenchmark, freeze_benchmark
from aethersparse.controller.models import ControllerDisposition

REPO_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_PATH = REPO_ROOT / "data/v050/benchmark/INDEPENDENT_NATURAL_QUERY_SET_V050_R1.json"

FIT_PARTITIONS = ("tuning", "development")
HELD_OUT_PARTITIONS = ("evaluation", "final_held")


def load_benchmark(path: Path = BENCHMARK_PATH) -> FrozenBenchmark:
    """Load and re-verify the frozen benchmark; never mutate it."""

    benchmark = FrozenBenchmark.model_validate_json(path.read_text(encoding="utf-8"))
    # Full-size files get every qualification floor; deterministic shards
    # (v09_shard_benchmark.py) are hash-verified subsets — the content hash
    # check below runs either way, so subset integrity is still enforced.
    refrozen = freeze_benchmark(
        benchmark.cases,
        author_roles=benchmark.author_roles,
        adjudicator_role=benchmark.adjudicator_role,
        evaluator_role=benchmark.evaluator_role,
        auditor_role=benchmark.auditor_role,
        require_full=len(benchmark.cases) >= 2_000,
    )
    if refrozen.content_sha256 != benchmark.content_sha256:
        raise ValueError("benchmark content hash mismatch; refusing to evaluate")
    return benchmark


def pageid(document_id: str) -> str:
    """Extract the pageid component from simplewiki:/mw: document IDs."""

    parts = document_id.split(":")
    if len(parts) >= 3 and parts[0] in {"simplewiki", "mw"}:
        return parts[1]
    return document_id


def case_gold_pageids(case: Any) -> set[str]:
    return {pageid(item.document_id) for item in case.gold_evidence}


def answer_cases(benchmark: FrozenBenchmark) -> list[Any]:
    return [
        case
        for case in benchmark.cases
        if case.accepted_disposition is ControllerDisposition.ANSWER
    ]


def conversation_order(cases: list[Any]) -> list[Any]:
    """Topologically order cases so declared parents precede their children."""

    by_id = {case.case_id: case for case in cases}
    ordered: list[Any] = []
    state: dict[str, int] = {}

    def visit(case_id: str) -> None:
        mark = state.get(case_id, 0)
        if mark == 2:
            return
        if mark == 1:
            raise ValueError(f"prior_case_ids cycle at {case_id}")
        case = by_id.get(case_id)
        if case is None:
            return
        state[case_id] = 1
        for parent_id in case.prior_case_ids:
            visit(parent_id)
        state[case_id] = 2
        ordered.append(case)

    for case in cases:
        visit(case.case_id)
    return ordered


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


class RecallAccumulator:
    """Lenient (intersection) and strict (subset) pageid recall aggregation."""

    def __init__(self) -> None:
        self._by_partition: dict[str, Counter[str]] = defaultdict(Counter)
        self._by_category: dict[str, Counter[str]] = defaultdict(Counter)
        self._overall: Counter[str] = Counter()

    def add(self, case: Any, retrieved_pageids: set[str]) -> tuple[bool, bool]:
        gold = case_gold_pageids(case)
        lenient = bool(gold & retrieved_pageids)
        strict = bool(gold) and gold <= retrieved_pageids
        partition = str(case.partition)
        for bucket in (self._overall, self._by_partition[partition]):
            bucket["n"] += 1
            bucket["lenient"] += int(lenient)
            bucket["strict"] += int(strict)
        for category in case.categories:
            bucket = self._by_category[category]
            bucket["n"] += 1
            bucket["lenient"] += int(lenient)
            bucket["strict"] += int(strict)
        return lenient, strict

    @staticmethod
    def _render(counter: Counter[str]) -> dict[str, Any]:
        n = counter["n"]
        return {
            "n": n,
            "article_recall_lenient": counter["lenient"] / n if n else 0.0,
            "article_recall_strict": counter["strict"] / n if n else 0.0,
        }

    def report(self) -> dict[str, Any]:
        return {
            "overall": self._render(self._overall),
            "by_partition": {
                key: self._render(self._by_partition[key])
                for key in sorted(self._by_partition)
            },
            "by_category": {
                key: self._render(self._by_category[key])
                for key in sorted(self._by_category)
            },
        }


def latency_summary(values: list[float]) -> dict[str, float]:
    return {
        "mean_ms": fmean(values) if values else 0.0,
        "p50_ms": percentile(values, 0.50),
        "p95_ms": percentile(values, 0.95),
    }


def write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
