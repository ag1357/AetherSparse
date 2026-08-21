#!/usr/bin/env python3
"""Fit and autonomously qualify the smallest AetherCore learned policy."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from aethersparse.controller.learned_policy import (
    MaskedLinearPolicy,
    PolicyDecisionRecord,
    finite_weights,
    fit_masked_linear_policy,
    legal_mask,
    state_sha256,
    workspace_summary,
)
from aethersparse.controller.micro_ops import (
    MicroAction,
    MicroState,
    execute_action,
    legal_actions,
    state_from_replay,
)
from aethersparse.controller.replay import ReplayCase, verify_replay_bundle
from aethersparse.controller.search import (
    SearchConfig,
    TerminalTrajectory,
    canonical_answer_match,
    search,
)

TRAINING_PARTITIONS = frozenset({"development", "tuning"})
PUBLISHED_V12_REACHABLE = 572
STRICT_COHORT = 695


def _read(path: Path) -> dict[str, Any]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _trajectory_identity(key: tuple[str, str], terminal: TerminalTrajectory) -> str:
    payload = json.dumps(
        {"key": key, "terminal": terminal.model_dump(mode="json")},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return "trajectory:v13:" + hashlib.sha256(payload).hexdigest()[:24]


def _load_inputs(
    bundle: Path, benchmark_path: Path, mission5_path: Path
) -> tuple[
    dict[tuple[str, str], ReplayCase],
    dict[str, dict[str, Any]],
    set[tuple[str, str]],
    dict[str, str],
]:
    manifest = verify_replay_bundle(bundle)
    benchmark = _read(benchmark_path)
    mission5 = _read(mission5_path)
    benchmark_by_id = {
        str(row["case_id"]): row for row in benchmark.get("cases", ()) if isinstance(row, dict)
    }
    strict_keys = {
        (str(row["case_id"]), str(row["corpus_tier"]))
        for row in mission5.get("per_case", ())
        if isinstance(row, dict) and row.get("partition") in TRAINING_PARTITIONS
    }
    witnessed_keys = {
        (str(row["case_id"]), str(row["corpus_tier"]))
        for row in mission5.get("per_case", ())
        if isinstance(row, dict)
        and row.get("partition") in TRAINING_PARTITIONS
        and row.get("training_oracle_reachable") is True
    }
    if len(strict_keys) != STRICT_COHORT:
        raise ValueError(f"strict cohort changed: {len(strict_keys)}")
    cases: dict[tuple[str, str], ReplayCase] = {}
    with gzip.open(bundle / manifest.cases_file, "rt", encoding="utf-8") as stream:
        for line in stream:
            case = ReplayCase.model_validate_json(line)
            key = (case.case_id, case.corpus_tier)
            if key in witnessed_keys:
                cases[key] = case
    if set(cases) != witnessed_keys:
        raise ValueError("authenticated replay is missing certified Mission-5 witnesses")
    hashes = {
        "replay_bundle_sha256": manifest.bundle_sha256,
        "benchmark_sha256": _sha256(benchmark_path),
        "mission5_sha256": _sha256(mission5_path),
    }
    return cases, benchmark_by_id, witnessed_keys, hashes


def _correct_terminal(
    initial: MicroState, accepted: tuple[str, ...]
) -> TerminalTrajectory | None:
    for configuration in (
        SearchConfig(kind="best_first", max_depth=12, max_expansions=5_000, argument_cap=64),
        SearchConfig(
            kind="beam",
            max_depth=12,
            max_expansions=5_000,
            argument_cap=64,
            beam_width=64,
        ),
    ):
        result = search(initial, configuration, accepted_answers=accepted, allow_gold=True)
        correct = [
            terminal
            for terminal in result.terminal_candidates
            if terminal.terminal == "ANSWER"
            and terminal.verifier_passed
            and canonical_answer_match(terminal.answer_values, accepted)
        ]
        if correct:
            return min(
                correct,
                key=lambda item: (
                    item.total_actions,
                    tuple(step.operation_id for step in item.steps),
                    item.selected_claim_ids,
                ),
            )
    return None


def _examples_and_records(
    cases: dict[tuple[str, str], ReplayCase], benchmark: dict[str, dict[str, Any]]
) -> tuple[
    dict[tuple[str, str], MicroState],
    dict[tuple[str, str], tuple[tuple[MicroState, MicroAction], ...]],
    tuple[PolicyDecisionRecord, ...],
]:
    initials: dict[tuple[str, str], MicroState] = {}
    examples: dict[tuple[str, str], tuple[tuple[MicroState, MicroAction], ...]] = {}
    records: list[PolicyDecisionRecord] = []
    for key in sorted(cases):
        case = cases[key]
        gold = benchmark[case.case_id]
        accepted = tuple(str(value) for value in gold.get("accepted_answers", ()))
        initial = state_from_replay(case)
        terminal = _correct_terminal(initial, accepted)
        if terminal is None:
            raise ValueError(f"published certified witness did not reproduce: {key}")
        trajectory_id = _trajectory_identity(key, terminal)
        state = initial
        trajectory_examples: list[tuple[MicroState, MicroAction]] = []
        for step in terminal.steps:
            action = MicroAction(operation_id=step.operation_id, arguments=step.arguments)
            actions = legal_actions(state, argument_cap=64)
            if action not in actions:
                raise ValueError(f"trajectory action is no longer legal: {key} step {step}")
            after = execute_action(state, action)
            trajectory_examples.append((state, action))
            records.append(
                PolicyDecisionRecord(
                    query_session_identity=f"session:{case.case_id}:{case.corpus_tier}",
                    semantic_address_candidates=tuple(
                        str(item) for item in state.frame.get("candidate_entity_ids", ())
                    ),
                    exact_evidence_handles=tuple(
                        sorted(str(span.get("span_id", "")) for span in state.source_spans)
                    ),
                    unresolved_state=tuple(
                        str(item) for item in state.frame.get("unresolved_hypotheses", ())
                    ),
                    workspace_before_sha256=state_sha256(state),
                    workspace_after_sha256=state_sha256(after),
                    workspace_summary=workspace_summary(state),
                    legal_action_mask=legal_mask(actions),
                    selected_operation=action.operation_id,
                    operation_arguments=action.arguments,
                    verifier_disposition={
                        "before": state.verification_passed,
                        "after": after.verification_passed,
                        "terminal": after.terminal,
                    },
                    trajectory_identity=trajectory_id,
                    split_identity=case.partition,
                )
            )
            state = after
        initials[key] = initial
        examples[key] = tuple(trajectory_examples)
    return initials, examples, tuple(records)


def _teacher_metrics(
    policy: MaskedLinearPolicy,
    examples: dict[tuple[str, str], tuple[tuple[MicroState, MicroAction], ...]],
    cases: dict[tuple[str, str], ReplayCase],
    partition: str,
) -> dict[str, Any]:
    selected = [
        example
        for key, trajectory in sorted(examples.items())
        if cases[key].partition == partition
        for example in trajectory
    ]
    correct = sum(policy.select(state, argument_cap=64) == target for state, target in selected)
    return {"correct": correct, "decisions": len(selected), "accuracy": correct / len(selected)}


def _rollout(
    policy: MaskedLinearPolicy,
    initial: MicroState,
    accepted: tuple[str, ...],
    *,
    max_depth: int = 12,
) -> dict[str, Any]:
    state = initial
    verifier_rejections = 0
    invalid_actions = 0
    for _step in range(max_depth):
        action = policy.select(state, argument_cap=64)
        if action is None:
            return {
                "success": False,
                "operations": state.total_actions,
                "failure": "NO_LEGAL_ACTION",
                "invalid_actions": invalid_actions,
                "verifier_rejections": verifier_rejections,
            }
        if action not in legal_actions(state, argument_cap=64):
            invalid_actions += 1
            return {
                "success": False,
                "operations": state.total_actions,
                "failure": "INVALID_ACTION",
                "invalid_actions": invalid_actions,
                "verifier_rejections": verifier_rejections,
            }
        state = execute_action(state, action)
        if action.operation_id == 59 and not state.verification_passed:
            verifier_rejections += 1
        if state.terminal is not None:
            success = bool(
                state.terminal == "ANSWER"
                and state.verification_passed
                and canonical_answer_match(state.answer_values, accepted)
            )
            if success:
                failure = None
            elif state.terminal != "ANSWER":
                failure = "PREMATURE_HALT"
            elif state.verification_passed:
                failure = "WRONG_GROUNDED_ANSWER"
            else:
                failure = "VERIFIER_REJECTION"
            return {
                "success": success,
                "operations": state.total_actions,
                "failure": failure,
                "invalid_actions": invalid_actions,
                "verifier_rejections": verifier_rejections,
            }
    return {
        "success": False,
        "operations": state.total_actions,
        "failure": "MAX_DEPTH",
        "invalid_actions": invalid_actions,
        "verifier_rejections": verifier_rejections,
    }


def _percentile(values: list[int], quantile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def qualify(
    *, bundle: Path, benchmark_path: Path, mission5_path: Path, epochs: int
) -> dict[str, Any]:
    cases, benchmark, witnessed_keys, source_hashes = _load_inputs(
        bundle, benchmark_path, mission5_path
    )
    initials, examples, records = _examples_and_records(cases, benchmark)
    development = [
        example
        for key, trajectory in sorted(examples.items())
        if cases[key].partition == "development"
        for example in trajectory
    ]
    baseline = fit_masked_linear_policy(development, epochs=epochs)
    repaired = fit_masked_linear_policy(development, epochs=epochs, averaged=True)
    if not finite_weights(baseline) or not finite_weights(repaired):
        raise ValueError("policy contains non-finite weights")

    def evaluate(policy: MaskedLinearPolicy) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for key in sorted(witnessed_keys):
            gold = benchmark[key[0]]
            outcome = _rollout(
                policy,
                initials[key],
                tuple(str(item) for item in gold.get("accepted_answers", ())),
            )
            results.append({"key": key, "partition": cases[key].partition, **outcome})
        return results

    baseline_outcomes = evaluate(baseline)
    repaired_outcomes = evaluate(repaired)

    def selection_key(item: tuple[MaskedLinearPolicy, list[dict[str, Any]]]) -> tuple[int, int]:
        _policy, policy_outcomes = item
        tuning_hits = sum(
            bool(outcome["success"])
            for outcome in policy_outcomes
            if outcome["partition"] == "tuning"
        )
        total_hits = sum(bool(outcome["success"]) for outcome in policy_outcomes)
        return tuning_hits, total_hits

    policy, outcomes = max(
        ((baseline, baseline_outcomes), (repaired, repaired_outcomes)), key=selection_key
    )
    success = sum(bool(item["success"]) for item in outcomes)
    operations = [int(item["operations"]) for item in outcomes]
    failures = Counter(str(item["failure"]) for item in outcomes if item["failure"] is not None)
    by_partition = {}
    for partition in ("development", "tuning"):
        selected = [item for item in outcomes if item["partition"] == partition]
        hits = sum(bool(item["success"]) for item in selected)
        by_partition[partition] = {
            "successful": hits,
            "reachable_evaluated": len(selected),
            "rate": hits / len(selected),
        }
    records_payload = b"\n".join(
        json.dumps(record.model_dump(mode="json"), separators=(",", ":"), sort_keys=True).encode()
        for record in records
    )
    return {
        "schema_version": "aethercore.v13-policy-qualification.v1",
        "status": "WORKING_LEARNED_POLICY" if success else "POLICY_NOT_WORKING",
        "scope": {
            "published_v12_reachable_ceiling": PUBLISHED_V12_REACHABLE,
            "strict_all_states": STRICT_COHORT,
            "actual_policy_evaluated_reachable": len(outcomes),
            "coverage_reason": (
                "Uses the authenticated, per-case reproducible Mission-5 witness subset. "
                "The V12 report retained its 572 aggregate certificate but not its bulky "
                "per-case 397k alias export; no missing witness identity was inferred from gold."
            ),
        },
        "policy": {
            "architecture": "typed-legal-mask structured multiclass perceptron",
            "parameter_count": policy.parameter_count,
            "serialized_model": policy.model_dump(mode="json"),
            "learned_world_facts": 0,
            "numeric_format": "float32-compatible reference weights",
        },
        "cheap_generic_repair": {
            "measured_failure": (
                "teacher accuracy remained high while autonomous claim selection compounded"
            ),
            "repair": "averaged structured perceptron over the same features and parameter count",
            "selection_partition": "tuning",
            "baseline": {
                "algorithm": baseline.training_algorithm,
                "development_success": sum(
                    bool(item["success"])
                    for item in baseline_outcomes
                    if item["partition"] == "development"
                ),
                "tuning_success": sum(
                    bool(item["success"])
                    for item in baseline_outcomes
                    if item["partition"] == "tuning"
                ),
            },
            "repair_candidate": {
                "algorithm": repaired.training_algorithm,
                "development_success": sum(
                    bool(item["success"])
                    for item in repaired_outcomes
                    if item["partition"] == "development"
                ),
                "tuning_success": sum(
                    bool(item["success"])
                    for item in repaired_outcomes
                    if item["partition"] == "tuning"
                ),
            },
            "selected": policy.training_algorithm,
            "parameter_growth": 0,
        },
        "decision_records": {
            "count": len(records),
            "sha256": hashlib.sha256(records_payload).hexdigest(),
            "development_records_used_for_fit": len(development),
            "tuning_records_used_for_fit": 0,
            "evaluation_or_final_held_records": 0,
            "closed_schema": list(PolicyDecisionRecord.model_fields),
        },
        "teacher_next_action": {
            partition: _teacher_metrics(policy, examples, cases, partition)
            for partition in ("development", "tuning")
        },
        "autonomous_rollout": {
            "successful": success,
            "reachable_evaluated": len(outcomes),
            "successful_per_reachable_evaluated": success / len(outcomes),
            "successful_per_published_v12_reachable": success / PUBLISHED_V12_REACHABLE,
            "successful_per_all_695": success / STRICT_COHORT,
            "by_partition": by_partition,
            "average_operations": statistics.fmean(operations),
            "p95_operations": _percentile(operations, 0.95),
            "invalid_action_attempts": sum(int(item["invalid_actions"]) for item in outcomes),
            "verifier_rejections": sum(int(item["verifier_rejections"]) for item in outcomes),
            "premature_halt": failures["PREMATURE_HALT"],
            "runaway_max_depth": failures["MAX_DEPTH"],
            "failure_taxonomy": dict(sorted(failures.items())),
        },
        "verifier_contract": {
            "bypass": False,
            "success_requires_exact_verifier_and_canonical_answer": True,
            "legal_action_mask_enforced_before_every_transition": True,
        },
        "source_identity": source_hashes,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--mission5-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=24)
    args = parser.parse_args()
    result = qualify(
        bundle=args.bundle,
        benchmark_path=args.benchmark,
        mission5_path=args.mission5_report,
        epochs=args.epochs,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "policy"}))


if __name__ == "__main__":
    main()
