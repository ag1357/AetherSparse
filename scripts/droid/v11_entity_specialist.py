#!/usr/bin/env python3
"""Freeze and qualify the Mission 6 entity hard-negative lane."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

from aethersparse.controller.entity_specialist import (
    ENTITY_FEATURE_NAMES,
    LinearEntityRanker,
    WeightedCandidate,
    classify_entity_residual,
    extract_anchor_statistics,
    fit_linear_entity_ranker,
)
from aethersparse.controller.models import EntityCandidate, EntityMention

SCHEMA_VERSION = "aethersparse.entity-hard-negatives.v11"
BASELINE_SCHEMA_VERSION = "aethersparse.entity-specialist-baselines.v11"
AVAILABLE_PARTITIONS = frozenset({"development", "tuning"})
EXPECTED_REPLICA_COUNT = 346
EXPECTED_UNIQUE_CASE_COUNT = 175


def _stable_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _gzip_bytes(value: bytes) -> bytes:
    return gzip.compress(value, compresslevel=9, mtime=0)


def _write_json(path: Path, value: object) -> None:
    path.write_bytes(json.dumps(value, indent=2, sort_keys=True).encode() + b"\n")


def _final_frame(case: dict[str, Any]) -> dict[str, Any]:
    for decision in reversed(case["decisions"]):
        frame = decision.get("query_frame")
        if frame:
            return dict(frame)
    raise ValueError(f"replay case {case['case_id']} has no retained query frame")


def _mention_record(mention: EntityMention, required: frozenset[str]) -> dict[str, Any]:
    candidates = [candidate.model_dump(mode="json") for candidate in mention.candidates]
    runner_up = mention.candidates[1].confidence if len(mention.candidates) > 1 else 0.0
    margin = mention.candidates[0].confidence - runner_up if mention.candidates else None
    return {
        "surface": mention.surface,
        "char_start": mention.char_start,
        "char_end": mention.char_end,
        "copy_status": mention.copy_status,
        "resolution_method": mention.resolution_method,
        "selected_entity_id": mention.selected_entity_id,
        "selected_confidence": mention.selected_confidence,
        "candidate_count_retained": len(mention.candidates),
        "top_candidate_margin": margin,
        "candidates": candidates,
        "hard_negative_candidates": [
            candidate for candidate in candidates if candidate["entity_id"] not in required
        ],
        "correct_candidate_ids_present": sorted(
            candidate["entity_id"] for candidate in candidates if candidate["entity_id"] in required
        ),
        "correct_entity_per_mention": None,
    }


def _load_residual_index(
    report_path: Path,
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    with gzip.open(report_path, "rt", encoding="utf-8") as stream:
        report = json.load(stream)
    selected = {
        (str(item["case_id"]), str(item["corpus_tier"])): item
        for item in report["per_case"]
        if item["failure_class"] == "ENTITY_BINDING_WRONG"
        and item["partition"] in AVAILABLE_PARTITIONS
    }
    return selected, report


def _load_training_benchmark(
    benchmark_path: Path, residual_case_ids: set[str]
) -> dict[str, dict[str, Any]]:
    document = json.loads(benchmark_path.read_text(encoding="utf-8"))
    selected: dict[str, dict[str, Any]] = {}
    for item in document["cases"]:
        case_id = str(item["case_id"])
        if case_id not in residual_case_ids:
            continue
        if item["partition"] not in AVAILABLE_PARTITIONS:
            raise ValueError(f"residual case {case_id} would expose a sealed partition")
        selected[case_id] = {
            "case_id": case_id,
            "partition": str(item["partition"]),
            "question": str(item["question"]),
            "required_answer_shape": str(item["required_answer_shape"]),
            "required_entity_ids": tuple(str(value) for value in item["required_entity_ids"]),
            "prior_case_ids": tuple(str(value) for value in item.get("prior_case_ids", ())),
        }
    if set(selected) != residual_case_ids:
        missing = sorted(residual_case_ids - set(selected))
        raise ValueError(f"training benchmark lacks residual cases: {missing[:5]}")
    return selected


def _freeze_corpus(
    *,
    report_path: Path,
    replay_bundle: Path,
    benchmark_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    residuals, report = _load_residual_index(report_path)
    residual_case_ids = {case_id for case_id, _tier in residuals}
    benchmark = _load_training_benchmark(benchmark_path, residual_case_ids)
    replay_manifest_path = replay_bundle / "manifest.json"
    replay_cases_path = replay_bundle / "cases.jsonl.gz"
    replay_manifest = json.loads(replay_manifest_path.read_text(encoding="utf-8"))
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    observed: set[tuple[str, str]] = set()
    with gzip.open(replay_cases_path, "rt", encoding="utf-8") as stream:
        for line in stream:
            case = json.loads(line)
            key = (str(case["case_id"]), str(case["corpus_tier"]))
            if key not in residuals:
                continue
            if not case.get("training_eligible") or case["partition"] not in AVAILABLE_PARTITIONS:
                raise ValueError(f"residual replay is not training eligible: {key}")
            benchmark_case = benchmark[key[0]]
            if case["partition"] != benchmark_case["partition"]:
                raise ValueError(f"partition mismatch for {key}")
            frame = _final_frame(case)
            mentions = tuple(
                EntityMention.model_validate(item) for item in frame["entity_mentions"]
            )
            required = frozenset(benchmark_case["required_entity_ids"])
            groups[key[0]].append(
                {
                    "corpus_tier": key[1],
                    "source_trace_sha256": case["source_trace_sha256"],
                    "training_eligible": True,
                    "failure_class": classify_entity_residual(tuple(required), mentions),
                    "answer_shape": frame["answer_shape"],
                    "requested_relation_families": frame["requested_relation_families"],
                    "query_uncertainty": frame["uncertainty"],
                    "clarification_need": frame["clarification_need"],
                    "selected_entity_ids": frame["candidate_entity_ids"],
                    "discourse_references": frame["discourse_references"],
                    "mentions": [_mention_record(mention, required) for mention in mentions],
                }
            )
            observed.add(key)
    if observed != set(residuals):
        missing = sorted(set(residuals) - observed)
        raise ValueError(f"replay bundle lacks residual replicas: {missing[:5]}")
    if len(observed) != EXPECTED_REPLICA_COUNT or len(groups) != EXPECTED_UNIQUE_CASE_COUNT:
        raise ValueError(
            "Mission 5 entity residual identity changed: "
            f"replicas={len(observed)}, unique={len(groups)}"
        )

    cases = []
    partition_case_ids: dict[str, list[str]] = defaultdict(list)
    for case_id in sorted(groups):
        item = benchmark[case_id]
        replicas = sorted(groups[case_id], key=lambda value: value["corpus_tier"])
        partition_case_ids[item["partition"]].append(case_id)
        cases.append(
            {
                "case_id": case_id,
                "partition": item["partition"],
                "query": item["question"],
                "query_context": item["question"],
                "required_answer_shape": item["required_answer_shape"],
                "correct_entity_ids": item["required_entity_ids"],
                "correct_entity_supervision": "case_level_only",
                "prior_case_ids": item["prior_case_ids"],
                "replicas": replicas,
            }
        )

    unavailable_fields = {
        "anchor_occurrence_count": "canonical v0.5 SQLite pack is not present in Work",
        "anchor_prior": "requires occurrence-level anchor rows from the tier pack",
        "alias_redirect_anchor_support": "only the winning method enum is retained per candidate",
        "edit_similarity": "name_score is retained, but the underlying edit distance is not",
        "actual_entity_type": "type_score=1.0 is a neutral placeholder in this replay",
        "correct_entity_per_mention": (
            "gold required IDs are case-level and have no mention alignment"
        ),
        "candidate_pool_before_top8": "only each mention's retained bounded candidates are present",
        "correct_entity_outside_top_k": "cannot be distinguished from not generated",
        "candidate_cap_failure": "pre-cap candidates and generation counts are absent",
        "wrong_alias_target": "raw alias-to-target support is absent",
        "redirect_canonicalization_failure": "raw redirect path is absent",
        "type_mismatch": "actual candidate entity types are absent",
        "discourse_coreference_mismatch": "case-level entity labels are not aligned to references",
    }
    corpus = {
        "schema_version": SCHEMA_VERSION,
        "name": "ENTITY_HARD_NEGATIVES_V11",
        "source_mission5_status": report["status"],
        "sealed_partitions_excluded": ["evaluation", "final_held"],
        "split_policy": {
            "development": "model fitting only",
            "tuning": "calibration and model selection only",
            "group_key": "case_id",
            "replicas_never_cross_partitions": True,
        },
        "replica_count": len(observed),
        "unique_case_count": len(groups),
        "partition_counts": {
            partition: {
                "unique_cases": len(case_ids),
                "replicas": sum(len(groups[case_id]) for case_id in case_ids),
            }
            for partition, case_ids in sorted(partition_case_ids.items())
        },
        "available_fields": [
            "query",
            "mention surface and offsets",
            "retained candidate IDs/titles/methods",
            "current name/relation/context scores",
            "selected entity IDs",
            "confidence and retained-set margin",
            "retained ambiguity count",
            "discourse reference state",
            "case-level correct entity IDs",
        ],
        "unavailable_fields": unavailable_fields,
        "cases": cases,
    }
    input_hashes = {
        "mission5_report_gzip_sha256": _sha256_file(report_path),
        "mission5_report_json_sha256": _sha256_bytes(gzip.decompress(report_path.read_bytes())),
        "replay_manifest_sha256": _sha256_file(replay_manifest_path),
        "replay_cases_gzip_sha256": _sha256_file(replay_cases_path),
        "replay_cases_sha256": replay_manifest["cases_sha256"],
        "replay_bundle_sha256": replay_manifest["bundle_sha256"],
        "benchmark_sha256": _sha256_file(benchmark_path),
    }
    return corpus, input_hashes


def _candidate_pool(replica: dict[str, Any]) -> tuple[EntityCandidate, ...]:
    candidates: dict[str, EntityCandidate] = {}
    for mention in replica["mentions"]:
        for raw in mention["candidates"]:
            candidate = EntityCandidate.model_validate(raw)
            previous = candidates.get(candidate.entity_id)
            if previous is None or candidate.confidence > previous.confidence:
                candidates[candidate.entity_id] = candidate
    return tuple(sorted(candidates.values(), key=lambda item: (-item.confidence, item.entity_id)))


def _replicas(
    corpus: dict[str, Any], partition: str
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    return [
        (case, replica)
        for case in corpus["cases"]
        if case["partition"] == partition
        for replica in case["replicas"]
    ]


def _calibration(values: list[tuple[float, bool]]) -> dict[str, float | int]:
    if not values:
        return {"count": 0, "positive_count": 0, "brier": 0.0, "nll": 0.0, "ece_10": 0.0}
    brier = sum((probability - float(label)) ** 2 for probability, label in values) / len(values)
    nll = -sum(
        float(label) * math.log(max(1e-12, probability))
        + (1.0 - float(label)) * math.log(max(1e-12, 1.0 - probability))
        for probability, label in values
    ) / len(values)
    ece = 0.0
    for index in range(10):
        lower = index / 10
        upper = (index + 1) / 10
        bucket = [
            item for item in values if lower <= item[0] < upper or (index == 9 and item[0] == 1.0)
        ]
        if not bucket:
            continue
        confidence = sum(item[0] for item in bucket) / len(bucket)
        accuracy = sum(item[1] for item in bucket) / len(bucket)
        ece += len(bucket) / len(values) * abs(confidence - accuracy)
    return {
        "count": len(values),
        "positive_count": sum(item[1] for item in values),
        "brier": brier,
        "nll": nll,
        "ece_10": ece,
    }


def _metrics(
    corpus: dict[str, Any], partition: str, ranker: LinearEntityRanker | None
) -> dict[str, Any]:
    replicas = _replicas(corpus, partition)
    counters: Counter[str] = Counter()
    candidate_probabilities: list[tuple[float, bool]] = []
    selective_rows: list[tuple[float, bool]] = []
    taxonomy: Counter[str] = Counter()
    single_generated = 0
    single_top1 = 0
    for case, replica in replicas:
        required = set(case["correct_entity_ids"])
        selected = set(replica["selected_entity_ids"])
        pool = _candidate_pool(replica)
        ranked = sorted(
            pool,
            key=lambda candidate: (
                -(ranker.probability(candidate) if ranker else candidate.confidence),
                candidate.entity_id,
            ),
        )
        candidate_ids = [candidate.entity_id for candidate in ranked]
        counters["selected_all_required"] += required.issubset(selected)
        counters["selected_any_required"] += bool(required.intersection(selected))
        counters["selected_contains_wrong"] += bool(selected - required)
        counters["selected_empty"] += not selected
        counters["candidate_recall_any"] += bool(required.intersection(candidate_ids))
        counters["candidate_recall_all"] += required.issubset(candidate_ids)
        counters["top1_any"] += bool(candidate_ids) and candidate_ids[0] in required
        counters["top2_recall_all"] += required.issubset(candidate_ids[:2])
        counters["top4_recall_all"] += required.issubset(candidate_ids[:4])
        if len(required) == 1:
            counters["single_entity_replicas"] += 1
            if required.issubset(candidate_ids):
                single_generated += 1
                single_top1 += bool(candidate_ids) and candidate_ids[0] in required
        taxonomy[replica["failure_class"]] += 1
        for candidate in pool:
            probability = ranker.probability(candidate) if ranker else candidate.confidence
            candidate_probabilities.append((probability, candidate.entity_id in required))
        if ranked:
            top_probability = ranker.probability(ranked[0]) if ranker else ranked[0].confidence
            selective_rows.append((top_probability, ranked[0].entity_id in required))

    count = len(replicas)
    result: dict[str, Any] = {
        "replica_count": count,
        "taxonomy": dict(sorted(taxonomy.items())),
        "selected_all_required": counters["selected_all_required"],
        "selected_all_required_rate": counters["selected_all_required"] / count,
        "selected_any_required": counters["selected_any_required"],
        "selected_contains_wrong": counters["selected_contains_wrong"],
        "selected_empty": counters["selected_empty"],
        "candidate_recall_any": counters["candidate_recall_any"],
        "candidate_recall_any_rate": counters["candidate_recall_any"] / count,
        "candidate_recall_all": counters["candidate_recall_all"],
        "candidate_recall_all_rate": counters["candidate_recall_all"] / count,
        "top1_candidate_relevant": counters["top1_any"],
        "top1_candidate_relevant_rate": counters["top1_any"] / count,
        "top2_recall_all": counters["top2_recall_all"],
        "top4_recall_all": counters["top4_recall_all"],
        "single_entity_replicas": counters["single_entity_replicas"],
        "single_entity_candidate_generated": single_generated,
        "single_entity_top1_correct": single_top1,
        "single_entity_top1_accuracy_all": single_top1 / max(1, counters["single_entity_replicas"]),
        "single_entity_top1_accuracy_when_generated": single_top1 / max(1, single_generated),
        "candidate_relevance_calibration": _calibration(candidate_probabilities),
    }
    result["top1_selective_risk"] = [
        {
            "threshold": threshold,
            "covered": len(covered),
            "coverage": len(covered) / count,
            "risk": 1.0 - sum(correct for _probability, correct in covered) / len(covered)
            if covered
            else None,
        }
        for threshold in (0.2, 0.4, 0.6, 0.8)
        if (covered := [item for item in selective_rows if item[0] >= threshold]) is not None
    ]
    return result


def _fit_ranker(corpus: dict[str, Any]) -> LinearEntityRanker:
    development = _replicas(corpus, "development")
    replicas_per_case = Counter(case["case_id"] for case, _replica in development)
    observations: list[WeightedCandidate] = []
    for case, replica in development:
        pool = _candidate_pool(replica)
        if not pool:
            continue
        required = set(case["correct_entity_ids"])
        weight = 1.0 / (replicas_per_case[case["case_id"]] * len(pool))
        observations.extend(
            WeightedCandidate(candidate, candidate.entity_id in required, weight)
            for candidate in pool
        )
    return fit_linear_entity_ranker(observations)


def _unique_case_summary(corpus: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for partition in sorted(AVAILABLE_PARTITIONS):
        cases = [case for case in corpus["cases"] if case["partition"] == partition]
        any_complete = 0
        for case in cases:
            required = set(case["correct_entity_ids"])
            complete = any(
                required.issubset(candidate.entity_id for candidate in _candidate_pool(replica))
                for replica in case["replicas"]
            )
            any_complete += complete
        summary[partition] = {
            "unique_cases": len(cases),
            "candidate_complete_in_any_replica": any_complete,
            "candidate_complete_in_any_replica_rate": any_complete / len(cases),
        }
    return summary


def _baseline_report(corpus: dict[str, Any]) -> dict[str, Any]:
    ranker = _fit_ranker(corpus)
    report = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "dataset": "ENTITY_HARD_NEGATIVES_V11",
        "metric_scope": {
            "strict_entity_binding": "all case-level required IDs are selected",
            "top1_candidate_relevant": "top pooled candidate is any case-level required ID",
            "calibration": "candidate relevance under weak case-level multi-label supervision",
            "warning": "per-mention correctness is not measurable from this replay",
        },
        "current_linker": {
            partition: _metrics(corpus, partition, None) for partition in ("development", "tuning")
        },
        "development_fitted_linear_reranker": {
            "feature_names": ENTITY_FEATURE_NAMES,
            "weights": ranker.weights,
            "parameter_count": len(ranker.weights),
            "fit_partition": "development",
            "case_replica_weighting": (
                "each case has unit total weight; candidates divide replica weight"
            ),
            "partitions": {
                partition: _metrics(corpus, partition, ranker)
                for partition in ("development", "tuning")
            },
        },
        "unique_case_summary": _unique_case_summary(corpus),
        "baseline_support": {
            "A_current_linker": "measured",
            "B_anchor_prior": "blocked: tier SQLite packs absent; targeted exporter implemented",
            "C_ambiguity_entropy": "blocked with B",
            "D_actual_type_compatibility": "unsupported: actual types absent",
            "E_relation_compatibility": "already present as current binary relation_score",
            "F_discourse_compatibility": "unsupported: no mention-to-gold antecedent alignment",
            "G_calibrated_linear_scoring": (
                "measured with development fitting and tuning evaluation"
            ),
            "H_compact_nonlinear_tabular": "not justified before candidate generation is repaired",
        },
        "contextual_specialist_decision": {
            "decision": "DO_NOT_TRAIN_CONTEXTUAL_ENTITY_SPECIALIST_FROM_V10_REPLAY",
            "reason": [
                "most residual replicas lack at least one required candidate",
                "correct entities are labeled only at case level, not per mention",
                "candidate context and occurrence-level anchor evidence are absent",
                "a bounded scorer cannot recover an entity outside its candidate set",
            ],
            "parameter_sweep_started": False,
        },
    }
    return report


def _freeze(args: argparse.Namespace) -> None:
    output_directory: Path = args.output_directory
    output_directory.mkdir(parents=True, exist_ok=True)
    corpus, input_hashes = _freeze_corpus(
        report_path=args.mission5_report,
        replay_bundle=args.replay_bundle,
        benchmark_path=args.benchmark,
    )
    raw = _stable_json(corpus)
    compressed = _gzip_bytes(raw)
    corpus_path = output_directory / "ENTITY_HARD_NEGATIVES_V11.json.gz"
    corpus_path.write_bytes(compressed)
    baseline = _baseline_report(corpus)
    baseline_path = output_directory / "entity-specialist-baselines.json"
    _write_json(baseline_path, baseline)
    manifest = {
        "schema_version": "aethersparse.entity-hard-negatives-manifest.v11",
        "name": "ENTITY_HARD_NEGATIVES_V11",
        "replica_count": corpus["replica_count"],
        "unique_case_count": corpus["unique_case_count"],
        "partition_counts": corpus["partition_counts"],
        "input_hashes": input_hashes,
        "output": {
            "file": corpus_path.name,
            "compressed_bytes": len(compressed),
            "uncompressed_bytes": len(raw),
            "gzip_sha256": _sha256_bytes(compressed),
            "json_sha256": _sha256_bytes(raw),
        },
        "baseline": {
            "file": baseline_path.name,
            "sha256": _sha256_file(baseline_path),
        },
        "partition_case_id_sha256": {
            partition: _sha256_bytes(
                (
                    "\n".join(
                        case["case_id"]
                        for case in corpus["cases"]
                        if case["partition"] == partition
                    )
                    + "\n"
                ).encode()
            )
            for partition in sorted(AVAILABLE_PARTITIONS)
        },
        "sealed_partitions_excluded": corpus["sealed_partitions_excluded"],
        "unavailable_fields": corpus["unavailable_fields"],
    }
    manifest_path = output_directory / "ENTITY_HARD_NEGATIVES_V11.manifest.json"
    _write_json(manifest_path, manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True))


def _anchor_export(args: argparse.Namespace) -> None:
    corpus = json.loads(gzip.decompress(args.hard_negatives.read_bytes()))
    mentions = sorted(
        {
            mention["surface"]
            for case in corpus["cases"]
            for replica in case["replicas"]
            for mention in replica["mentions"]
        }
    )
    statistics = extract_anchor_statistics(args.pack, alpha=args.alpha, mentions=mentions)
    document = {
        "schema_version": "aethersparse.entity-anchor-statistics.v11",
        "source_pack_sha256": _sha256_file(args.pack),
        "alpha": args.alpha,
        "requested_mention_count": len(mentions),
        "covered_mention_count": len({item.mention for item in statistics}),
        "statistics": [asdict(item) for item in statistics],
    }
    raw = _stable_json(document)
    compressed = _gzip_bytes(raw)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(compressed)
    manifest = {
        "schema_version": "aethersparse.entity-anchor-statistics-manifest.v11",
        "source_pack_sha256": _sha256_file(args.pack),
        "hard_negatives_sha256": _sha256_file(args.hard_negatives),
        "alpha": args.alpha,
        "requested_mention_count": len(mentions),
        "covered_mention_count": document["covered_mention_count"],
        "statistic_count": len(statistics),
        "output_gzip_sha256": _sha256_bytes(compressed),
        "output_json_sha256": _sha256_bytes(raw),
    }
    manifest_path = args.output.with_suffix(args.output.suffix + ".manifest.json")
    _write_json(manifest_path, manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    freeze = subparsers.add_parser("freeze")
    freeze.add_argument("--mission5-report", required=True, type=Path)
    freeze.add_argument("--replay-bundle", required=True, type=Path)
    freeze.add_argument("--benchmark", required=True, type=Path)
    freeze.add_argument("--output-directory", required=True, type=Path)
    freeze.set_defaults(run=_freeze)
    anchor = subparsers.add_parser("anchor-export")
    anchor.add_argument("--pack", required=True, type=Path)
    anchor.add_argument("--hard-negatives", required=True, type=Path)
    anchor.add_argument("--output", required=True, type=Path)
    anchor.add_argument("--alpha", default=1.0, type=float)
    anchor.set_defaults(run=_anchor_export)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
