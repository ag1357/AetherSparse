"""Train and falsify compact evidence selectors on frozen real-corpus questions."""

from __future__ import annotations

import hashlib
import json
import math
import os
import resource
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any, cast

from aethersparse.selection.models import (
    FEATURE_NAMES,
    CandidateScore,
    QuantizedLinearModel,
)
from aethersparse.selection.selector import EvidenceSelector, model_identity


def _load_questions(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return cast(list[dict[str, Any]], payload["questions"])


def _gold_candidate(
    selector: EvidenceSelector, question: dict[str, Any]
) -> tuple[float, ...] | None:
    gold = set(question["gold_chunk_ids"])
    for candidate in selector.candidates(question["query"]):
        if candidate.chunk_id in gold:
            return candidate.features
    return None


def _negative_kind(
    features: tuple[float, ...],
    candidate_doc: str,
    question: dict[str, Any],
) -> str:
    gold_path = question["gold_document_path"]
    if candidate_doc in gold_path[:-1]:
        return "first_hop_missing_second_facet"
    if features[3] >= 0.5 and features[12] < 0.45:
        return "correct_entity_wrong_relation"
    if features[3] < 0.5 and features[0] >= 0.45:
        return "correct_relation_wrong_entity"
    if features[7] < 1.0:
        return "same_terms_wrong_time"
    if features[11] < 1.0:
        return "quotation_without_attribution"
    if features[9] > 0 and features[12] < 0.45:
        return "related_article_without_answer"
    if features[0] >= 0.5:
        return "lexically_strong_distractor"
    return "duplicated_source_lineage_or_other"


def train_reranker(
    corpus_path: Path,
    development_questions: Path,
    output_model: Path,
    output_manifest: Path,
    *,
    epochs: int = 24,
    learning_rate: float = 0.08,
) -> dict[str, Any]:
    selector = EvidenceSelector(corpus_path, candidate_limit=64)
    questions = _load_questions(development_questions)
    weights = [0.0] * len(FEATURE_NAMES)
    hard_negative_counts: Counter[str] = Counter()
    usable = 0
    pairs = 0
    prepared = [
        (question, selector.candidates(question["query"]))
        for question in questions
    ]
    for epoch in range(epochs):
        for question, candidates in prepared:
            gold_ids = set(question["gold_chunk_ids"])
            positive = next(
                (candidate for candidate in candidates if candidate.chunk_id in gold_ids),
                None,
            )
            if positive is None:
                continue
            usable += int(epoch == 0)
            negatives = [
                candidate for candidate in candidates
                if candidate.chunk_id not in gold_ids
            ]
            negatives.sort(
                key=lambda candidate: (
                    -candidate.deterministic_score, candidate.chunk_id
                )
            )
            for negative in negatives[:8]:
                if epoch == 0:
                    hard_negative_counts[
                        _negative_kind(
                            negative.features, negative.document_id, question
                        )
                    ] += 1
                difference = [
                    pos - neg
                    for pos, neg in zip(
                        positive.features, negative.features, strict=True
                    )
                ]
                margin = sum(
                    weight * value for weight, value in zip(
                        weights, difference, strict=True
                    )
                )
                gradient = 1.0 / (1.0 + math.exp(min(30.0, margin)))
                for index, value in enumerate(difference):
                    weights[index] += learning_rate * gradient * value
                pairs += int(epoch == 0)
        learning_rate *= 0.94
    max_weight = max(abs(weight) for weight in weights) or 1.0
    scale = max_weight / 127
    quantized = tuple(
        max(-127, min(127, round(weight / scale))) for weight in weights
    )
    training_manifest = {
        "development_questions_sha256": hashlib.sha256(
            development_questions.read_bytes()
        ).hexdigest(),
        "corpus_manifest": selector.store.stats(),
        "epochs": epochs,
        "pair_count": pairs,
        "usable_questions": usable,
        "hard_negative_counts": dict(sorted(hard_negative_counts.items())),
        "feature_names": FEATURE_NAMES,
    }
    model = QuantizedLinearModel(
        int8_weights=quantized,
        weight_scale=scale,
        bias=0.0,
        training_identity=model_identity(weights, training_manifest),
    )
    output_model.parent.mkdir(parents=True, exist_ok=True)
    output_model.write_text(model.model_dump_json(indent=2) + "\n", encoding="utf-8")
    output_manifest.write_text(
        json.dumps(training_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        **training_manifest,
        "model": model.model_dump(mode="json"),
        "output_model": str(output_model),
    }


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * percentile))]


