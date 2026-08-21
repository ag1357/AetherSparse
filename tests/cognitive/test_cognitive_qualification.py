from __future__ import annotations

from aethersparse.cognitive.qualification import (
    qualify_coding_obligations,
    qualify_reasoning_stress,
)


def test_small_reasoning_stress_is_structurally_complete() -> None:
    result = qualify_reasoning_stress()
    assert result["passed"] == result["total"] == 9


def test_multi_file_coding_obligations_execute_and_verify(tmp_path) -> None:  # type: ignore[no-untyped-def]
    result = qualify_coding_obligations(tmp_path / "coding")
    assert result["passed"] == result["total"] == 3
    assert result["mean_affected_object_recall"] == 1.0
    assert result["invariant_violations"] == 0
    assert result["redundant_cycles"] == 0
