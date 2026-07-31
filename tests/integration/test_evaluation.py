from __future__ import annotations

from aethersparse.evaluation import run_evaluation


def test_public_smoke_baselines_pass_without_overclaiming_gate_zero() -> None:
    report = run_evaluation()

    assert "not Gate 0" in report["scope_warning"]
    assert len(report["baselines"]) == 2
    for baseline in report["baselines"]:
        assert baseline["case_count"] == 13
        assert baseline["accuracy"] == 1.0
        assert baseline["unsupported_answer_rate"] == 0.0
        assert baseline["grounded_answer_rate"] == 1.0
        assert baseline["max_fraction_of_logical_pack_read"] < 1.0

