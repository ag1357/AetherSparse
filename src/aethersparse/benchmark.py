"""Cold-start and sustained host-emulation benchmarks."""

from __future__ import annotations

import statistics
import time
import tracemalloc
from typing import Any

from aethersparse.models import QueryRequest
from aethersparse.runtime import AetherSparseRuntime


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * percentile_value))
    return ordered[index]


def run_benchmark(iterations: int = 500) -> dict[str, Any]:
    if iterations < 1:
        raise ValueError("iterations must be positive")

    tracemalloc.start()
    init_start = time.perf_counter_ns()
    runtime = AetherSparseRuntime()
    init_ms = (time.perf_counter_ns() - init_start) / 1_000_000

    questions = (
        "When did Apollo 11 land on the Moon?",
        "Who landed on the Moon during Apollo 11?",
        "Which lunar module did Apollo 11 use to land?",
        "When did Apollo 11 launch?",
        "Who said one small step for a man?",
        "When did Apollo 13 land on the Moon?",
        "Who won the 1969 World Series?",
    )
    latencies_ms: list[float] = []
    manifest_hashes: set[str] = set()
    answer_fingerprints: dict[str, str | None] = {}

    for index in range(iterations):
        question = questions[index % len(questions)]
        start = time.perf_counter_ns()
        response = runtime.query(
            QueryRequest(
                request_id=f"bench:{index}",
                session_id="benchmark",
                text=question,
                trace=True,
            )
        )
        latencies_ms.append((time.perf_counter_ns() - start) / 1_000_000)
        manifest_hashes.add(response.pack_manifest_hash)
        prior = answer_fingerprints.setdefault(question, response.sentence or response.reason)
        if prior != (response.sentence or response.reason):
            raise RuntimeError(f"non-deterministic output for {question!r}")

    _current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "iterations": iterations,
        "question_variants": len(questions),
        "cold_runtime_initialization_ms": round(init_ms, 4),
        "warm_end_to_end_ms": {
            "min": round(min(latencies_ms), 4),
            "median": round(statistics.median(latencies_ms), 4),
            "p95": round(percentile(latencies_ms, 0.95), 4),
            "max": round(max(latencies_ms), 4),
        },
        "throughput_queries_per_second": round(
            iterations / (sum(latencies_ms) / 1000), 2
        ),
        "tracemalloc_peak_bytes": peak_bytes,
        "deterministic_output": True,
        "unique_manifest_hashes": len(manifest_hashes),
        "scope_warning": (
            "Measured on the cloud host Python emulator; target hardware figures remain estimates."
        ),
    }
