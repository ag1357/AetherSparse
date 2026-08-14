#!/usr/bin/env python3
"""Rerun all 695 Mission 5 training failures after upstream address repair."""

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
from aethersparse.controller.semantic_address import SemanticAddressPlane
from aethersparse.controller.semantic_state import enrich_state_with_semantic_addresses
from aethersparse.controller.value_repair import repair_state_with_typed_values
from aethersparse.controller.value_trace import ValueTraceFailure, qualify_value_trace

_TRAINING = frozenset({"development", "tuning"})
_VALUE_EXTRACTION_FAILURES = frozenset(
    {
        ValueTraceFailure.COMPILER_AND_RUNTIME_EXTRACTION,
        ValueTraceFailure.COMPILER_EXTRACTION,
        ValueTraceFailure.RUNTIME_EXTRACTION,
    }
)
_VALUE_RETRIEVAL_FAILURES = frozenset(
    {
        ValueTraceFailure.SOURCE_DOCUMENT_ABSENT,
        ValueTraceFailure.SOURCE_DOCUMENT_OUTSIDE_TOP8,
        ValueTraceFailure.SOURCE_CHUNK_ABSENT,
    }
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read(path: Path) -> dict[str, Any]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            payload = json.load(stream)
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


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


def _value_trace_index(
    diagnostic: dict[str, Any],
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, dict[str, Any]]]:
    scope = diagnostic.get("scope")
    if not isinstance(scope, dict) or bool(scope.get("evaluation_and_final_held_used")):
        raise ValueError("value diagnostic does not prove a training-only scope")
    if set(scope.get("partitions", ())) != _TRAINING:
        raise ValueError("value diagnostic partition scope changed")
    replicas = diagnostic.get("replicas")
    cases = diagnostic.get("unique_cases")
    if not isinstance(replicas, list) or not isinstance(cases, list):
        raise ValueError("value diagnostic lacks replica/case rows")
    return (
        {
            (str(item["case_id"]), str(item["corpus_tier"])): item
            for item in replicas
            if isinstance(item, dict) and item.get("partition") in _TRAINING
        },
        {
            str(item["case_id"]): item
            for item in cases
            if isinstance(item, dict) and item.get("partition") in _TRAINING
        },
    )


def _residual_category(
    *,
    entity_valid: bool,
    possible: bool,
    verifier_attempts: int,
    verifier_rejections: int,
    trace_failure: ValueTraceFailure | None,
) -> str:
    if not entity_valid:
        return "SEMANTIC_ADDRESS_GENERATION"
    if trace_failure in _VALUE_RETRIEVAL_FAILURES:
        return "EVIDENCE_RETRIEVAL"
    if trace_failure in _VALUE_EXTRACTION_FAILURES or not possible:
        return "VALUE_AVAILABILITY"
    if verifier_attempts > 0 and verifier_attempts == verifier_rejections:
        return "STATE_REPRESENTATION"
    return "TOOLSET_CONTROLLER"


