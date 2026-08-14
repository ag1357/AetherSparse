#!/usr/bin/env python3
"""Qualify the targeted V11 pack capture without using protected partitions."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from aethersparse.controller.value_trace import (
    ValueTraceFailure,
    ValueTraceQualification,
    qualify_value_trace,
)

SCHEMA_VERSION = "aethersparse.value-upstream-qualification.v11"
TRAINING_PARTITIONS = frozenset({"development", "tuning"})


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read(path: Path) -> dict[str, Any]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    else:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _uncompressed_identity(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    with gzip.open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            count += len(block)
            digest.update(block)
    return count, digest.hexdigest()


def _verify_manifest(capture: Path, manifest: dict[str, Any]) -> dict[str, object]:
    size, digest = _uncompressed_identity(capture)
    checks = {
        "compressed_sha256": _sha256(capture) == manifest.get("output_sha256"),
        "compressed_bytes": capture.stat().st_size == manifest.get("output_compressed_bytes"),
        "uncompressed_sha256": digest == manifest.get("output_uncompressed_sha256"),
        "uncompressed_bytes": size == manifest.get("output_uncompressed_bytes"),
    }
    if not all(checks.values()):
        raise ValueError(f"capture manifest verification failed: {checks}")
    return {"checks": checks, "uncompressed_bytes": size, "uncompressed_sha256": digest}


def _capture_counts(replicas: list[dict[str, Any]]) -> dict[str, int]:
    chunks = [
        chunk
        for replica in replicas
        for chunk in replica["pack_capture"]["selected_chunks"]
    ]
    compiler_documents = [
        document
        for replica in replicas
        for document in replica["pack_capture"]["compiler_documents"]
    ]
    matches = [
        match
        for chunk in chunks
        for match in chunk["runtime_boundary"]["all_matches_before_region_pruning"]
    ]
    top8_matches = [
        match
        for chunk in chunks
        for match in chunk["runtime_boundary"]["top8_matches_before_deduplication"]
    ]
    return {
        "selected_chunk_replicas": len(chunks),
        "selected_chunks_unique_by_tier": len(
            {
                (str(replica["corpus_tier"]), str(chunk["chunk_id"]))
                for replica in replicas
                for chunk in replica["pack_capture"]["selected_chunks"]
            }
        ),
        "selected_chunks_missing": sum(bool(item.get("missing_from_pack")) for item in chunks),
        "compiler_documents": len(compiler_documents),
        "compiler_documents_missing": sum(
            bool(item.get("missing_from_pack")) for item in compiler_documents
        ),
        "runtime_regions": sum(
            len(chunk["runtime_boundary"]["regions"]) for chunk in chunks
        ),
        "runtime_matches_before_region_pruning": len(matches),
        "runtime_matches_after_region_pruning": len(top8_matches),
        "runtime_exact_surface_matches": sum(
            bool(match.get("exact_surface_bound")) for match in matches
        ),
        "runtime_exact_document_rebindings": sum(
            bool(match.get("document_binding_success")) for match in matches
        ),
    }


def _validate_capture(payload: dict[str, Any]) -> list[dict[str, Any]]:
    scope = payload.get("scope", {})
    if not isinstance(scope, dict):
        raise ValueError("capture scope is absent")
    if bool(scope.get("evaluation_and_final_held_used")):
        raise ValueError("capture reports protected partition use")
    if set(scope.get("partitions", ())) != TRAINING_PARTITIONS:
        raise ValueError("capture does not contain exactly development/tuning")
    replicas = payload.get("replicas")
    if not isinstance(replicas, list) or len(replicas) != 43:
        raise ValueError("capture must contain exactly 43 replicas")
    rows = [item for item in replicas if isinstance(item, dict)]
    if len(rows) != len(replicas):
        raise ValueError("capture contains a non-object replica")
    if any(item.get("partition") not in TRAINING_PARTITIONS for item in rows):
        raise ValueError("protected partition entered targeted value qualification")
    required_chunk_fields = {
        "chunk_id",
        "complete_chunk_text",
        "document_id",
        "raw_end",
        "raw_start",
        "runtime_boundary",
        "section",
    }
    required_boundary_fields = {
        "all_matches_before_region_pruning",
        "post_cap_values",
        "post_dedup_values",
        "pre_cap_values",
        "pre_dedup_values",
        "region_cap",
        "regions",
        "schema_version",
        "top8_matches_before_deduplication",
        "value_cap",
    }
    for replica in rows:
        capture = replica.get("pack_capture")
        if not isinstance(capture, dict):
            raise ValueError("replica lacks pack_capture")
        chunks = capture.get("selected_chunks")
        documents = capture.get("compiler_documents")
        if not isinstance(chunks, list) or len(chunks) != 8:
            raise ValueError("replica must contain all eight selected chunks")
        if not isinstance(documents, list) or not documents:
            raise ValueError("replica lacks compiler source documents")
        for chunk in chunks:
            if not isinstance(chunk, dict) or not required_chunk_fields <= set(chunk):
                raise ValueError("selected chunk fields are incomplete")
            if chunk.get("missing_from_pack"):
                raise ValueError("selected chunk is missing from sidecar")
            text = str(chunk["complete_chunk_text"])
            if len(text) != int(chunk["raw_end"]) - int(chunk["raw_start"]):
                raise ValueError("selected chunk text and character offsets disagree")
            boundary = chunk["runtime_boundary"]
            if not isinstance(boundary, dict) or not required_boundary_fields <= set(boundary):
                raise ValueError("runtime boundary fields are incomplete")
            for match in boundary["all_matches_before_region_pruning"]:
                if not match.get("exact_surface_bound") or not match.get(
                    "document_binding_success"
                ):
                    raise ValueError("runtime match violates exact source binding")
        if any(
            not isinstance(document, dict) or document.get("missing_from_pack")
            for document in documents
        ):
            raise ValueError("compiler source document is missing from sidecar")
    return rows


def _stage_counts(
    results: list[tuple[dict[str, Any], ValueTraceQualification]],
) -> dict[str, int]:
    missing_values = [result for replica, result in results if not result.replay_values_complete]
    selected_span_rows = [
        result for result in missing_values if result.target_spans_in_selected_chunks
    ]
    return {
        "value_present_address_binding_unresolved": sum(
            result.replay_values_complete for _, result in results
        ),
        "value_missing_from_replay": len(missing_values),
        "source_document_absent": sum(
            not result.source_documents_retrieved for result in missing_values
        ),
        "source_document_outside_top8": sum(
            result.source_documents_retrieved and not result.source_documents_top8
            for result in missing_values
        ),
        "source_chunk_absent": sum(
            result.source_documents_top8 and not result.target_spans_in_selected_chunks
            for result in missing_values
        ),
        "compiler_pre_target_alignment_failure_nonexclusive": sum(
            not result.compiler_pre_complete for result in missing_values
        ),
        "compiler_type_cap_loss_nonexclusive": sum(
            result.compiler_pre_complete and not result.compiler_type_complete
            for result in missing_values
        ),
        "compiler_page_cap_loss_nonexclusive": sum(
            result.compiler_type_complete and not result.compiler_page_complete
            for result in missing_values
        ),
        "runtime_extraction_failure_after_target_chunk_present": sum(
            not result.runtime_pre_region_complete for result in selected_span_rows
        ),
        "region_pruning_loss": sum(
            result.runtime_pre_region_complete and not result.runtime_post_region_complete
            for result in selected_span_rows
        ),
        "deduplication_loss": sum(
            result.runtime_post_region_complete and not result.runtime_post_dedup_complete
            for result in selected_span_rows
        ),
        "value_cap_loss": sum(
            result.runtime_post_dedup_complete and not result.runtime_post_cap_complete
            for result in selected_span_rows
        ),
        "rebinding_loss": sum(
            result.exact_rebinding_complete is False for result in selected_span_rows
        ),
    }


def build_qualification(
    capture_path: Path, manifest_path: Path
) -> dict[str, Any]:
    manifest = _read(manifest_path)
    integrity = _verify_manifest(capture_path, manifest)
    payload = _read(capture_path)
    replicas = _validate_capture(payload)
    unique_cases = payload.get("unique_cases")
    if not isinstance(unique_cases, list):
        raise ValueError("capture lacks unique cases")
    by_case = {
        str(item["case_id"]): item
        for item in unique_cases
        if isinstance(item, dict) and item.get("partition") in TRAINING_PARTITIONS
    }
    if len(by_case) != 16:
        raise ValueError("capture must contain exactly 16 training case groups")
    results: list[tuple[dict[str, Any], ValueTraceQualification]] = []
    for replica in replicas:
        case_id = str(replica["case_id"])
        if case_id not in by_case:
            raise ValueError(f"replica lacks protected training case data: {case_id}")
        if replica["partition"] != by_case[case_id]["partition"]:
            raise ValueError(f"partition drift for {case_id}")
        results.append((replica, qualify_value_trace(replica, by_case[case_id])))
    classifications = Counter(result.failure.value for _, result in results)
    extraction_residual_partitions = Counter(
        str(replica["partition"])
        for replica, result in results
        if result.failure
        in {
            ValueTraceFailure.COMPILER_AND_RUNTIME_EXTRACTION,
            ValueTraceFailure.COMPILER_EXTRACTION,
            ValueTraceFailure.RUNTIME_EXTRACTION,
        }
    )
    counts = _capture_counts(replicas)
    if counts["selected_chunks_missing"] or counts["compiler_documents_missing"]:
        raise ValueError("capture is incomplete")
    if counts["runtime_exact_surface_matches"] != counts[
        "runtime_matches_before_region_pruning"
    ]:
        raise ValueError("not every runtime match is an exact source surface")
    if counts["runtime_exact_document_rebindings"] != counts[
        "runtime_matches_before_region_pruning"
    ]:
        raise ValueError("not every runtime match rebinds to its immutable source")
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_id": "VALUE_UPSTREAM_QUALIFICATION_V11",
        "source_identity": {
            "capture_file": capture_path.name,
            "capture_sha256": _sha256(capture_path),
            "manifest_file": manifest_path.name,
            "manifest_sha256": _sha256(manifest_path),
            **payload["source_identity"],
        },
        "integrity": integrity,
        "scope": {
            "partitions": sorted(TRAINING_PARTITIONS),
            "evaluation_and_final_held_used": False,
            "replicas": len(replicas),
            "unique_cases": len(by_case),
            "partition_counts": dict(
                sorted(Counter(str(item["partition"]) for item in replicas).items())
            ),
            "tier_counts": dict(
                sorted(Counter(str(item["corpus_tier"]) for item in replicas).items())
            ),
        },
        "capture_counts": counts,
        "first_loss_classification_counts": dict(sorted(classifications.items())),
        "stage_counts": _stage_counts(results),
        "deterministic_decision": {
            "selected_chunk_runtime_change": "NOT_JUSTIFIED_BY_DEVELOPMENT_DATA",
            "neural_value_specialist": "NOT_TRAINED",
            "reason": (
                "the only target-present extraction residual is tuning-only; development "
                "failures are source-chunk availability or already-enumerated address binding"
            ),
            "extraction_residual_partition_counts": dict(
                sorted(extraction_residual_partitions.items())
            ),
        },
    }


def main() -> int:
    args = _arguments()
    payload = build_qualification(args.capture, args.manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
