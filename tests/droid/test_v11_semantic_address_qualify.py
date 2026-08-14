from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from aethersparse.controller.semantic_address import canonical_entity_id
from scripts.droid.v11_semantic_address_qualify import qualify, write_report


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def _write_fixture(tmp_path: Path, *, partition: str = "development") -> dict[str, Path]:
    correct = canonical_entity_id("Alpha")
    wrong = canonical_entity_id("Beta")
    candidate = {
        "entity_id": wrong,
        "title": "Beta",
        "method": "alias",
        "name_score": 0.97,
        "type_score": 1.0,
        "relation_score": 1.0,
        "context_score": 0.0,
        "confidence": 0.88,
    }
    hard = {
        "schema_version": "aethersparse.entity-hard-negatives.v11",
        "sealed_partitions_excluded": ["evaluation", "final_held"],
        "replica_count": 1,
        "unique_case_count": 1,
        "cases": [
            {
                "case_id": "case:1",
                "partition": partition,
                "query": "Find Alpha",
                "correct_entity_ids": [correct],
                "replicas": [
                    {
                        "corpus_tier": "10k",
                        "training_eligible": True,
                        "mentions": [
                            {
                                "surface": "Alpha",
                                "char_start": 5,
                                "char_end": 10,
                                "candidates": [candidate],
                                "selected_entity_id": wrong,
                                "selected_confidence": 0.88,
                                "resolution_method": "alias",
                                "copy_status": "linked",
                                "candidate_count_retained": 1,
                                "correct_entity_per_mention": None,
                            }
                        ],
                    }
                ],
            }
        ],
    }
    hard_raw = _json_bytes(hard)
    hard_gzip = gzip.compress(hard_raw, mtime=0)
    hard_path = tmp_path / "hard.json.gz"
    hard_manifest_path = tmp_path / "hard.manifest.json"
    hard_path.write_bytes(hard_gzip)
    case_hash = hashlib.sha256(b"case:1\n").hexdigest()
    hard_manifest = {
        "schema_version": "aethersparse.entity-hard-negatives-manifest.v11",
        "sealed_partitions_excluded": ["evaluation", "final_held"],
        "replica_count": 1,
        "unique_case_count": 1,
        "partition_counts": {partition: {"replicas": 1, "unique_cases": 1}},
        "partition_case_id_sha256": {partition: case_hash},
        "input_hashes": {"benchmark_sha256": "c" * 64},
        "output": {
            "gzip_sha256": hashlib.sha256(hard_gzip).hexdigest(),
            "json_sha256": hashlib.sha256(hard_raw).hexdigest(),
            "compressed_bytes": len(hard_gzip),
            "uncompressed_bytes": len(hard_raw),
        },
    }
    hard_manifest_path.write_text(json.dumps(hard_manifest), encoding="utf-8")

    anchor = {
        "schema_version": "aethersparse.entity-anchor-statistics.v11",
        "source_pack_sha256": "d" * 64,
        "alpha": 1.0,
        "requested_mention_count": 1,
        "covered_mention_count": 1,
        "statistics": [
            {
                "mention": "alpha",
                "target_title": "alpha",
                "target_entity_id": correct,
                "occurrence_count": 2,
                "total_mention_occurrences": 2,
                "probability": 1.0,
                "ambiguity_count": 1,
                "entropy_nats": -0.0,
                "source_document_count": 2,
                "title_indicator": True,
                "title_prior": 1.0,
                "redirect_indicator": False,
                "redirect_support_count": 0,
                "redirect_prior": 0.0,
                "alias_types": ["anchor", "title"],
            }
        ],
    }
    anchor_raw = _json_bytes(anchor)
    anchor_gzip = gzip.compress(anchor_raw, mtime=0)
    anchor_path = tmp_path / "anchor.json.gz"
    anchor_manifest_path = tmp_path / "anchor.json.gz.manifest.json"
    anchor_path.write_bytes(anchor_gzip)
    anchor_manifest = {
        "schema_version": "aethersparse.entity-anchor-statistics-manifest.v11",
        "source_pack_sha256": anchor["source_pack_sha256"],
        "hard_negatives_sha256": hashlib.sha256(hard_gzip).hexdigest(),
        "alpha": 1.0,
        "requested_mention_count": 1,
        "covered_mention_count": 1,
        "statistic_count": 1,
        "output_gzip_sha256": hashlib.sha256(anchor_gzip).hexdigest(),
        "output_json_sha256": hashlib.sha256(anchor_raw).hexdigest(),
    }
    anchor_manifest_path.write_text(json.dumps(anchor_manifest), encoding="utf-8")
    return {
        "hard": hard_path,
        "hard_manifest": hard_manifest_path,
        "anchor": anchor_path,
        "anchor_manifest": anchor_manifest_path,
    }


def test_qualification_is_split_safe_and_reports_candidate_ceiling(tmp_path: Path) -> None:
    paths = _write_fixture(tmp_path)
    report = qualify(
        anchor_statistics=paths["anchor"],
        anchor_manifest=paths["anchor_manifest"],
        hard_negatives=paths["hard"],
        hard_negatives_manifest=paths["hard_manifest"],
    )

    assert report["status"] == "PARTIAL_TARGETED_SEMANTIC_ADDRESS_QUALIFIED"
    assert report["integrity"]["split_audit"]["partitions_present"] == ["development"]
    assert report["candidate_address_coverage"] == {
        "replica_surface_coverage": {"at_least_one_covered_surface": 1},
        "current_complete_replicas": 0,
        "address_augmented_complete_replicas": 1,
        "new_complete_replicas": 1,
        "current_complete_unique_cases": 0,
        "address_augmented_complete_unique_cases": 1,
        "by_partition_and_tier": {
            "development:10k": {
                "address_augmented_complete": 1,
                "current_complete": 0,
                "replicas": 1,
            }
        },
    }
    assert report["training_gate"]["contextual_specialist_started"] is False
    assert report["failure_distinguishability"]["candidate_outside_cap"].startswith(
        "not observable"
    )

    output = tmp_path / "report.json"
    manifest_output = tmp_path / "report.manifest.json"
    manifest = write_report(report, output, manifest_output)
    assert manifest["private_payload_included"] is False
    assert manifest["report"]["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()


def test_qualification_rejects_a_sealed_partition(tmp_path: Path) -> None:
    paths = _write_fixture(tmp_path, partition="evaluation")
    with pytest.raises(ValueError, match="sealed or unknown partition"):
        qualify(
            anchor_statistics=paths["anchor"],
            anchor_manifest=paths["anchor_manifest"],
            hard_negatives=paths["hard"],
            hard_negatives_manifest=paths["hard_manifest"],
        )
