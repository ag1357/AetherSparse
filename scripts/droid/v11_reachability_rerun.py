#!/usr/bin/env python3
"""Rerun certified reachability on the Mission 5 unresolved training residual."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any

from aethersparse.controller.micro_ops import state_from_replay, verifier_eligible_claim_values
from aethersparse.controller.replay import ReplayCase, verify_replay_bundle
from aethersparse.controller.search import (
    SearchConfig,
    candidate_set_oracle,
    canonical_answer_match,
    canonicalize,
    search,
)
from aethersparse.controller.value_repair import repair_state_with_typed_values

_TRAINING = frozenset({"development", "tuning"})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _benchmark(path: Path) -> dict[str, dict[str, Any]]:
    payload = _load_json(path)
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise ValueError("benchmark lacks cases")
    return {str(item["case_id"]): item for item in cases if isinstance(item, dict)}


def _goal_possible(values: tuple[str, ...], accepted: tuple[str, ...], shape: str) -> bool:
    if shape == "list":
        parts = [part.strip() for answer in accepted for part in answer.split(";") if part.strip()]
        return bool(parts) and all(
            any(canonical_answer_match((value,), (part,)) for value in values) for part in parts
        )
    if shape == "comparison":
        accepted_canonical = tuple(canonicalize(answer) for answer in accepted)
        matching = {
            canonicalize(value)
            for value in values
            if any(canonicalize(value) in answer for answer in accepted_canonical)
        }
        return len(matching) >= 2
    return any(canonical_answer_match((value,), accepted) for value in values)


def _selected_terminal(results: tuple[Any, ...], accepted: tuple[str, ...]) -> Any | None:
    matches = [
        terminal
        for result in results
        for terminal in result.terminal_candidates
        if terminal.terminal == "ANSWER"
        and terminal.verifier_passed
        and canonical_answer_match(terminal.answer_values, accepted)
    ]
    return min(
        matches,
        key=lambda item: (item.total_actions, item.selection_priority),
        default=None,
    )


def rerun(
    bundle: Path,
    benchmark_path: Path,
    mission5_report_path: Path,
    *,
    max_depth: int,
    max_expansions: int,
    beam_width: int,
) -> dict[str, Any]:
    manifest = verify_replay_bundle(bundle)
    mission5 = _load_json(mission5_report_path)
    if mission5.get("replay_bundle_sha256") != manifest.bundle_sha256:
        raise ValueError("Mission 5 report and replay bundle do not match")
    benchmark = _benchmark(benchmark_path)
    old_rows = mission5.get("per_case")
    if not isinstance(old_rows, list):
        raise ValueError("Mission 5 report lacks per_case rows")
    unresolved = {
        (str(row["case_id"]), str(row["corpus_tier"])): dict(row)
        for row in old_rows
        if isinstance(row, dict)
        and row.get("partition") in _TRAINING
        and not bool(row.get("training_oracle_reachable"))
    }
    old_reachable = int(mission5["controller_failures_reachable"])
    old_training_failures = int(mission5["training_eligible_controller_failures"])
    if old_training_failures != old_reachable + len(unresolved):
        raise ValueError("Mission 5 reachable/unresolved accounting mismatch")
    cases: dict[tuple[str, str], ReplayCase] = {}
    with gzip.open(bundle / manifest.cases_file, "rt", encoding="utf-8") as handle:
        for line in handle:
            case = ReplayCase.model_validate_json(line)
            key = (case.case_id, case.corpus_tier)
            if key in unresolved:
                if case.partition not in _TRAINING or not case.training_eligible:
                    raise ValueError(f"protected case entered architecture rerun: {key}")
                cases[key] = case
    if set(cases) != set(unresolved):
        raise ValueError("not every unresolved Mission 5 case is present in replay")
    configs = (
        SearchConfig(
            kind="best_first",
            max_depth=max_depth,
            max_expansions=max_expansions,
            argument_cap=64,
        ),
        SearchConfig(
            kind="beam",
            max_depth=max_depth,
            max_expansions=max_expansions,
            beam_width=beam_width,
            argument_cap=64,
        ),
    )
    recovered = 0
    residuals: Counter[str] = Counter()
    recovery_by_old_class: Counter[str] = Counter()
    semantic_rejections: Counter[str] = Counter()
    action_usage: Counter[str] = Counter()
    trajectory_lengths: list[int] = []
    per_case: list[dict[str, Any]] = []
    for key in sorted(unresolved):
        case = cases[key]
        old = unresolved[key]
        gold = benchmark[case.case_id]
        accepted = tuple(str(item) for item in gold.get("accepted_answers", ()))
        state = state_from_replay(case)
        repair = repair_state_with_typed_values(state)
        state_signature = hashlib.sha256(
            json.dumps(
                repair.state.model_dump(mode="json"),
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        shape = str(gold.get("required_answer_shape", state.frame.get("answer_shape", "")))
        required_entities = {str(item) for item in gold.get("required_entity_ids", ())}
        frame_entities = {
            str(item) for item in state.frame.get("candidate_entity_ids", ())
        }
        semantic_entity_binding_valid = required_entities.issubset(frame_entities)
        possible = _goal_possible(
            verifier_eligible_claim_values(repair.state), accepted, shape
        )
        results = (
            tuple(
                search(
                    repair.state,
                    config,
                    accepted_answers=accepted,
                    allow_gold=True,
                )
                for config in configs
            )
            if possible
            else ()
        )
        verifier_canonical_reachable = any(
            candidate_set_oracle(result, accepted) for result in results
        )
        reachable = verifier_canonical_reachable and semantic_entity_binding_valid
        if verifier_canonical_reachable and not semantic_entity_binding_valid:
            semantic_rejections["WRONG_ENTITY_GROUNDED_ANSWER"] += 1
        selected = _selected_terminal(results, accepted) if reachable else None
        old_class = str(old.get("failure_class", "UNKNOWN"))
        recovered += int(reachable)
        if reachable:
            recovery_by_old_class[old_class] += 1
            if selected is not None:
                trajectory_lengths.append(selected.total_actions)
                action_usage.update(step.operation_name for step in selected.steps)
        else:
            residuals[old_class] += 1
        per_case.append(
            {
                "case_id": case.case_id,
                "partition": case.partition,
                "corpus_tier": case.corpus_tier,
                "old_failure_class": old_class,
                "source_bound_repair_sha256": state_signature,
                "original_claim_count": len(state.claims),
                "added_claims": repair.added_claims,
                "added_source_spans": repair.added_source_spans,
                "capacity_exhausted": repair.candidate_capacity_exhausted,
                "canonical_goal_present_before_search": possible,
                "verifier_canonical_reachable": verifier_canonical_reachable,
                "semantic_entity_binding_valid": semantic_entity_binding_valid,
                "recovered": reachable,
                "selected_trajectory_length": selected.total_actions if selected else None,
                "searches": [
                    {
                        "kind": result.search_kind,
                        "expansions": result.expansions,
                        "visited_states": result.visited_states,
                        "terminal_candidates": len(result.terminal_candidates),
                        "verifier_attempts": result.verifier_attempts,
                        "verifier_rejections": result.verifier_rejections,
                        "exhausted": result.exhausted,
                    }
                    for result in results
                ],
            }
        )
    new_reachable = old_reachable + recovered
    fraction = new_reachable / old_training_failures
    decision = (
        "UPSTREAM_REPRESENTATION_STILL_LIMITING"
        if fraction < 0.50
        else "SPECIALIST_STATE_IMPROVED_POLICY_STILL_PREMATURE"
        if fraction <= 0.60
        else "AETHERCORE_POLICY_FEASIBLE"
    )
    return {
        "schema_version": "aethercore.v11-reachability-rerun.v1",
        "status": "COMPLETE",
        "decision": decision,
        "source_identity": {
            "replay_bundle_sha256": manifest.bundle_sha256,
            "mission5_report_sha256": _sha256(mission5_report_path),
            "benchmark_sha256": _sha256(benchmark_path),
        },
        "gold_policy": {
            "repair_constructor_accepts_gold": False,
            "gold_used_only_for_training_oracle_search_and_posthoc_scoring": True,
            "partitions": sorted(_TRAINING),
            "evaluation_final_held_consumed": False,
        },
        "old_reachability": old_reachable / old_training_failures,
        "old_reachable": old_reachable,
        "training_failures": old_training_failures,
        "old_unresolved_rerun": len(unresolved),
        "newly_recovered": recovered,
        "new_reachable": new_reachable,
        "new_reachability": fraction,
        "recovery_by_old_class": dict(sorted(recovery_by_old_class.items())),
        "semantic_rejections": dict(sorted(semantic_rejections.items())),
        "residual_by_old_class": dict(sorted(residuals.items())),
        "new_trajectory_length_median": median(trajectory_lengths) if trajectory_lengths else 0,
        "new_trajectory_length_p95": (
            sorted(trajectory_lengths)[max(0, int(0.95 * len(trajectory_lengths)) - 1)]
            if trajectory_lengths
            else 0
        ),
        "new_micro_op_distribution": dict(sorted(action_usage.items())),
        "search_limits": {
            "max_depth": max_depth,
            "max_expansions": max_expansions,
            "beam_width": beam_width,
            "argument_cap": 64,
        },
        "certification_scope": (
            "The 260 previously certified trajectories are carried forward from the "
            "byte-authenticated Mission 5 report. Every one of its 435 unresolved "
            "development/tuning states is rerun after monotonic exact-source value repair."
        ),
        "per_case": per_case,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--mission5-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-depth", type=int, default=12)
    parser.add_argument("--max-expansions", type=int, default=5_000)
    parser.add_argument("--beam-width", type=int, default=64)
    args = parser.parse_args()
    result = rerun(
        args.bundle,
        args.benchmark,
        args.mission5_report,
        max_depth=args.max_depth,
        max_expansions=args.max_expansions,
        beam_width=args.beam_width,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = {key: value for key, value in result.items() if key != "per_case"}
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
