#!/usr/bin/env python3
"""Build the sealed integrated Mission 6 architecture registry."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from aethersparse.observer.models import ActivationCost, ArchitectureModule, ArchitectureRegistry
from aethersparse.observer.registry import write_registry


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_registry(repository: Path) -> ArchitectureRegistry:
    p4 = json.loads(
        (repository / "reports/droid/v11/upstream-p4-cost-qualification.json").read_text(
            encoding="utf-8"
        )
    )
    return ArchitectureRegistry(
        architecture_id="aethercore-v11-semantic-address-work-checkpoint",
        architecture_version="11.0.0-work.2",
        modules=(
            ArchitectureModule(
                module_id="aethercore.exact-controller",
                module_version="10.0.0",
                purpose="Exact micro-operations and deterministic truth invariants",
                inputs=("query_frame", "structured_claim", "exact_source_span"),
                outputs=("verified_answer", "clarification", "abstention"),
                parameter_count=0,
                quantization="none",
                activation_cost=ActivationCost(
                    integer_ops=0, macs=0, memory_bytes=0, scratch_ram_bytes=0
                ),
                supported_state_types=(
                    "query_frame",
                    "structured_claim",
                    "exact_source_span",
                    "verification_report",
                ),
                dependencies=(),
                model_hash=_sha256(
                    repository / "src/aethersparse/controller/reachability.py"
                ),
                calibration_artifact=None,
                known_failure_clusters=(
                    "ENTITY_BINDING_WRONG",
                    "VALUE_NOT_ENUMERATED",
                ),
                status="active",
            ),
            ArchitectureModule(
                module_id="aethercore.semantic-address-plane",
                module_version="11.0.0",
                purpose=(
                    "Preserve bounded occurrence-backed canonical entity-address "
                    "distributions"
                ),
                inputs=("query_frame", "anchor_occurrence_statistics"),
                outputs=("semantic_address_distribution", "bounded_entity_candidates"),
                parameter_count=0,
                quantization="none",
                activation_cost=ActivationCost(
                    integer_ops=0,
                    macs=0,
                    memory_bytes=136_164,
                    scratch_ram_bytes=1_536,
                ),
                supported_state_types=(
                    "entity_mention",
                    "entity_distribution",
                    "query_frame",
                ),
                dependencies=("aethercore.exact-controller",),
                model_hash=_sha256(
                    repository / "src/aethersparse/controller/semantic_state.py"
                ),
                calibration_artifact=(
                    "reports/droid/v11/semantic-address-plane-qualification.json"
                ),
                known_failure_clusters=(
                    "missing_mention_alignment",
                    "missing_pre_cap_candidates",
                    "missing_25k_397k_occurrence_statistics",
                ),
                status="active",
            ),
            ArchitectureModule(
                module_id="aethercore.value-exact-scan",
                module_version="11.0.0",
                purpose="Bounded typed value hypotheses copied from retained exact spans",
                inputs=("query_frame", "exact_source_span", "structured_claim"),
                outputs=("typed_value_lattice", "source_bound_claim_hypothesis"),
                parameter_count=0,
                quantization="none",
                activation_cost=ActivationCost(
                    integer_ops=math.ceil(float(p4["integer_operations_p95"])),
                    macs=0,
                    memory_bytes=math.ceil(float(p4["source_bytes_p95"])),
                    scratch_ram_bytes=int(p4["peak_workspace_ram_bytes"]),
                ),
                supported_state_types=(
                    "date",
                    "quantity",
                    "comparison",
                    "quotation",
                ),
                dependencies=(
                    "aethercore.exact-controller",
                    "aethercore.semantic-address-plane",
                ),
                model_hash=_sha256(
                    repository / "src/aethersparse/controller/value_repair.py"
                ),
                calibration_artifact="reports/droid/v11/upstream-reachability.json",
                known_failure_clusters=(
                    "VALUE_NOT_ENUMERATED",
                    "quotation_without_development_support",
                    "missing_pre_pruning_evidence",
                ),
                status="active",
            ),
            ArchitectureModule(
                module_id="aethercore.entity-linear-baseline",
                module_version="11.0.0",
                purpose="Development-fitted candidate relevance calibration baseline",
                inputs=("bounded_entity_candidates", "retained_candidate_scores"),
                outputs=("candidate_relevance_probability",),
                parameter_count=9,
                quantization="not_qualified",
                activation_cost=ActivationCost(
                    integer_ops=9, macs=9, memory_bytes=72, scratch_ram_bytes=128
                ),
                supported_state_types=("entity_candidate",),
                dependencies=("aethercore.exact-controller",),
                model_hash=_sha256(
                    repository / "reports/droid/v11/entity-specialist-baselines.json"
                ),
                calibration_artifact=(
                    "reports/droid/v11/entity-specialist-baselines.json"
                ),
                known_failure_clusters=(
                    "correct_entity_not_generated",
                    "missing_mention_alignment",
                    "missing_anchor_occurrence_statistics",
                ),
                status="inactive",
            ),
            ArchitectureModule(
                module_id="aethercore.probabilistic-fusion",
                module_version="11.0.0",
                purpose="Preserve and fuse specialist uncertainty distributions",
                inputs=("categorical_belief", "expert_update", "reliability_precision"),
                outputs=("posterior_belief", "expert_disagreement"),
                parameter_count=0,
                quantization="not_selected",
                activation_cost=ActivationCost(
                    integer_ops=0, macs=0, memory_bytes=0, scratch_ram_bytes=0
                ),
                supported_state_types=(
                    "entity_distribution",
                    "relation_distribution",
                    "answer_shape_distribution",
                    "value_distribution",
                ),
                dependencies=("aethercore.exact-controller",),
                model_hash=_sha256(repository / "src/aethersparse/specialists/fusion.py"),
                calibration_artifact=(
                    "reports/droid/v11/workspace-fusion-ablation.json"
                ),
                known_failure_clusters=(
                    "unfitted_reliability",
                    "confident_conflict_detection_incomplete",
                ),
                status="inactive",
            ),
            ArchitectureModule(
                module_id="aethercore.adaptive-depth",
                module_version="11.0.0",
                purpose="Bounded specialist routing by expected value of computation",
                inputs=("shared_workspace", "pre_outcome_uncertainty", "compute_budget"),
                outputs=("parallel_specialist_groups", "halt_decision"),
                parameter_count=0,
                quantization="not_trained",
                activation_cost=ActivationCost(
                    integer_ops=0, macs=0, memory_bytes=0, scratch_ram_bytes=0
                ),
                supported_state_types=("shared_workspace", "route_decision"),
                dependencies=(
                    "aethercore.probabilistic-fusion",
                    "aethercore.value-exact-scan",
                ),
                model_hash=_sha256(repository / "src/aethersparse/specialists/gating.py"),
                calibration_artifact="reports/droid/v11/depth-data-audit.json",
                known_failure_clusters=(
                    "counterfactual_cycle_labels_absent",
                    "workspace_cycle_checkpoints_absent",
                ),
                status="inactive",
            ),
            ArchitectureModule(
                module_id="aethercore.research-observer",
                module_version="11.0.0",
                purpose="Optional training/research telemetry and causal diagnosis",
                inputs=("completed_cycle_telemetry", "completed_case_outcome"),
                outputs=(
                    "sampled_telemetry",
                    "route_analysis",
                    "optimization_proposal",
                ),
                parameter_count=0,
                quantization="not_applicable",
                activation_cost=ActivationCost(
                    integer_ops=0, macs=0, memory_bytes=0, scratch_ram_bytes=0
                ),
                supported_state_types=(
                    "telemetry_record",
                    "counterfactual_record",
                    "architecture_registry",
                ),
                dependencies=("aethercore.exact-controller",),
                model_hash=_sha256(repository / "src/aethersparse/observer/models.py"),
                calibration_artifact="reports/droid/v11/observer-qualification.json",
                known_failure_clusters=(
                    "observer_not_enabled",
                    "selected_activation_unavailable",
                ),
                status="training_only",
            ),
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    registry = write_registry(args.output, build_registry(args.repository.resolve()))
    print(json.dumps(registry.model_dump(mode="json"), sort_keys=True))


if __name__ == "__main__":
    main()
