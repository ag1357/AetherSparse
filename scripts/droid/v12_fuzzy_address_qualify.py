#!/usr/bin/env python3
"""Qualify bounded fuzzy addressing on authenticated 397k title surfaces.

This runner deliberately refuses to describe the post-cap candidate diagnostic
as a corpus-wide address index.  It uses development labels only to choose the
two approximate thresholds and opens tuning labels only after those choices are
frozen.  Evaluation and final-held rows are rejected before candidate/query
content is consumed.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aethersparse.controller.fuzzy_address import (
    AddressSurfaceRecord,
    FuzzyAddressIndex,
    FuzzyChannel,
    FuzzyLookupResult,
    logical_index_bytes,
    normalize_fuzzy_surface,
    union_address_results,
)
from aethersparse.controller.replay import ReplayCase, load_replay_bundle
from aethersparse.controller.semantic_address import canonical_entity_id, normalize_mention
from aethersparse.specialists.p4_cost import (
    P4OperationCost,
    project_p4,
    v11_reference_assumptions,
)

ALLOWED_PARTITIONS = frozenset({"development", "tuning"})
PROTECTED_PARTITIONS = frozenset({"evaluation", "final_held"})
EXPECTED_DIAGNOSTIC_SCHEMA = "aethersparse.v10-candidate-diagnostic.v1"
EXPECTED_REPLAY_IDENTITY = "099cd28b5c2a090d1b08d1e07d39e59ae6583854971c539085741fbb369f0246"
SCHEMA_VERSION = "aethersparse.fuzzy-address-qualification.v12"


@dataclass(frozen=True)
class _Case:
    case_id: str
    partition: str
    query: str
    required_entity_ids: tuple[str, ...]


@dataclass(frozen=True)
class _Perturbation:
    kind: str
    query: str
    entity_id: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _stable_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        + b"\n"
    )


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(value, indent=2, sort_keys=True).encode() + b"\n")


def _p95(values: Sequence[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _mean(values: Sequence[int]) -> float:
    return sum(values) / len(values) if values else 0.0


def _percentile(values: Sequence[int | float], quantile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _distribution(values: Sequence[int]) -> dict[str, float | int]:
    return {
        "mean": _mean(values),
        "p50": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
        "maximum": max(values, default=0),
    }


def _final_query(case: ReplayCase) -> str:
    for decision in reversed(case.decisions):
        value = decision.query_frame.get("normalized_query")
        if isinstance(value, str) and value:
            return value
    raise ValueError(f"replay case lacks a final normalized query: {case.case_id}")


def _load_diagnostic(
    path: Path, manifest_path: Path
) -> tuple[dict[str, dict[str, Any]], tuple[AddressSurfaceRecord, ...], dict[str, Any]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, Mapping) or manifest.get("schema") != EXPECTED_DIAGNOSTIC_SCHEMA:
        raise ValueError("candidate diagnostic manifest schema mismatch")
    output = manifest.get("output")
    if not isinstance(output, Mapping) or output.get("sha256") != _sha256(path):
        raise ValueError("candidate diagnostic gzip hash mismatch")
    if output.get("compressed_bytes") != path.stat().st_size:
        raise ValueError("candidate diagnostic compressed byte count mismatch")
    selected: dict[str, dict[str, Any]] = {}
    support: Counter[tuple[str, str]] = Counter()
    documents: dict[tuple[str, str], set[str]] = defaultdict(set)
    support_provenance: dict[tuple[str, str], set[str]] = defaultdict(set)
    titles_by_entity: dict[str, set[str]] = defaultdict(set)
    total = 0
    rejected_protected = Counter[str]()
    unusable_title_rows = 0
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            total += 1
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("candidate diagnostic row must be an object")
            partition = str(row.get("partition"))
            if partition in PROTECTED_PARTITIONS:
                rejected_protected[partition] += 1
                continue
            if partition not in ALLOWED_PARTITIONS:
                raise ValueError(f"unexpected candidate diagnostic partition: {partition}")
            case_id = str(row.get("case_id"))
            if case_id in selected:
                raise ValueError(f"duplicate 397k candidate diagnostic case: {case_id}")
            raw_candidates = row.get("candidates")
            if not isinstance(raw_candidates, list):
                raise ValueError(f"candidate diagnostic lacks candidates: {case_id}")
            candidates: list[dict[str, str]] = []
            for raw_index, raw in enumerate(raw_candidates):
                if not isinstance(raw, Mapping):
                    raise ValueError(f"candidate row must be an object: {case_id}")
                title = str(raw.get("title", ""))
                document_id = str(raw.get("document_id", ""))
                chunk_id = str(raw.get("chunk_id", ""))
                if not title or not document_id:
                    raise ValueError(f"candidate lacks title/document identity: {case_id}")
                entity_id = canonical_entity_id(title)
                titles_by_entity[entity_id].add(title)
                candidates.append(
                    {"title": title, "document_id": document_id, "entity_id": entity_id}
                )
                if not normalize_fuzzy_surface(title):
                    unusable_title_rows += 1
                    continue
                support[(title, entity_id)] += 1
                documents[(title, entity_id)].add(document_id)
                support_provenance[(title, entity_id)].add(
                    "candidate-observation:"
                    + hashlib.sha256(
                        f"{case_id}\0{raw_index}\0{document_id}\0{chunk_id}\0{title}".encode()
                    ).hexdigest()[:24]
                )
            selected[case_id] = {
                "case_id": case_id,
                "partition": partition,
                "candidates": candidates,
            }
    if total != manifest.get("case_count"):
        raise ValueError("candidate diagnostic case count mismatch")
    canonical_titles: dict[str, str] = {}
    display_variant_count = 0
    for entity_id, titles in titles_by_entity.items():
        normalized_titles = {normalize_mention(title) for title in titles}
        if len(normalized_titles) != 1:
            raise ValueError("canonical ID hash collision across normalized titles")
        canonical_titles[entity_id] = min(titles, key=lambda item: (item.casefold(), item))
        display_variant_count += len(titles) - 1
    records = tuple(
        AddressSurfaceRecord(
            surface=title,
            entity_id=entity_id,
            canonical_title=canonical_titles[entity_id],
            support_count=count,
            source_document_count=len(documents[(title, entity_id)]),
            source_document_ids=tuple(sorted(documents[(title, entity_id)])),
            support_provenance_ids=tuple(sorted(support_provenance[(title, entity_id)])),
            source_channels=(),
            source_provenance=("candidate-diagnostic-397k-post-cap-title",),
        )
        for (title, entity_id), count in sorted(support.items())
    )
    audit = {
        "schema": manifest["schema"],
        "gzip_sha256": output["sha256"],
        "manifest_sha256": _sha256(manifest_path),
        "total_rows_verified": total,
        "allowed_rows_loaded": len(selected),
        "protected_rows_rejected_before_candidate_consumption": dict(rejected_protected),
        "title_surface_records": len(records),
        "canonical_title_display_variants_collapsed": display_variant_count,
        "unusable_punctuation_only_title_rows_excluded": unusable_title_rows,
        "unavailable_fields": dict(manifest.get("unavailable_fields", {})),
    }
    return selected, records, audit


def _load_benchmark(path: Path) -> dict[str, dict[str, Any]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, Mapping) or not isinstance(document.get("cases"), list):
        raise ValueError("benchmark must contain cases")
    result: dict[str, dict[str, Any]] = {}
    for raw in document["cases"]:
        if not isinstance(raw, Mapping):
            raise ValueError("benchmark case must be an object")
        partition = str(raw.get("partition"))
        if partition not in ALLOWED_PARTITIONS:
            continue
        case_id = str(raw.get("case_id"))
        result[case_id] = {
            "partition": partition,
            "required_entity_ids": tuple(str(item) for item in raw.get("required_entity_ids", ())),
        }
    return result


def _join_cases(
    diagnostic: Mapping[str, Mapping[str, Any]],
    replay_bundle: Path,
    benchmark: Mapping[str, Mapping[str, Any]],
) -> tuple[tuple[_Case, ...], dict[str, Any]]:
    manifest, replay = load_replay_bundle(replay_bundle)
    if manifest.bundle_sha256 != EXPECTED_REPLAY_IDENTITY:
        raise ValueError("authenticated replay identity mismatch")
    selected: dict[str, _Case] = {}
    rejected_protected = Counter[str]()
    rejected_other_tier = 0
    for case in replay:
        if case.partition in PROTECTED_PARTITIONS:
            rejected_protected[case.partition] += 1
            continue
        if case.partition not in ALLOWED_PARTITIONS:
            raise ValueError(f"unexpected replay partition: {case.partition}")
        if case.corpus_tier != "397k":
            rejected_other_tier += 1
            continue
        diagnostic_row = diagnostic.get(case.case_id)
        benchmark_row = benchmark.get(case.case_id)
        if diagnostic_row is None or benchmark_row is None:
            raise ValueError(f"397k case lacks diagnostic or benchmark join: {case.case_id}")
        if (
            diagnostic_row["partition"] != case.partition
            or benchmark_row["partition"] != case.partition
        ):
            raise ValueError(f"partition mismatch in joined case: {case.case_id}")
        if not case.training_eligible:
            raise ValueError(f"development/tuning replay is not training eligible: {case.case_id}")
        selected[case.case_id] = _Case(
            case_id=case.case_id,
            partition=case.partition,
            query=_final_query(case),
            required_entity_ids=tuple(benchmark_row["required_entity_ids"]),
        )
    if set(selected) != set(diagnostic):
        raise ValueError("diagnostic/replay case identities do not match")
    audit = {
        "bundle_sha256": manifest.bundle_sha256,
        "cases_sha256": manifest.cases_sha256,
        "manifest_sha256": _sha256(replay_bundle / "manifest.json"),
        "full_case_count_verified": manifest.case_count,
        "full_decision_count_verified": manifest.decision_count,
        "allowed_397k_rows_loaded": len(selected),
        "other_allowed_tier_rows_rejected": rejected_other_tier,
        "protected_rows_rejected_before_query_consumption": dict(rejected_protected),
    }
    return tuple(sorted(selected.values(), key=lambda item: item.case_id)), audit


def _typo(value: str) -> str | None:
    characters = list(value)
    candidates = [
        index
        for index in range(len(characters) - 1)
        if characters[index].isalpha()
        and characters[index + 1].isalpha()
        and characters[index] != characters[index + 1]
    ]
    if not candidates:
        return None
    index = candidates[len(candidates) // 2]
    characters[index], characters[index + 1] = characters[index + 1], characters[index]
    return "".join(characters)


def _perturbations(
    records: Sequence[AddressSurfaceRecord], *, limit: int
) -> tuple[_Perturbation, ...]:
    unique: dict[str, AddressSurfaceRecord] = {}
    for record in records:
        if record.entity_id is not None:
            unique.setdefault(record.surface, record)
    selected = sorted(
        unique.values(),
        key=lambda item: (hashlib.sha256(item.surface.encode()).hexdigest(), item.surface),
    )[:limit]
    rows: list[_Perturbation] = []
    for record in selected:
        assert record.entity_id is not None
        typo = _typo(record.surface)
        if typo is not None:
            rows.append(_Perturbation("typo", typo, record.entity_id))
        tokens = record.surface.split()
        if len(tokens) > 1:
            rows.append(_Perturbation("partial_alias", " ".join(tokens[:-1]), record.entity_id))
            rows.append(
                _Perturbation("tokenization", record.surface.replace(" ", "", 1), record.entity_id)
            )
        final = tokens[-1]
        if final.isalpha() and len(final) >= 4:
            rows.append(
                _Perturbation(
                    "morphology",
                    " ".join((*tokens[:-1], f"{final}'s")),
                    record.entity_id,
                )
            )
    return tuple(rows)


def _lookup(
    index: FuzzyAddressIndex,
    query: str,
    *,
    char_threshold: float,
    simhash_hamming: int,
) -> dict[FuzzyChannel, FuzzyLookupResult]:
    common = {
        "mention_cap": 64,
        "address_cap": 64,
        "max_spans": 128,
        "postings_cap": 16_384,
        "per_span_cap": 8,
    }
    return {
        FuzzyChannel.EXACT: index.lookup(query, FuzzyChannel.EXACT, **common),
        FuzzyChannel.CHAR_NGRAM: index.lookup(
            query,
            FuzzyChannel.CHAR_NGRAM,
            char_score_threshold=char_threshold,
            **common,
        ),
        FuzzyChannel.EDIT_DISTANCE: index.lookup(query, FuzzyChannel.EDIT_DISTANCE, **common),
        FuzzyChannel.SIMHASH_LSH: index.lookup(
            query,
            FuzzyChannel.SIMHASH_LSH,
            simhash_max_hamming=simhash_hamming,
            **common,
        ),
    }


def _selected_exact_char_p4(
    index: FuzzyAddressIndex,
    cases: Sequence[_Case],
    *,
    char_threshold: float,
    resident_index_bytes: int,
) -> dict[str, Any]:
    """Account selected fuzzy-normalized exact+char under v11 assumptions.

    This is intentionally analytical. Logical lookup and packed-page counts
    cannot establish a physical PSRAM/eMMC layout or measured board I/O.
    """

    metric_rows: dict[str, list[int]] = defaultdict(list)
    costs: list[P4OperationCost] = []
    cap_saturation = Counter[str]()
    partition_counts = Counter(case.partition for case in cases)
    for case in cases:
        common = {
            "mention_cap": 64,
            "address_cap": 64,
            "max_spans": 128,
            "postings_cap": 16_384,
            "per_span_cap": 8,
        }
        exact = index.lookup(case.query, FuzzyChannel.EXACT, **common)
        char = index.lookup(
            case.query,
            FuzzyChannel.CHAR_NGRAM,
            char_score_threshold=char_threshold,
            **common,
        )
        union = union_address_results((exact, char), address_cap=64)
        cap_saturation["fuzzy_exact_mention_cap"] += exact.mention_cap_saturated
        cap_saturation["char_ngram_mention_cap"] += char.mention_cap_saturated
        cap_saturation["fuzzy_exact_local_address_cap"] += exact.address_cap_saturated
        cap_saturation["char_ngram_local_address_cap"] += char.address_cap_saturated
        cap_saturation["char_ngram_postings_cap"] += (
            char.cost.postings_read >= char.cap_accounting.postings_cap
        )
        cap_saturation["global_address_cap"] += union.global_cap_saturated
        bytes_read = exact.cost.estimated_bytes_read + char.cost.estimated_bytes_read
        integer_ops = exact.cost.integer_ops + char.cost.integer_ops
        posting_lookups = char.cost.posting_list_lookups
        postings_read = char.cost.postings_read
        random_logical_reads = exact.cost.surface_scores + posting_lookups
        sequential_logical_reads = postings_read
        ideal_pages = math.ceil(bytes_read / 4096) if bytes_read else 0
        working_bytes = (
            len(case.query.encode("utf-8"))
            + 24 * max(exact.cost.spans_considered, char.cost.spans_considered)
            + 8 * char.cost.peak_accumulator_entries
            + 64 * (len(exact.mention_hypotheses) + len(char.mention_hypotheses))
            + 64 * len(union.address_proposals)
            + 4096
        )
        values = {
            "pre_global_cap_candidates": union.pre_cap_address_count,
            "retained_candidates_at_64": len(union.address_proposals),
            "global_pruned_candidates_at_64": len(union.pruned_address_proposals),
            "fuzzy_exact_locally_pruned_candidates": len(exact.pruned_address_proposals),
            "char_ngram_locally_pruned_candidates": len(char.pruned_address_proposals),
            "estimated_bytes_touched": bytes_read,
            "integer_operations": integer_ops,
            "posting_list_lookups": posting_lookups,
            "posting_entries_read": postings_read,
            "xor_popcount_operations": 0,
            "ideal_packed_4kb_pages": ideal_pages,
            "random_logical_index_reads": random_logical_reads,
            "sequential_logical_posting_reads": sequential_logical_reads,
            "analytical_working_bytes": working_bytes,
        }
        for name, value in values.items():
            metric_rows[name].append(value)
        costs.append(
            P4OperationCost(
                operation_id="address.fuzzy-normalized-exact-char.v12",
                integer_operations=integer_ops,
                macs=0,
                memory_bytes=bytes_read,
                psram_bytes=bytes_read,
                flash_bytes=0,
                psram_accesses=random_logical_reads + ideal_pages,
                flash_accesses=0,
                random_psram_reads=random_logical_reads,
                random_flash_reads=0,
                sequential_reads=ideal_pages,
                scratch_ram_bytes=working_bytes,
                model_bytes=resident_index_bytes,
            )
        )

    scenarios: dict[str, object] = {}
    for name, assumptions in v11_reference_assumptions().items():
        projections = [project_p4((cost,), assumptions) for cost in costs]
        components = {
            field: [float(getattr(projection, field)) for projection in projections]
            for field in (
                "compute_ms",
                "psram_transfer_ms",
                "flash_transfer_ms",
                "random_access_ms",
                "virtual_latency_ms",
            )
        }
        scenarios[name] = {
            "nominal": name == "nominal_300mhz",
            "evidence_class": "analytical_projection_not_hardware_measurement",
            "assumptions": assumptions.model_dump(mode="json"),
            "latency_ms": {
                field: {
                    "p50": _percentile(values, 0.50),
                    "p95": _percentile(values, 0.95),
                }
                for field, values in components.items()
            },
        }
    working_distribution = _distribution(metric_rows["analytical_working_bytes"])
    working_peak = int(working_distribution["maximum"])
    return {
        "evidence_class": "analytical_projection_not_hardware_measurement",
        "selected_runtime": "fuzzy_normalized_exact_plus_char_ngram",
        "selected_channels": ["fuzzy_normalized_exact", "char_ngram"],
        "inactive_channels": [
            "edit_distance_not_selected",
            "simhash_lsh_rejected",
            "semantic_ann_not_active",
        ],
        "case_count": len(cases),
        "partition_counts": dict(sorted(partition_counts.items())),
        "global_address_cap": 64,
        "union_semantics": (
            "complete channel pre-cap proposals are canonical-ID unioned before the one "
            "global K=64 cap; local pruned records remain exposed for audit"
        ),
        "learned_parameter_count": 0,
        "macs": {"p50": 0.0, "p95": 0.0},
        "distributions": {
            name: _distribution(values) for name, values in sorted(metric_rows.items())
        },
        "cap_saturation": {
            name: {
                "count": cap_saturation[name],
                "rate": cap_saturation[name] / len(cases) if cases else 0.0,
            }
            for name in (
                "fuzzy_exact_mention_cap",
                "char_ngram_mention_cap",
                "fuzzy_exact_local_address_cap",
                "char_ngram_local_address_cap",
                "char_ngram_postings_cap",
                "global_address_cap",
            )
        },
        "memory": {
            "resident_index_bytes": resident_index_bytes,
            "resident_index_scope": (
                "standalone serialized fuzzy-normalized title records plus char-ngram "
                "postings; no allocator/runtime overhead and no shared-FST deduction"
            ),
            "analytical_working_bytes": working_distribution,
            "resident_plus_peak_working_bytes": resident_index_bytes + working_peak,
        },
        "logical_io": {
            "page_size_bytes": 4096,
            "ideal_packed_page_count_semantics": (
                "ceil(logical bytes touched / 4096), a lower bound under ideal packing; "
                "not a measured physical page count"
            ),
            "random_read_semantics": (
                "exact hash/surface probes plus char posting-list probes; logical, not "
                "physical page-order observations"
            ),
            "sequential_read_semantics": (
                "posting entries scanned plus ideal packed-page transfer counters; logical, "
                "not measured storage reads"
            ),
            "physical_random_pages": None,
            "physical_sequential_pages": None,
        },
        "storage": {
            "resident_mode": "ideal_resident_psram_analytical_assumption",
            "external_storage_projection": "not_projected_missing_physical_layout",
            "external_bandwidth_mb_s": None,
            "external_random_access_us": None,
            "external_bytes": None,
            "external_random_pages": None,
            "external_latency_formula_ms": (
                "external_bytes/(bandwidth_mb_s*1e6)*1000 + random_pages*random_access_us/1000"
            ),
            "note": (
                "v11 flash calibration fields are reported for scenario continuity but are "
                "inactive because no external bytes/pages are assigned; they are not an eMMC spec"
            ),
        },
        "p4_scenarios": scenarios,
        "assumptions": [
            "all selected index bytes are ideally resident in PSRAM",
            "one scalar integer operation per cycle and zero MACs",
            "logical bytes are charged once through the v11 PSRAM bandwidth calibration",
            "logical random probes are charged through the v11 PSRAM random-access calibration",
            "4KB pages are packed lower bounds, not physical layout measurements",
            "working bytes are a packed analytical buffer estimate, not Python RSS",
            "no board timing, cache behavior, eMMC bandwidth, or physical page order was measured",
        ],
    }


def _select_thresholds(
    index: FuzzyAddressIndex, perturbations: Sequence[_Perturbation]
) -> tuple[float, int, dict[str, object]]:
    char_sweep = []
    for threshold in (0.46, 0.52, 0.58):
        hits = 0
        candidates = []
        for row in perturbations:
            result = index.lookup(
                row.query,
                FuzzyChannel.CHAR_NGRAM,
                address_cap=16,
                char_score_threshold=threshold,
                postings_cap=16_384,
            )
            hits += row.entity_id in {item.entity_id for item in result.address_proposals}
            candidates.append(len(result.address_proposals))
        char_sweep.append(
            {
                "threshold": threshold,
                "recall_at_16": hits / len(perturbations),
                "mean_candidates": _mean(candidates),
            }
        )
    chosen_char = min(
        char_sweep,
        key=lambda item: (
            -float(item["recall_at_16"]),
            float(item["mean_candidates"]),
            -float(item["threshold"]),
        ),
    )
    simhash_sweep = []
    for hamming in (8, 12, 16):
        hits = 0
        candidates = []
        for row in perturbations:
            result = index.lookup(
                row.query,
                FuzzyChannel.SIMHASH_LSH,
                address_cap=16,
                simhash_max_hamming=hamming,
                postings_cap=16_384,
            )
            hits += row.entity_id in {item.entity_id for item in result.address_proposals}
            candidates.append(len(result.address_proposals))
        simhash_sweep.append(
            {
                "max_hamming": hamming,
                "recall_at_16": hits / len(perturbations),
                "mean_candidates": _mean(candidates),
            }
        )
    chosen_simhash = min(
        simhash_sweep,
        key=lambda item: (
            -float(item["recall_at_16"]),
            float(item["mean_candidates"]),
            int(item["max_hamming"]),
        ),
    )
    return (
        float(chosen_char["threshold"]),
        int(chosen_simhash["max_hamming"]),
        {
            "fit_partition": "development-derived real-title perturbations only",
            "char_ngram": char_sweep,
            "simhash_lsh": simhash_sweep,
            "chosen_char_threshold": chosen_char["threshold"],
            "chosen_simhash_max_hamming": chosen_simhash["max_hamming"],
        },
    )


def _perturbation_metrics(
    index: FuzzyAddressIndex,
    perturbations: Sequence[_Perturbation],
    *,
    char_threshold: float,
    simhash_hamming: int,
) -> dict[str, object]:
    totals = Counter(row.kind for row in perturbations)
    hits: dict[str, Counter[str]] = defaultdict(Counter)
    for row in perturbations:
        results = _lookup(
            index,
            row.query,
            char_threshold=char_threshold,
            simhash_hamming=simhash_hamming,
        )
        for channel, result in results.items():
            if row.entity_id in {item.entity_id for item in result.address_proposals[:16]}:
                hits[str(channel)][row.kind] += 1
        union = union_address_results(tuple(results.values()), address_cap=16)
        if row.entity_id in {item.entity_id for item in union}:
            hits["union"][row.kind] += 1
    return {
        "scope": (
            "deterministic perturbations of real development title surfaces; "
            "not natural-query recall"
        ),
        "count": len(perturbations),
        "by_kind": {
            kind: {
                "count": totals[kind],
                "recall_at_16": {
                    channel: channel_hits[kind] / totals[kind]
                    for channel, channel_hits in sorted(hits.items())
                },
            }
            for kind in sorted(totals)
        },
    }


def _evaluate_partition(
    index: FuzzyAddressIndex,
    cases: Sequence[_Case],
    *,
    char_threshold: float,
    simhash_hamming: int,
) -> dict[str, object]:
    channels = (
        FuzzyChannel.EXACT,
        FuzzyChannel.CHAR_NGRAM,
        FuzzyChannel.EDIT_DISTANCE,
        FuzzyChannel.SIMHASH_LSH,
    )
    title_entity_ids = index.entity_ids()
    required_total = 0
    addressable_total = 0
    mention_hits = Counter[str]()
    entity_hits: dict[str, Counter[int]] = defaultdict(Counter)
    complete_hits: dict[str, Counter[int]] = defaultdict(Counter)
    addressable_complete: dict[str, Counter[int]] = defaultdict(Counter)
    case_count = 0
    addressable_case_count = 0
    candidate_counts: dict[str, list[int]] = defaultdict(list)
    bytes_read: dict[str, list[int]] = defaultdict(list)
    integer_ops: dict[str, list[int]] = defaultdict(list)
    popcount_ops: dict[str, list[int]] = defaultdict(list)
    mention_saturation = Counter[str]()
    cap_saturation: dict[str, Counter[int]] = defaultdict(Counter)
    unique_recoveries = Counter[str]()
    union_gain_required = 0
    union_gain_complete = 0
    union_configs = {
        "exact_char_union": (FuzzyChannel.EXACT, FuzzyChannel.CHAR_NGRAM),
        "exact_edit_union": (FuzzyChannel.EXACT, FuzzyChannel.EDIT_DISTANCE),
        "exact_simhash_union": (FuzzyChannel.EXACT, FuzzyChannel.SIMHASH_LSH),
        "deterministic_union": (
            FuzzyChannel.EXACT,
            FuzzyChannel.CHAR_NGRAM,
            FuzzyChannel.EDIT_DISTANCE,
        ),
        "union": channels,
    }
    for case in cases:
        required = set(case.required_entity_ids)
        if not required:
            continue
        case_count += 1
        required_total += len(required)
        addressable = required & title_entity_ids
        addressable_total += len(addressable)
        if required <= title_entity_ids:
            addressable_case_count += 1
        results = _lookup(
            index,
            case.query,
            char_threshold=char_threshold,
            simhash_hamming=simhash_hamming,
        )
        rank_sets: dict[str, list[str]] = {}
        for channel in channels:
            result = results[channel]
            name = str(channel)
            mention_ids = {
                entity_id for item in result.mention_hypotheses for entity_id in item.entity_ids
            }
            mention_hits[name] += len(required & mention_ids)
            ranked = [item.entity_id for item in result.pre_cap_address_proposals]
            rank_sets[name] = ranked
            candidate_counts[name].append(len(ranked))
            bytes_read[name].append(result.cost.estimated_bytes_read)
            integer_ops[name].append(result.cost.integer_ops)
            popcount_ops[name].append(result.cost.xor_popcount_ops)
            mention_saturation[name] += result.mention_cap_saturated
            for cap in (1, 4, 8, 16, 32):
                selected = set(ranked[:cap])
                entity_hits[name][cap] += len(required & selected)
                complete_hits[name][cap] += required <= selected
                addressable_complete[name][cap] += (
                    required <= title_entity_ids and required <= selected
                )
                cap_saturation[name][cap] += result.pre_cap_address_count > cap
        for union_name, selected_channels in union_configs.items():
            selected_results = tuple(results[channel] for channel in selected_channels)
            union = union_address_results(selected_results, address_cap=64)
            union_ranked = [item.entity_id for item in union.pre_cap_address_proposals]
            union_mentions = {
                entity_id
                for result in selected_results
                for item in result.mention_hypotheses
                for entity_id in item.entity_ids
            }
            mention_hits[union_name] += len(required & union_mentions)
            rank_sets[union_name] = union_ranked
            candidate_counts[union_name].append(len(union_ranked))
            bytes_read[union_name].append(
                sum(result.cost.estimated_bytes_read for result in selected_results)
            )
            integer_ops[union_name].append(
                sum(result.cost.integer_ops for result in selected_results)
            )
            popcount_ops[union_name].append(
                sum(result.cost.xor_popcount_ops for result in selected_results)
            )
            mention_saturation[union_name] += any(
                result.mention_cap_saturated for result in selected_results
            )
            for cap in (1, 4, 8, 16, 32):
                selected = set(union_ranked[:cap])
                entity_hits[union_name][cap] += len(required & selected)
                complete_hits[union_name][cap] += required <= selected
                addressable_complete[union_name][cap] += (
                    required <= title_entity_ids and required <= selected
                )
                cap_saturation[union_name][cap] += len(union_ranked) > cap
        exact_32 = set(rank_sets[str(FuzzyChannel.EXACT)][:32])
        union_32 = set(rank_sets["union"][:32])
        union_gain_required += len((required & union_32) - exact_32)
        union_gain_complete += required <= union_32 and not required <= exact_32
        for entity_id in required & union_32:
            supporting = [
                name
                for name in (str(channel) for channel in channels)
                if entity_id in rank_sets[name][:32]
            ]
            if len(supporting) == 1:
                unique_recoveries[supporting[0]] += 1
    names = (*map(str, channels), *union_configs)
    return {
        "case_count_with_required_entities": case_count,
        "required_entity_count": required_total,
        "addressable_required_entity_count": addressable_total,
        "cases_with_all_required_entities_in_available_title_index": addressable_case_count,
        "mention_hypothesis_recall": {name: mention_hits[name] / required_total for name in names},
        "mention_hypothesis_recall_on_addressable": {
            name: mention_hits[name] / addressable_total for name in names
        },
        "entity_recall": {
            name: {
                f"at_{cap}": entity_hits[name][cap] / required_total for cap in (1, 4, 8, 16, 32)
            }
            for name in names
        },
        "entity_recall_on_addressable": {
            name: {
                f"at_{cap}": entity_hits[name][cap] / addressable_total for cap in (1, 4, 8, 16, 32)
            }
            for name in names
        },
        "multi_entity_completeness": {
            name: {f"at_{cap}": complete_hits[name][cap] / case_count for cap in (1, 4, 8, 16, 32)}
            for name in names
        },
        "addressable_case_completeness": {
            name: {
                f"at_{cap}": (
                    addressable_complete[name][cap] / addressable_case_count
                    if addressable_case_count
                    else 0.0
                )
                for cap in (1, 4, 8, 16, 32)
            }
            for name in names
        },
        "unique_required_recovery_at_32": dict(sorted(unique_recoveries.items())),
        "union_gain_over_exact_at_32": {
            "required_entities": union_gain_required,
            "complete_cases": union_gain_complete,
        },
        "cost": {
            name: {
                "mean_candidates": _mean(candidate_counts[name]),
                "p95_candidates": _p95(candidate_counts[name]),
                "mean_estimated_bytes_read": _mean(bytes_read[name]),
                "p95_estimated_bytes_read": _p95(bytes_read[name]),
                "mean_integer_ops": _mean(integer_ops[name]),
                "p95_integer_ops": _p95(integer_ops[name]),
                "mean_xor_popcount_ops": _mean(popcount_ops[name]),
                "p95_xor_popcount_ops": _p95(popcount_ops[name]),
                "mention_cap_saturation_rate": mention_saturation[name] / case_count,
                "address_cap_saturation_rate": {
                    f"at_{cap}": cap_saturation[name][cap] / case_count for cap in (1, 4, 8, 16, 32)
                },
            }
            for name in names
        },
    }


def _report_markdown(report: Mapping[str, Any]) -> str:
    dev = report["natural_query_qualification"]["development"]
    tuning = report["natural_query_qualification"]["tuning"]
    sizes = report["index_footprint"]
    p4 = report["selected_fuzzy_exact_char_p4_accounting"]
    chosen = report["development_threshold_selection"]
    decoded_mib = sizes["serialized_json_bytes"] / (1024 * 1024)
    exact_char_mib = sizes["fuzzy_normalized_exact_char_standalone_bytes"] / (1024 * 1024)
    tuning_recall = tuning["entity_recall"]
    tuning_cost = tuning["cost"]
    table = "\n".join(
        (
            "| partition | required IDs | title-addressable | mention recall | "
            "entity recall@16 | addressable recall@16 | completeness@16 |",
            "|---|---:|---:|---:|---:|---:|---:|",
            (
                f"| development | {dev['required_entity_count']} | "
                f"{dev['addressable_required_entity_count']} | "
                f"{dev['mention_hypothesis_recall']['union']:.4f} | "
                f"{dev['entity_recall']['union']['at_16']:.4f} | "
                f"{dev['entity_recall_on_addressable']['union']['at_16']:.4f} | "
                f"{dev['multi_entity_completeness']['union']['at_16']:.4f} |"
            ),
            (
                f"| tuning | {tuning['required_entity_count']} | "
                f"{tuning['addressable_required_entity_count']} | "
                f"{tuning['mention_hypothesis_recall']['union']:.4f} | "
                f"{tuning['entity_recall']['union']['at_16']:.4f} | "
                f"{tuning['entity_recall_on_addressable']['union']['at_16']:.4f} | "
                f"{tuning['multi_entity_completeness']['union']['at_16']:.4f} |"
            ),
        )
    )
    p4_metrics = p4["distributions"]
    p4_table = "\n".join(
        (
            "| v11 scenario | clock | p50 virtual ms | p95 virtual ms | "
            "p50 random-access ms | p95 random-access ms |",
            "|---|---:|---:|---:|---:|---:|",
            *(
                f"| {name} | {scenario['assumptions']['clock_mhz']} MHz | "
                f"{scenario['latency_ms']['virtual_latency_ms']['p50']:.4f} | "
                f"{scenario['latency_ms']['virtual_latency_ms']['p95']:.4f} | "
                f"{scenario['latency_ms']['random_access_ms']['p50']:.4f} | "
                f"{scenario['latency_ms']['random_access_ms']['p95']:.4f} |"
                for name, scenario in p4["p4_scenarios"].items()
            ),
        )
    )
    return f"""# Mission 7 bounded fuzzy-address qualification

