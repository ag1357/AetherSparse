"""Certified real-replay reachability with split-safe oracle and blind metrics."""

from __future__ import annotations

import gzip
import json
import math
from collections import Counter, defaultdict
from collections.abc import Iterator
from pathlib import Path
from statistics import median
from typing import Any

from aethersparse.controller.micro_ops import (
    MicroState,
    state_from_replay,
    verifier_eligible_claim_values,
)
from aethersparse.controller.replay import ReplayCase, ReplayManifest, verify_replay_bundle
from aethersparse.controller.search import (
    SearchConfig,
    SearchResult,
    TerminalTrajectory,
    candidate_set_oracle,
    canonical_answer_match,
    canonicalize,
    posthoc_reachable,
    search,
)

TRAINING_PARTITIONS = frozenset({"development", "tuning"})
HELD_OUT_PARTITIONS = frozenset({"evaluation", "final_held"})


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


def _iter_cases(bundle: Path, manifest: ReplayManifest) -> Iterator[ReplayCase]:
    with gzip.open(bundle / manifest.cases_file, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield ReplayCase.model_validate_json(line)


def _baseline_answer(case: ReplayCase) -> str | None:
    for decision in reversed(case.decisions):
        value = decision.selection_state.get("answer_text")
        if isinstance(value, str) and value:
            return value
    return None


def _claim_values(state: MicroState) -> tuple[str, ...]:
    values: list[str] = []
    shape = str(state.frame.get("answer_shape", ""))
    for claim in state.claims:
        candidates = (
            (claim.get("quotation"), claim.get("object_value"))
            if shape == "quotation" and claim.get("quotation")
            else (
                claim.get("object_value"),
                claim.get("quantity_value"),
                claim.get("quotation"),
            )
        )
        for value in candidates:
            if isinstance(value, str) and value and value not in values:
                values.append(value)
    return tuple(values)


def _matching_terminal(
    result: SearchResult, accepted: tuple[str, ...]
) -> TerminalTrajectory | None:
    matches = [
        terminal
        for terminal in result.terminal_candidates
        if terminal.terminal == "ANSWER"
        and terminal.verifier_passed
        and canonical_answer_match(terminal.answer_values, accepted)
    ]
    return min(matches, key=lambda item: item.total_actions, default=None)


def _oracle_solution_can_exist(
    state: MicroState, accepted: tuple[str, ...], answer_shape: str
) -> bool:
    """Prove impossible pointer-copy goals without expanding the state graph."""

    values = verifier_eligible_claim_values(state)
    if not values:
        return False
    if answer_shape == "list":
        parts = [part.strip() for answer in accepted for part in answer.split(";") if part.strip()]
        return bool(parts) and all(
            any(canonical_answer_match((value,), (part,)) for value in values) for part in parts
        )
    if answer_shape == "comparison":
        accepted_canonical = tuple(canonicalize(answer) for answer in accepted)
        matching_values = {
            canonicalize(value)
            for value in values
            if any(canonicalize(value) in answer for answer in accepted_canonical)
        }
        return len(matching_values) >= 2
    return any(canonical_answer_match((value,), accepted) for value in values)


def _primary_blind_result(results: tuple[SearchResult, ...]) -> SearchResult:
    """Freeze a method/output choice using only gold-independent search metadata."""

    return min(
        results,
        key=lambda result: (
            result.selected_trajectory is None,
            (
                result.selected_trajectory.selection_priority[2:]
                if result.selected_trajectory is not None
                else (10**9,)
            ),
            0 if result.search_kind == "best_first" else 1,
            result.selection_sha256 or "",
        ),
    )


def _failure_class(
    state: MicroState,
    benchmark: dict[str, Any],
    accepted: tuple[str, ...],
    *,
    reachable: bool,
    results: tuple[SearchResult, ...],
) -> str:
    if reachable:
        return "TOOLSET_REACHABLE"
    if not state.claims:
        return "CLAIM_MISSING"
    required_shape = str(benchmark.get("required_answer_shape", ""))
    if required_shape and str(state.frame.get("answer_shape", "")) != required_shape:
        return "FRAME_WRONG"
    required_entities = set(str(item) for item in benchmark.get("required_entity_ids", ()))
    frame_entities = set(str(item) for item in state.frame.get("candidate_entity_ids", ()))
    if required_entities and not required_entities.issubset(frame_entities):
        return "ENTITY_BINDING_WRONG"
    values = _claim_values(state)
    if any(canonical_answer_match((value,), accepted) for value in values):
        if any(not result.exhausted for result in results):
            return "SEARCH_BUDGET_EXHAUSTED"
        return "VALUE_MISRANKED"
    accepted_parts = [
        part.strip() for answer in accepted for part in str(answer).split(";") if part.strip()
    ]
    if (
        required_shape in {"list", "comparison"}
        and accepted_parts
        and all(
            any(canonicalize(value) in canonicalize(part) for value in values)
            for part in accepted_parts
        )
    ):
        return "COMPOSITION_OPERATOR_MISSING"
    return "VALUE_NOT_ENUMERATED"


def _projection(groups: dict[str, Counter[str]]) -> dict[str, dict[str, int]]:
    return {key: dict(sorted(value.items())) for key, value in sorted(groups.items())}


def qualify_reachability(
    bundle: Path,
    benchmark: Path,
    *,
    max_depth: int = 12,
    max_expansions: int = 5000,
    beam_width: int = 64,
) -> dict[str, Any]:
    """Run exact search, gating only on training-eligible oracle reachability."""

    bundle = Path(bundle)
    manifest = verify_replay_bundle(bundle)
    benchmark_cases = _load_benchmark(benchmark)
    missing_benchmark: list[str] = []
    incomplete: list[str] = []
    for case in _iter_cases(bundle, manifest):
        if case.case_id not in benchmark_cases:
            missing_benchmark.append(case.case_id)
        if not case.replay_complete:
            incomplete.append(case.case_id)
    if missing_benchmark or incomplete:
        return {
            "schema_version": "aethercore.reachability-report.v2",
            "status": "REACHABILITY_BLOCKED_INCOMPLETE_REPLAY",
            "control_decision": None,
            "replay_bundle_sha256": manifest.bundle_sha256,
            "case_count": manifest.case_count,
            "missing_benchmark_case_ids": sorted(missing_benchmark),
            "incomplete_replay_case_ids": sorted(incomplete),
            "gold_leakage_detected": False,
        }

    configs = (
        SearchConfig(
            kind="best_first",
            max_depth=max_depth,
            max_expansions=max_expansions,
            argument_cap=32,
        ),
        SearchConfig(
            kind="beam",
            max_depth=max_depth,
            max_expansions=max_expansions,
            beam_width=beam_width,
            argument_cap=32,
        ),
    )
    totals: Counter[str] = Counter()
    baseline_by_tier: dict[str, Counter[str]] = defaultdict(Counter)
    search_metrics: dict[str, Counter[str]] = defaultdict(Counter)
    breakdowns: dict[str, dict[str, Counter[str]]] = {
        name: defaultdict(Counter)
        for name in ("tier", "answer_shape", "category", "source_mode", "partition")
    }
    residual: Counter[str] = Counter()
    residual_by_tier: dict[str, Counter[str]] = defaultdict(Counter)
    trajectory_lengths: list[int] = []
    expanded_states: list[int] = []
    branching: list[int] = []
    read_actions: list[int] = []
    p4_costs: list[int] = []
    action_usage: Counter[str] = Counter()
    repeated_trajectories = 0
    repeated_action_count = 0
    per_case: list[dict[str, Any]] = []

    for case in _iter_cases(bundle, manifest):
        gold = benchmark_cases[case.case_id]
        accepted = tuple(str(item) for item in gold.get("accepted_answers", ()))
        accepted_disposition = str(gold.get("accepted_disposition", "ANSWER"))
        totals["cases"] += 1
        totals["exact_correct"] += int(case.outcome == "correct")
        baseline_by_tier[case.corpus_tier]["cases"] += 1
        baseline_by_tier[case.corpus_tier]["exact_correct"] += int(case.outcome == "correct")
        baseline_answer = _baseline_answer(case)
        if accepted_disposition == "ANSWER":
            totals["answer_cases"] += 1
            baseline_by_tier[case.corpus_tier]["answer_cases"] += 1
            canonical_correct = bool(
                baseline_answer and canonical_answer_match((baseline_answer,), accepted)
            )
            totals["canonical_answer_correct"] += int(canonical_correct)
            baseline_by_tier[case.corpus_tier]["canonical_answer_correct"] += int(canonical_correct)

        is_controller_failure = accepted_disposition == "ANSWER" and case.outcome != "correct"
        if not is_controller_failure:
            continue
        totals["controller_failures"] += 1
        training_oracle = case.partition in TRAINING_PARTITIONS and case.training_eligible
        held_out = case.partition in HELD_OUT_PARTITIONS and not case.training_eligible
        if not training_oracle and not held_out:
            raise ValueError(f"invalid partition/training contract for {case.case_id}")
        state = state_from_replay(case)
        shape = str(gold.get("required_answer_shape", state.frame.get("answer_shape", "unknown")))
        oracle_impossible = training_oracle and not _oracle_solution_can_exist(
            state, accepted, shape
        )
        totals["oracle_search_skipped_proven_impossible"] += int(oracle_impossible)
        results = (
            ()
            if oracle_impossible
            else tuple(
                search(
                    state,
                    config,
                    accepted_answers=accepted if training_oracle else None,
                    allow_gold=training_oracle,
                )
                for config in configs
            )
        )
        oracle_reachable = training_oracle and any(
            candidate_set_oracle(result, accepted) for result in results
        )
        blind_primary = _primary_blind_result(results) if held_out else None
        blind_correct = bool(
            held_out and blind_primary is not None and posthoc_reachable(blind_primary, accepted)
        )
        held_out_candidate_oracle = held_out and any(
            candidate_set_oracle(result, accepted) for result in results
        )
        proven_reachable = bool(oracle_reachable or held_out_candidate_oracle)
        if training_oracle:
            totals["training_failures"] += 1
            totals["training_oracle_reachable"] += int(oracle_reachable)
        if held_out:
            totals["held_out_failures"] += 1
            totals["held_out_blind_correct"] += int(blind_correct)
            totals["held_out_candidate_oracle"] += int(held_out_candidate_oracle)

        selected_success: TerminalTrajectory | None = None
        if training_oracle:
            matches = [
                terminal
                for result in results
                if (terminal := _matching_terminal(result, accepted)) is not None
            ]
            selected_success = min(matches, key=lambda item: item.total_actions, default=None)
        elif blind_primary is not None:
            selected_success = blind_primary.selected_trajectory
        if selected_success is not None:
            trajectory_lengths.append(selected_success.total_actions)
            read_actions.append(selected_success.read_actions)
            p4_costs.append(selected_success.estimated_p4_cost)
            operation_names = [step.operation_name for step in selected_success.steps]
            action_usage.update(operation_names)
            repeats = len(operation_names) - len(set(operation_names))
            repeated_trajectories += int(repeats > 0)
            repeated_action_count += repeats

        for result in results:
            method = search_metrics[result.search_kind]
            method["cases"] += 1
            method["oracle_reachable"] += int(
                training_oracle and candidate_set_oracle(result, accepted)
            )
            method["blind_correct"] += int(held_out and posthoc_reachable(result, accepted))
            method["candidate_oracle"] += int(held_out and candidate_set_oracle(result, accepted))
            method["expansions"] += result.expansions
            method["verifier_attempts"] += result.verifier_attempts
            method["verifier_rejections"] += result.verifier_rejections
            expanded_states.append(result.expansions)
            branching.append(result.maximum_branching_factor)

        categories = tuple(str(item) for item in gold.get("categories", ()))
        source_mode = "composition" if len(gold.get("gold_evidence", ())) > 1 else "single-value"
        dimensions = {
            "tier": (case.corpus_tier,),
            "answer_shape": (shape,),
            "category": categories or ("uncategorized",),
            "source_mode": (source_mode,),
            "partition": (case.partition,),
        }
        for dimension, keys in dimensions.items():
            for key in keys:
                cell = breakdowns[dimension][key]
                cell["failures"] += 1
                cell["training_failures"] += int(training_oracle)
                cell["oracle_reachable"] += int(oracle_reachable)
                cell["held_out_failures"] += int(held_out)
                cell["blind_correct"] += int(blind_correct)
                cell["candidate_oracle"] += int(held_out_candidate_oracle)

        label = _failure_class(
            state,
            gold,
            accepted,
            reachable=proven_reachable,
            results=results,
        )
        residual[label] += 1
        residual_by_tier[case.corpus_tier][label] += 1
        per_case.append(
            {
                "case_id": case.case_id,
                "partition": case.partition,
                "corpus_tier": case.corpus_tier,
                "answer_shape": shape,
                "categories": categories,
                "failure_class": label,
                "oracle_search_skipped_proven_impossible": oracle_impossible,
                "training_oracle_reachable": oracle_reachable,
                "held_out_blind_correct": blind_correct,
                "held_out_candidate_set_oracle": held_out_candidate_oracle,
                "blind_selection_method": (
                    blind_primary.search_kind if blind_primary is not None else None
                ),
                "blind_selection_sha256": (
                    blind_primary.selection_sha256 if blind_primary is not None else None
                ),
                "searches": [
                    {
                        "kind": result.search_kind,
                        "expansions": result.expansions,
                        "visited_states": result.visited_states,
                        "maximum_branching_factor": result.maximum_branching_factor,
                        "terminal_candidates": len(result.terminal_candidates),
                        "selected_sha256": result.selection_sha256,
                        "verifier_attempts": result.verifier_attempts,
                        "verifier_rejections": result.verifier_rejections,
                        "exhausted": result.exhausted,
                        "gold_used_during_search": result.gold_used_during_search,
                    }
                    for result in results
                ],
            }
        )

    training_failures = totals["training_failures"]
    reachable_fraction = (
        totals["training_oracle_reachable"] / training_failures if training_failures else 0.0
    )
    if not training_failures:
        decision = None
    elif reachable_fraction < 0.30:
        decision = "POLICY_NOT_JUSTIFIED_TOOLSET_INSUFFICIENT"
    elif reachable_fraction <= 0.60:
        decision = "MICRO_OP_TOOLSET_EXTENSION_REQUIRED"
    else:
        decision = "AETHERCORE_POLICY_FEASIBLE"

    compiler_classes = {
        "CLAIM_MISSING",
        "CLAIM_MANGLED",
        "FRAME_WRONG",
        "ENTITY_BINDING_WRONG",
    }
    policy_recoverable = sum(
        count for label, count in residual.items() if label not in compiler_classes
    )
    answer_cases = totals["answer_cases"]
    canonical_baseline = totals["canonical_answer_correct"] / answer_cases if answer_cases else 0.0
    oracle_ceiling = (
        (totals["canonical_answer_correct"] + totals["training_oracle_reachable"]) / answer_cases
        if answer_cases
        else 0.0
    )
    verifier_attempts = sum(row["verifier_attempts"] for row in search_metrics.values())
    verifier_rejections = sum(row["verifier_rejections"] for row in search_metrics.values())

    return {
        "schema_version": "aethercore.reachability-report.v2",
        "status": "COMPLETE",
        "control_decision": decision,
        "replay_bundle_sha256": manifest.bundle_sha256,
        "case_count": totals["cases"],
        "current_deterministic_exact_case_accuracy": totals["exact_correct"] / totals["cases"],
        "current_deterministic_canonical_answer_accuracy": canonical_baseline,
        "current_deterministic_accuracy": totals["exact_correct"] / totals["cases"],
        "search_oracle_reachable_canonical_ceiling": oracle_ceiling,
        "gold_blind_held_out_failure_accuracy": (
            totals["held_out_blind_correct"] / totals["held_out_failures"]
            if totals["held_out_failures"]
            else 0.0
        ),
        "controller_failures": totals["controller_failures"],
        "training_eligible_controller_failures": training_failures,
        "controller_failures_reachable": totals["training_oracle_reachable"],
        "controller_failure_reachable_fraction": reachable_fraction,
        "controller_failure_unresolved_fraction": 1.0 - reachable_fraction,
        "oracle_search_skipped_proven_impossible": totals[
            "oracle_search_skipped_proven_impossible"
        ],
        "held_out_controller_failures": totals["held_out_failures"],
        "held_out_blind_correct": totals["held_out_blind_correct"],
        "held_out_candidate_set_oracle": totals["held_out_candidate_oracle"],
        "policy_recoverable_residual_count": policy_recoverable,
        "policy_recoverable_residual_fraction_of_failures": (
            policy_recoverable / totals["controller_failures"]
            if totals["controller_failures"]
            else 0.0
        ),
        "trajectory_length_median": median(trajectory_lengths) if trajectory_lengths else 0.0,
        "trajectory_length_p95": _percentile(trajectory_lengths, 0.95),
        "expanded_states_median": median(expanded_states) if expanded_states else 0.0,
        "expanded_states_p95": _percentile(expanded_states, 0.95),
        "state_action_branching_factor_p95": _percentile(branching, 0.95),
        "read_actions_median": median(read_actions) if read_actions else 0.0,
        "read_actions_p95": _percentile(read_actions, 0.95),
        "estimated_p4_operations_median": median(p4_costs) if p4_costs else 0.0,
        "estimated_p4_operations_p95": _percentile(p4_costs, 0.95),
        "repeated_action_statistics": {
            "trajectories_with_repeats": repeated_trajectories,
            "repeated_action_count": repeated_action_count,
        },
        "micro_op_usage_distribution": dict(sorted(action_usage.items())),
        "action_distribution": dict(sorted(action_usage.items())),
        "verifier_attempts": verifier_attempts,
        "verifier_rejections": verifier_rejections,
        "verifier_rejection_rate": (
            verifier_rejections / verifier_attempts if verifier_attempts else 0.0
        ),
        "search_by_method": {
            key: dict(sorted(value.items())) for key, value in sorted(search_metrics.items())
        },
        "baseline_by_tier": {
            key: dict(sorted(value.items())) for key, value in sorted(baseline_by_tier.items())
        },
        "breakdowns": {dimension: _projection(groups) for dimension, groups in breakdowns.items()},
        "residual_taxonomy": dict(sorted(residual.items())),
        "residual_taxonomy_by_tier": _projection(residual_by_tier),
        "gold_policy": {
            "development_and_tuning": ("gold search goal only when training_eligible=true"),
            "evaluation_and_final_held": (
                "accepted answers withheld during search; one output frozen before post-hoc scoring"
            ),
            "architecture_gate_source": "training-eligible development+tuning only",
            "evidence_oracle_scope": (
                "controller-isolation replay contains oracle-injected evidence, "
                "including held-out; "
                "held-out search is answer-label-blind, not gold-data-blind"
            ),
            "gold_leakage_detected": False,
        },
        "search_limits": {
            "max_depth": max_depth,
            "max_expansions": max_expansions,
            "beam_width": beam_width,
            "argument_cap": 32,
        },
        "certification_scope": (
            "bounded claim-state controller reachability over authenticated evidence-oracle replay "
            "objects using the exact AetherSparse verifier"
        ),
        "per_case": per_case,
    }
