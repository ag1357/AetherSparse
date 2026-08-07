"""Regression tests for scripts/droid/v08_pipeline_eval.py (Mission 3 Lane B).

Covers oracle discipline (default off), stage-attribution precedence, the
compositional answer-component splitter, and — when the 10k p3 pack and the
frozen benchmark are available — oracles-off retrieval equivalence with
scripts/droid/v050_selector_eval.py per-case strict article recall.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "droid" / "v08_pipeline_eval.py"
BENCHMARK_PATH = (
    REPO_ROOT / "data" / "v050" / "benchmark" / "INDEPENDENT_NATURAL_QUERY_SET_V050_R1.json"
)
PACK_PATH = Path("/media/cloud/2982-E16B/work/artifacts/packs/selector-10k-p3.sqlite")

spec = importlib.util.spec_from_file_location("v08_pipeline_eval", SCRIPT_PATH)
assert spec is not None and spec.loader is not None
harness = importlib.util.module_from_spec(spec)
sys.modules.setdefault("v08_pipeline_eval", harness)
spec.loader.exec_module(harness)

# The harness insert puts scripts/droid on sys.path for v050_common.
from v050_common import answer_cases, case_gold_pageids, load_benchmark, pageid  # noqa: E402

from aethersparse.selection.selector import EvidenceSelector  # noqa: E402

ANSWER_CASES_FOR_TEST = 6


def test_oracles_default_off() -> None:
    args = harness._parse_args(["--pack", "pack.sqlite", "--output", "out.json"])
    assert args.oracle is None
    assert harness._resolve_oracles(args) == frozenset()


def test_oracle_flags_are_explicit_and_validated() -> None:
    args = harness._parse_args(
        [
            "--pack",
            "pack.sqlite",
            "--output",
            "out.json",
            "--oracle",
            "candidate",
            "--oracle",
            "ranking",
        ]
    )
    assert harness._resolve_oracles(args) == frozenset({"candidate", "ranking"})
    with pytest.raises(SystemExit):
        harness._parse_args(["--pack", "pack.sqlite", "--output", "out.json", "--oracle", "bogus"])
    with pytest.raises(AssertionError):
        harness.run_evaluation(
            pack=Path("missing.sqlite"),
            benchmark_path=Path("missing.json"),
            limit=None,
            partitions=None,
            oracles=frozenset({"bogus"}),
        )


def test_stage_attribution_precedence() -> None:
    base = {
        "defect": None,
        "gold_in_pool": True,
        "strict_recall": True,
        "evidence_hit": True,
        "exact_answer": False,
    }
    assert harness._attribute_stage(**base) == "D_CONTROLLER_FAILED"
    assert harness._attribute_stage(**{**base, "evidence_hit": False}) == "C_EVIDENCE_FAILED"
    assert harness._attribute_stage(**{**base, "strict_recall": False}) == "B_CANDIDATE_MISRANKED"
    assert harness._attribute_stage(**{**base, "gold_in_pool": False}) == "A_CANDIDATE_MISSING"
    assert (
        harness._attribute_stage(
            **{**base, "gold_in_pool": False, "defect": "gold_exact_text_absent"}
        )
        == "E_BENCHMARK_DEFECT"
    )
    assert harness._attribute_stage(**{**base, "exact_answer": True}) is None


def test_answer_components_split_compositions() -> None:
    assert harness._answer_components("1998") == ("1998",)
    assert harness._answer_components("24% compared with 80%.") == ("24%", "80%")
    assert harness._answer_components("5395% > 016%.") == ("5395%", "016%")
    assert harness._answer_components("a kind of oscillation; a decade") == (
        "a kind of oscillation",
        "a decade",
    )


@pytest.mark.skipif(
    not PACK_PATH.is_file() or not BENCHMARK_PATH.is_file(),
    reason="10k p3 pack or frozen benchmark unavailable on this host",
)
def test_oracles_off_retrieval_equivalence_with_v050_selector_eval() -> None:
    """Per-case strict recall must equal v050_selector_eval.py exactly."""

    benchmark = load_benchmark(BENCHMARK_PATH)
    wanted = {case.case_id for case in answer_cases(benchmark)[:ANSWER_CASES_FOR_TEST]}
    limit = max(
        index for index, case in enumerate(benchmark.cases, start=1) if case.case_id in wanted
    )
    report, outcomes, _ = harness.run_evaluation(
        pack=PACK_PATH,
        benchmark_path=BENCHMARK_PATH,
        limit=limit,
        partitions=None,
        oracles=frozenset(),
        candidate_limit=96,
        selected_limit=8,
        progress=False,
    )
    assert report["config"]["oracle_free"] is True
    assert report["config"]["accuracy_class"] == "SYSTEM"
    harness_strict = {
        row["case_id"]: row["article_recall_strict"] for row in outcomes if row["case_id"] in wanted
    }
    assert len(harness_strict) == ANSWER_CASES_FOR_TEST

    # Independent reference: the exact per-case semantics of
    # scripts/droid/v050_selector_eval.py (same selector, same config).
    selector = EvidenceSelector(PACK_PATH, None, candidate_limit=96, selected_limit=8)
    cases_by_id = {case.case_id: case for case in benchmark.cases}
    reference: dict[str, bool] = {}
    for case_id in sorted(wanted):
        case = cases_by_id[case_id]
        candidates = selector.candidates(case.question)
        trace = selector.select(case.question, stage="reranker", initial_candidates=candidates)
        retrieved = {pageid(item.document_id) for item in trace.selected_evidence}
        gold = case_gold_pageids(case)
        reference[case_id] = bool(gold) and gold <= retrieved
    assert harness_strict == reference
    expected = sum(reference.values()) / len(reference)
    assert report["metrics"]["answer_cases"]["article_recall_strict"] == pytest.approx(expected)
