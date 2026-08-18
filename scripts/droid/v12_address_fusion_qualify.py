#!/usr/bin/env python3
"""Qualify the v12 address-fusion interface against the lawful v11 evidence boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from aethersparse.controller.address_fusion import (
    assess_specialist_readiness,
    plan_successive_halving,
)

BASE_COMMIT = "a7dcb187a985164648549eb18f67a7a6a4a964c6"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def qualify(
    semantic_path: Path,
    readiness_path: Path,
    baseline_path: Path,
) -> dict[str, Any]:
    semantic = _load(semantic_path)
    readiness_v11 = _load(readiness_path)
    baseline = _load(baseline_path)
    if semantic.get("schema_version") != "aethersparse.semantic-address-plane-qualification.v1":
        raise ValueError("unexpected Semantic Address v1 qualification schema")
    if readiness_v11.get("schema_version") != "aethercore.specialist-readiness.v1":
        raise ValueError("unexpected specialist-readiness schema")
    if baseline.get("schema_version") != "aethersparse.entity-specialist-baselines.v11":
        raise ValueError("unexpected entity baseline schema")

    split = semantic["integrity"]["split_audit"]
    partitions = set(split["partitions_present"])
    if partitions != {"development", "tuning"}:
        raise ValueError("address evidence must contain development and tuning only")
    if readiness_v11["protected_partition_labels_consumed"]:
        raise ValueError("v11 readiness consumed protected labels")
    if readiness_v11["integrity"]["evaluation_and_final_held_used"]:
        raise ValueError("protected partition use is forbidden")

    coverage = semantic["candidate_address_coverage"]
    tuning_replicas = int(split["partition_counts"]["tuning"]["replicas"])
    retained_complete = sum(
        int(row["current_complete"])
        for key, row in coverage["by_partition_and_tier"].items()
        if key.startswith("tuning:")
    )
    union_complete = sum(
        int(row["address_augmented_complete"])
        for key, row in coverage["by_partition_and_tier"].items()
        if key.startswith("tuning:")
    )
    if (retained_complete, union_complete, tuning_replicas) != (37, 39, 193):
        raise ValueError("v11 tuning candidate-completeness baseline changed")

    readiness = assess_specialist_readiness(
        None,
        unavailable_reasons=(
            "v11_aggregate_is_not_a_hashed_v12_tuning_qualification",
            "verified_pre_cap_capture_manifest_unavailable",
            "verified_mention_alignment_manifest_unavailable",
            "verified_source_manifest_unavailable",
        ),
    )
    plan = plan_successive_halving(readiness)
    linear = baseline["development_fitted_linear_reranker"]["partitions"]["tuning"]
    return {
        "schema_version": "aethersparse.address-fusion-qualification.v12",
        "base_commit": BASE_COMMIT,
        "decision": readiness.decision.value,
        "truth_boundary": {
            "fit_partition": "development",
            "calibration_selection_partition": "tuning",
            "sealed_partitions_consumed": [],
            "evaluation_final_held_labels_used": False,
            "authenticated_replay_used": False,
            "candidate_diagnostic_397k_used": False,
            "why_diagnostic_excluded": (
                "post-cap evidence candidates lack entity-channel and pre-cap provenance"
            ),
        },
        "source_artifacts": {
            str(semantic_path): _sha256(semantic_path),
            str(readiness_path): _sha256(readiness_path),
            str(baseline_path): _sha256(baseline_path),
        },
        "implemented_contract": {
            "canonical_union_before_global_cap": True,
            "canonical_corpus_id_syntax_and_title_hash_authority": True,
            "retained_pruned_entity_ids_disjoint": True,
            "complete_pruned_candidate_sidecar": True,
            "versioned_persisted_union_envelope": ("aethersparse.address-union-envelope.v12"),
            "versioned_persisted_belief_envelope": ("aethersparse.address-belief-envelope.v12"),
            "retained_features": [
                "mention_hypothesis",
                "channel_provenance",
                "channel_and_global_pre_cap_ranks",
                "source_subchannel_and_source_record_id",
                "raw_score_bounded_score_and_transform",
                "exact_fuzzy_semantic_scores",
                "anchor_prior_support_source_diversity",
                "title_redirect_alias_indicators",
                "type_relation_context_scores",
                "ambiguity_entropy",
                "unresolved_mass",
                "channel_generated_emitted_counts",
                "channel_cap_and_pre_cap_completeness",
                "source_artifact_bundle_sha256_and_schema_version",
            ],
            "temporary_k_values": [8, 16, 32, 64],
            "belief_labels": "P(E1..EN, UNRESOLVED)",
            "observer_contract": "aethercore.observer.v1",
            "specialist_readiness_evidence": (
                "hashed tuning AddressQualification bound to verified pre-cap and "
                "mention-alignment and source manifests"
            ),
        },
        "current_lawful_measurement": {
            "scope": "v11 retained entity candidates plus 10k aggregate anchor overlay",
            "tuning_replicas": tuning_replicas,
            "candidate_completeness": {
                "retained_post_cap_at_most_8": {
                    "complete": retained_complete,
                    "rate": retained_complete / tuning_replicas,
                },
                "retained_plus_10k_overlay_at_most_16_per_mention_proxy": {
                    "complete": union_complete,
                    "rate": union_complete / tuning_replicas,
                },
                "k32": None,
                "k64": None,
            },
            "mention_aligned_entity_recall": {
                "at8": None,
                "at16": None,
                "at32": None,
                "at64": None,
            },
            "multi_entity_completeness": {
                "note": "case-level required-set proxy only; mention alignment is absent",
                "retained_at_most_8": retained_complete / tuning_replicas,
                "v1_union_at_most_16_per_mention": union_complete / tuning_replicas,
            },
            "readiness_authorization_use": (
                "none; legacy post-cap aggregate is reported only and cannot open the gate"
            ),
            "channel_availability": {
                "retained": "post-cap only",
                "anchor_prior": "10k aggregate overlay only",
                "fst_title_alias_redirect": "awaiting lane B",
                "fuzzy": "awaiting lane C",
                "semantic_ann": "awaiting lanes D/E",
                "pre_cap_provenance": "unavailable in current evidence",
            },
        },
        "calibration": {
            "v12_address_distribution_fitted": False,
            "reason": "zero mention-aligned development labels and incomplete candidate generation",
            "v11_weak_case_level_candidate_relevance_reference": {
                "nll": linear["candidate_relevance_calibration"]["nll"],
                "brier": linear["candidate_relevance_calibration"]["brier"],
                "ece_10": linear["candidate_relevance_calibration"]["ece_10"],
                "warning": "not a calibrated P(entity|mention,context) result",
            },
            "implemented_metrics": [
                "availability_state_multiclass_nll",
                "availability_state_multiclass_brier",
                "availability_state_ece_10",
                "resolved_address_ece_10",
                "resolved_address_coverage-indexed_selective_risk",
                "normalized_entropy",
                "channel_disagreement",
            ],
            "metric_scopes": {
                "availability_state": (
                    "P(E1..EN, UNRESOLVED); an unavailable target is labelled UNRESOLVED"
                ),
                "resolved_address": (
                    "entity predictions only; UNRESOLVED is abstention and reduces coverage "
                    "against all examples"
                ),
            },
            "fixed_confidence_threshold_selected": False,
        },
        "readiness": readiness.model_dump(mode="json"),
        "successive_halving": plan.model_dump(mode="json"),
        "blockers": [
            "full v12 channel outputs are not yet integrated",
            "mention-aligned development/tuning examples are absent in this lane",
            "v11 tuning candidate-completeness proxy is 39/193 (20.2073%)",
            "K=32/64 and recall@16 cannot be inferred from post-cap v11 evidence",
            "no hashed v12 tuning qualification or verified pre-cap/alignment/source "
            "manifests exist",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--semantic",
        type=Path,
        default=Path("reports/droid/v11/semantic-address-plane-qualification.json"),
    )
    parser.add_argument(
        "--readiness",
        type=Path,
        default=Path("reports/droid/v11/specialist-readiness.json"),
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path("reports/droid/v11/entity-specialist-baselines.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/droid/v12/address-fusion-qualification.json"),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("reports/droid/v12/address-fusion-qualification.manifest.json"),
    )
    args = parser.parse_args()
    report = qualify(args.semantic, args.readiness, args.baseline)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
    args.output.write_bytes(payload)
    manifest = {
        "schema_version": "aethersparse.address-fusion-qualification-manifest.v12",
        "output_file": args.output.name,
        "output_bytes": len(payload),
        "output_sha256": hashlib.sha256(payload).hexdigest(),
        "base_commit": BASE_COMMIT,
        "source_artifacts": report["source_artifacts"],
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