def _io_bytes() -> int:
    try:
        for line in Path("/proc/self/io").read_text(encoding="utf-8").splitlines():
            if line.startswith("read_bytes:"):
                return int(line.split(":", 1)[1])
    except (OSError, ValueError):
        pass
    return 0


def _evaluate_stage(
    selector: EvidenceSelector,
    questions: list[dict[str, Any]],
    stage: str,
    *,
    traversal: bool = False,
    candidate_cache: dict[str, tuple[list[CandidateScore], float]] | None = None,
) -> dict[str, Any]:
    article_hits = span_hits = hard_hits = hard_count = 0
    unsupported = wrong_entity = answered = 0
    latencies: list[float] = []
    source_bytes: list[int] = []
    candidate_counts: list[int] = []
    depths: list[int] = []
    activations = gains = 0
    before_io = _io_bytes()
    before_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    for question in questions:
        cached = (
            candidate_cache[question["question_id"]]
            if candidate_cache is not None
            else None
        )
        trace = selector.select(
            question["query"],
            stage=stage,
            permit_targeted_traversal=traversal,
            initial_candidates=cached[0] if cached is not None else None,
        )
        gold_doc = question["gold_document_path"][-1]
        gold_chunks = set(question["gold_chunk_ids"])
        docs = {candidate.document_id for candidate in trace.selected_evidence}
        chunks = {candidate.chunk_id for candidate in trace.selected_evidence}
        article_hit = gold_doc in docs
        span_hit = bool(gold_chunks & chunks)
        article_hits += int(article_hit)
        span_hits += int(span_hit)
        if question["category"] in {"two_article", "three_article"}:
            hard_count += 1
            hard_hits += int(article_hit)
        if trace.traversal_activated:
            activations += 1
            base_docs = {
                candidate.document_id
                for candidate in trace.initial_candidates[: selector.selected_limit]
            }
            gain = int(gold_doc not in base_docs and article_hit)
            gains += gain
        # Constrained realization copies a verified candidate only at high confidence.
        top = trace.selected_evidence[0] if trace.selected_evidence else None
        if top is not None and top.final_score >= 0.60 and not trace.missing_facets:
            answered += 1
            unsupported += int(not span_hit)
            wrong_entity += int(not article_hit)
        # Every system pays the same bounded candidate-generation cost. Caching
        # avoids repeating database work between matched stages, but must not
        # make the reported end-to-end latency disappear.
        latencies.append(trace.latency_ms + (cached[1] if cached is not None else 0.0))
        source_bytes.append(trace.source_bytes)
        candidate_counts.append(len(trace.initial_candidates))
        depths.append(trace.traversal_depth)
    count = len(questions)
    io_delta = max(0, _io_bytes() - before_io)
    after_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    page_size = selector.store.db.execute("PRAGMA page_size").fetchone()[0]
    return {
        "question_count": count,
        "article_recall_at_k": article_hits / count,
        "evidence_span_recall_at_k": span_hits / count,
        "hard_subset_article_recall": hard_hits / max(1, hard_count),
        "mean_latency_ms": statistics.fmean(latencies),
        "p95_latency_ms": _percentile(latencies, 0.95),
        "mean_candidate_count": statistics.fmean(candidate_counts),
        "mean_source_bytes": statistics.fmean(source_bytes),
        "p95_source_bytes": _percentile([float(value) for value in source_bytes], 0.95),
        "os_physical_read_bytes": io_delta,
        "measured_physical_pages_read": io_delta / max(1, page_size),
        "page_size": page_size,
        "peak_rss_kib": max(before_rss, after_rss),
        "model_parameters": selector.model.parameter_count,
        "int8_model_bytes": selector.model.int8_model_bytes,
        "macs_per_candidate": selector.model.macs_per_candidate,
        "mean_macs_per_query": statistics.fmean(candidate_counts)
        * selector.model.macs_per_candidate,
        "traversal_activation_rate": activations / count,
        "mean_traversal_depth": statistics.fmean(depths),
        "marginal_recall_gain_per_activation": gains / max(1, activations),
        "answer_rate": answered / count,
        "unsupported_answer_rate": unsupported / max(1, answered),
        "silent_wrong_entity_rate": wrong_entity / max(1, answered),
    }


