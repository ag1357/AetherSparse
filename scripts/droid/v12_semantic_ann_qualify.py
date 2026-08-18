#!/usr/bin/env python3
"""Run the Mission 7 label-free semantic compression/ANN screen."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from aethersparse.addressing.semantic_ann import (
    BinaryIVFIndex,
    BinaryVariant,
    Int8Vector,
    ProductQuantizer,
    StaticSubwordEncoder,
    binary_code,
    build_binary_ivf,
    dot,
    fit_product_quantizer,
    hamming_distance,
    progressive_ivf_search,
    semantic_manifest_contract,
    training_readiness,
)

SCHEMA_VERSION = "aethersparse.semantic-encoder-ann-qualification.v2"
ALLOWED_PARTITIONS = frozenset({"development", "tuning"})
PROTECTED_PARTITIONS = frozenset({"evaluation", "final_held"})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _objects(value: object, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{field} must be a list of objects")
    return [dict(item) for item in value]


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1)
    return ordered[max(0, index)]


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _register_case_partition(
    partitions: dict[str, str], *, case_id: str, partition: str, source: str
) -> None:
    if not case_id:
        raise ValueError(f"{source} row lacks case_id")
    previous = partitions.get(case_id)
    if previous is not None:
        raise ValueError(
            f"{source} duplicate case_id {case_id}: first={previous}, duplicate={partition}"
        )
    partitions[case_id] = partition


def _load_questions(path: Path) -> tuple[dict[str, str], dict[str, str], dict[str, int]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = _objects(payload.get("cases"), "benchmark.cases")
    questions: dict[str, str] = {}
    case_partitions: dict[str, str] = {}
    counts = {"development": 0, "tuning": 0, "protected_excluded": 0}
    for case in cases:
        partition = str(case.get("partition", ""))
        case_id = str(case.get("case_id", ""))
        _register_case_partition(
            case_partitions, case_id=case_id, partition=partition, source="benchmark"
        )
        if partition in PROTECTED_PARTITIONS:
            counts["protected_excluded"] += 1
            continue
        if partition not in ALLOWED_PARTITIONS:
            raise ValueError(f"benchmark contains unsupported partition: {partition}")
        question = str(case.get("question", ""))
        if not question:
            raise ValueError("training-side benchmark case lacks ID/question")
        questions[case_id] = question
        counts[partition] += 1
    return questions, case_partitions, counts


def _load_diagnostic(
    payload_path: Path,
    manifest_path: Path,
) -> tuple[dict[str, str], tuple[str, ...], dict[str, str], dict[str, Any]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = str(manifest.get("output", {}).get("sha256", ""))
    actual = _sha256(payload_path)
    if actual != expected:
        raise ValueError("397k candidate diagnostic SHA-256 mismatch")
    if manifest.get("schema") != "aethersparse.v10-candidate-diagnostic.v1":
        raise ValueError("unexpected candidate diagnostic schema")
    development_documents: dict[str, str] = {}
    tuning_case_ids: set[str] = set()
    case_partitions: dict[str, str] = {}
    counts = {
        "development_cases": 0,
        "tuning_cases": 0,
        "protected_rows_excluded": 0,
        "development_candidate_rows": 0,
        "title_conflicts": 0,
    }
    with gzip.open(payload_path, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            partition = str(row.get("partition", ""))
            case_id = str(row.get("case_id", ""))
            _register_case_partition(
                case_partitions,
                case_id=case_id,
                partition=partition,
                source="candidate diagnostic",
            )
            if partition in PROTECTED_PARTITIONS:
                counts["protected_rows_excluded"] += 1
                continue
            if partition not in ALLOWED_PARTITIONS:
                raise ValueError(f"candidate diagnostic has unsupported partition: {partition}")
            if partition == "tuning":
                tuning_case_ids.add(case_id)
                counts["tuning_cases"] += 1
                continue
            counts["development_cases"] += 1
            candidates = _objects(row.get("candidates"), "candidate_diagnostic.candidates")
            counts["development_candidate_rows"] += len(candidates)
            for candidate in candidates:
                document_id = str(candidate.get("document_id", ""))
                title = str(candidate.get("title", ""))
                if not document_id or not title:
                    raise ValueError("candidate diagnostic row lacks document ID/title")
                previous = development_documents.get(document_id)
                if previous is not None and previous != title:
                    counts["title_conflicts"] += 1
                    raise ValueError(f"document title conflict: {document_id}")
                development_documents[document_id] = title
    if not development_documents or not tuning_case_ids:
        raise ValueError("candidate diagnostic lacks development index or tuning queries")
    return (
        development_documents,
        tuple(sorted(tuning_case_ids)),
        case_partitions,
        {
            "manifest_sha256": _sha256(manifest_path),
            "payload_sha256": actual,
            "manifest": manifest,
            "counts": counts,
        },
    )


def _verify_training_partition_alignment(
    benchmark_partitions: dict[str, str], diagnostic_partitions: dict[str, str]
) -> None:
    benchmark_training = {
        case_id: partition
        for case_id, partition in benchmark_partitions.items()
        if partition in ALLOWED_PARTITIONS
    }
    diagnostic_training = {
        case_id: partition
        for case_id, partition in diagnostic_partitions.items()
        if partition in ALLOWED_PARTITIONS
    }
    if set(benchmark_training) != set(diagnostic_training):
        missing = sorted(set(benchmark_training) - set(diagnostic_training))
        extra = sorted(set(diagnostic_training) - set(benchmark_training))
        raise ValueError(
            "candidate diagnostic/benchmark training case identities differ: "
            f"missing={missing[:5]}, extra={extra[:5]}"
        )
    partition_mismatches = sorted(
        case_id
        for case_id, partition in diagnostic_training.items()
        if benchmark_training[case_id] != partition
    )
    if partition_mismatches:
        case_id = partition_mismatches[0]
        raise ValueError(
            "candidate diagnostic/benchmark partition mismatch for "
            f"{case_id}: benchmark={benchmark_training[case_id]}, "
            f"diagnostic={diagnostic_training[case_id]}"
        )


def _top_ids(
    identifiers: Sequence[str],
    score: Callable[[int], float],
    *,
    largest: bool,
    top_k: int,
) -> tuple[str, ...]:
    order = sorted(
        range(len(identifiers)),
        key=lambda index: ((-score(index)) if largest else score(index), identifiers[index]),
    )
    return tuple(identifiers[index] for index in order[:top_k])


def _overlap(reference: Sequence[str], candidate: Sequence[str]) -> float:
    return len(set(reference) & set(candidate)) / len(reference) if reference else 0.0


def _float_top_ids(
    identifiers: Sequence[str],
    vectors: Sequence[Sequence[float]],
    query: Sequence[float],
) -> tuple[str, ...]:
    def score(index: int) -> float:
        return dot(query, vectors[index])

    return _top_ids(identifiers, score, largest=True, top_k=16)


def _hamming_top_ids(
    identifiers: Sequence[str], codes: Sequence[bytes], query_code: bytes
) -> tuple[str, ...]:
    def score(index: int) -> float:
        return float(hamming_distance(query_code, codes[index]))

    return _top_ids(identifiers, score, largest=False, top_k=16)


def _pq_top_ids(
    identifiers: Sequence[str],
    quantizer: ProductQuantizer,
    codes: Sequence[bytes],
    query: Sequence[float],
) -> tuple[str, ...]:
    def score(index: int) -> float:
        return quantizer.adc_distance(query, codes[index])

    return _top_ids(identifiers, score, largest=False, top_k=16)


def _int8_top_ids(
    identifiers: Sequence[str],
    codes: Sequence[Int8Vector],
    query: Sequence[float],
) -> tuple[str, ...]:
    def score(index: int) -> float:
        return codes[index].approximate_dot(query)

    return _top_ids(identifiers, score, largest=True, top_k=16)


def _binary_ablation(
    identifiers: Sequence[str],
    vectors: Sequence[Sequence[float]],
    queries: Sequence[Sequence[float]],
    references: Sequence[Sequence[str]],
    *,
    variant: BinaryVariant,
    bits: int,
) -> tuple[dict[str, Any], tuple[bytes, ...], tuple[bytes, ...]]:
    codes = tuple(binary_code(vector, variant=variant, bits=bits) for vector in vectors)
    query_codes = tuple(binary_code(query, variant=variant, bits=bits) for query in queries)
    overlaps: list[float] = []
    for query_code, reference in zip(query_codes, references, strict=True):
        retrieved = _hamming_top_ids(identifiers, codes, query_code)
        overlaps.append(_overlap(reference, retrieved))
    return (
        {
            "variant": variant.value,
            "bits": bits,
            "bytes_per_address": bits // 8,
            "index_code_bytes": len(codes) * bits // 8,
            "mean_top16_overlap_with_float_static": _mean(overlaps),
            "p05_top16_overlap_with_float_static": _percentile(overlaps, 0.05),
            "metric_scope": "compression fidelity to untrained float static reference",
            "semantic_accuracy_measured": False,
            "qualification_scope": "sparse_static_proxy",
        },
        codes,
        query_codes,
    )


def _pq_ablation(
    identifiers: Sequence[str],
    vectors: Sequence[Sequence[float]],
    queries: Sequence[Sequence[float]],
    references: Sequence[Sequence[str]],
    *,
    code_bytes: int,
) -> dict[str, Any]:
    quantizer: ProductQuantizer = fit_product_quantizer(
        vectors,
        code_bytes=code_bytes,
        centroid_count=16,
        iterations=3,
    )
    codes = tuple(quantizer.encode(vector) for vector in vectors)
    overlaps: list[float] = []
    for query, reference in zip(queries, references, strict=True):
        retrieved = _pq_top_ids(identifiers, quantizer, codes, query)
        overlaps.append(_overlap(reference, retrieved))
    return {
        "variant": f"pq_adc_{code_bytes}_byte",
        "bytes_per_address": code_bytes,
        "centroids_per_subquantizer": quantizer.centroid_count,
        "small_data_centroid_screen": quantizer.centroid_count < 256,
        "qualification_status": "PARTIAL_SMALL_DATA_SCREEN",
        "effective_bits_per_subquantizer": math.ceil(math.log2(quantizer.centroid_count)),
        "minimum_packed_code_bytes": math.ceil(
            code_bytes * math.ceil(math.log2(quantizer.centroid_count)) / 8
        ),
        "index_code_bytes": len(codes) * code_bytes,
        "codebook_bytes_float32": quantizer.codebook_bytes_float32,
        "mean_top16_overlap_with_float_static": _mean(overlaps),
        "p05_top16_overlap_with_float_static": _percentile(overlaps, 0.05),
        "metric_scope": "compression fidelity to untrained float static reference",
        "semantic_accuracy_measured": False,
    }


def _int8_ablation(
    identifiers: Sequence[str],
    vectors: Sequence[Sequence[float]],
    queries: Sequence[Sequence[float]],
    references: Sequence[Sequence[str]],
) -> dict[str, Any]:
    codes = tuple(Int8Vector.encode(vector) for vector in vectors)
    overlaps: list[float] = []
    for query, reference in zip(queries, references, strict=True):
        retrieved = _int8_top_ids(identifiers, codes, query)
        overlaps.append(_overlap(reference, retrieved))
    return {
        "variant": "int8_full_vector_rerank",
        "bytes_per_address": len(vectors[0]) + 4,
        "index_code_bytes": len(codes) * (len(vectors[0]) + 4),
        "mean_top16_overlap_with_float_static": _mean(overlaps),
        "p05_top16_overlap_with_float_static": _percentile(overlaps, 0.05),
        "metric_scope": "compression fidelity to untrained float static reference",
        "semantic_accuracy_measured": False,
    }


def _list_metrics(index: BinaryIVFIndex) -> dict[str, float | int]:
    sizes = [float(len(index.lists.get(bucket, ()))) for bucket in range(index.nlist)]
    mean = _mean(sizes)
    variance = _mean([(value - mean) ** 2 for value in sizes])
    return {
        "nonempty_lists": sum(value > 0 for value in sizes),
        "empty_lists": sum(value == 0 for value in sizes),
        "mean_list_size": mean,
        "maximum_list_size": int(max(sizes, default=0.0)),
        "p95_list_size": _percentile(sizes, 0.95),
        "coefficient_of_variation": math.sqrt(variance) / mean if mean else 0.0,
    }


def _ivf_ablation(
    identifiers: Sequence[str],
    codes: Sequence[bytes],
    query_codes: Sequence[bytes],
    exhaustive_hamming: Sequence[Sequence[str]],
    float_references: Sequence[Sequence[str]],
    *,
    nlist: int,
) -> dict[str, Any]:
    index = build_binary_ivf(identifiers, codes, nlist=nlist)
    candidate_counts: list[float] = []
    bytes_read: list[float] = []
    pages: list[float] = []
    hamming_overlap: list[float] = []
    float_overlap: list[float] = []
    for query_code, hamming_reference, float_reference in zip(
        query_codes, exhaustive_hamming, float_references, strict=True
    ):
        result = progressive_ivf_search(index, query_code, nprobe=8, top_k=16)
        candidate_counts.append(float(result.probed_candidates))
        bytes_read.append(float(result.total_bytes_read))
        pages.append(float(result.pages_4k))
        hamming_overlap.append(_overlap(hamming_reference, result.identifiers))
        float_overlap.append(_overlap(float_reference, result.identifiers))
    return {
        "variant": f"binary_ivf_{nlist}",
        "nlist": nlist,
        "nprobe": 8,
        "coarse_partition": "first log2(nlist) raw-BQ bits; no float k-means binarization",
        "list_distribution": _list_metrics(index),
        "mean_probed_candidates": _mean(candidate_counts),
        "p95_probed_candidates": _percentile(candidate_counts, 0.95),
        "mean_progressive_bytes_read": _mean(bytes_read),
        "p95_progressive_bytes_read": _percentile(bytes_read, 0.95),
        "p95_4k_pages": _percentile(pages, 0.95),
        "mean_top16_overlap_with_exhaustive_hamming": _mean(hamming_overlap),
        "mean_top16_overlap_with_float_static": _mean(float_overlap),
        "qualification_scope": "sparse_static_raw_bq_prefix_proxy",
        "physical_layout_serialized": False,
        "physical_io_measured": False,
        "page_count_semantics": "ideal contiguous ceil(total_staged_bytes/4096)",
    }


def qualify(args: argparse.Namespace) -> dict[str, Any]:
    questions, benchmark_partitions, benchmark_counts = _load_questions(args.benchmark)
    documents, tuning_case_ids, diagnostic_partitions, diagnostic = _load_diagnostic(
        args.candidate_diagnostic, args.candidate_manifest
    )
    _verify_training_partition_alignment(benchmark_partitions, diagnostic_partitions)
    eligible_queries = [case_id for case_id in tuning_case_ids if case_id in questions]
    if len(eligible_queries) != len(tuning_case_ids):
        raise ValueError("tuning diagnostic/benchmark case identities differ")
    measured_case_ids = tuple(eligible_queries[: args.query_limit])
    if not measured_case_ids:
        raise ValueError("no tuning questions selected for qualification")

    encoder = StaticSubwordEncoder(dimension=256)
    identifiers = tuple(sorted(documents))
    vectors = tuple(encoder.encode(documents[identifier]) for identifier in identifiers)
    query_vectors = tuple(encoder.encode(questions[case_id]) for case_id in measured_case_ids)
    float_references = tuple(_float_top_ids(identifiers, vectors, query) for query in query_vectors)

    binary_results: list[dict[str, Any]] = []
    raw_256_codes: tuple[bytes, ...] = ()
    raw_256_query_codes: tuple[bytes, ...] = ()
    for variant, bits in (
        (BinaryVariant.RAW, 64),
        (BinaryVariant.RAW, 128),
        (BinaryVariant.RAW, 256),
        (BinaryVariant.GLOBAL_FWHT, 256),
        (BinaryVariant.PREFIX_BLOCK_FWHT, 64),
        (BinaryVariant.PREFIX_BLOCK_FWHT, 128),
        (BinaryVariant.PREFIX_BLOCK_FWHT, 256),
    ):
        result, codes, query_codes = _binary_ablation(
            identifiers,
            vectors,
            query_vectors,
            float_references,
            variant=variant,
            bits=bits,
        )
        binary_results.append(result)
        if variant is BinaryVariant.RAW and bits == 256:
            raw_256_codes = codes
            raw_256_query_codes = query_codes

    exhaustive_hamming = tuple(
        _hamming_top_ids(identifiers, raw_256_codes, query_code)
        for query_code in raw_256_query_codes
    )
    ivf = [
        _ivf_ablation(
            identifiers,
            raw_256_codes,
            raw_256_query_codes,
            exhaustive_hamming,
            float_references,
            nlist=nlist,
        )
        for nlist in (256, 512, 1024)
    ]
    readiness = training_readiness(())
    result = {
        "schema_version": SCHEMA_VERSION,
        "status": "STATIC_MECHANICS_QUALIFIED_LEARNED_TRAINING_BLOCKED",
        "decision": "DO_NOT_TRAIN_WITHOUT_HYPERLINK_OCCURRENCE_SUPERVISION",
        "base_commit": args.base_commit,
        "data_scope": {
            "candidate_diagnostic": "397k post-cap title/document mechanics only",
            "static_proxy_index_source_partition": "benchmark development",
            "static_proxy_query_partition": "benchmark tuning",
            "static_proxy_partitions_are_not_corpus_source_splits": True,
            "development_index_documents": len(identifiers),
            "tuning_queries_available": len(eligible_queries),
            "tuning_queries_measured": len(measured_case_ids),
            "query_selection": "lexicographically first IDs; fixed before metrics",
            "benchmark_counts": benchmark_counts,
            "diagnostic_counts": diagnostic["counts"],
            "evaluation_final_held_used": False,
            "semantic_correctness_labels_used": False,
            "case_id_partition_identity_verified": True,
            "duplicate_case_ids": 0,
            "warning": (
                "top-k overlap measures compression fidelity to a parameter-free static "
                "reference, not entity correctness or hyperlink-supervised recall"
            ),
        },
        "integrity": {
            "candidate_manifest_sha256": diagnostic["manifest_sha256"],
            "candidate_payload_sha256": diagnostic["payload_sha256"],
            "benchmark_sha256": _sha256(args.benchmark),
        },
        "learned_encoder_readiness": {
            **readiness.__dict__,
            "requested_parameter_sweep": [250_000, 1_000_000, 3_000_000, 5_000_000],
            "sweep_started": False,
            "learned_rotation_started": False,
            "additional_blockers": [
                "397k diagnostic has no hyperlink mention-to-target supervision",
                "25k/397k raw occurrence contexts are absent",
                (
                    "verified compiler fit/calibration/holdout hyperlink supervision bundle "
                    "was not supplied"
                ),
                "pre-cap semantic candidate provenance is absent",
            ],
        },
        "compiler_interoperability": {
            **semantic_manifest_contract(),
            "compiler_bundle_loaded_for_this_measurement": False,
            "supervision_manifest_emitted_for_this_measurement": False,
            "index_manifest_emitted_for_this_measurement": False,
            "reason": (
                "the available post-cap candidate diagnostic is not a v2 compiler "
                "hyperlink-supervision bundle"
            ),
            "learned_fit_source_split": "fit",
            "successive_halving_and_model_selection_source_split": "calibration",
            "corpus_only_qualification_source_split": "holdout",
        },
        "static_reference": {
            "kind": "parameter-free signed word/character n-gram hashing",
            "dimension": 256,
            "active_parameters": 0,
            "float32_index_bytes": len(identifiers) * 256 * 4,
            "metric_reference": "exact cosine-equivalent dot-product top16",
        },
        "compression_ablation": {
            "binary": binary_results,
            "pq": [
                _pq_ablation(
                    identifiers,
                    vectors,
                    query_vectors,
                    float_references,
                    code_bytes=code_bytes,
                )
                for code_bytes in (8, 16)
            ],
            "int8": _int8_ablation(identifiers, vectors, query_vectors, float_references),
            "learned_rotation_bq": {
                "status": "NOT_RUN",
                "reason": (
                    "semantic-supervised rotation is unqualified without occurrence labels; "
                    "an unsupervised development-only rotation was not evaluated"
                ),
            },
            "matryoshka_fwht_constraint": {
                "global_fwht": "diagnostic only; its prefix is not original-prefix semantic",
                "prefix_block_fwht": "64-coordinate blocks preserve 64/128/256 boundaries",
            },
        },
        "hamming_associative_baseline": {
            "kind": "exhaustive raw-sign BQ Hamming lookup",
            "code_bits": 256,
            "mean_top16_overlap_with_float_static": _mean(
                [
                    _overlap(reference, result_ids)
                    for reference, result_ids in zip(
                        float_references, exhaustive_hamming, strict=True
                    )
                ]
            ),
        },
        "ivf_progressive_io": ivf,
        "progressive_io_evidence": {
            "status": "ANALYTICAL_STAGED_BYTE_ACCOUNTING_ONLY",
            "physical_layout_serialized": False,
            "physical_io_measured": False,
            "random_vs_sequential_reads_measured": False,
        },
        "limitations": [
            "candidate diagnostic is post-cap and carries no semantic-channel provenance",
            "document titles are mechanics proxies, not canonical entity-address supervision",
            "no entity recall, multi-entity completeness, NLL/Brier/ECE, or policy gate is claimed",
            (
                "PQ uses 16 centroids per byte for a small-data screen; "
                "256-centroid scaling is untested"
            ),
            "host timing is omitted because this pure-Python reference is not the P4 digital twin",
            "global FWHT used one deterministic sign seed; no seed-robust conclusion is claimed",
            "IVF used fixed nprobe=8 over a sparse static proxy; learned dense IVF is untested",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-diagnostic", required=True, type=Path)
    parser.add_argument("--candidate-manifest", required=True, type=Path)
    parser.add_argument("--benchmark", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--query-limit", type=int, default=64)
    parser.add_argument("--base-commit", default="3f74d44f11e6d913520e8d3f110ce3d8912f1f0d")
    args = parser.parse_args()
    if args.query_limit < 1:
        parser.error("--query-limit must be positive")
    result = qualify(args)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
