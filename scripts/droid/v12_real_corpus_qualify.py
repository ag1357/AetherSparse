#!/usr/bin/env python3
"""Evaluate a deterministic char-trigram union on Factory 397k aligned mentions."""

# ruff: noqa: E501, RUF001 -- report prose and the Unicode tokenizer are intentional.

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import re
import statistics
import time
from array import array
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from aethersparse.controller.fuzzy_address import normalize_fuzzy_surface
from aethersparse.specialists.p4_cost import (
    V11_P4_CALIBRATION_ID,
    P4OperationCost,
    project_p4,
    v11_reference_assumptions,
)

KS = (1, 4, 8, 16, 32)
GLOBAL_CAP = 32
SCHEMA_VERSION = "aethersparse.semantic-address-v2-real-397k-fuzzy-union.v1"
TOKEN_RE = re.compile(r"[^\W_]+(?:['’\-][^\W_]+)*", re.UNICODE)
GENERIC_STOPWORDS = frozenset(
    {
        "a",
        "according",
        "an",
        "and",
        "are",
        "compare",
        "did",
        "do",
        "does",
        "for",
        "give",
        "how",
        "in",
        "is",
        "of",
        "on",
        "or",
        "state",
        "stated",
        "the",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
    }
)


def load_jsonl_gz(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def grams(value: str) -> tuple[str, ...]:
    framed = f"^{value}$"
    if len(framed) <= 3:
        return (framed,)
    return tuple(sorted({framed[index : index + 3] for index in range(len(framed) - 2)}))


def damerau_similarity(left: str, right: str) -> tuple[float, int]:
    """Return optimal-string-alignment similarity and a scalar-op estimate."""
    if left == right:
        return 1.0, 1
    if not left or not right:
        return 0.0, 1
    previous_previous: list[int] | None = None
    previous = list(range(len(right) + 1))
    operations = len(right) + 1
    for i, left_char in enumerate(left, 1):
        current = [i]
        for j, right_char in enumerate(right, 1):
            value = min(
                current[j - 1] + 1, previous[j] + 1, previous[j - 1] + (left_char != right_char)
            )
            if (
                previous_previous is not None
                and i > 1
                and j > 1
                and left_char == right[j - 2]
                and left[i - 2] == right_char
            ):
                value = min(value, previous_previous[j - 2] + 1)
            current.append(value)
            operations += 8
        previous_previous, previous = previous, current
    return 1.0 - previous[-1] / max(len(left), len(right)), operations


def percentile(values: list[float | int], quantile: float) -> float:
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


def distribution(values: list[float | int]) -> dict[str, float]:
    return {
        "mean": statistics.fmean(values) if values else 0.0,
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "maximum": float(max(values, default=0)),
    }


class CharIndex:
    def __init__(self, aliases_path: Path) -> None:
        self.surfaces: list[str] = []
        self.surface_grams: list[tuple[str, ...]] = []
        self.entity_ids: list[tuple[str, ...]] = []
        self.canonical_titles: dict[str, str] = {}
        self.surface_sources: list[tuple[str, ...]] = []
        self.resolved_redirect_surfaces: set[str] = set()
        self.postings: dict[str, array[int]] = defaultdict(lambda: array("I"))
        collapsed: dict[str, dict[str, set[str]]] = {}
        rows = 0
        with gzip.open(aliases_path, "rt", encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                rows += 1
                entity_id = row.get("canonical_entity_id")
                title = row.get("canonical_title")
                if not isinstance(entity_id, str) or not isinstance(title, str):
                    continue
                normalized = normalize_fuzzy_surface(str(row.get("surface", "")))
                if not normalized:
                    continue
                self.canonical_titles[entity_id] = title
                source = "redirect" if len(row.get("redirect_path", ())) > 1 else "title"
                record = collapsed.setdefault(normalized, defaultdict(set))
                record[entity_id].add(source)
        for normalized, mapping in sorted(collapsed.items()):
            surface_id = len(self.surfaces)
            surface_gram_tuple = grams(normalized)
            self.surfaces.append(normalized)
            self.surface_grams.append(surface_gram_tuple)
            ids = tuple(sorted(mapping))
            self.entity_ids.append(ids)
            self.surface_sources.append(
                tuple(sorted({source for eid in ids for source in mapping[eid]}))
            )
            if any("redirect" in sources for sources in mapping.values()):
                self.resolved_redirect_surfaces.add(normalized)
            for gram in surface_gram_tuple:
                self.postings[gram].append(surface_id)
        self.alias_rows = rows
        self.total_postings = sum(len(values) for values in self.postings.values())
        self.logical_bytes = (
            sum(len(value.encode("utf-8")) + 2 for value in self.surfaces)
            + 4 * self.total_postings
            + sum(len(key.encode("utf-8")) + 8 for key in self.postings)
            + 4 * sum(len(ids) for ids in self.entity_ids)
        )

    def lookup(self, query: str, *, postings_cap: int, proposal_cap: int = 128) -> dict[str, Any]:
        normalized = normalize_fuzzy_surface(query)
        query_grams = grams(normalized) if normalized else ()
        overlap: Counter[int] = Counter()
        postings_read = 0
        posting_lookups = 0
        saturated = False
        ordered_grams = sorted(
            query_grams, key=lambda item: (len(self.postings.get(item, ())), item)
        )
        for gram in ordered_grams:
            values = self.postings.get(gram, ())
            posting_lookups += 1
            remaining = postings_cap - postings_read
            if remaining <= 0:
                saturated = True
                break
            selected = values if len(values) <= remaining else values[:remaining]
            overlap.update(selected)
            postings_read += len(selected)
            if len(selected) < len(values):
                saturated = True
                break
        scored_surfaces: list[tuple[float, str, int]] = []
        for surface_id, common in overlap.items():
            denominator = len(query_grams) + len(self.surface_grams[surface_id])
            score = (2.0 * common / denominator) if denominator else 0.0
            scored_surfaces.append((score, self.surfaces[surface_id], surface_id))
        scored_surfaces.sort(key=lambda item: (-item[0], item[1], item[2]))
        distance_operations = 0
        rescored_surfaces: list[tuple[float, str, int]] = []
        for dice_score, matched_surface, surface_id in scored_surfaces[
            : max(256, proposal_cap * 4)
        ]:
            edit_score, edit_operations = damerau_similarity(normalized, matched_surface)
            distance_operations += edit_operations
            rescored_surfaces.append((max(dice_score, edit_score), matched_surface, surface_id))
        rescored_surfaces.sort(key=lambda item: (-item[0], item[1], item[2]))
        entities: dict[str, dict[str, Any]] = {}
        for score, matched_surface, surface_id in rescored_surfaces[:proposal_cap]:
            for entity_id in self.entity_ids[surface_id]:
                proposal = entities.get(entity_id)
                if proposal is None or score > proposal["char_score"]:
                    entities[entity_id] = {
                        "entity_id": entity_id,
                        "canonical_title": self.canonical_titles[entity_id],
                        "char_score": score,
                        "matched_surface": matched_surface,
                        "surface_sources": self.surface_sources[surface_id],
                    }
        ranked = sorted(
            entities.values(),
            key=lambda item: (
                -item["char_score"],
                item["canonical_title"].casefold(),
                item["entity_id"],
            ),
        )[:proposal_cap]
        return {
            "normalized_query": normalized,
            "proposals": ranked,
            "posting_lookups": posting_lookups,
            "postings_read": postings_read,
            "postings_cap_saturated": saturated,
            "surface_scores": len(overlap),
            "peak_accumulator_entries": len(overlap),
            "integer_operations": postings_read * 3
            + sum(
                len(query_grams) + len(self.surface_grams[item[2]]) + 5 for item in scored_surfaces
            )
            + distance_operations,
            "estimated_bytes_read": postings_read * 4 + len(query_grams) * 11 + len(overlap) * 8,
        }

    def lookup_query(
        self, query: str, *, postings_cap: int, proposal_cap: int = 128, max_span_tokens: int = 4
    ) -> dict[str, Any]:
        tokens = [match.group(0) for match in TOKEN_RE.finditer(query)]
        spans: list[tuple[str, int]] = []
        for token_count in range(1, min(max_span_tokens, len(tokens)) + 1):
            for start in range(len(tokens) - token_count + 1):
                selected = tokens[start : start + token_count]
                if token_count == 1:
                    token = selected[0]
                    normalized = normalize_fuzzy_surface(token)
                    redirect_authorized = normalized in self.resolved_redirect_surfaces
                    if (
                        (normalized in GENERIC_STOPWORDS and not redirect_authorized)
                        or len(normalized) < 2
                        or not (
                            redirect_authorized
                            or token[:1].isupper()
                            or any(ch.isdigit() for ch in token)
                            or len(token) >= 4
                        )
                    ):
                        continue
                spans.append((" ".join(selected), token_count))
        spans = sorted(set(spans), key=lambda item: (-item[1], -len(item[0]), item[0].casefold()))
        totals: Counter[str] = Counter()
        saturated = False
        best: dict[str, dict[str, Any]] = {}
        for span, token_count in spans:
            result = self.lookup(span, postings_cap=postings_cap, proposal_cap=32)
            for key in (
                "posting_lookups",
                "postings_read",
                "surface_scores",
                "integer_operations",
                "estimated_bytes_read",
            ):
                totals[key] += int(result[key])
            totals["peak_accumulator_entries"] = max(
                totals["peak_accumulator_entries"], int(result["peak_accumulator_entries"])
            )
            saturated = saturated or bool(result["postings_cap_saturated"])
            for proposal in result["proposals"]:
                candidate = dict(proposal)
                candidate["matched_query_span"] = span
                candidate["span_tokens"] = token_count
                current = best.get(candidate["entity_id"])
                key = (candidate["char_score"], token_count, len(span))
                if current is None or key > (
                    current["char_score"],
                    current["span_tokens"],
                    len(current["matched_query_span"]),
                ):
                    best[candidate["entity_id"]] = candidate
        ranked = sorted(
            best.values(),
            key=lambda item: (
                -item["char_score"],
                -item["span_tokens"],
                -len(item["matched_query_span"]),
                item["canonical_title"].casefold(),
                item["entity_id"],
            ),
        )[:proposal_cap]
        return {
            "proposals": ranked,
            "spans_considered": len(spans),
            "posting_lookups": totals["posting_lookups"],
            "postings_read": totals["postings_read"],
            "postings_cap_saturated": saturated,
            "surface_scores": totals["surface_scores"],
            "peak_accumulator_entries": totals["peak_accumulator_entries"],
            "integer_operations": totals["integer_operations"],
            "estimated_bytes_read": totals["estimated_bytes_read"],
        }


def factory_baseline(runtime: dict[str, Any]) -> list[dict[str, Any]]:
    by_entity: dict[str, dict[str, Any]] = {}
    for position, candidate in enumerate(runtime.get("pre_cap_candidates", ())):
        entity_id = str(candidate["entity_id"])
        channel = str(candidate["channel"])
        channel_score = float(candidate.get("channel_score", 0.0))
        # Factory exact title/redirect/alias channels outrank probabilistic anchors.
        rank_score = 1.0 if channel in {"title", "redirect", "alias"} else channel_score
        current = by_entity.get(entity_id)
        proposal = {
            "entity_id": entity_id,
            "canonical_title": str(candidate.get("canonical_title", "")),
            "rank_score": rank_score,
            "factory_position": position,
            "channels": [channel],
            "char_score": None,
        }
        if current is None:
            by_entity[entity_id] = proposal
        else:
            current["channels"] = sorted(set(current["channels"]) | {channel})
            if (rank_score, -position) > (current["rank_score"], -current["factory_position"]):
                current["rank_score"] = rank_score
                current["factory_position"] = position
    return sorted(
        by_entity.values(),
        key=lambda item: (-item["rank_score"], item["factory_position"], item["entity_id"]),
    )


def union_rank(
    baseline: list[dict[str, Any]],
    fuzzy: list[dict[str, Any]],
    *,
    threshold: float,
    fuzzy_weight: float,
    max_span_tokens: int = 4,
) -> tuple[list[dict[str, Any]], int, int]:
    by_entity = {item["entity_id"]: dict(item) for item in baseline}
    accepted = 0
    for proposal in fuzzy:
        if int(proposal.get("span_tokens", 1)) > max_span_tokens:
            continue
        char_score = float(proposal["char_score"])
        if char_score < threshold:
            continue
        accepted += 1
        entity_id = proposal["entity_id"]
        rank_score = char_score * fuzzy_weight
        if entity_id in by_entity:
            current = by_entity[entity_id]
            current["channels"] = sorted(set(current["channels"]) | {"char_fuzzy"})
            current["char_score"] = char_score
            current["rank_score"] = max(float(current["rank_score"]), rank_score)
        else:
            by_entity[entity_id] = {
                "entity_id": entity_id,
                "canonical_title": proposal["canonical_title"],
                "rank_score": rank_score,
                "factory_position": 1_000_000,
                "channels": ["char_fuzzy"],
                "char_score": char_score,
                "matched_surface": proposal["matched_surface"],
                "surface_sources": proposal["surface_sources"],
            }
    ranked = sorted(
        by_entity.values(),
        key=lambda item: (
            -float(item["rank_score"]),
            0 if any(ch != "char_fuzzy" for ch in item["channels"]) else 1,
            item["factory_position"],
            item["canonical_title"].casefold(),
            item["entity_id"],
        ),
    )
    return ranked[:GLOBAL_CAP], len(ranked), accepted


def metric_block(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    total = len(rows)
    recalls = {}
    for k in KS:
        hits = sum(row["gold"] in [item["entity_id"] for item in row[key][:k]] for row in rows)
        recalls[f"r_at_{k}"] = {
            "hits": hits,
            "total": total,
            "rate": hits / total if total else 0.0,
        }
    return recalls


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--factory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    benchmark = args.factory / "benchmark" / "397k"
    address = args.factory / "address" / "397k"
    runtime_rows = load_jsonl_gz(benchmark / "runtime.jsonl.gz")
    runtime_by_mention = {row["mention_id"]: row for row in runtime_rows}
    capture_rows = load_jsonl_gz(args.factory / "capture" / "397k.jsonl.gz")
    query_by_mention = {row["mention_id"]: row["query"] for row in capture_rows}
    labels = load_jsonl_gz(benchmark / "development_labels.jsonl.gz") + load_jsonl_gz(
        benchmark / "tuning_labels.jsonl.gz"
    )

    started = time.perf_counter()
    index = CharIndex(address / "aliases.jsonl.gz")
    build_seconds = time.perf_counter() - started
    lookup_rows: list[dict[str, Any]] = []
    for label in labels:
        runtime = runtime_by_mention[label["mention_id"]]
        lookup_started = time.perf_counter()
        surface_fuzzy = index.lookup(runtime["surface"], postings_cap=65_536)
        query_fuzzy = index.lookup_query(query_by_mention[label["mention_id"]], postings_cap=16_384)
        lookup_ms = (time.perf_counter() - lookup_started) * 1000.0
        lookup_rows.append(
            {
                "case_id": label["case_id"],
                "mention_id": label["mention_id"],
                "partition": label["partition"],
                "surface": runtime["surface"],
                "gold": label["correct_entity_ids"][0],
                "source_failure_state": label["failure_state"],
                "baseline": factory_baseline(runtime),
                "query": query_by_mention[label["mention_id"]],
                "surface_fuzzy": surface_fuzzy["proposals"],
                "surface_cost": {
                    key: value
                    for key, value in surface_fuzzy.items()
                    if key not in {"proposals", "normalized_query"}
                },
                "fuzzy": query_fuzzy["proposals"],
                "cost": {key: value for key, value in query_fuzzy.items() if key != "proposals"},
                "lookup_ms": lookup_ms,
            }
        )

    development = [row for row in lookup_rows if row["partition"] == "development"]
    tuning = [row for row in lookup_rows if row["partition"] == "tuning"]
    surface_sweep: list[dict[str, Any]] = []
    for threshold in (0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80):
        for fuzzy_weight in (0.70, 0.80, 0.90, 1.00):
            for row in development:
                ranked, pre_cap, _ = union_rank(
                    row["baseline"],
                    row["surface_fuzzy"],
                    threshold=threshold,
                    fuzzy_weight=fuzzy_weight,
                )
                row["surface_candidate_union"] = ranked
                row["surface_pre_cap"] = pre_cap
            metrics = metric_block(development, "surface_candidate_union")
            surface_sweep.append(
                {
                    "threshold": threshold,
                    "fuzzy_weight": fuzzy_weight,
                    "r_at_32": metrics["r_at_32"]["rate"],
                    "r_at_8": metrics["r_at_8"]["rate"],
                    "r_at_1": metrics["r_at_1"]["rate"],
                    "mean_pre_cap": statistics.fmean(row["surface_pre_cap"] for row in development),
                }
            )
    selected_surface = min(
        surface_sweep,
        key=lambda row: (
            -row["r_at_32"],
            -row["r_at_8"],
            -row["r_at_1"],
            row["mean_pre_cap"],
            -row["threshold"],
            row["fuzzy_weight"],
        ),
    )
    sweep: list[dict[str, Any]] = []
    for max_span_tokens in (1, 2, 3, 4):
        for threshold in (0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80):
            for fuzzy_weight in (0.70, 0.80, 0.90, 1.00):
                for row in development:
                    ranked, pre_cap, accepted = union_rank(
                        row["baseline"],
                        row["fuzzy"],
                        threshold=threshold,
                        fuzzy_weight=fuzzy_weight,
                        max_span_tokens=max_span_tokens,
                    )
                    row["candidate_union"] = ranked
                    row["pre_cap"] = pre_cap
                    row["accepted_fuzzy"] = accepted
                metrics = metric_block(development, "candidate_union")
                sweep.append(
                    {
                        "threshold": threshold,
                        "fuzzy_weight": fuzzy_weight,
                        "max_span_tokens": max_span_tokens,
                        "r_at_32": metrics["r_at_32"]["rate"],
                        "r_at_8": metrics["r_at_8"]["rate"],
                        "r_at_1": metrics["r_at_1"]["rate"],
                        "mean_pre_cap": statistics.fmean(row["pre_cap"] for row in development),
                    }
                )
    selected = min(
        sweep,
        key=lambda row: (
            -row["r_at_32"],
            -row["r_at_8"],
            -row["r_at_1"],
            row["mean_pre_cap"],
            -row["threshold"],
            row["max_span_tokens"],
            row["fuzzy_weight"],
        ),
    )

    # Re-run only the development-selected span architecture for final metrics and cost.
    for row in lookup_rows:
        selected_lookup_started = time.perf_counter()
        selected_fuzzy = index.lookup_query(
            row["query"],
            postings_cap=16_384,
            max_span_tokens=int(selected["max_span_tokens"]),
        )
        row["fuzzy"] = selected_fuzzy["proposals"]
        row["cost"] = {key: value for key, value in selected_fuzzy.items() if key != "proposals"}
        row["lookup_ms"] = (time.perf_counter() - selected_lookup_started) * 1000.0
        ranked, pre_cap, accepted = union_rank(
            row["baseline"],
            row["fuzzy"],
            threshold=float(selected["threshold"]),
            fuzzy_weight=float(selected["fuzzy_weight"]),
            max_span_tokens=int(selected["max_span_tokens"]),
        )
        row["candidate_union"] = ranked
        row["pre_cap"] = pre_cap
        row["accepted_fuzzy"] = accepted
        surface_ranked, surface_pre_cap, _ = union_rank(
            row["baseline"],
            row["surface_fuzzy"],
            threshold=float(selected_surface["threshold"]),
            fuzzy_weight=float(selected_surface["fuzzy_weight"]),
        )
        row["surface_candidate_union"] = surface_ranked
        row["surface_pre_cap"] = surface_pre_cap

    def partition_result(rows: list[dict[str, Any]]) -> dict[str, Any]:
        baseline_metrics = metric_block(rows, "baseline")
        union_metrics = metric_block(rows, "candidate_union")
        unique = {}
        for k in KS:
            unique[f"at_{k}"] = sum(
                row["gold"] not in [item["entity_id"] for item in row["baseline"][:k]]
                and row["gold"] in [item["entity_id"] for item in row["candidate_union"][:k]]
                for row in rows
            )
        taxonomy = Counter()
        for row in rows:
            gold = row["gold"]
            baseline_ids = [item["entity_id"] for item in row["baseline"]]
            fuzzy_ids = [item["entity_id"] for item in row["fuzzy"]]
            union_ids = [item["entity_id"] for item in row["candidate_union"]]
            if gold in baseline_ids[:GLOBAL_CAP]:
                taxonomy["factory_recovered_at_32"] += 1
            elif gold in union_ids:
                taxonomy["fuzzy_unique_recovery_at_32"] += 1
            elif gold in fuzzy_ids and gold not in union_ids:
                taxonomy["fuzzy_generated_but_threshold_or_global_cap_pruned"] += 1
            elif gold in index.canonical_titles:
                taxonomy["known_entity_not_character_retrieved"] += 1
            else:
                taxonomy["gold_entity_absent_from_alias_registry"] += 1
        total = len(rows)
        hypothesis_hits = sum(
            row["gold"]
            in {
                item["entity_id"]
                for item in row["fuzzy"]
                if int(item.get("span_tokens", 1)) <= int(selected["max_span_tokens"])
            }
            for row in rows
        )
        return {
            "aligned_mentions": total,
            "capture_mention_detected_flag": {"hits": total, "total": total, "rate": 1.0},
            "query_wide_mention_hypothesis_recall": {
                "hits": hypothesis_hits,
                "total": total,
                "rate": hypothesis_hits / total if total else 0.0,
                "definition": "gold entity generated from at least one selected generic query span before threshold/global cap",
            },
            "factory_baseline_entity_recall": baseline_metrics,
            "selected_union_entity_recall": union_metrics,
            "case_completeness": {f"at_{k}": union_metrics[f"r_at_{k}"] for k in KS},
            "fuzzy_unique_recoveries": unique,
            "union_gain_rate_points": {
                f"at_{k}": union_metrics[f"r_at_{k}"]["rate"]
                - baseline_metrics[f"r_at_{k}"]["rate"]
                for k in KS
            },
            "pre_global_cap_candidates": distribution([row["pre_cap"] for row in rows]),
            "global_cap_saturation": {
                "count": sum(row["pre_cap"] > GLOBAL_CAP for row in rows),
                "rate": sum(row["pre_cap"] > GLOBAL_CAP for row in rows) / total if total else 0.0,
            },
            "fuzzy_postings_cap_saturation": {
                "count": sum(row["cost"]["postings_cap_saturated"] for row in rows),
                "rate": sum(row["cost"]["postings_cap_saturated"] for row in rows) / total
                if total
                else 0.0,
            },
            "failure_taxonomy": dict(sorted(taxonomy.items())),
        }

    cost_rows = [row["cost"] for row in lookup_rows]
    operation_costs = []
    for index_number, row in enumerate(lookup_rows):
        cost = row["cost"]
        working = 4096 + int(cost["peak_accumulator_entries"]) * 8 + GLOBAL_CAP * 64
        operation_costs.append(
            P4OperationCost(
                operation_id=f"address.char-trigram-union.real397k.{index_number}",
                integer_operations=int(cost["integer_operations"]),
                macs=0,
                memory_bytes=int(cost["estimated_bytes_read"]),
                psram_bytes=int(cost["estimated_bytes_read"]),
                flash_bytes=0,
                psram_accesses=int(cost["posting_lookups"]),
                flash_accesses=0,
                random_psram_reads=int(cost["posting_lookups"]),
                random_flash_reads=0,
                sequential_reads=math.ceil(int(cost["estimated_bytes_read"]) / 4096),
                scratch_ram_bytes=working,
                model_bytes=index.logical_bytes,
            )
        )
    scenarios = {}
    for name, assumptions in v11_reference_assumptions().items():
        projections = [project_p4((cost,), assumptions) for cost in operation_costs]
        scenarios[name] = {
            "assumptions": assumptions.model_dump(mode="json"),
            "virtual_latency_ms": distribution([item.virtual_latency_ms for item in projections]),
            "compute_ms": distribution([item.compute_ms for item in projections]),
            "psram_transfer_ms": distribution([item.psram_transfer_ms for item in projections]),
            "random_access_ms": distribution([item.random_access_ms for item in projections]),
        }

    result = {
        "schema_version": SCHEMA_VERSION,
        "corpus_tier": "397k",
        "architecture": {
            "name": "factory-precap-plus-generic-query-span-char-trigram-dice-osa",
            "learned_parameters": 0,
            "global_entity_cap": GLOBAL_CAP,
            "union_semantics": "canonical entity ID union across complete Factory pre-cap and fuzzy proposals before one global cap",
            "fuzzy_surface_scope": "397k aliases export; canonical titles and resolved redirect source-title surfaces",
            "factory_channel_scope": "independently emitted title/redirect/alias/anchor pre-cap candidates",
            "selected_development_parameters": {
                "char_dice_or_osa_threshold": selected["threshold"],
                "fuzzy_rank_weight": selected["fuzzy_weight"],
                "postings_cap_per_query_span": 16_384,
                "fuzzy_proposal_cap": 128,
                "fuzzy_proposal_cap_per_query_span": 32,
                "max_span_tokens": selected["max_span_tokens"],
            },
        },
        "partition_protocol": {
            "development": "parameter selection",
            "tuning": "opened once after selection",
            "evaluation_and_final_held": "not present in Factory export and not consumed",
            "development_sweep": {
                "configuration_count": len(sweep),
                "selected": selected,
                "selection_objective": "lexicographic max R@32, R@8, R@1; then min pre-cap candidates",
            },
        },
        "inputs": {
            "aliases_sha256": sha256(address / "aliases.jsonl.gz"),
            "runtime_sha256": sha256(benchmark / "runtime.jsonl.gz"),
            "development_labels_sha256": sha256(benchmark / "development_labels.jsonl.gz"),
            "tuning_labels_sha256": sha256(benchmark / "tuning_labels.jsonl.gz"),
            "capture_sha256": sha256(args.factory / "capture" / "397k.jsonl.gz"),
        },
        "index": {
            "aliases_rows_read": index.alias_rows,
            "normalized_unique_surfaces": len(index.surfaces),
            "canonical_entities": len(index.canonical_titles),
            "char_trigram_keys": len(index.postings),
            "posting_entries": index.total_postings,
            "logical_resident_bytes": index.logical_bytes,
            "build_seconds_python_measurement": build_seconds,
        },
        "development": partition_result(development),
        "tuning": partition_result(tuning),
        "surface_only_control": {
            "parameters_selected_on_development": {
                "char_dice_osa_threshold": selected_surface["threshold"],
                "fuzzy_rank_weight": selected_surface["fuzzy_weight"],
                "configuration_count": len(surface_sweep),
            },
            "development_entity_recall": metric_block(development, "surface_candidate_union"),
            "tuning_entity_recall": metric_block(tuning, "surface_candidate_union"),
            "development_pre_cap_candidates": distribution(
                [row["surface_pre_cap"] for row in development]
            ),
            "tuning_pre_cap_candidates": distribution([row["surface_pre_cap"] for row in tuning]),
        },
        "resource": {
            "desktop_python_selected_query_lookup_ms": distribution(
                [row["lookup_ms"] for row in lookup_rows]
            ),
            "posting_list_lookups": distribution([row["posting_lookups"] for row in cost_rows]),
            "postings_read": distribution([row["postings_read"] for row in cost_rows]),
            "surface_scores": distribution([row["surface_scores"] for row in cost_rows]),
            "estimated_bytes_read": distribution(
                [row["estimated_bytes_read"] for row in cost_rows]
            ),
            "integer_operations": distribution([row["integer_operations"] for row in cost_rows]),
        },
        "p4_projection": {
            "calibration_id": V11_P4_CALIBRATION_ID,
            "evidence_class": "analytical_projection_not_hardware_measurement",
            "selected_architecture_only": True,
            "scenarios": scenarios,
            "caveat": "unchanged v11 scalar assumptions applied to deterministic logical work; no board, cache, or physical-page measurement",
        },
        "caveats": [
            "The authenticated exact-alignment cohort contains only 19 development and 31 tuning single-mention/single-gold rows; case completeness therefore equals entity recall on this cohort.",
            "The other 187 runtime mentions are alignment-quarantined and cannot be scored against entity gold labels.",
            "Factory aliases.jsonl.gz has kind=title only; redirect-vs-canonical source-title provenance is inferred from redirect_path length, and there is no separately labeled free-form alias class.",
            "Character retrieval is deterministic and label-free, but development labels selected the score threshold and fuzzy rank weight.",
            "The query-wide repair uses generic contiguous one-to-four-token hypotheses with a fixed language-level stopword/shape filter; it does not contain case-specific strings.",
            "Stopword-shaped spans are retained only when the authoritative alias registry gives that exact surface resolved redirect provenance.",
            "Desktop Python timing and compact logical byte accounting are not device measurements or allocator RSS.",
        ],
        "case_diagnostics": [
            {
                "case_id": row["case_id"],
                "partition": row["partition"],
                "surface": row["surface"],
                "query": row["query"],
                "gold": row["gold"],
                "baseline_rank": next(
                    (
                        i + 1
                        for i, item in enumerate(row["baseline"])
                        if item["entity_id"] == row["gold"]
                    ),
                    None,
                ),
                "fuzzy_rank": next(
                    (
                        i + 1
                        for i, item in enumerate(row["fuzzy"])
                        if item["entity_id"] == row["gold"]
                    ),
                    None,
                ),
                "matched_query_span": next(
                    (
                        item.get("matched_query_span")
                        for item in row["fuzzy"]
                        if item["entity_id"] == row["gold"]
                    ),
                    None,
                ),
                "union_rank": next(
                    (
                        i + 1
                        for i, item in enumerate(row["candidate_union"])
                        if item["entity_id"] == row["gold"]
                    ),
                    None,
                ),
                "pre_global_cap_candidates": row["pre_cap"],
                "source_failure_state": row["source_failure_state"],
            }
            for row in lookup_rows
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
