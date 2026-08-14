#!/usr/bin/env python3
"""Qualify the targeted Mission 6 Semantic Address Plane without training."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from aethersparse.controller.models import EntityMention
from aethersparse.controller.semantic_address import (
    SemanticAddressPlane,
    classify_retained_address_state,
    normalize_mention,
)

HARD_NEGATIVE_SCHEMA = "aethersparse.entity-hard-negatives.v11"
HARD_NEGATIVE_MANIFEST_SCHEMA = "aethersparse.entity-hard-negatives-manifest.v11"
ALLOWED_PARTITIONS = frozenset({"development", "tuning"})
SEALED_PARTITIONS = ("evaluation", "final_held")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _stable_json(value: object) -> bytes:
    return json.dumps(value, indent=2, sort_keys=True).encode() + b"\n"


def _load_hard_negatives(
    path: Path, manifest_path: Path
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    compressed = path.read_bytes()
    raw = gzip.decompress(compressed)
    document = json.loads(raw)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if document.get("schema_version") != HARD_NEGATIVE_SCHEMA:
        raise ValueError("unsupported hard-negative schema")
    if manifest.get("schema_version") != HARD_NEGATIVE_MANIFEST_SCHEMA:
        raise ValueError("unsupported hard-negative manifest schema")
    output = manifest.get("output", {})
    observed = {
        "gzip_sha256": _sha256_bytes(compressed),
        "json_sha256": _sha256_bytes(raw),
        "compressed_bytes": len(compressed),
        "uncompressed_bytes": len(raw),
    }
    for field, value in observed.items():
        if output.get(field) != value:
            raise ValueError(f"hard-negative manifest mismatch for {field}")
    if manifest.get("replica_count") != document.get("replica_count"):
        raise ValueError("hard-negative replica count mismatch")
    if manifest.get("unique_case_count") != document.get("unique_case_count"):
        raise ValueError("hard-negative unique-case count mismatch")
    return (
        document,
        manifest,
        {
            **observed,
            "manifest_sha256": _sha256_file(manifest_path),
        },
    )


def _model_mention(raw: dict[str, Any]) -> EntityMention:
    return EntityMention.model_validate(
        {
            "surface": raw["surface"],
            "char_start": raw["char_start"],
            "char_end": raw["char_end"],
            "candidates": raw["candidates"],
            "selected_entity_id": raw["selected_entity_id"],
            "selected_confidence": raw["selected_confidence"],
            "resolution_method": raw["resolution_method"],
            "copy_status": raw["copy_status"],
        }
    )


def _validate_splits(
    document: dict[str, Any], manifest: dict[str, Any]
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], dict[str, Any]]:
    if tuple(document.get("sealed_partitions_excluded", ())) != SEALED_PARTITIONS:
        raise ValueError("hard-negative document does not seal evaluation/final-held")
    if tuple(manifest.get("sealed_partitions_excluded", ())) != SEALED_PARTITIONS:
        raise ValueError("hard-negative manifest does not seal evaluation/final-held")
    cases = document.get("cases")
    if not isinstance(cases, list) or len(cases) != document.get("unique_case_count"):
        raise ValueError("hard-negative case collection is malformed")
    seen_cases: set[str] = set()
    seen_replicas: set[tuple[str, str]] = set()
    partition_case_ids: dict[str, list[str]] = defaultdict(list)
    partition_counts: dict[str, Counter[str]] = defaultdict(Counter)
    replicas: list[tuple[dict[str, Any], dict[str, Any]]] = []
    mention_count = 0
    null_alignment_count = 0
    retained_cap_count = 0
    for case in cases:
        case_id = str(case["case_id"])
        partition = str(case["partition"])
        if partition not in ALLOWED_PARTITIONS:
            raise ValueError(f"sealed or unknown partition entered qualification: {partition}")
        if case_id in seen_cases:
            raise ValueError(f"duplicate hard-negative case: {case_id}")
        seen_cases.add(case_id)
        partition_case_ids[partition].append(case_id)
        partition_counts[partition]["unique_cases"] += 1
        for replica in case["replicas"]:
            key = (case_id, str(replica["corpus_tier"]))
            if key in seen_replicas:
                raise ValueError(f"duplicate hard-negative replica: {key}")
            seen_replicas.add(key)
            if not replica.get("training_eligible"):
                raise ValueError(f"non-training replica entered qualification: {key}")
            partition_counts[partition]["replicas"] += 1
            replicas.append((case, replica))
            for raw_mention in replica["mentions"]:
                mention = _model_mention(raw_mention)
                if case["query"][mention.char_start : mention.char_end] != mention.surface:
                    raise ValueError(f"mention offsets do not copy query text: {key}")
                if raw_mention["candidate_count_retained"] != len(mention.candidates):
                    raise ValueError(f"retained candidate count mismatch: {key}")
                mention_count += 1
                null_alignment_count += raw_mention.get("correct_entity_per_mention") is None
                retained_cap_count += len(mention.candidates) == 8
    if len(replicas) != document.get("replica_count"):
        raise ValueError("hard-negative replica collection is incomplete")
    expected_counts = {
        partition: dict(sorted(counts.items()))
        for partition, counts in sorted(partition_counts.items())
    }
    if expected_counts != manifest.get("partition_counts"):
        raise ValueError("hard-negative partition counts mismatch")
    observed_hashes = {
        partition: _sha256_bytes(("\n".join(ids) + "\n").encode())
        for partition, ids in sorted(partition_case_ids.items())
    }
    if observed_hashes != manifest.get("partition_case_id_sha256"):
        raise ValueError("hard-negative partition identity mismatch")
    return replicas, {
        "partitions_present": sorted(partition_case_ids),
        "sealed_partitions_excluded": list(SEALED_PARTITIONS),
        "partition_counts": expected_counts,
        "partition_case_id_sha256": observed_hashes,
        "replicas_never_cross_partitions": True,
        "mention_count": mention_count,
        "mention_offsets_verified": mention_count,
        "null_correct_entity_per_mention": null_alignment_count,
        "mentions_at_retained_cap_8": retained_cap_count,
    }


def _quantiles(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)

    def at(fraction: float) -> float:
        return ordered[round((len(ordered) - 1) * fraction)]

    return {
        "minimum": min(ordered),
        "p25": at(0.25),
        "median": statistics.median(ordered),
        "p75": at(0.75),
        "p95": at(0.95),
        "maximum": max(ordered),
        "mean": statistics.mean(ordered),
    }


def qualify(
    *,
    anchor_statistics: Path,
    anchor_manifest: Path,
    hard_negatives: Path,
    hard_negatives_manifest: Path,
    eligible_case_count: int = 695,
    mission5_reachable: int = 260,
    mission6_reachable: int = 306,
) -> dict[str, Any]:
    hard, hard_manifest, hard_identity = _load_hard_negatives(
        hard_negatives, hard_negatives_manifest
    )
    replicas, split_audit = _validate_splits(hard, hard_manifest)
    plane = SemanticAddressPlane.from_gzip(
        anchor_statistics,
        anchor_manifest,
        expected_hard_negatives_sha256=hard_identity["gzip_sha256"],
    )
    if plane.identity is None:
        raise AssertionError("verified gzip loader did not retain artifact identity")

    anchor_document = json.loads(gzip.decompress(anchor_statistics.read_bytes()))
    rows = anchor_document["statistics"]
    raw_surfaces = {
        mention["surface"] for case, replica in replicas for mention in replica["mentions"]
    }
    normalized_surfaces = {normalize_mention(surface) for surface in raw_surfaces}
    covered = set(plane.mentions())

    taxonomy: Counter[str] = Counter()
    candidate_metrics: dict[str, Counter[str]] = defaultdict(Counter)
    case_metrics: dict[str, dict[str, bool]] = defaultdict(
        lambda: {"current": False, "address_augmented": False}
    )
    replica_surface_coverage: Counter[str] = Counter()
    current_complete_total = 0
    augmented_complete_total = 0
    newly_complete_total = 0
    for case, replica in replicas:
        mentions = tuple(_model_mention(item) for item in replica["mentions"])
        state = classify_retained_address_state(case["correct_entity_ids"], mentions)
        taxonomy[state.value] += 1
        required = set(case["correct_entity_ids"])
        retained = {candidate.entity_id for mention in mentions for candidate in mention.candidates}
        occurrence_addresses: set[str] = set()
        covered_mentions = 0
        for mention in mentions:
            distribution = plane.distribution(
                mention.surface,
                retained_candidates=tuple(
                    (candidate.entity_id, candidate.confidence) for candidate in mention.candidates
                ),
            )
            covered_mentions += distribution.ambiguity_count > 0
            occurrence_addresses.update(item.entity_id for item in distribution.hypotheses)
        if not mentions:
            replica_surface_coverage["no_detected_mentions"] += 1
        elif covered_mentions:
            replica_surface_coverage["at_least_one_covered_surface"] += 1
        else:
            replica_surface_coverage["only_uncovered_surfaces"] += 1
        current = required.issubset(retained)
        augmented = required.issubset(retained | occurrence_addresses)
        current_complete_total += current
        augmented_complete_total += augmented
        newly_complete_total += augmented and not current
        partition = str(case["partition"])
        tier = str(replica["corpus_tier"])
        key = f"{partition}:{tier}"
        candidate_metrics[key]["replicas"] += 1
        candidate_metrics[key]["current_complete"] += current
        candidate_metrics[key]["address_augmented_complete"] += augmented
        case_metrics[str(case["case_id"])]["current"] |= current
        case_metrics[str(case["case_id"])]["address_augmented"] |= augmented

    resolved_rows = [item for item in rows if item["target_entity_id"] is not None]
    source_diversity = [item["source_document_count"] / item["occurrence_count"] for item in rows]
    entropy_by_mention = {item["mention"]: item["entropy_nats"] for item in rows}
    resolved_mass_by_mention: dict[str, float] = defaultdict(float)
    for item in resolved_rows:
        resolved_mass_by_mention[item["mention"]] += item["probability"]
    perfect_within_address_complete = mission6_reachable + augmented_complete_total
    newly_generated_only = mission6_reachable + newly_complete_total
    return {
        "schema_version": "aethersparse.semantic-address-plane-qualification.v1",
        "status": "PARTIAL_TARGETED_SEMANTIC_ADDRESS_QUALIFIED",
        "decision": "IMPLEMENT_GENERIC_PLANE_DEFER_CONTEXTUAL_SPECIALIST",
        "scope": {
            "occurrence_statistics_tiers_present": ["10k"],
            "occurrence_statistics_tiers_missing": ["25k", "397k"],
            "source_pack_available_for_independent_reverification": False,
            "private_payload_copied_into_checkpoint": False,
            "contextual_specialist_training_started": False,
        },
        "integrity": {
            "hard_negatives": hard_identity,
            "hard_negative_input_hashes": hard_manifest["input_hashes"],
            "occurrence_statistics": {
                "gzip_sha256": plane.identity.gzip_sha256,
                "json_sha256": plane.identity.json_sha256,
                "manifest_sha256": plane.identity.manifest_sha256,
                "source_pack_sha256": plane.identity.source_pack_sha256,
                "hard_negatives_sha256": plane.identity.hard_negatives_sha256,
            },
            "split_audit": split_audit,
        },
        "field_audit": {
            "available": [
                "mention surface and copied query offsets",
                "retained candidate IDs, titles, methods, scores, confidence, and margin",
                "occurrence count and distinct source-document support",
                "Laplace-smoothed P(entity|mention) and ambiguity entropy",
                "title, redirect, and alias-channel indicators",
                "case-level development/tuning correct entity IDs",
            ],
            "missing": [
                "correct canonical entity ID aligned to each mention",
                "pre-cap candidate pool, generation count, and pre-cap rank",
                "raw occurrence context and occurrence source-document IDs",
                "25k and 397k occurrence statistics",
                "actual entity type and raw edit similarity",
            ],
            "consequence": (
                "absence from the retained set cannot be separated into never-generated "
                "versus outside-cap, and partial mention misses are not labelable"
            ),
        },
        "occurrence_statistics": {
            "requested_raw_surfaces": len(raw_surfaces),
            "requested_normalized_surfaces": len(normalized_surfaces),
            "covered_normalized_surfaces": plane.covered_mention_count,
            "missing_normalized_surfaces": len(normalized_surfaces - covered),
            "statistic_rows": plane.statistic_count,
            "resolved_canonical_rows": len(resolved_rows),
            "unresolved_target_rows": len(rows) - len(resolved_rows),
            "mentions_with_any_canonical_address": len(resolved_mass_by_mention),
            "mentions_without_canonical_address": (
                plane.covered_mention_count - len(resolved_mass_by_mention)
            ),
            "total_occurrences": sum(
                next(
                    item["total_mention_occurrences"] for item in rows if item["mention"] == mention
                )
                for mention in covered
            ),
            "ambiguous_mentions": sum(
                1
                for mention in covered
                if next(item["ambiguity_count"] for item in rows if item["mention"] == mention) > 1
            ),
            "unambiguous_mentions": sum(
                1
                for mention in covered
                if next(item["ambiguity_count"] for item in rows if item["mention"] == mention) == 1
            ),
            "entropy_nats": _quantiles(list(entropy_by_mention.values())),
            "occurrence_support": _quantiles([float(item["occurrence_count"]) for item in rows]),
            "source_document_support": _quantiles(
                [float(item["source_document_count"]) for item in rows]
            ),
            "source_diversity": _quantiles(source_diversity),
            "title_signal_rows": sum(bool(item["title_indicator"]) for item in rows),
            "redirect_signal_rows": sum(bool(item["redirect_indicator"]) for item in rows),
            "mean_resolved_probability_mass": statistics.mean(
                resolved_mass_by_mention.get(mention, 0.0) for mention in covered
            ),
            "probability_validation": "all rows match alpha=1 Laplace smoothing and entropy",
        },
        "retained_failure_taxonomy": dict(sorted(taxonomy.items())),
        "failure_distinguishability": {
            "mention_missing": "only a fully empty retained mention set is observable",
            "correct_candidate_absent": (
                "observable only as absent from the retained set; generation provenance unknown"
            ),
            "candidate_outside_cap": "not observable and never emitted",
            "candidate_misranked": (
                "case-level required set is present but selection is incomplete; "
                "no per-mention gold"
            ),
            "candidate_rejected_by_confidence": (
                "top-ranked required set not selected is observable; threshold versus margin "
                "requires current-linker policy context"
            ),
        },
        "candidate_address_coverage": {
            "replica_surface_coverage": dict(sorted(replica_surface_coverage.items())),
            "current_complete_replicas": current_complete_total,
            "address_augmented_complete_replicas": augmented_complete_total,
            "new_complete_replicas": newly_complete_total,
            "current_complete_unique_cases": sum(item["current"] for item in case_metrics.values()),
            "address_augmented_complete_unique_cases": sum(
                item["address_augmented"] for item in case_metrics.values()
            ),
            "by_partition_and_tier": {
                key: dict(sorted(value.items())) for key, value in sorted(candidate_metrics.items())
            },
        },
        "certified_reachability_context": {
            "eligible_replicas": eligible_case_count,
            "mission5_reachable": mission5_reachable,
            "mission5_rate": mission5_reachable / eligible_case_count,
            "mission6_reachable": mission6_reachable,
            "mission6_rate": mission6_reachable / eligible_case_count,
            "new_address_only_optimistic_ceiling": newly_generated_only,
            "new_address_only_optimistic_ceiling_rate": (
                newly_generated_only / eligible_case_count
            ),
            "perfect_selection_within_address_complete_optimistic_ceiling": (
                perfect_within_address_complete
            ),
            "perfect_selection_within_address_complete_optimistic_ceiling_rate": (
                perfect_within_address_complete / eligible_case_count
            ),
            "warning": ("ceilings are candidate-set upper bounds, not measured semantic recovery"),
        },
        "training_gate": {
            "contextual_specialist_started": False,
            "reasons": [
                "all mention-level correct_entity_per_mention fields are null",
                "only 10k aggregate occurrences are present; raw occurrence context is absent",
                "the occurrence overlay raises complete retained-address coverage by only "
                f"{newly_complete_total} replicas",
                "outside-cap and never-generated candidates remain observationally confounded",
            ],
            "permitted_next_training_input": (
                "development-only mention-aligned examples after candidate-generation provenance "
                "is captured; tuning remains calibration/model-selection only"
            ),
        },
    }


def write_report(report: dict[str, Any], output: Path, manifest_output: Path) -> dict[str, Any]:
    payload = _stable_json(report)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(payload)
    manifest = {
        "schema_version": "aethersparse.semantic-address-plane-qualification-manifest.v1",
        "report": {
            "file": output.name,
            "bytes": len(payload),
            "sha256": _sha256_bytes(payload),
        },
        "inputs": {
            "hard_negatives_gzip_sha256": report["integrity"]["hard_negatives"]["gzip_sha256"],
            "hard_negatives_manifest_sha256": report["integrity"]["hard_negatives"][
                "manifest_sha256"
            ],
            "occurrence_statistics_gzip_sha256": report["integrity"]["occurrence_statistics"][
                "gzip_sha256"
            ],
            "occurrence_statistics_manifest_sha256": report["integrity"]["occurrence_statistics"][
                "manifest_sha256"
            ],
        },
        "private_payload_included": False,
    }
    manifest_output.write_bytes(_stable_json(manifest))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--anchor-statistics", required=True, type=Path)
    parser.add_argument("--anchor-manifest", required=True, type=Path)
    parser.add_argument("--hard-negatives", required=True, type=Path)
    parser.add_argument("--hard-negatives-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest-output", required=True, type=Path)
    args = parser.parse_args()
    report = qualify(
        anchor_statistics=args.anchor_statistics,
        anchor_manifest=args.anchor_manifest,
        hard_negatives=args.hard_negatives,
        hard_negatives_manifest=args.hard_negatives_manifest,
    )
    manifest = write_report(report, args.output, args.manifest_output)
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