Status: **implemented and bounded-title-surface qualified**. This is not a
full-corpus address-recall claim.

## Evidence boundary

- Base commit: `{report["base_commit"]}`.
- Authenticated replay: `{report["integrity"]["replay"]["bundle_sha256"]}`
  (6,150 cases / 54,477 decisions verified).
- Candidate diagnostic: `{report["integrity"]["candidate_diagnostic"]["gzip_sha256"]}`.
- Only development/tuning 397k rows entered the index or query evaluation.
  Evaluation/final-held rows were rejected before candidates or query frames
  were consumed.
- The diagnostic is post-cap and has no aliases, redirects, channel provenance,
  semantic proposals, pre-cap candidates, or full pack. Results therefore bound
  fuzzy recovery over the available title-surface universe only.

## Qualified compact baselines

Development-only deterministic real-title perturbations selected char threshold
`{chosen["chosen_char_threshold"]}` and SimHash maximum Hamming distance
`{chosen["chosen_simhash_max_hamming"]}`. Edit expansion is token-level
Damerau-OSA <=2 over a hashed symmetric-delete vocabulary; every proposal is
verified before its exact canonical ID is returned.

The selected runtime for cost projection is **fuzzy-normalized exact + character
n-gram**. “Exact” here means equality after the fuzzy title normalizer; it is
not the `ExactAddressIndex` FST. Edit distance remains an offline ablation;
SimHash/LSH is rejected and semantic ANN is inactive.

