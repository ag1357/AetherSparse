"""Corpus-independent certified reachability qualification over replay bundles."""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any

from aethersparse.controller.micro_ops import state_from_replay
from aethersparse.controller.replay import ReplayCase, load_replay_bundle
from aethersparse.controller.search import (
    SearchConfig,
    SearchResult,
    canonical_answer_match,
    posthoc_reachable,
    search,
)


def _percentile(values: list[int], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, math.ceil(percentile * len(ordered)) - 1)
    return float(ordered[rank])


def _load_benchmark(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise ValueError("benchmark must contain a cases list")
    return {
        str(case["case_id"]): dict(case)
        for case in payload["cases"]
        if isinstance(case, dict) and "case_id" in case
    }


def _terminal_length(result: SearchResult, accepted: tuple[str, ...]) -> int | None:
    matches = [
        terminal.total_actions
        for terminal in result.terminal_candidates
        if terminal.terminal == "ANSWER"
        and terminal.verifier_passed
        and canonical_answer_match(terminal.answer_values, accepted)
    ]
    return min(matches) if matches else None


def _case_reachable(
    case: ReplayCase,
    accepted: tuple[str, ...],
    configs: tuple[SearchConfig, ...],
) -> tuple[bool, tuple[SearchResult, ...], int | None]:
    initial = state_from_replay(case)
    allow_gold = case.partition in {"development", "tuning"}
    results = tuple(
        search(
            initial,
            config,
            accepted_answers=accepted if allow_gold else None,
            allow_gold=allow_gold,
        )
        for config in configs
    )
    reachable = False
    lengths: list[int] = []
    for result in results:
        if allow_gold:
            hit = any(
                terminal.terminal == "ANSWER"
                and terminal.verifier_passed
                and canonical_answer_match(terminal.answer_values, accepted)
                for terminal in result.terminal_candidates
            )
        else:
            hit = posthoc_reachable(result, accepted)
        reachable = reachable or hit
        length = _terminal_length(result, accepted)
        if length is not None:
            lengths.append(length)
    return reachable, results, min(lengths) if lengths else None


def qualify_reachability(
    bundle: Path,
    benchmark: Path,
    *,
    max_depth: int = 12,
    max_expansions: int = 5000,
    beam_width: int = 64,
) -> dict[str, Any]:
    """Run both bounded searches and apply the Mission 5 reachability gate."""

    manifest, cases = load_replay_bundle(bundle)
    benchmark_cases = _load_benchmark(benchmark)
    configs = (
        SearchConfig(kind="best_first", max_depth=max_depth, max_expansions=max_expansions),
        SearchConfig(
            kind="beam",
            max_depth=max_depth,
            max_expansions=max_expansions,
            beam_width=beam_width,
        ),
    )
    missing_benchmark = sorted(
        case.case_id for case in cases if case.case_id not in benchmark_cases
    )
    incomplete = sorted(case.case_id for case in cases if not case.replay_complete)
    if missing_benchmark or incomplete:
        return {
            "schema_version": "aethercore.reachability-report.v1",
            "status": "REACHABILITY_BLOCKED_INCOMPLETE_REPLAY",
            "control_decision": None,
            "replay_bundle_sha256": manifest.bundle_sha256,
            "case_count": len(cases),
            "missing_benchmark_case_ids": missing_benchmark,
            "incomplete_replay_case_ids": incomplete,
            "gold_leakage_detected": False,
        }

    correct = sum(case.outcome == "correct" for case in cases)
    failures = [case for case in cases if case.outcome != "correct"]
    reachable_failures = 0
    trajectory_lengths: list[int] = []
    expansions: list[int] = []
    branching: list[int] = []
    actions: Counter[str] = Counter()
    per_case: list[dict[str, Any]] = []
    gold_leakage = False
    for case in failures:
        gold = benchmark_cases[case.case_id]
        accepted = tuple(str(item) for item in gold.get("accepted_answers", ()))
        reachable, results, length = _case_reachable(case, accepted, configs)
        reachable_failures += reachable
        if length is not None:
            trajectory_lengths.append(length)
        for result in results:
            expansions.append(result.expansions)
            branching.append(result.maximum_branching_factor)
            if case.partition in {"evaluation", "final_held"} and result.gold_used_during_search:
                gold_leakage = True
        best_steps = [
            terminal.steps
            for result in results
            for terminal in result.terminal_candidates
            if terminal.terminal == "ANSWER"
            and terminal.verifier_passed
            and canonical_answer_match(terminal.answer_values, accepted)
        ]
        if best_steps:
            for step in min(best_steps, key=len):
                actions[step.operation_name] += 1
        per_case.append(
            {
                "case_id": case.case_id,
                "partition": case.partition,
                "corpus_tier": case.corpus_tier,
                "reachable": reachable,
                "minimum_trajectory_length": length,
                "searches": [
                    {
                        "kind": result.search_kind,
                        "expansions": result.expansions,
                        "visited_states": result.visited_states,
                        "maximum_branching_factor": result.maximum_branching_factor,
                        "terminal_candidates": len(result.terminal_candidates),
                        "exhausted": result.exhausted,
                        "gold_used_during_search": result.gold_used_during_search,
                    }
                    for result in results
                ],
            }
        )
    failure_count = len(failures)
    reachable_fraction = reachable_failures / failure_count if failure_count else 0.0
    if reachable_fraction < 0.30:
        decision = "POLICY_NOT_JUSTIFIED_TOOLSET_INSUFFICIENT"
    elif reachable_fraction <= 0.60:
        decision = "MICRO_OP_TOOLSET_EXTENSION_REQUIRED"
    else:
        decision = "AETHERCORE_POLICY_FEASIBLE"
    total = len(cases)
    report = {
        "schema_version": "aethercore.reachability-report.v1",
        "status": "COMPLETE",
        "control_decision": decision,
        "replay_bundle_sha256": manifest.bundle_sha256,
        "case_count": total,
        "current_deterministic_accuracy": correct / total if total else 0.0,
        "certified_reachable_accuracy": (correct + reachable_failures) / total if total else 0.0,
        "absolute_reachable_canonical_ceiling": (
            (correct + reachable_failures) / total if total else 0.0
        ),
        "controller_failures": failure_count,
        "controller_failures_reachable": reachable_failures,
        "controller_failure_reachable_fraction": reachable_fraction,
        "trajectory_length_median": median(trajectory_lengths) if trajectory_lengths else 0.0,
        "trajectory_length_p95": _percentile(trajectory_lengths, 0.95),
        "search_expansions_median": median(expansions) if expansions else 0.0,
        "state_action_branching_factor_p95": _percentile(branching, 0.95),
        "action_distribution": dict(sorted(actions.items())),
        "gold_policy": {
            "development_and_tuning": "permitted for trajectory objective",
            "evaluation_and_final_held": "blind search; post-hoc scoring only",
            "gold_leakage_detected": gold_leakage,
        },
        "search_limits": {
            "max_depth": max_depth,
            "max_expansions": max_expansions,
            "beam_width": beam_width,
        },
        "certification_scope": "bounded state graph under recorded replay objects",
        "per_case": per_case,
    }
    if gold_leakage:
        report["status"] = "INVALID_GOLD_LEAKAGE"
        report["control_decision"] = None
    return report
