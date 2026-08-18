#!/usr/bin/env python3
"""Build the Mission 7 architecture registry without activating unqualified v12 paths."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from aethersparse.observer.models import (
    ActivationCost,
    ArchitectureModule,
    ArchitectureRegistry,
)
from aethersparse.observer.registry import load_registry, write_registry


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_registry(repository: Path) -> ArchitectureRegistry:
    previous = load_registry(
        repository / "config/architecture/aethercore-v11-integrated.registry.json"
    )
    additions = (
        ArchitectureModule(
            module_id="aethercore.exact-address-fst-v2",
            module_version="12.0.0-work.1",
            purpose="Immutable canonical surface-to-entity postings with explicit priors",
            inputs=("verified_address_bundle", "copied_mention_span"),
            outputs=("exact_address_proposals", "unresolved_mass"),
            parameter_count=0,
            quantization="lossless_integer_support_ratio",
            activation_cost=ActivationCost(
                integer_ops=0,
                macs=0,
                memory_bytes=1_542_711,
                scratch_ram_bytes=0,
            ),
            supported_state_types=("entity_mention", "entity_distribution"),
            dependencies=("aethercore.exact-controller",),
            model_hash=_sha256(repository / "src/aethersparse/addressing/exact.py"),
            calibration_artifact="reports/droid/v12/fst-prior-qualification.json",
            known_failure_clusters=(
                "full_corpus_bundle_absent",
                "measured_title_proxy_uses_noncanonical_diagnostic_ids",
                "runtime_operation_cost_unqualified",
            ),
            status="inactive",
        ),
        ArchitectureModule(
            module_id="aethercore.fuzzy-address-v2",
            module_version="12.0.0-work.1",
            purpose="Fuzzy-normalized exact and character n-gram address proposals",
            inputs=("verified_address_bundle", "copied_mention_span"),
            outputs=("fuzzy_address_proposals", "unresolved_mass"),
            parameter_count=0,
            quantization="none",
            activation_cost=ActivationCost(
                integer_ops=808_153,
                macs=0,
                memory_bytes=7_151_500,
                scratch_ram_bytes=43_905,
            ),
            supported_state_types=("entity_mention", "entity_distribution"),
            dependencies=("aethercore.exact-controller",),
            model_hash=_sha256(
                repository / "src/aethersparse/controller/fuzzy_address.py"
            ),
            calibration_artifact="reports/droid/v12/fuzzy-address-qualification.json",
            known_failure_clusters=(
                "full_corpus_bundle_absent",
                "title_proxy_not_mention_aligned",
                "physical_external_io_unqualified",
            ),
            status="inactive",
        ),
        ArchitectureModule(
            module_id="aethercore.semantic-ann-v2",
            module_version="12.0.0-work.1",
            purpose="Contextual semantic address proposals from hyperlink supervision",
            inputs=("verified_hyperlink_supervision", "mention_context"),
            outputs=("semantic_address_proposals",),
            parameter_count=0,
            quantization="not_selected",
            activation_cost=ActivationCost(
                integer_ops=0, macs=0, memory_bytes=0, scratch_ram_bytes=0
            ),
            supported_state_types=("entity_mention", "entity_distribution"),
            dependencies=("aethercore.exact-controller",),
            model_hash=_sha256(
                repository / "src/aethersparse/addressing/semantic_ann.py"
            ),
            calibration_artifact=(
                "reports/droid/v12/semantic-encoder-ann-ablation.json"
            ),
            known_failure_clusters=(
                "hyperlink_supervision_absent",
                "learned_training_not_run",
                "p4_cost_unqualified",
            ),
            status="inactive",
        ),
        ArchitectureModule(
            module_id="aethercore.semantic-address-plane-v2",
            module_version="12.0.0-work.1",
            purpose="Canonical union-before-cap and calibrated entity/unresolved belief",
            inputs=(
                "exact_address_proposals",
                "fuzzy_address_proposals",
                "semantic_address_proposals",
            ),
            outputs=("semantic_address_distribution", "bounded_entity_candidates"),
            parameter_count=0,
            quantization="none",
            activation_cost=ActivationCost(
                integer_ops=0, macs=0, memory_bytes=0, scratch_ram_bytes=0
            ),
            supported_state_types=("entity_mention", "entity_distribution"),
            dependencies=(
                "aethercore.exact-address-fst-v2",
                "aethercore.fuzzy-address-v2",
                "aethercore.semantic-ann-v2",
            ),
            model_hash=_sha256(
                repository / "src/aethersparse/controller/address_fusion.py"
            ),
            calibration_artifact="reports/droid/v12/address-fusion-qualification.json",
            known_failure_clusters=(
                "integrated_mention_alignment_absent",
                "verified_pre_cap_channel_capture_absent",
                "address_calibration_not_fitted",
            ),
            status="inactive",
        ),
        ArchitectureModule(
            module_id="aethercore.contextual-address-specialist-v2",
            module_version="12.0.0-work.1",
            purpose="Resolve ambiguity only after candidate-generation readiness",
            inputs=("semantic_address_distribution", "mention_context"),
            outputs=("specialist_entity_update",),
            parameter_count=0,
            quantization="not_trained",
            activation_cost=ActivationCost(
                integer_ops=0, macs=0, memory_bytes=0, scratch_ram_bytes=0
            ),
            supported_state_types=("entity_distribution",),
            dependencies=("aethercore.semantic-address-plane-v2",),
            model_hash=_sha256(
                repository / "src/aethersparse/controller/address_fusion.py"
            ),
            calibration_artifact="reports/droid/v12/address-fusion-qualification.json",
            known_failure_clusters=(
                "candidate_generation_gate_closed",
                "zero_lawful_training_examples",
                "successive_halving_not_run",
            ),
            status="inactive",
        ),
        ArchitectureModule(
            module_id="aethercore.claim-address-direct",
            module_version="12.0.0-work.1",
            purpose="Exact bounded entity-relation-type claim and source-region lookup",
            inputs=(
                "canonical_entity_id",
                "relation_address",
                "typed_answer_shape",
                "exact_source_region",
            ),
            outputs=("bounded_source_bound_claims", "typed_value_lattice"),
            parameter_count=0,
            quantization="none",
            activation_cost=ActivationCost(
                integer_ops=6_813,
                macs=0,
                memory_bytes=172_032,
                scratch_ram_bytes=6_144,
            ),
            supported_state_types=(
                "query_frame",
                "structured_claim",
                "exact_source_span",
                "typed_value_lattice",
            ),
            dependencies=(
                "aethercore.exact-controller",
                "aethercore.semantic-address-plane-v2",
            ),
            model_hash=_sha256(
                repository / "src/aethersparse/controller/claim_address.py"
            ),
            calibration_artifact=(
                "reports/droid/v12/claim-address-p4-qualification.json"
            ),
            known_failure_clusters=(
                "address_union_not_qualified",
                "direct_recall_below_repaired_v11_claim_pool",
                "nominal_p95_above_target_under_v11_reference_assumptions",
            ),
            status="inactive",
        ),
    )
    return ArchitectureRegistry(
        architecture_id="aethercore-v12-semantic-address-v2",
        architecture_version="12.0.0-work.1",
        modules=previous.modules + additions,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    write_registry(
        args.output,
        build_registry(args.repository.resolve()),
    )


if __name__ == "__main__":
    main()