## Natural query results

{table}

Per-channel recall@1/4/8/16/32, unique recoveries, union gains, bytes, logical
operations, and cap saturation are in `fuzzy-address-qualification.json`.
The addressable-normalized metric is a bounded mechanism diagnostic only; the
all-required denominator is the architecture-relevant result.

## Footprint and determinism

- Surfaces: {report["index_scope"]["surface_count"]}; canonical address rows:
  {report["index_scope"]["address_count"]}.
- Compiled JSON: {sizes["serialized_json_bytes"]} bytes; deterministic gzip:
  {sizes["serialized_gzip_bytes"]} bytes.
- N-gram postings: {sizes["ngram_posting_bytes"]} bytes; edit delete postings:
  {sizes["edit_posting_bytes"]} bytes; token->surface postings:
  {sizes["token_surface_posting_bytes"]} bytes; SimHash/LSH:
  {sizes["simhash_lsh_bytes"]} bytes.
- The standalone fuzzy-normalized exact+char serialized tables are
  {exact_char_mib:.2f} MiB before allocator/runtime overhead. This is neither
  an additive integration estimate nor a shared-footprint deduction against
  the exact FST.
- The external compiled index round-trips byte-identically and its committed
  manifest records both compressed and decoded SHA-256 identities.
- The all-channel diagnostic serialization is {decoded_mib:.2f} MiB decoded.
  These standalone JSON table counts do not establish integrated <=8 MiB
  residency; allocator, shared registry, and physical layout are unmeasured.

