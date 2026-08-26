#!/usr/bin/env python3
"""Split-safe V15 sparse claim-context specialist qualification."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from aethersparse.controller.adaptive_policy import QuantizedAdaptivePolicy
from aethersparse.controller.claim_context_specialist import (
    SparseContextPolicy,
    fit_claim_context_specialist,
)
from aethersparse.controller.micro_ops import MicroAction, MicroState
from scripts.droid.v13_policy_qualify import _examples_and_records, _load_inputs
from scripts.droid.v14_controller_qualify import _evaluate, _selection_key, _summary


def _teacher_metrics(
    policy: SparseContextPolicy,
    trajectories: dict[tuple[str, str], tuple[tuple[MicroState, MicroAction], ...]],
    cases: dict[tuple[str, str], Any],
    partition: str,
) -> dict[str, Any]:
    selected = [
        item
        for key, trajectory in sorted(trajectories.items())
        if cases[key].partition == partition
        for item in trajectory
    ]
    correct = sum(policy.select(state, argument_cap=64) == target for state, target in selected)
    return {"correct": correct, "decisions": len(selected), "accuracy": correct / len(selected)}


def _residual(
    outcomes: list[dict[str, Any]],
    initials: dict[tuple[str, str], MicroState],
    benchmark: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    failed = [item for item in outcomes if not item["success"]]
    return {
        "count": len(failed),
        "unique_case_count": len({str(item["key"][0]) for item in failed}),
        "by_answer_shape": dict(
            sorted(
                Counter(
                    str(initials[tuple(item["key"])].frame.get("answer_shape", ""))
                    for item in failed
                ).items()
            )
        ),
        "by_category": dict(
            sorted(
                Counter(
                    str(category)
                    for item in failed
                    for category in benchmark[str(item["key"][0])].get("categories", ())
                ).items()
            )
        ),
        "case_ids_sha256_only": sorted(
            {
                __import__("hashlib").sha256(str(item["key"][0]).encode()).hexdigest()
                for item in failed
            }
        ),
    }


def qualify(
    *,
    bundle: Path,
    benchmark_path: Path,
    mission5_path: Path,
    base_policy_path: Path,
) -> tuple[dict[str, Any], SparseContextPolicy]:
    cases, benchmark, witnessed_keys, source_hashes = _load_inputs(
        bundle, benchmark_path, mission5_path
    )
    initials, trajectories, _records = _examples_and_records(cases, benchmark)
    if set(initials) != witnessed_keys:
        raise ValueError("V14 authenticated witness cohort changed")
    base = QuantizedAdaptivePolicy.model_validate_json(base_policy_path.read_text())
    base_outcomes = _evaluate(base, initials, cases, benchmark)
    if _selection_key(base_outcomes) != (138, 242):
        raise ValueError(f"frozen V14 baseline changed: {_selection_key(base_outcomes)}")
    development = [
        item
        for key, trajectory in sorted(trajectories.items())
        if cases[key].partition == "development"
        for item in trajectory
    ]
    candidates: list[tuple[SparseContextPolicy, list[dict[str, Any]]]] = []
    for epochs in (1, 2, 4, 8, 16, 32):
        specialist = fit_claim_context_specialist(development, epochs=epochs)
        policy = SparseContextPolicy(base=base, specialist=specialist)
        candidates.append((policy, _evaluate(policy, initials, cases, benchmark)))
    best_candidate, candidate_outcomes = max(
        candidates, key=lambda item: _selection_key(item[1])
    )
    selected_key = _selection_key(candidate_outcomes)
    selected_status = (
        "SELECTED_SPARSE_CONTEXT_SPECIALIST"
        if selected_key[0] >= 138 and selected_key[1] > 242
        else "REJECTED_NO_CAPABILITY_BYTE_GAIN"
    )
    selected_outcomes = (
        candidate_outcomes
        if selected_status == "SELECTED_SPARSE_CONTEXT_SPECIALIST"
        else base_outcomes
    )
    candidate_metrics = {
        str(policy.specialist.training_epochs): {
            "tuning_success": _selection_key(candidate_outcomes)[0],
            "total_success": _selection_key(candidate_outcomes)[1],
            "residual": 260 - _selection_key(candidate_outcomes)[1],
        }
        for policy, candidate_outcomes in candidates
    }
    report = {
        "schema_version": "aethercore.v15-specialist-capacity-qualification.v1",
        "status": selected_status,
        "scope": {
            "frozen_v14_controller": True,
            "semantic_address_changed": False,
            "operation_grammar_changed": False,
            "verifier_changed": False,
            "fit_partitions": ["development"],
            "selection_partition": "tuning",
            "sealed_partitions_loaded_or_used": 0,
            "authenticated_states": len(initials),
            "development_states": sum(cases[key].partition == "development" for key in initials),
            "tuning_states": sum(cases[key].partition == "tuning" for key in initials),
        },
        "v14_baseline": {
            "architecture": "1292-int8-parameter COG legal-mask structured perceptron",
            "autonomous": _summary(base_outcomes),
            "residual": _residual(base_outcomes, initials, benchmark),
        },
        "candidate": {
            "architecture": "three-head shared passage-context legal-argument specialist",
            "authority": (
                "may rank legal SELECT_CLAIM arguments only after frozen V14 "
                "selects SELECT_CLAIM"
            ),
            "feature_count": len(best_candidate.specialist.feature_names),
            "head_count": len(best_candidate.specialist.head_names),
            "specialist_stored_parameters": best_candidate.specialist.parameter_count,
            "specialist_resident_parameters": best_candidate.specialist.parameter_count,
            "specialist_active_parameters_per_claim_decision": len(
                best_candidate.specialist.feature_names
            ),
            "combined_stored_parameters": best_candidate.stored_parameter_count,
            "combined_active_parameters_per_claim_decision": best_candidate.active_parameter_count,
            "specialist_weight_representation": "int8",
            "activation_representation": "signed fixed-point integer / 256",
            "best_candidate_epochs": best_candidate.specialist.training_epochs,
            "candidate_epoch_metrics": candidate_metrics,
            "teacher_next_action": {
                partition: _teacher_metrics(best_candidate, trajectories, cases, partition)
                for partition in ("development", "tuning")
            },
            "autonomous": _summary(candidate_outcomes),
            "residual": _residual(candidate_outcomes, initials, benchmark),
        },
        "selected": {
            "architecture": (
                "frozen V14 1292-int8-parameter COG legal-mask structured perceptron"
                if selected_status.startswith("REJECTED")
                else "frozen V14 controller plus 54-int8-parameter claim-context specialist"
            ),
            "autonomous": _summary(selected_outcomes),
            "residual": _residual(selected_outcomes, initials, benchmark),
        },
        "capacity_ladder_disposition": {
            "4k_run": False,
            "16k_run": False,
            "64k_run": False,
            "256k_run": False,
            "reason": (
                "The measured hypothesis is one 18-feature local-context family. "
                "Escalation is prohibited unless this 54-byte branch demonstrates a positive "
                "capability/byte result; unused dense capacity is not an experiment."
            ),
        },
        "deferred_architecture_disposition": {
            "shared_recurrent_core_1_2_4_8": "UNTESTED_NOT_AUTHORIZED_NO_PRIOR_STATE_LOSS",
            "adaptive_depth": "DEFERRED_EXPLICIT_COG_HAS_NO_TEMPORAL_LOSS_SIGNAL",
            "early_halt": "KEEP_DETERMINISTIC_COG_VERIFIER_GATE",
            "cognitive_lookup_memory": "UNTESTED_DEFER_UNTIL_LEAKAGE_SAFE_MEMORY_CONTRACT",
            "factorized_bilinear": "UNTESTED_NOT_JUSTIFIED_BEFORE_NARROW_CONTEXT_HEAD",
            "dagger": "TESTED_REJECTED_DO_NOT_REPEAT_243_STATES_231_OF_260",
        },
        "cross_source_policy": {
            "mandatory_reads_added": False,
            "conditional_only": [
                "conflict",
                "uncertainty",
                "high-risk ambiguity",
                "explicit verification request",
            ],
        },
        "source_identity": source_hashes,
    }
    return report, best_candidate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--mission5-report", type=Path, required=True)
    parser.add_argument("--base-policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policy-output", type=Path, required=True)
    args = parser.parse_args()
    report, policy = qualify(
        bundle=args.bundle,
        benchmark_path=args.benchmark,
        mission5_path=args.mission5_report,
        base_policy_path=args.base_policy,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    args.policy_output.write_text(
        json.dumps(policy.specialist.model_dump(mode="json"), separators=(",", ":"), sort_keys=True)
        + "\n"
    )
    print(json.dumps({"status": report["status"], "candidate": report["candidate"]}))


if __name__ == "__main__":
    main()
