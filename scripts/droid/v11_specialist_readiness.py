#!/usr/bin/env python3
"""Qualify whether the Mission 6 contextual specialist sweep may start."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

from aethersparse.specialists.readiness import qualify_specialist_readiness


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entity-hard-negatives", type=Path, required=True)
    parser.add_argument("--entity-manifest", type=Path, required=True)
    parser.add_argument("--anchor-statistics", type=Path, required=True)
    parser.add_argument("--anchor-manifest", type=Path, required=True)
    parser.add_argument("--value-diagnostic", type=Path, required=True)
    parser.add_argument("--value-manifest", type=Path, required=True)
    parser.add_argument("--mission5-report", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--reachability", type=Path, required=True)
    parser.add_argument(
        "--anchor-tier",
        action="append",
        default=[],
        choices=("10k", "25k", "397k"),
        help="tier represented by the supplied anchor export; repeat when applicable",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path, required=True)
    return parser.parse_args()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _read_gzip_json(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = gzip.decompress(path.read_bytes())
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"expected gzip JSON object: {path}")
    return value, raw


def _expect(observed: str, expected: object, field: str) -> None:
    if observed != str(expected):
        raise ValueError(f"integrity mismatch for {field}: {observed} != {expected}")


def _verify_inputs(args: argparse.Namespace) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, str],
    dict[str, Any],
]:
    entity, entity_raw = _read_gzip_json(args.entity_hard_negatives)
    entity_manifest = _read_json(args.entity_manifest)
    anchor, anchor_raw = _read_gzip_json(args.anchor_statistics)
    anchor_manifest = _read_json(args.anchor_manifest)
    value, value_raw = _read_gzip_json(args.value_diagnostic)
    value_manifest = _read_json(args.value_manifest)
    reachability = _read_json(args.reachability)

    hashes = {
        "entity_hard_negatives_gzip_sha256": _sha256(args.entity_hard_negatives),
        "entity_hard_negatives_json_sha256": _sha256_bytes(entity_raw),
        "entity_manifest_sha256": _sha256(args.entity_manifest),
        "anchor_statistics_gzip_sha256": _sha256(args.anchor_statistics),
        "anchor_statistics_json_sha256": _sha256_bytes(anchor_raw),
        "anchor_manifest_sha256": _sha256(args.anchor_manifest),
        "value_diagnostic_gzip_sha256": _sha256(args.value_diagnostic),
        "value_diagnostic_json_sha256": _sha256_bytes(value_raw),
        "value_manifest_sha256": _sha256(args.value_manifest),
        "mission5_report_gzip_sha256": _sha256(args.mission5_report),
        "benchmark_sha256": _sha256(args.benchmark),
        "reachability_sha256": _sha256(args.reachability),
    }

    entity_output = entity_manifest.get("output", {})
    if not isinstance(entity_output, dict):
        raise ValueError("entity manifest output must be an object")
    _expect(
        hashes["entity_hard_negatives_gzip_sha256"],
        entity_output.get("gzip_sha256"),
        "entity.output.gzip_sha256",
    )
    _expect(
        hashes["entity_hard_negatives_json_sha256"],
        entity_output.get("json_sha256"),
        "entity.output.json_sha256",
    )
    _expect(
        hashes["anchor_statistics_gzip_sha256"],
        anchor_manifest.get("output_gzip_sha256"),
        "anchor.output_gzip_sha256",
    )
    _expect(
        hashes["anchor_statistics_json_sha256"],
        anchor_manifest.get("output_json_sha256"),
        "anchor.output_json_sha256",
    )
    _expect(
        hashes["entity_hard_negatives_gzip_sha256"],
        anchor_manifest.get("hard_negatives_sha256"),
        "anchor.hard_negatives_sha256",
    )
    _expect(
        hashes["value_diagnostic_gzip_sha256"],
        value_manifest.get("output_sha256"),
        "value.output_sha256",
    )
    _expect(
        hashes["value_diagnostic_json_sha256"],
        value_manifest.get("output_uncompressed_sha256"),
        "value.output_uncompressed_sha256",
    )

    entity_inputs = entity_manifest.get("input_hashes", {})
    value_sources = value_manifest.get("source_identity", {})
    reach_sources = reachability.get("source_identity", {})
    if not all(isinstance(item, dict) for item in (entity_inputs, value_sources, reach_sources)):
        raise ValueError("source identities must be objects")
    _expect(
        hashes["mission5_report_gzip_sha256"],
        entity_inputs.get("mission5_report_gzip_sha256"),
        "entity.input.mission5_report_gzip_sha256",
    )
    _expect(
        hashes["benchmark_sha256"],
        entity_inputs.get("benchmark_sha256"),
        "entity.input.benchmark_sha256",
    )
    _expect(
        hashes["benchmark_sha256"],
        value_sources.get("benchmark_sha256"),
        "value.source.benchmark_sha256",
    )
    _expect(
        hashes["benchmark_sha256"],
        reach_sources.get("benchmark_sha256"),
        "reachability.source.benchmark_sha256",
    )
    replay_hashes = {
        str(entity_inputs.get("replay_bundle_sha256")),
        str(value_sources.get("replay_bundle_sha256")),
        str(reach_sources.get("replay_bundle_sha256")),
    }
    if len(replay_hashes) != 1 or "None" in replay_hashes:
        raise ValueError("handoff inputs disagree on replay bundle identity")
    replay_sha256 = next(iter(replay_hashes))
    if bool(value_manifest.get("evaluation_and_final_held_used")):
        raise ValueError("value manifest declares protected label use")

    source_identity = {**hashes, "replay_bundle_sha256": replay_sha256}
    integrity = {
        "all_verified": True,
        "compressed_and_uncompressed_payload_hashes_verified": True,
        "mission5_report_identity_verified": True,
        "benchmark_identity_verified": True,
        "replay_bundle_identity_consistent": True,
        "evaluation_and_final_held_used": False,
    }
    return entity, anchor, value, reachability, source_identity, integrity


def main() -> None:
    args = _arguments()
    entity, anchor, value, reachability, source_identity, integrity = _verify_inputs(args)
    result = qualify_specialist_readiness(
        entity,
        anchor,
        value,
        reachability,
        available_anchor_tiers=tuple(args.anchor_tier or ("10k",)),
        source_identity=source_identity,
        integrity=integrity,
    )
    encoded = json.dumps(result, indent=2, sort_keys=True, allow_nan=False).encode() + b"\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(encoded)
    manifest = {
        "schema_version": "aethercore.specialist-readiness-manifest.v1",
        "decision": result["decision"],
        "contextual_model_trained": False,
        "protected_partition_labels_consumed": False,
        "input_sha256": source_identity,
        "output": {
            "file": args.output.name,
            "bytes": len(encoded),
            "sha256": _sha256_bytes(encoded),
        },
    }
    manifest_encoded = (
        json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False).encode() + b"\n"
    )
    args.manifest_output.parent.mkdir(parents=True, exist_ok=True)
    args.manifest_output.write_bytes(manifest_encoded)
    print(json.dumps({key: value for key, value in result.items() if key != "entity_readiness"}))


if __name__ == "__main__":
    main()