## Selected fuzzy-exact+char P4 analytical accounting

The accounting covers all {p4["case_count"]} authenticated development/tuning
397k cases after threshold selection. It projects only the selected
fuzzy-normalized exact+char path: edit, SimHash/LSH, and semantic ANN are not
active.

- Pre-global-cap candidates: p50
  {p4_metrics["pre_global_cap_candidates"]["p50"]:.1f}, p95
  {p4_metrics["pre_global_cap_candidates"]["p95"]:.1f}.
- Logical bytes touched: p50
  {p4_metrics["estimated_bytes_touched"]["p50"]:.1f}, p95
  {p4_metrics["estimated_bytes_touched"]["p95"]:.1f}; integer operations: p50
  {p4_metrics["integer_operations"]["p50"]:.1f}, p95
  {p4_metrics["integer_operations"]["p95"]:.1f}.
- Posting entries read: p50
  {p4_metrics["posting_entries_read"]["p50"]:.1f}, p95
  {p4_metrics["posting_entries_read"]["p95"]:.1f}; XOR/popcount operations are
  zero because SimHash is inactive.
- Ideal packed 4 KiB page lower bound: p50
  {p4_metrics["ideal_packed_4kb_pages"]["p50"]:.1f}, p95
  {p4_metrics["ideal_packed_4kb_pages"]["p95"]:.1f}; logical random index reads:
  p50 {p4_metrics["random_logical_index_reads"]["p50"]:.1f}, p95
  {p4_metrics["random_logical_index_reads"]["p95"]:.1f}.
