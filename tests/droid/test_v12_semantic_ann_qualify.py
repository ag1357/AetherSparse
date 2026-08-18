from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from scripts.droid.v12_semantic_ann_qualify import (
    _load_diagnostic,
    _load_questions,
    _verify_training_partition_alignment,
)


def _write_benchmark(tmp_path: Path, cases: list[dict[str, str]]) -> Path:
    path = tmp_path / "benchmark.json"
    path.write_text(json.dumps({"cases": cases}), encoding="utf-8")
    return path


def _write_diagnostic(tmp_path: Path, rows: list[dict[str, object]]) -> tuple[Path, Path]:
    raw = b"".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")).encode() + b"\n" for row in rows
    )
    compressed = gzip.compress(raw, mtime=0)
    payload = tmp_path / "diagnostic.jsonl.gz"
    manifest = tmp_path / "diagnostic.manifest.json"
    payload.write_bytes(compressed)
    manifest.write_text(
        json.dumps(
            {
                "schema": "aethersparse.v10-candidate-diagnostic.v1",
                "output": {"sha256": hashlib.sha256(compressed).hexdigest()},
            }
        ),
        encoding="utf-8",
    )
    return payload, manifest


def _candidate_row(case_id: str, partition: str) -> dict[str, object]:
    return {
        "case_id": case_id,
        "partition": partition,
        "candidates": [{"document_id": f"doc:{case_id}", "title": f"Title {case_id}"}],
    }


@pytest.mark.parametrize(
    "duplicate_partition",
    ["development", "tuning"],
)
def test_benchmark_loader_rejects_duplicate_case_id(
    tmp_path: Path, duplicate_partition: str
) -> None:
    path = _write_benchmark(
        tmp_path,
        [
            {"case_id": "case:duplicate", "partition": "development", "question": "Q1"},
            {
                "case_id": "case:duplicate",
                "partition": duplicate_partition,
                "question": "Q2",
            },
        ],
    )
    with pytest.raises(ValueError, match="benchmark duplicate case_id"):
        _load_questions(path)


@pytest.mark.parametrize(
    "duplicate_partition",
    ["development", "tuning"],
)
def test_diagnostic_loader_rejects_duplicate_case_id(
    tmp_path: Path, duplicate_partition: str
) -> None:
    payload, manifest = _write_diagnostic(
        tmp_path,
        [
            _candidate_row("case:duplicate", "development"),
            _candidate_row("case:duplicate", duplicate_partition),
        ],
    )
    with pytest.raises(ValueError, match="candidate diagnostic duplicate case_id"):
        _load_diagnostic(payload, manifest)


def test_partition_alignment_rejects_partition_mismatch() -> None:
    with pytest.raises(ValueError, match="partition mismatch"):
        _verify_training_partition_alignment(
            {"case:1": "development", "case:2": "tuning"},
            {"case:1": "tuning", "case:2": "development"},
        )


def test_partition_alignment_rejects_missing_or_extra_case_id() -> None:
    with pytest.raises(ValueError, match="case identities differ"):
        _verify_training_partition_alignment(
            {"case:1": "development", "case:2": "tuning"},
            {"case:1": "development", "case:3": "tuning"},
        )


def test_loaders_return_matching_case_partition_maps(tmp_path: Path) -> None:
    benchmark = _write_benchmark(
        tmp_path,
        [
            {"case_id": "case:dev", "partition": "development", "question": "Dev?"},
            {"case_id": "case:tune", "partition": "tuning", "question": "Tune?"},
            {"case_id": "case:sealed", "partition": "evaluation", "question": "Sealed?"},
        ],
    )
    payload, manifest = _write_diagnostic(
        tmp_path,
        [
            _candidate_row("case:dev", "development"),
            _candidate_row("case:tune", "tuning"),
            _candidate_row("case:sealed", "evaluation"),
        ],
    )
    questions, benchmark_partitions, benchmark_counts = _load_questions(benchmark)
    documents, tuning_ids, diagnostic_partitions, diagnostic = _load_diagnostic(payload, manifest)
    _verify_training_partition_alignment(benchmark_partitions, diagnostic_partitions)
    assert set(questions) == {"case:dev", "case:tune"}
    assert benchmark_counts["protected_excluded"] == 1
    assert documents == {"doc:case:dev": "Title case:dev"}
    assert tuning_ids == ("case:tune",)
    assert diagnostic["counts"]["protected_rows_excluded"] == 1
