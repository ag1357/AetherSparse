from __future__ import annotations

from aethersparse.benchmark import run_benchmark


def test_benchmark_checks_sustained_determinism() -> None:
    report = run_benchmark(iterations=14)

    assert report["iterations"] == 14
    assert report["deterministic_output"] is True
    assert report["unique_manifest_hashes"] == 1
    assert report["warm_end_to_end_ms"]["p95"] > 0