- Resident standalone selected tables: {p4["memory"]["resident_index_bytes"]}
  bytes. Packed analytical working memory: p50
  {p4_metrics["analytical_working_bytes"]["p50"]:.1f}, p95
  {p4_metrics["analytical_working_bytes"]["p95"]:.1f} bytes.
- The char posting-work cap saturated in
  {p4["cap_saturation"]["char_ngram_postings_cap"]["count"]} /
  {p4["case_count"]} cases
  ({p4["cap_saturation"]["char_ngram_postings_cap"]["rate"]:.4f}); the global
  K=64 cap saturated in {p4["cap_saturation"]["global_address_cap"]["count"]}.

{p4_table}

These are analytical projections, not hardware measurements. They assume an
ideal resident-PSRAM layout and reuse the unchanged v11 200/300/400 MHz scalar,
bandwidth, and random-access calibration. The 4 KiB pages are ideal packing
lower bounds; random/sequential counts are logical, not observed physical page
order. No external-storage bytes or pages are assigned, so eMMC/storage latency
is deliberately unprojected. The explicit conditional formula is
`bytes/(bandwidth_MBps*1e6)*1000 + random_pages*random_access_us/1000`.

## Decision and limitation

The channel implementation is reusable and canonical-ID safe, and it measures
mention recovery separately from post-union entity recall. Its full-corpus
architecture gate remains **blocked by missing global address data**, not by
this implementation: a query-conditioned post-cap title set cannot establish
full-corpus recall, alias/redirect recovery, or never-generated versus
pre-cap-pruned failures.

