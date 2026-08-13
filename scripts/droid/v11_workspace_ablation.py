#!/usr/bin/env python3
"""Run label-free v11 fusion ablations on authenticated replay candidates.

This runner deliberately consumes no benchmark answers or replay outcomes. It
measures uncertainty, disagreement, top-choice stability, and a controlled
confident-conflict stress test on development/tuning states only.
"""

from __future__ import annotations

import argparse
import gzip
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from aethersparse.controller.replay import ReplayCase, verify_replay_bundle
from aethersparse.specialists.fusion import (
    BeliefFusion,
    FusionMethod,
    LearnedFusionParameters,
)
from aethersparse.specialists.workspace import (
    BeliefSlot,
    CategoricalBelief,
    ComputeBudget,
    ExpertUpdate,
    SharedWorkspace,
)

_ALLOWED_PARTITIONS = frozenset({"development", "tuning"})


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _candidate_updates(
    mention: dict[str, Any],
) -> tuple[CategoricalBelief, tuple[ExpertUpdate, ...]] | None:
    raw_candidates = mention.get("candidates")
    if not isinstance(raw_candidates, list) or len(raw_candidates) < 2:
        return None
    candidates = tuple(item for item in raw_candidates if isinstance(item, dict))
    labels = tuple(str(item.get("entity_id", "")) for item in candidates)
    if len(candidates) < 2 or any(not label for label in labels) or len(set(labels)) != len(labels):
        return None
    prior = CategoricalBelief.normalized(
        labels, tuple(max(0.0, float(item.get("confidence", 0.0))) for item in candidates)
    )
    features = (
        ("entity.name", "name_score"),
        ("entity.type", "type_score"),
        ("entity.relation", "relation_score"),
        ("entity.context", "context_score"),
    )
    updates = tuple(
        ExpertUpdate(
            expert_id=expert_id,
            target=BeliefSlot.ENTITY,
            distribution=CategoricalBelief.normalized(
                labels,
                tuple(max(0.0, float(item.get(field, 0.0))) for item in candidates),
            ),
            reliability_precision=1.0,
            gate_probability=1.0,
        )
        for expert_id, field in features
    )
    return prior, updates


def _richest_frame(case: ReplayCase) -> dict[str, Any]:
    frames = [decision.query_frame for decision in case.decisions if decision.query_frame]
    return max(
        frames,
        key=lambda frame: (
            sum(
                len(mention.get("candidates", ()))
                for mention in frame.get("entity_mentions", ())
                if isinstance(mention, dict)
            ),
            len(frame),
        ),
        default={},
    )


def _workspace(prior: CategoricalBelief) -> SharedWorkspace:
    return SharedWorkspace(
        entity_distribution=prior,
        evidence_sufficiency=0.0,
        compute_budget=ComputeBudget(
            active_macs_remaining=1_000_000,
            read_operations_remaining=16,
            cycles_remaining=6,
        ),
    )


def _conflict_update(prior: CategoricalBelief) -> ExpertUpdate:
    order = tuple(reversed(prior.probabilities))
    peaked = tuple(value**8 for value in order)
    return ExpertUpdate(
        expert_id="controlled.confident_conflict",
        target=BeliefSlot.ENTITY,
        distribution=CategoricalBelief.normalized(prior.labels, peaked),
        reliability_precision=8.0,
        gate_probability=1.0,
    )


def run(bundle: Path) -> dict[str, Any]:
    manifest = verify_replay_bundle(bundle)
    methods = tuple(FusionMethod)
    metrics: dict[FusionMethod, dict[str, list[float]]] = {
        method: defaultdict(list) for method in methods
    }
    partitions: Counter[str] = Counter()
    cases_seen = 0
    mentions_seen = 0
    with gzip.open(bundle / manifest.cases_file, "rt", encoding="utf-8") as handle:
        for line in handle:
            case = ReplayCase.model_validate_json(line)
            if case.partition not in _ALLOWED_PARTITIONS:
                continue
            if not case.training_eligible:
                raise ValueError(f"non-training record entered v11 fusion ablation: {case.case_id}")
            cases_seen += 1
            partitions[case.partition] += 1
            frame = _richest_frame(case)
            mentions = frame.get("entity_mentions", ())
            if not isinstance(mentions, list):
                continue
            for mention in mentions:
                if not isinstance(mention, dict):
                    continue
                prepared = _candidate_updates(mention)
                if prepared is None:
                    continue
                prior, updates = prepared
                mentions_seen += 1
                for method in methods:
                    learned = LearnedFusionParameters() if method == FusionMethod.LEARNED else None
                    outcome = BeliefFusion(method, learned=learned).fuse(
                        _workspace(prior), BeliefSlot.ENTITY, updates
                    )
                    method_metrics = metrics[method]
                    method_metrics["prior_entropy"].append(prior.normalized_entropy)
                    method_metrics["posterior_entropy"].append(
                        outcome.posterior.normalized_entropy
                    )
                    method_metrics["posterior_top_probability"].append(
                        outcome.posterior.top_probability
                    )
                    method_metrics["top_choice_changed"].append(
                        float(outcome.posterior.top_label != prior.top_label)
                    )
                    method_metrics["expert_disagreement"].append(
                        outcome.disagreement.aggregate
                    )
                    stressed = BeliefFusion(method, learned=learned).fuse(
                        _workspace(prior),
                        BeliefSlot.ENTITY,
                        (*updates, _conflict_update(prior)),
                    )
                    method_metrics["conflict_detected"].append(
                        float(stressed.disagreement.confidence_contradiction >= 0.5)
                    )
                    method_metrics["conflict_top_probability"].append(
                        stressed.posterior.top_probability
                    )
    if mentions_seen == 0:
        raise ValueError("replay contains no multi-candidate entity mentions")
    result_methods: dict[str, dict[str, float | int | str]] = {}
    for method in methods:
        values = metrics[method]
        result_methods[method] = {
            "sample_count": mentions_seen,
            "mean_prior_normalized_entropy": _mean(values["prior_entropy"]),
            "mean_posterior_normalized_entropy": _mean(values["posterior_entropy"]),
            "mean_posterior_top_probability": _mean(values["posterior_top_probability"]),
            "top_choice_change_fraction": _mean(values["top_choice_changed"]),
            "mean_expert_disagreement": _mean(values["expert_disagreement"]),
            "controlled_conflict_detection_fraction": _mean(values["conflict_detected"]),
            "controlled_conflict_mean_top_probability": _mean(
                values["conflict_top_probability"]
            ),
            "selection_status": (
                "UNFITTED_NEUTRAL_PARAMETERS"
                if method == FusionMethod.LEARNED
                else "LABEL_FREE_ABLATION_ONLY"
            ),
        }
    return {
        "schema_version": "aethercore.workspace-fusion-ablation.v1",
        "replay_bundle_sha256": manifest.bundle_sha256,
        "partitions": dict(sorted(partitions.items())),
        "cases_seen": cases_seen,
        "multi_candidate_mentions": mentions_seen,
        "gold_or_outcome_fields_consumed": False,
        "protected_partitions_consumed": False,
        "methods": result_methods,
        "limitations": [
            "This label-free lane measures uncertainty behavior, not semantic correctness.",
            "The learned fusion parameters are neutral and must be fitted on development "
            "then selected on tuning.",
            "The confident-conflict result is a controlled stress test over real candidate sets.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.bundle)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
