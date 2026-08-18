#!/usr/bin/env python3
"""Compile Semantic Address v2 data and audit the strict v11 baseline."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from aethersparse.addressing import (
    compile_address_pack,
    compile_benchmark_capture,
    compile_verified_exact_address_index,
    export_v11_benchmark_capture,
)
from aethersparse.controller.replay import load_replay_bundle

_TRAINING = frozenset({"development", "tuning"})
_SEALED = frozenset({"evaluation", "final_held"})
_EXPECTED_BUNDLE = "099cd28b5c2a090d1b08d1e07d39e59ae6583854971c539085741fbb369f0246"
_EXPECTED_CASES = "1254196c179a8d87b9ce6c8301d4873fe1ddf836364a8e03e5b75b9b10c113aa"
_EXPECTED_BENCHMARK = "1e8b89427898df3c3e5efef55135192cf6d48240f0f568c95eb1570470ead113"
_EXPECTED_MISSION5 = "280b314b313b69c72583702898bf135b614d725405587725d4d5f047601327cd"
_PARTITION_PATTERN = re.compile(rb'"partition":"([^"\\]+)"')


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            value = json.load(stream)
    else:
        value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _mission5_training_keys(document: dict[str, Any]) -> set[tuple[str, str]]:
    rows = document.get("per_case")
    if not isinstance(rows, list):
        raise ValueError("Mission 5 report lacks per_case rows")
    keys: set[tuple[str, str]] = set()
    for raw in rows:
        if not isinstance(raw, dict) or raw.get("partition") not in _TRAINING:
            continue
        key = (str(raw.get("case_id", "")), str(raw.get("corpus_tier", "")))
        if key in keys:
            raise ValueError(f"duplicate Mission 5 training key: {key}")
        keys.add(key)
    return keys


def _diagnostic_partition_envelope(path: Path, manifest_path: Path) -> dict[str, object]:
    manifest = _read_json(manifest_path)
    output = manifest.get("output")
    if not isinstance(output, dict) or _sha256(path) != output.get("sha256"):
        raise ValueError("397k candidate diagnostic identity mismatch")
    counts: Counter[str] = Counter()
    rows = 0
    with gzip.open(path, "rb") as stream:
        for line in stream:
            match = _PARTITION_PATTERN.search(line)
            if match is None:
                raise ValueError("candidate diagnostic row lacks partition metadata")
            counts[match.group(1).decode()] += 1
            rows += 1
    if rows != int(manifest["case_count"]):
        raise ValueError("candidate diagnostic row count mismatch")
    return {
        "sha256": _sha256(path),
        "rows": rows,
        "partition_counts_metadata_only": dict(sorted(counts.items())),
        "candidate_payloads_from_sealed_partitions_parsed": False,
        "pre_cap_provenance_available": False,
        "unavailable_fields": manifest.get("unavailable_fields", {}),
    }


def audit_baseline(
    *,
    replay_bundle: Path,
    benchmark: Path,
    mission5_report: Path,
    upstream_report: Path,
    candidate_diagnostic: Path | None,
    candidate_manifest: Path | None,
) -> dict[str, object]:
    """Independently reaggregate the published strict 324/695 evidence."""

    replay_manifest, replay_cases = load_replay_bundle(replay_bundle)
    if replay_manifest.bundle_sha256 != _EXPECTED_BUNDLE:
        raise ValueError("authenticated replay bundle identity changed")
    if replay_manifest.cases_sha256 != _EXPECTED_CASES:
        raise ValueError("authenticated replay cases identity changed")
    if replay_manifest.case_count != 6150 or replay_manifest.decision_count != 54477:
        raise ValueError("authenticated replay dimensions changed")
    if _sha256(benchmark) != _EXPECTED_BENCHMARK:
        raise ValueError("frozen benchmark identity changed")
    if _sha256(mission5_report) != _EXPECTED_MISSION5:
        raise ValueError("Mission 5 report identity changed")
    mission5 = _read_json(mission5_report)
    upstream = _read_json(upstream_report)
    source_identity = upstream.get("source_identity")
    if not isinstance(source_identity, dict):
        raise ValueError("upstream report lacks source identity")
    expected_sources = {
        "replay_bundle_sha256": _EXPECTED_BUNDLE,
        "replay_cases_sha256": _EXPECTED_CASES,
        "benchmark_sha256": _EXPECTED_BENCHMARK,
        "mission5_report_sha256": _EXPECTED_MISSION5,
    }
    if any(source_identity.get(key) != value for key, value in expected_sources.items()):
        raise ValueError("upstream report source identity changed")

    mission5_keys = _mission5_training_keys(mission5)
    if len(mission5_keys) != 695:
        raise ValueError("Mission 5 training failure cohort is not 695 replicas")
    replay_by_key = {(case.case_id, case.corpus_tier): case for case in replay_cases}
    if not mission5_keys.issubset(replay_by_key):
        raise ValueError("authenticated replay lacks Mission 5 training rows")
    if any(
        replay_by_key[key].partition not in _TRAINING or not replay_by_key[key].training_eligible
        for key in mission5_keys
    ):
        raise ValueError("sealed or ineligible replay entered the 695-state cohort")

    rows = upstream.get("per_case")
    if not isinstance(rows, list) or len(rows) != 695:
        raise ValueError("upstream report does not contain exactly 695 per-case rows")
    upstream_keys: set[tuple[str, str]] = set()
    reachable = 0
    residual: Counter[str] = Counter()
    partitions: Counter[str] = Counter()
    old_classes: Counter[str] = Counter()
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("upstream per-case row is malformed")
        partition = str(raw.get("partition", ""))
        if partition not in _TRAINING:
            raise ValueError(f"sealed partition entered upstream report: {partition}")
        key = (str(raw.get("case_id", "")), str(raw.get("corpus_tier", "")))
        if key in upstream_keys:
            raise ValueError(f"duplicate upstream row: {key}")
        upstream_keys.add(key)
        partitions[partition] += 1
        old_classes[str(raw.get("old_failure_class", ""))] += 1
        if bool(raw.get("reachable")):
            reachable += 1
            if raw.get("residual_category") is not None:
                raise ValueError("reachable row retains a residual category")
        else:
            residual[str(raw.get("residual_category", ""))] += 1
    if upstream_keys != mission5_keys:
        raise ValueError("upstream and Mission 5 cohort identities differ")
    if reachable != 324 or residual != Counter(
        {
            "SEMANTIC_ADDRESS_GENERATION": 355,
            "EVIDENCE_RETRIEVAL": 8,
            "VALUE_AVAILABILITY": 7,
            "TOOLSET_CONTROLLER": 1,
        }
    ):
        raise ValueError("published 324/695 baseline did not reproduce")
    if upstream.get("residual_limitation") != dict(sorted(residual.items())):
        raise ValueError("upstream aggregate residual does not match per-case rows")
    comparison = upstream.get("comparison")
    if not isinstance(comparison, dict) or comparison.get("new_reachable") != reachable:
        raise ValueError("upstream reachability aggregate does not match per-case rows")

    diagnostic: dict[str, object] | None = None
    if candidate_diagnostic is not None or candidate_manifest is not None:
        if candidate_diagnostic is None or candidate_manifest is None:
            raise ValueError("candidate diagnostic and manifest must be supplied together")
        diagnostic = _diagnostic_partition_envelope(candidate_diagnostic, candidate_manifest)
    return {
        "schema_version": "aethersparse.semantic-address-v2-data-audit.v1",
        "status": "STRICT_BASELINE_EVIDENCE_REPRODUCED",
        "reproduction_scope": {
            "fresh_search_rerun": False,
            "reason": "private v11 occurrence/value inputs are not present in Work",
            "independent_per_case_reaggregation": True,
            "authenticated_replay_verified": True,
        },
        "source_identity": {
            **expected_sources,
            "upstream_report_sha256": _sha256(upstream_report),
        },
        "replay": {
            "cases": replay_manifest.case_count,
            "decisions": replay_manifest.decision_count,
            "tier_counts": replay_manifest.tier_counts,
            "partition_counts": replay_manifest.partition_counts,
        },
        "strict_baseline": {
            "eligible": len(rows),
            "reachable": reachable,
            "reachability": reachable / len(rows),
            "partition_counts": dict(sorted(partitions.items())),
            "old_failure_classes": dict(sorted(old_classes.items())),
            "residual": dict(sorted(residual.items())),
        },
        "partition_policy": {
            "training_partitions": sorted(_TRAINING),
            "sealed_partitions": sorted(_SEALED),
            "sealed_rows_in_qualification": 0,
            "evaluation_final_labels_used_for_design_fitting_calibration": False,
        },
        "candidate_diagnostic": diagnostic,
        "available_address_data": {
            "v11_10k_aggregate_rows": 345,
            "v11_10k_occurrences": 6112,
            "v11_10k_covered_surfaces": 126,
            "occurrence_level_10k_payload_present": False,
            "occurrence_level_25k_payload_present": False,
            "occurrence_level_397k_payload_present": False,
            "explicit_mention_alignment_present": False,
            "pre_cap_candidate_provenance_present": False,
        },
        "semantic_address_v2_compiler": {
            "source_mode": "sqlite mode=ro&immutable=1",
            "occurrence_export_streaming": True,
            "surface_aggregation_disk_backed": True,
            "deterministic_content_addressed_streams": True,
            "runtime_features_exclude_labels": True,
            "development_labels_separate": True,
            "tuning_scoring_labels_separate": True,
            "ambiguous_alignment_quarantined": True,
            "sealed_partitions_rejected": True,
            "source_document_holdout": True,
            "pre_cap_channels": ["alias", "anchor", "redirect", "title"],
            "source_schema": "schemas/semantic-address-v2.schema.json",
            "factory_handoff": "docs/reproduction/V12_SEMANTIC_ADDRESS_DATA_HANDOFF.md",
        },
        "decision": "FACTORY_ADDRESS_V2_CAPTURE_REQUIRED",
    }


def finalize_handoff(root: Path, output: Path) -> dict[str, object]:
    """Hash every regular file in a targeted Factory handoff directory."""

    if not root.is_dir():
        raise FileNotFoundError(root)
    paths = sorted(root.rglob("*"))
    if any(path.is_symlink() for path in paths):
        raise ValueError("handoff root may not contain symlinks")
    files = [path for path in paths if path.is_file()]
    if not files:
        raise ValueError("handoff root contains no files")
    forbidden_suffixes = {".aeth", ".bz2", ".sqlite", ".sqlite3"}
    raw_payloads = [path for path in files if path.suffix.casefold() in forbidden_suffixes]
    if raw_payloads:
        raise ValueError(f"handoff root contains raw corpus packs: {raw_payloads}")
    resolved_output = output.resolve()
    entries = [
        {
            "file": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in files
        if path.resolve() != resolved_output
    ]
    manifest = {
        "schema_version": "aethersparse.semantic-address-v2-factory-handoff.v1",
        "file_count": len(entries),
        "files": entries,
        "raw_corpus_pack_included": False,
        "sealed_partition_labels_included": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    compile_pack = commands.add_parser("compile-pack")
    compile_pack.add_argument("--pack", type=Path, required=True)
    compile_pack.add_argument("--tier", required=True)
    compile_pack.add_argument("--output", type=Path, required=True)
    compile_pack.add_argument("--context-characters", type=int, default=96)

    compile_benchmark = commands.add_parser("compile-benchmark")
    compile_benchmark.add_argument("--capture", type=Path, required=True)
    compile_benchmark.add_argument("--output", type=Path, required=True)

    compile_exact = commands.add_parser("compile-exact")
    compile_exact.add_argument("--address-export", type=Path, required=True)
    compile_exact.add_argument("--output", type=Path, required=True)
    compile_exact.add_argument(
        "--consumer-phase",
        choices=("fit", "selection", "holdout_qualification", "descriptive"),
        default="fit",
    )
    compile_exact.add_argument(
        "--source-splits",
        nargs="+",
        choices=("fit", "calibration", "holdout"),
        default=("fit",),
    )

    export_benchmark = commands.add_parser("export-v11-benchmark")
    export_benchmark.add_argument("--pack", type=Path, required=True)
    export_benchmark.add_argument("--hard-negatives", type=Path, required=True)
    export_benchmark.add_argument("--tier", required=True)
    export_benchmark.add_argument("--output", type=Path, required=True)

    audit = commands.add_parser("audit-baseline")
    audit.add_argument("--replay-bundle", type=Path, required=True)
    audit.add_argument("--benchmark", type=Path, required=True)
    audit.add_argument("--mission5-report", type=Path, required=True)
    audit.add_argument("--upstream-report", type=Path, required=True)
    audit.add_argument("--candidate-diagnostic", type=Path)
    audit.add_argument("--candidate-manifest", type=Path)
    audit.add_argument("--output", type=Path, required=True)

    handoff = commands.add_parser("finalize-handoff")
    handoff.add_argument("--root", type=Path, required=True)
    handoff.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    printable: dict[str, object]

    if args.command == "compile-pack":
        pack_result = compile_address_pack(
            args.pack,
            args.output,
            corpus_tier=args.tier,
            context_characters=args.context_characters,
        )
        printable = {
            "schema_version": pack_result.schema_version,
            "corpus_tier": pack_result.corpus_tier,
            "counts": pack_result.counts,
        }
    elif args.command == "compile-benchmark":
        benchmark_result = compile_benchmark_capture(args.capture, args.output)
        printable = {
            "schema_version": benchmark_result.schema_version,
            "counts": benchmark_result.counts,
        }
    elif args.command == "compile-exact":
        exact_result = compile_verified_exact_address_index(
            args.address_export,
            args.output,
            included_source_splits=args.source_splits,
            consumer_phase=args.consumer_phase,
        )
        printable = {
            "schema_version": "aethersparse.exact-address-index.v12",
            "root_sha256": exact_result.root_sha256,
            "file_sha256": exact_result.file_sha256,
            "surface_count": exact_result.surface_count,
            "posting_count": exact_result.posting_count,
        }
    elif args.command == "export-v11-benchmark":
        factory_result = export_v11_benchmark_capture(
            pack=args.pack,
            hard_negatives=args.hard_negatives,
            corpus_tier=args.tier,
            output=args.output,
        )
        manifest_path = args.output.with_suffix(args.output.suffix + ".manifest.json")
        manifest_path.write_text(
            json.dumps(factory_result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        printable = {key: value for key, value in factory_result.items() if key != "output"}
    elif args.command == "finalize-handoff":
        handoff_result = finalize_handoff(args.root, args.output)
        printable = {
            "schema_version": handoff_result["schema_version"],
            "file_count": handoff_result["file_count"],
        }
    else:
        audit_result = audit_baseline(
            replay_bundle=args.replay_bundle,
            benchmark=args.benchmark,
            mission5_report=args.mission5_report,
            upstream_report=args.upstream_report,
            candidate_diagnostic=args.candidate_diagnostic,
            candidate_manifest=args.candidate_manifest,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(audit_result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        printable = {
            key: value for key, value in audit_result.items() if key != "candidate_diagnostic"
        }
    print(json.dumps(printable, sort_keys=True))


if __name__ == "__main__":
    main()