The compact matched ablation rejects SimHash/LSH for this substrate: the
all-channel union and exact+char+edit both reach tuning recall@16
`{tuning_recall["union"]["at_16"]:.4f}`, while SimHash has zero unique tuning
recoveries and adds {sizes["simhash_lsh_bytes"]} standalone diagnostic bytes.
Edit expansion remains an inactive offline ablation. Selected fuzzy-normalized
exact+char reaches `{tuning_recall["exact_char_union"]["at_16"]:.4f}` at a mean
{tuning_cost["exact_char_union"]["mean_estimated_bytes_read"]:.0f} logical
bytes/query. This lane does not treat that table count as an additive or shared
footprint relative to the exact FST.

## Reproduction

```bash
PYTHONPATH=src python scripts/droid/v12_fuzzy_address_qualify.py \\
  --diagnostic /path/candidate-diagnostic-397k.jsonl.gz \\
  --diagnostic-manifest /path/candidate-diagnostic-397k.manifest.json \\
  --replay-bundle /path/controller-replay-3tier \\
  --benchmark data/v050/benchmark/INDEPENDENT_NATURAL_QUERY_SET_V050_R1.json \\
  --output reports/droid/v12/fuzzy-address-qualification.json \\
  --report reports/droid/v12/FUZZY_ADDRESS_QUALIFICATION.md \\
  --index-output /external/fuzzy-address-397k-postcap.json.gz \\
  --index-manifest reports/droid/v12/fuzzy-address-index.manifest.json
```
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnostic", required=True, type=Path)
    parser.add_argument("--diagnostic-manifest", required=True, type=Path)
    parser.add_argument("--replay-bundle", required=True, type=Path)
    parser.add_argument("--benchmark", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--index-output", required=True, type=Path)
    parser.add_argument("--index-manifest", required=True, type=Path)
    parser.add_argument("--base-commit", default="a7dcb187a985164648549eb18f67a7a6a4a964c6")
    args = parser.parse_args()

    diagnostic, records, diagnostic_audit = _load_diagnostic(
        args.diagnostic, args.diagnostic_manifest
    )
    benchmark = _load_benchmark(args.benchmark)
    cases, replay_audit = _join_cases(diagnostic, args.replay_bundle, benchmark)
    index = FuzzyAddressIndex(records)
    development_entity_ids = {
        entity_id
        for case in cases
        if case.partition == "development"
        for entity_id in case.required_entity_ids
    }
    development_records = tuple(
        record for record in records if record.entity_id in development_entity_ids
    )
    perturbations = _perturbations(development_records, limit=128)
    if not perturbations:
        raise ValueError("development title surfaces produced no lawful perturbations")
    char_threshold, simhash_hamming, selection = _select_thresholds(index, perturbations)
    natural: dict[str, Any] = {
        partition: _evaluate_partition(
            index,
            tuple(case for case in cases if case.partition == partition),
            char_threshold=char_threshold,
            simhash_hamming=simhash_hamming,
        )
        for partition in ("development", "tuning")
    }
    footprint = logical_index_bytes(index)
    selected_p4 = _selected_exact_char_p4(
        index,
        cases,
        char_threshold=char_threshold,
        resident_index_bytes=footprint["fuzzy_normalized_exact_char_standalone_bytes"],
    )
    args.index_output.parent.mkdir(parents=True, exist_ok=True)
    args.index_manifest.parent.mkdir(parents=True, exist_ok=True)
    index_manifest = index.write_artifact(args.index_output, args.index_manifest)
    index_manifest["selected_runtime"] = {
        "name": "fuzzy_normalized_exact_plus_char_ngram",
        "channels": ["fuzzy_normalized_exact", "char_ngram"],
        "inactive_channels": [
            "edit_distance_not_selected",
            "simhash_lsh_rejected",
            "semantic_ann_not_active",
        ],
        "case_count": selected_p4["case_count"],
        "cost_evidence_class": selected_p4["evidence_class"],
        "standalone_resident_index_bytes": selected_p4["memory"]["resident_index_bytes"],
        "global_address_cap": selected_p4["global_address_cap"],
        "p50_p95": {
            metric: {key: selected_p4["distributions"][metric][key] for key in ("p50", "p95")}
            for metric in (
                "pre_global_cap_candidates",
                "estimated_bytes_touched",
                "integer_operations",
                "posting_entries_read",
                "xor_popcount_operations",
                "ideal_packed_4kb_pages",
                "random_logical_index_reads",
                "sequential_logical_posting_reads",
                "analytical_working_bytes",
            )
        },
        "nominal_300mhz_virtual_latency_ms": selected_p4["p4_scenarios"]["nominal_300mhz"][
            "latency_ms"
        ]["virtual_latency_ms"],
        "logical_io_evidence": "analytical_ideal_resident_not_physical_io",
        "external_storage_projection": "not_projected_missing_physical_layout",
    }
    args.index_manifest.write_bytes(_stable_json(index_manifest))
    reloaded = FuzzyAddressIndex.from_artifact(args.index_output, args.index_manifest)
    if reloaded.to_bytes() != index.to_bytes():
        raise ValueError("fuzzy index round trip is not byte-identical")
    index_manifest_sha256 = _sha256(args.index_manifest)
    report: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "base_commit": args.base_commit,
        "data_scope": (
            "authenticated development/tuning post-cap 397k candidate titles joined to "
            "authenticated 397k replay query frames; not full-corpus address recall"
        ),
        "sealed_partitions": {
            "evaluation": "rejected before candidate/query consumption",
            "final_held": "rejected before candidate/query consumption",
        },
        "integrity": {
            "candidate_diagnostic": diagnostic_audit,
            "replay": replay_audit,
            "benchmark_sha256": _sha256(args.benchmark),
            "external_index_manifest_sha256": index_manifest_sha256,
            "external_index": index_manifest,
        },
        "index_scope": {
            "surface_count": index.surface_count,
            "address_count": index.address_count,
            "unresolved_record_count": index.unresolved_record_count,
            "source": "post-cap query-conditioned title surfaces",
        },
        "index_footprint": footprint,
        "selected_fuzzy_exact_char_p4_accounting": selected_p4,
        "development_threshold_selection": selection,
        "real_title_perturbation_qualification": _perturbation_metrics(
            index,
            perturbations,
            char_threshold=char_threshold,
            simhash_hamming=simhash_hamming,
        ),
        "natural_query_qualification": natural,
        "limitations": [
            "no full 397k title/alias/redirect registry",
            "no pre-cap candidate generation provenance",
            "no retrieval-channel provenance",
            "no mention-level gold alignment; natural-query recall uses case-level required IDs",
            "query-conditioned post-cap title coverage is not full-corpus address recall",
            "fuzzy-normalized exact is not the ExactAddressIndex FST",
            "selected standalone table bytes are not an additive/shared integration footprint",
            "P4 latency is analytical under ideal resident layout, not a hardware measurement",
            "external storage latency is unprojected without physical bytes/page layout",
        ],
        "lane_decision": "IMPLEMENTED_NOT_FULL_CORPUS_QUALIFIED",
    }
    _write_json(args.output, report)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(_report_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "development_union_recall_at_16": natural["development"]["entity_recall"]["union"][
                    "at_16"
                ],
                "tuning_union_recall_at_16": natural["tuning"]["entity_recall"]["union"]["at_16"],
                "index_gzip_bytes": footprint["serialized_gzip_bytes"],
                "index_manifest_sha256": index_manifest_sha256,
                "nominal_300mhz_virtual_latency_ms": selected_p4["p4_scenarios"]["nominal_300mhz"][
                    "latency_ms"
                ]["virtual_latency_ms"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
