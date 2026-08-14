#!/usr/bin/env python3
"""Authenticate and scope the Mission 6 targeted upstream handoff."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, cast

from aethersparse.controller.replay import verify_replay_bundle

_TRAINING_PARTITIONS = frozenset({"development", "tuning"})
_EXPECTED_SOURCE_IDENTITIES = {
    "benchmark_sha256": "1e8b89427898df3c3e5efef55135192cf6d48240f0f568c95eb1570470ead113",
    "mission5_report_sha256": "280b314b313b69c72583702898bf135b614d725405587725d4d5f047601327cd",
    "replay_bundle_sha256": "099cd28b5c2a090d1b08d1e07d39e59ae6583854971c539085741fbb369f0246",
}
_REQUIRED_ANCHOR_FIELDS = frozenset(
    {
        "alias_types",
        "ambiguity_count",
        "entropy_nats",
        "mention",
        "occurrence_count",
        "probability",
        "redirect_indicator",
        "redirect_prior",
        "redirect_support_count",
        "source_document_count",
        "target_entity_id",
        "target_title",
        "title_indicator",
        "title_prior",
        "total_mention_occurrences",
    }
)
_REQUIRED_RUNTIME_BOUNDARY_FIELDS = frozenset(
    {
        "all_matches_before_region_pruning",
        "post_cap_values",
        "post_dedup_values",
        "pre_cap_values",
        "pre_dedup_values",
        "region_cap",
        "regions",
        "top8_matches_before_deduplication",
        "value_cap",
    }
)
_REQUIRED_COMPILER_BOUNDARY_FIELDS = frozenset(
    {
        "all_typed_matches_before_type_caps",
        "max_claims_per_page",
        "typed_matches_after_page_cap",
        "typed_matches_after_type_caps",
    }
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _gzip_json(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = gzip.decompress(path.read_bytes())
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"expected compressed JSON object: {path}")
    return payload, raw


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _partition_case_hash(cases: list[dict[str, Any]], partition: str) -> str:
    value = "\n".join(
        str(case["case_id"]) for case in cases if case["partition"] == partition
    )
    return hashlib.sha256((value + "\n").encode()).hexdigest()


def _audit_entity(
    artifact_dir: Path,
    baseline_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    corpus_path = artifact_dir / "ENTITY_HARD_NEGATIVES_V11.json.gz"
    manifest_path = artifact_dir / "ENTITY_HARD_NEGATIVES_V11.manifest.json"
    anchor_path = artifact_dir / "entity-anchor-statistics-10k.json.gz"
    anchor_manifest_path = artifact_dir / "entity-anchor-statistics-10k.json.gz.manifest.json"
    corpus, corpus_raw = _gzip_json(corpus_path)
    manifest = _json(manifest_path)
    anchors, anchors_raw = _gzip_json(anchor_path)
    anchor_manifest = _json(anchor_manifest_path)

    _require(_sha256(corpus_path) == manifest["output"]["gzip_sha256"], "entity gzip hash")
    _require(
        hashlib.sha256(corpus_raw).hexdigest() == manifest["output"]["json_sha256"],
        "entity JSON hash",
    )
    _require(_sha256(baseline_path) == manifest["baseline"]["sha256"], "entity baseline hash")
    _require(
        _sha256(anchor_path) == anchor_manifest["output_gzip_sha256"],
        "anchor gzip hash",
    )
    _require(
        hashlib.sha256(anchors_raw).hexdigest() == anchor_manifest["output_json_sha256"],
        "anchor JSON hash",
    )
    _require(
        _sha256(corpus_path) == anchor_manifest["hard_negatives_sha256"],
        "anchor/entity chain hash",
    )

    cases_raw = corpus.get("cases")
    _require(
        isinstance(cases_raw, list) and all(isinstance(case, dict) for case in cases_raw),
        "entity cases missing",
    )
    cases = cast(list[dict[str, Any]], cases_raw)
    partitions = {str(case["partition"]) for case in cases}
    _require(partitions <= _TRAINING_PARTITIONS, "sealed entity partition present")
    replicas = [replica for case in cases for replica in case["replicas"]]
    _require(
        all(replica.get("training_eligible") is True for replica in replicas),
        "ineligible entity row",
    )
    _require(len(cases) == int(manifest["unique_case_count"]), "entity unique count")
    _require(len(replicas) == int(manifest["replica_count"]), "entity replica count")
    for partition in sorted(_TRAINING_PARTITIONS):
        _require(
            _partition_case_hash(cases, partition)
            == manifest["partition_case_id_sha256"][partition],
            f"entity {partition} identity",
        )

    mentions = [mention for replica in replicas for mention in replica["mentions"]]
    surfaces = {str(mention["surface"]) for mention in mentions}
    aligned_mentions = sum(
        1 for mention in mentions if mention.get("correct_entity_per_mention") is not None
    )
    taxonomy = Counter(str(replica["failure_class"]) for replica in replicas)
    at_retained_cap = sum(
        1 for mention in mentions if int(mention.get("candidate_count_retained", 0)) >= 8
    )

    statistics_raw = anchors.get("statistics")
    _require(
        isinstance(statistics_raw, list)
        and bool(statistics_raw)
        and all(isinstance(row, dict) for row in statistics_raw),
        "anchor statistics missing",
    )
    statistics = cast(list[dict[str, Any]], statistics_raw)
    by_mention: dict[str, list[dict[str, Any]]] = defaultdict(list)
    canonical_anchor_rows = 0
    unresolved_anchor_rows = 0
    for row in statistics:
        _require(row.keys() >= _REQUIRED_ANCHOR_FIELDS, "anchor fields incomplete")
        entity_id = row["target_entity_id"]
        if entity_id is None:
            unresolved_anchor_rows += 1
        else:
            _require(str(entity_id).startswith("as:v050:entity:"), "noncanonical entity ID")
            canonical_anchor_rows += 1
        _require(int(row["occurrence_count"]) > 0, "nonpositive anchor support")
        _require(int(row["source_document_count"]) > 0, "nonpositive source diversity")
        _require(float(row["entropy_nats"]) >= -1e-12, "negative entropy")
        by_mention[str(row["mention"])].append(row)
    for mention, rows in by_mention.items():
        probability_sum = sum(float(row["probability"]) for row in rows)
        _require(math.isclose(probability_sum, 1.0, abs_tol=1e-9), f"probability mass: {mention}")
        expected_ambiguity = len(rows)
        _require(
            all(int(row["ambiguity_count"]) == expected_ambiguity for row in rows),
            f"ambiguity count: {mention}",
        )

    _require(len(by_mention) == int(anchors["covered_mention_count"]), "covered mention count")
    _require(len(statistics) == int(anchor_manifest["statistic_count"]), "anchor row count")
    missing_surfaces = int(anchors["requested_mention_count"]) - int(
        anchors["covered_mention_count"]
    )
    return (
        {
            "artifact_hashes": {
                corpus_path.name: _sha256(corpus_path),
                manifest_path.name: _sha256(manifest_path),
                anchor_path.name: _sha256(anchor_path),
                anchor_manifest_path.name: _sha256(anchor_manifest_path),
            },
            "partitions": sorted(partitions),
            "unique_cases": len(cases),
            "replicas": len(replicas),
            "mentions": len(mentions),
            "mention_surfaces": len(surfaces),
            "aligned_mentions": aligned_mentions,
            "at_retained_cap_mentions": at_retained_cap,
            "failure_taxonomy": dict(sorted(taxonomy.items())),
            "anchor_tiers": ["10k"],
            "anchor_rows": len(statistics),
            "anchor_covered_surfaces": len(by_mention),
            "anchor_missing_surfaces": missing_surfaces,
            "anchor_occurrences": sum(
                (int(row["occurrence_count"]) for row in statistics), 0
            ),
            "canonical_anchor_rows": canonical_anchor_rows,
            "unresolved_anchor_rows": unresolved_anchor_rows,
            "anchor_target_entities": len(
                {str(row["target_entity_id"]) for row in statistics if row["target_entity_id"]}
            ),
        },
        {
            "occurrence_probabilities": True,
            "support_counts": True,
            "ambiguity_entropy": True,
            "source_diversity": True,
            "alias_redirect_title_features": True,
            "canonical_entity_ids": canonical_anchor_rows > 0,
            "all_anchor_targets_resolved": unresolved_anchor_rows == 0,
            "mention_level_gold_alignment": aligned_mentions == len(mentions),
            "pre_cap_candidate_pool": False,
            "candidate_outside_cap_distinguishable": False,
            "tier_coverage": "10k_ONLY",
        },
    )


def _audit_value(
    artifact_dir: Path,
    sidecar_report: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    value_path = artifact_dir / "value-enumeration-diagnostic-v11.json.gz"
    manifest_path = artifact_dir / "value-enumeration-diagnostic-v11.manifest.json"
    value, raw = _gzip_json(value_path)
    manifest = _json(manifest_path)
    _require(_sha256(value_path) == manifest["output_sha256"], "value gzip hash")
    _require(
        hashlib.sha256(raw).hexdigest() == manifest["output_uncompressed_sha256"],
        "value JSON hash",
    )
    _require(manifest.get("evaluation_and_final_held_used") is False, "value manifest split")
    replicas_raw = value.get("replicas")
    _require(
        isinstance(replicas_raw, list)
        and all(isinstance(replica, dict) for replica in replicas_raw),
        "value replicas missing",
    )
    replicas = cast(list[dict[str, Any]], replicas_raw)
    partitions = {str(replica["partition"]) for replica in replicas}
    _require(partitions <= _TRAINING_PARTITIONS, "sealed value partition present")
    _require(value["scope"]["evaluation_and_final_held_used"] is False, "value scope split")
    _require(len(replicas) == int(manifest["residual_replicas"]), "value replica count")
    _require(
        len({str(replica["case_id"]) for replica in replicas}) == int(manifest["unique_case_ids"]),
        "value unique case count",
    )

    selected_chunks = 0
    compiler_documents = 0
    runtime_matches = 0
    binding_failures = 0
    missing_chunks = 0
    missing_documents = 0
    for replica in replicas:
        capture_raw = replica.get("pack_capture")
        _require(isinstance(capture_raw, dict), "value pack capture absent")
        capture = cast(dict[str, Any], capture_raw)
        tier = str(replica["corpus_tier"])
        _require(
            capture["pack_sha256"] == manifest["source_identity"]["pack_sha256_by_tier"][tier],
            f"value pack identity: {tier}",
        )
        for chunk in capture["selected_chunks"]:
            selected_chunks += 1
            if chunk.get("missing_from_pack"):
                missing_chunks += 1
                continue
            _require("complete_chunk_text" in chunk, "complete chunk text absent")
            boundary = chunk["runtime_boundary"]
            _require(
                boundary.keys() >= _REQUIRED_RUNTIME_BOUNDARY_FIELDS,
                "runtime boundary fields",
            )
            for match in boundary["all_matches_before_region_pruning"]:
                runtime_matches += 1
                binding_failures += int(match.get("document_binding_success") is not True)
                _require(match.get("exact_surface_bound") is True, "inexact runtime surface")
        for document in capture["compiler_documents"]:
            compiler_documents += 1
            if document.get("missing_from_pack"):
                missing_documents += 1
                continue
            _require(
                document["boundary"].keys() >= _REQUIRED_COMPILER_BOUNDARY_FIELDS,
                "compiler boundary fields",
            )
    _require(missing_chunks == 0, "selected source chunks missing")
    _require(missing_documents == 0, "source documents missing")
    _require(binding_failures == 0, "exact document rebinding failure")

    for tier, report in sidecar_report["tiers"].items():
        _require(
            report["sidecar_sha256"]
            == manifest["source_identity"]["pack_sha256_by_tier"][tier],
            "sidecar identity",
        )
        _require(int(report["chunks_missing_from_source"]) == 0, "sidecar missing chunks")
        _require(not report["gold_documents_missing_from_source"], "sidecar missing documents")
        _require(int(report["text_copy_mismatches"]) == 0, "sidecar text mismatch")

    classifications = Counter(str(replica["classification"]) for replica in replicas)
    return (
        {
            "artifact_hashes": {
                value_path.name: _sha256(value_path),
                manifest_path.name: _sha256(manifest_path),
            },
            "partitions": sorted(partitions),
            "replicas": len(replicas),
            "unique_cases": len({str(replica["case_id"]) for replica in replicas}),
            "tiers": dict(sorted(Counter(str(row["corpus_tier"]) for row in replicas).items())),
            "classification_taxonomy": dict(sorted(classifications.items())),
            "selected_chunks": selected_chunks,
            "compiler_documents": compiler_documents,
            "runtime_matches": runtime_matches,
            "exact_rebinding_failures": binding_failures,
        },
        {
            "complete_selected_chunk_text": True,
            "source_chunk_membership": True,
            "runtime_regions_before_top8": True,
            "runtime_pre_post_dedup": True,
            "runtime_pre_post_cap": True,
            "compiler_pre_post_caps": True,
            "exact_document_rebinding": True,
            "all_targeted_tiers": True,
        },
    )


def audit(
    artifact_dir: Path,
    *,
    replay_bundle: Path,
    benchmark_path: Path,
    mission5_report_path: Path,
    baseline_path: Path,
) -> dict[str, Any]:
    replay = verify_replay_bundle(replay_bundle)
    _require(
        replay.bundle_sha256 == _EXPECTED_SOURCE_IDENTITIES["replay_bundle_sha256"],
        "replay bundle identity",
    )
    _require(
        _sha256(benchmark_path) == _EXPECTED_SOURCE_IDENTITIES["benchmark_sha256"],
        "benchmark identity",
    )
    _require(
        _sha256(mission5_report_path)
        == _EXPECTED_SOURCE_IDENTITIES["mission5_report_sha256"],
        "Mission 5 report identity",
    )

    sidecar_path = artifact_dir / "sidecar-derivation-report.json"
    completion_report_path = artifact_dir / "V11_TARGETED_DATA_REPORT.md"
    sidecar = _json(sidecar_path)
    entity, entity_fields = _audit_entity(artifact_dir, baseline_path)
    value, value_fields = _audit_value(artifact_dir, sidecar)
    all_hashes = {
        **entity.pop("artifact_hashes"),
        sidecar_path.name: _sha256(sidecar_path),
        completion_report_path.name: _sha256(completion_report_path),
        **value.pop("artifact_hashes"),
    }
    entity_complete = all(
        bool(entity_fields[field])
        for field in ("mention_level_gold_alignment", "pre_cap_candidate_pool")
    ) and entity_fields["tier_coverage"] == "ALL_TARGETED_TIERS"
    return {
        "schema_version": "aethersparse.v11-targeted-handoff-audit.v1",
        "status": "ACCEPTED_WITH_ENTITY_SCOPE_LIMITATIONS",
        "artifact_integrity": {
            "verified": True,
            "hashes": dict(sorted(all_hashes.items())),
            "completion_report_is_trust_root": True,
        },
        "source_identity": {
            **_EXPECTED_SOURCE_IDENTITIES,
            "replay_cases_sha256": replay.cases_sha256,
        },
        "partition_policy": {
            "training_partitions": sorted(_TRAINING_PARTITIONS),
            "evaluation_final_held_rows_consumed": 0,
            "evaluation_final_held_labels_permitted": False,
            "verified": True,
        },
        "entity": entity,
        "entity_field_coverage": entity_fields,
        "entity_handoff_complete": entity_complete,
        "value": value,
        "value_field_coverage": value_fields,
        "value_handoff_complete": all(value_fields.values()),
        "training_authorization": {
            "development": "model fitting and feature construction",
            "tuning": "calibration, successive-halving selection, and frozen threshold choice",
            "evaluation_final_held": "prohibited until architecture and thresholds are frozen",
            "contextual_entity_specialist": (
                "BLOCKED_PENDING_CANDIDATE_RECALL_AND_MENTION_ALIGNMENT"
            ),
            "neural_value_specialist": "CONDITIONAL_ON_POST_DETERMINISTIC_RESIDUAL",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--replay-bundle", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--mission5-report", type=Path, required=True)
    parser.add_argument("--entity-baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(
        args.artifact_dir,
        replay_bundle=args.replay_bundle,
        benchmark_path=args.benchmark,
        mission5_report_path=args.mission5_report,
        baseline_path=args.entity_baseline,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = {key: value for key, value in result.items() if key not in {"entity", "value"}}
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