def _advise_os_cold(corpus_path: Path) -> bool:
    if not hasattr(os, "posix_fadvise") or not hasattr(os, "POSIX_FADV_DONTNEED"):
        return False
    descriptor = os.open(corpus_path, os.O_RDONLY)
    try:
        os.posix_fadvise(descriptor, 0, 0, os.POSIX_FADV_DONTNEED)
    finally:
        os.close(descriptor)
    return True


def evaluate_selection(
    corpus_path: Path,
    questions_path: Path,
    model_path: Path,
    output: Path,
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    questions = _load_questions(questions_path)
    if limit is not None:
        questions = questions[:limit]
    model = QuantizedLinearModel.model_validate_json(model_path.read_text(encoding="utf-8"))
    stages = (
        ("static_lexical_topk", "lexical", False),
        ("deterministic_rank_fusion", "fusion", False),
        ("compact_int8_reranker", "reranker", False),
        ("gap_triggered_targeted_traversal", "reranker", True),
        ("small_constrained_verified_rag", "reranker", True),
    )
    cold_advice_succeeded = _advise_os_cold(corpus_path)
    cold_started = time.perf_counter_ns()
    cold_selector = EvidenceSelector(corpus_path, model)
    cold_selector.select(questions[0]["query"], stage="reranker")
    cold_latency = (time.perf_counter_ns() - cold_started) / 1_000_000
    selector = EvidenceSelector(corpus_path, model)
    candidate_cache: dict[str, tuple[list[CandidateScore], float]] = {}
    generation_latencies: list[float] = []
    generation_io_before = _io_bytes()
    for question in questions:
        started = time.perf_counter_ns()
        candidates = selector.candidates(question["query"])
        elapsed = (time.perf_counter_ns() - started) / 1_000_000
        candidate_cache[question["question_id"]] = (candidates, elapsed)
        generation_latencies.append(elapsed)
    generation_io = max(0, _io_bytes() - generation_io_before)
    results = {
        name: _evaluate_stage(
            selector,
            questions,
            stage,
            traversal=traversal,
            candidate_cache=candidate_cache,
        )
        for name, stage, traversal in stages
    }
    static = results["static_lexical_topk"]
    fusion = results["deterministic_rank_fusion"]
    reranker = results["compact_int8_reranker"]
    targeted = results["gap_triggered_targeted_traversal"]
    hard_gain = (
        targeted["hard_subset_article_recall"]
        - static["hard_subset_article_recall"]
    )
    payload_ratio = targeted["mean_source_bytes"] / max(
        1.0, static["mean_source_bytes"]
    )
    decision = "REAL_CORPUS_ARCHITECTURE_FAILED"
    if (
        targeted["article_recall_at_k"] >= 0.90
        and targeted["evidence_span_recall_at_k"] >= 0.90
        and hard_gain >= 0.10
        and targeted["unsupported_answer_rate"] < 0.01
        and targeted["silent_wrong_entity_rate"] < 0.01
        and payload_ratio <= 2.0
    ):
        if targeted["article_recall_at_k"] > reranker["article_recall_at_k"] + 0.005:
            decision = "TARGETED_TRAVERSAL_JUSTIFIED"
        elif reranker["article_recall_at_k"] > fusion["article_recall_at_k"] + 0.005:
            decision = "COMPACT_RERANKER_JUSTIFIED"
        else:
            decision = "DETERMINISTIC_RANK_FUSION_SUFFICIENT"
    report = {
        "classification": "EVIDENCE_SELECTION_AND_RANKING",
        "corpus": str(corpus_path),
        "corpus_stats": selector.store.stats(),
        "question_set_sha256": hashlib.sha256(questions_path.read_bytes()).hexdigest(),
        "model_identity": model.training_identity,
        "cold_cache": {
            "posix_fadvise_dontneed": cold_advice_succeeded,
            "first_query_latency_ms": cold_latency,
            "limitation": (
                "Kernel advice is measurable but cannot guarantee eviction from every "
                "filesystem or device cache."
            ),
        },
        "shared_candidate_generation": {
            "mean_latency_ms": statistics.fmean(generation_latencies),
            "p95_latency_ms": _percentile(generation_latencies, 0.95),
            "os_physical_read_bytes": generation_io,
            "limitation": (
                "Linux /proc/self/io reports physical reads observed for the process; "
                "SQLite logical page access is not exposed by the bundled Python driver."
            ),
        },
        "systems": results,
        "hard_subset_gain_over_static": hard_gain,
        "targeted_to_static_payload_ratio": payload_ratio,
        "decision": decision,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report