def rerun(
    bundle: Path,
    benchmark_path: Path,
    mission5_report_path: Path,
    statistics_path: Path,
    statistics_manifest_path: Path,
    hard_negatives_path: Path,
    value_diagnostic_path: Path,
    *,
    max_depth: int,
    max_expansions: int,
    beam_width: int,
) -> dict[str, Any]:
    manifest = verify_replay_bundle(bundle)
    benchmark = _read(benchmark_path)
    mission5 = _read(mission5_report_path)
    value_diagnostic = _read(value_diagnostic_path)
    if mission5.get("replay_bundle_sha256") != manifest.bundle_sha256:
        raise ValueError("Mission 5 report and replay bundle identities differ")
    benchmark_cases = benchmark.get("cases")
    if not isinstance(benchmark_cases, list):
        raise ValueError("benchmark lacks cases")
    benchmark_by_id = {
        str(item["case_id"]): item for item in benchmark_cases if isinstance(item, dict)
    }
    rows = mission5.get("per_case")
    if not isinstance(rows, list):
        raise ValueError("Mission 5 report lacks per-case rows")
    eligible = {
        (str(item["case_id"]), str(item["corpus_tier"])): item
        for item in rows
        if isinstance(item, dict) and item.get("partition") in _TRAINING
    }
    if len(eligible) != int(mission5["training_eligible_controller_failures"]):
        raise ValueError("Mission 5 training cohort identity changed")
    plane = SemanticAddressPlane.from_gzip(
        statistics_path,
        statistics_manifest_path,
        expected_hard_negatives_sha256=_sha256(hard_negatives_path),
    )
    value_replicas, value_cases = _value_trace_index(value_diagnostic)
    cases: dict[tuple[str, str], ReplayCase] = {}
    with gzip.open(bundle / manifest.cases_file, "rt", encoding="utf-8") as stream:
        for line in stream:
            case = ReplayCase.model_validate_json(line)
            key = (case.case_id, case.corpus_tier)
            if key not in eligible:
                continue
            if case.partition not in _TRAINING or not case.training_eligible:
                raise ValueError(f"protected or ineligible case entered rerun: {key}")
            cases[key] = case
    if set(cases) != set(eligible):
        raise ValueError("replay bundle does not contain the full Mission 5 training cohort")

    configs_by_variant = {
        "original": (
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
        ),
        "upstream": (
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
        ),
    }
    totals: Counter[str] = Counter()
    old_classes: Counter[str] = Counter()
    recovered_by_class: Counter[str] = Counter()
    legacy_rejected_by_class: Counter[str] = Counter()
    residuals: Counter[str] = Counter()
    first_success: Counter[str] = Counter()
    address_added_counts: list[int] = []
    trajectory_lengths: list[int] = []
    per_case: list[dict[str, Any]] = []
    for key in sorted(eligible):
        case = cases[key]
        old = eligible[key]
        gold = benchmark_by_id[case.case_id]
        if gold.get("partition") != case.partition:
            raise ValueError(f"benchmark/replay partition drift: {key}")
        accepted = tuple(str(item) for item in gold.get("accepted_answers", ()))
        shape = str(gold.get("required_answer_shape", ""))
        original = state_from_replay(case)
        semantic = enrich_state_with_semantic_addresses(original, plane)
        repair = repair_state_with_typed_values(semantic.state)
        required_entities = {str(item) for item in gold.get("required_entity_ids", ())}
        variants = (("original", original), ("upstream", repair.state))
        possible_by_variant = {
            name: _goal_possible(
                verifier_eligible_claim_values(state), accepted, shape
            )
            for name, state in variants
        }
        entity_valid_by_variant = {
            name: required_entities.issubset(
                {
                    str(item)
                    for item in state.frame.get("candidate_entity_ids", ())
                }
            )
            for name, state in variants
        }
        results: list[tuple[str, Any]] = []
        reachable = False
        for variant_name, variant_state in variants:
            if (
                not possible_by_variant[variant_name]
                or not entity_valid_by_variant[variant_name]
            ):
                continue
            for config in configs_by_variant[variant_name]:
                result = search(
                    variant_state,
                    config,
                    accepted_answers=accepted,
                    allow_gold=True,
                )
                results.append((variant_name, result))
                if candidate_set_oracle(result, accepted):
                    reachable = True
                    first_success[f"{variant_name}:{config.kind}"] += 1
                    break
            if reachable:
                break
        possible = any(possible_by_variant.values())
        entity_valid = any(entity_valid_by_variant.values())
        verifier_reachable = any(
            candidate_set_oracle(result, accepted) for _name, result in results
        )
        verifier_attempts = sum(result.verifier_attempts for _name, result in results)
        verifier_rejections = sum(result.verifier_rejections for _name, result in results)
        trace_failure: ValueTraceFailure | None = None
        if key in value_replicas:
            trace_failure = qualify_value_trace(
                value_replicas[key], value_cases[case.case_id]
            ).failure
        old_reachable = bool(old.get("training_oracle_reachable"))
        old_class = str(old.get("failure_class", "UNKNOWN"))
        totals["eligible"] += 1
        totals["reachable"] += int(reachable)
        totals["old_reachable"] += int(old_reachable)
        totals["newly_recovered"] += int(reachable and not old_reachable)
        totals["legacy_reachable_rejected"] += int(old_reachable and not reachable)
        totals["entity_valid"] += int(entity_valid)
        totals["goal_possible"] += int(possible)
        totals["verifier_reachable"] += int(verifier_reachable)
        totals["address_enriched"] += int(bool(semantic.added_entity_ids))
        totals["value_claims_added"] += repair.added_claims
        totals["address_capacity_exhausted"] += int(semantic.candidate_capacity_exhausted)
        totals["value_capacity_exhausted"] += int(repair.candidate_capacity_exhausted)
        old_classes[old_class] += 1
        recovered_by_class[old_class] += int(reachable and not old_reachable)
        legacy_rejected_by_class[old_class] += int(old_reachable and not reachable)
        address_added_counts.append(len(semantic.added_entity_ids))
        successful_terminals = [
            terminal
            for _name, result in results
            for terminal in result.terminal_candidates
            if terminal.terminal == "ANSWER"
            and terminal.verifier_passed
            and canonical_answer_match(terminal.answer_values, accepted)
        ]
        if reachable and successful_terminals:
            trajectory_lengths.append(
                min(terminal.total_actions for terminal in successful_terminals)
            )
        residual_category = None
        if not reachable:
            residual_category = _residual_category(
                entity_valid=entity_valid,
                possible=possible,
                verifier_attempts=verifier_attempts,
                verifier_rejections=verifier_rejections,
                trace_failure=trace_failure,
            )
            residuals[residual_category] += 1
        state_sha256 = hashlib.sha256(
            json.dumps(
                repair.state.model_dump(mode="json"),
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        per_case.append(
            {
                "case_id": case.case_id,
                "corpus_tier": case.corpus_tier,
                "partition": case.partition,
                "old_failure_class": old_class,
                "old_reachable": old_reachable,
                "address_state_sha256": state_sha256,
                "added_entity_ids": list(semantic.added_entity_ids),
                "address_enriched_mentions": semantic.enriched_mentions,
                "added_value_claims": repair.added_claims,
                "entity_binding_valid": entity_valid,
                "canonical_goal_present": possible,
                "verifier_reachable": verifier_reachable,
                "reachable": reachable,
                "residual_category": residual_category,
                "value_trace_failure": trace_failure.value if trace_failure else None,
                "searches": [
                    {
                        "state_variant": variant_name,
                        "kind": result.search_kind,
                        "expansions": result.expansions,
                        "visited_states": result.visited_states,
                        "terminal_candidates": len(result.terminal_candidates),
                        "verifier_attempts": result.verifier_attempts,
                        "verifier_rejections": result.verifier_rejections,
                        "exhausted": result.exhausted,
                    }
                    for variant_name, result in results
                ],
                "state_variants": {
                    name: {
                        "entity_binding_valid": entity_valid_by_variant[name],
                        "canonical_goal_present": possible_by_variant[name],
                    }
                    for name, _state in variants
                },
            }
        )
    new_reachable = totals["reachable"]
    eligible_count = totals["eligible"]
    reachability = new_reachable / eligible_count
    legacy_carried_forward = totals["old_reachable"] + totals["newly_recovered"]
    decision = (
        "AETHERCORE_POLICY_FEASIBLE"
        if reachability > 0.60
        else "UPSTREAM_LIMIT_REMAINS_POLICY_SWEEP_BLOCKED"
    )
    sorted_lengths = sorted(trajectory_lengths)
    return {
        "schema_version": "aethercore.v11-upstream-reachability.v1",
        "status": "COMPLETE",
        "decision": decision,
        "source_identity": {
            "replay_bundle_sha256": manifest.bundle_sha256,
            "replay_cases_sha256": manifest.cases_sha256,
            "benchmark_sha256": _sha256(benchmark_path),
            "mission5_report_sha256": _sha256(mission5_report_path),
            "semantic_statistics_sha256": _sha256(statistics_path),
            "semantic_statistics_manifest_sha256": _sha256(statistics_manifest_path),
            "entity_hard_negatives_sha256": _sha256(hard_negatives_path),
            "value_diagnostic_sha256": _sha256(value_diagnostic_path),
        },
        "gold_policy": {
            "upstream_constructors_accept_gold": False,
            "gold_used_only_for_training_search_oracle_and_posthoc_decomposition": True,
            "partitions": sorted(_TRAINING),
            "evaluation_final_held_consumed": False,
        },
        "comparison": {
            "mission5_reachable": 260,
            "mission5_reachability": 260 / 695,
            "mission6_reachable": 306,
            "mission6_reachability": 306 / 695,
            "new_reachable": new_reachable,
            "new_reachability": reachability,
            "legacy_carried_forward_counterfactual_reachable": legacy_carried_forward,
            "legacy_carried_forward_counterfactual_reachability": (
                legacy_carried_forward / eligible_count
            ),
            "strict_policy_gate": 0.60,
            "minimum_reachable_to_exceed_gate": 418,
        },
        "baseline_revalidation": {
            "all_695_states_rerun": True,
            "legacy_reachable_rejected": totals["legacy_reachable_rejected"],
            "reason": (
                "the legacy search oracle found canonical values but the regenerated frame "
                "does not contain every required canonical entity address"
            ),
            "legacy_carried_forward_counterfactual_is_certified": False,
            "legacy_carried_forward_counterfactual_still_exceeds_gate": (
                legacy_carried_forward / eligible_count > 0.60
            ),
        },
        "counts": dict(sorted(totals.items())),
        "old_failure_class_counts": dict(sorted(old_classes.items())),
        "new_recovery_by_old_class": dict(sorted(recovered_by_class.items())),
        "legacy_rejection_by_old_class": dict(
            sorted(legacy_rejected_by_class.items())
        ),
        "residual_limitation": dict(sorted(residuals.items())),
        "first_success": dict(sorted(first_success.items())),
        "address_records": {
            "max_added_per_state": max(address_added_counts, default=0),
            "mean_added_per_state": (
                sum(address_added_counts) / len(address_added_counts)
                if address_added_counts
                else 0.0
            ),
            "max_addresses_per_mention": 8,
            "max_frame_entity_ids": 64,
            "uncertainty_preserved": True,
            "original_state_branch_preserved": True,
        },
        "trajectory_length_median": median(trajectory_lengths) if trajectory_lengths else 0,
        "trajectory_length_p95": (
            sorted_lengths[max(0, int(0.95 * len(sorted_lengths)) - 1)]
            if sorted_lengths
            else 0
        ),
        "search_limits": {
            "max_depth": max_depth,
            "max_expansions": max_expansions,
            "beam_width": beam_width,
            "argument_cap_by_state_variant": {"original": 32, "upstream": 64},
        },
        "per_case": per_case,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--mission5-report", type=Path, required=True)
    parser.add_argument("--semantic-statistics", type=Path, required=True)
    parser.add_argument("--semantic-manifest", type=Path, required=True)
    parser.add_argument("--entity-hard-negatives", type=Path, required=True)
    parser.add_argument("--value-diagnostic", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-depth", type=int, default=12)
    parser.add_argument("--max-expansions", type=int, default=5_000)
    parser.add_argument("--beam-width", type=int, default=64)
    args = parser.parse_args()
    result = rerun(
        args.bundle,
        args.benchmark,
        args.mission5_report,
        args.semantic_statistics,
        args.semantic_manifest,
        args.entity_hard_negatives,
        args.value_diagnostic,
        max_depth=args.max_depth,
        max_expansions=args.max_expansions,
        beam_width=args.beam_width,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "per_case"}))


if __name__ == "__main__":
    main()
