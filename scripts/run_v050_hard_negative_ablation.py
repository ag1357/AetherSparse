#!/usr/bin/env python3
"""Train on tuning hard negatives and evaluate a tiny nonlinear evidence scorer."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path

from aethersparse.controller.evaluation import FrozenBenchmark, NaturalQueryCase, Partition
from aethersparse.controller.framing import QueryFramer
from aethersparse.controller.models import ControllerDisposition, EvidenceRecord
from aethersparse.controller.nonlinear_ranker import (
    RankerExample,
    ranker_features,
    rerank_records,
    train_tiny_evidence_mlp,
)
from aethersparse.controller.sqlite_provider import SQLiteControllerProvider


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--retrieval-limit", type=int, default=32)
    return parser.parse_args()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _relevant(record: EvidenceRecord, case: NaturalQueryCase) -> bool:
    return any(
        span.document_id == gold.document_id
        and gold.char_start <= span.char_start
        and span.char_end <= gold.char_end
        for span in record.source_spans
        for gold in case.gold_evidence
    )


def _article_relevant(record: EvidenceRecord, case: NaturalQueryCase) -> bool:
    gold_documents = {gold.document_id for gold in case.gold_evidence}
    return any(span.document_id in gold_documents for span in record.source_spans)


def _negative_kind(record: EvidenceRecord) -> str:
    if record.entity_fit == 1.0 and record.relation_fit < 1.0:
        return "correct_entity_wrong_relation"
    if record.entity_fit < 1.0 and record.relation_fit == 1.0:
        return "correct_relation_wrong_entity"
    if record.temporal_fit < 1.0:
        return "right_terms_wrong_date"
    if record.attribution_fit < 1.0:
        return "quotation_wrong_speaker"
    if record.answer_shape_fit < 1.0:
        return "answer_shape_mismatch"
    return "related_non_answer_or_duplicate_family"


def _collect(
    provider: SQLiteControllerProvider,
    cases: tuple[NaturalQueryCase, ...],
    *,
    limit: int,
) -> tuple[dict[str, tuple[EvidenceRecord, ...]], int]:
    framer = QueryFramer()
    collected: dict[str, tuple[EvidenceRecord, ...]] = {}
    record_count = 0
    for index, case in enumerate(cases, start=1):
        frame = provider.link_frame(framer.frame(case.question))
        records = provider.retrieve(frame, limit=limit)
        collected[case.case_id] = records
        record_count += len(records)
        if index % 50 == 0:
            print(f"collected {index}/{len(cases)} cases", file=sys.stderr, flush=True)
    return collected, record_count


def main() -> int:
    args = _args()
    if not 8 <= args.retrieval_limit <= 64:
        raise SystemExit("retrieval-limit must be in [8,64]")
    benchmark = FrozenBenchmark.model_validate_json(args.benchmark.read_text(encoding="utf-8"))
    tuning = tuple(
        case
        for case in benchmark.cases
        if case.partition is Partition.TUNING
        and case.accepted_disposition is ControllerDisposition.ANSWER
    )
    development = tuple(
        case
        for case in benchmark.cases
        if case.partition is Partition.DEVELOPMENT
        and case.accepted_disposition is ControllerDisposition.ANSWER
    )
    started = time.time()
    with SQLiteControllerProvider(args.pack) as provider:
        tuning_records, tuning_record_count = _collect(
            provider, tuning, limit=args.retrieval_limit
        )
        development_records, development_record_count = _collect(
            provider, development, limit=args.retrieval_limit
        )

    examples: list[RankerExample] = []
    taxonomy: Counter[str] = Counter()
    training_queries_with_positive = 0
    for case in tuning:
        records = tuning_records[case.case_id]
        positives = [record for record in records if _relevant(record, case)][:2]
        if not positives:
            continue
        training_queries_with_positive += 1
        negatives = [record for record in records if not _relevant(record, case)][:6]
        for record in positives:
            examples.append(
                RankerExample(
                    query_id=case.case_id,
                    features=ranker_features(record),
                    relevant=True,
                )
            )
        for record in negatives:
            taxonomy[_negative_kind(record)] += 1
            examples.append(
                RankerExample(
                    query_id=case.case_id,
                    features=ranker_features(record),
                    relevant=False,
                )
            )
    model = train_tiny_evidence_mlp(tuple(examples))
    baseline_evidence = model_evidence = baseline_article = model_article = 0
    for case in development:
        records = development_records[case.case_id]
        baseline = records[:8]
        reranked = rerank_records(model, records)[:8]
        baseline_evidence += int(any(_relevant(record, case) for record in baseline))
        model_evidence += int(any(_relevant(record, case) for record in reranked))
        baseline_article += int(any(_article_relevant(record, case) for record in baseline))
        model_article += int(any(_article_relevant(record, case) for record in reranked))
    denominator = len(development)
    baseline_evidence_rate = baseline_evidence / denominator if denominator else 0.0
    model_evidence_rate = model_evidence / denominator if denominator else 0.0
    baseline_article_rate = baseline_article / denominator if denominator else 0.0
    model_article_rate = model_article / denominator if denominator else 0.0
    evidence_gain = model_evidence_rate - baseline_evidence_rate
    article_gain = model_article_rate - baseline_article_rate
    eligible = evidence_gain >= 0.01 and article_gain >= 0.0
    report = {
        "experiment_id": "AETHERSPARSE_V050_NONLINEAR_HARD_NEGATIVE_R1",
        "benchmark_identity": benchmark.benchmark_identity,
        "benchmark_content_sha256": benchmark.content_sha256,
        "pack_path": str(args.pack.resolve()),
        "pack_sha256": _sha256_file(args.pack),
        "partitions": {"training": "tuning", "evaluation": "development"},
        "tuning_answerable_cases": len(tuning),
        "development_answerable_cases": len(development),
        "training_queries_with_positive": training_queries_with_positive,
        "training_examples": len(examples),
        "tuning_candidate_records": tuning_record_count,
        "development_candidate_records": development_record_count,
        "hard_negative_taxonomy": dict(sorted(taxonomy.items())),
        "deterministic_evidence_recall_at_8": baseline_evidence_rate,
        "nonlinear_evidence_recall_at_8": model_evidence_rate,
        "evidence_recall_gain": evidence_gain,
        "deterministic_article_recall_at_8": baseline_article_rate,
        "nonlinear_article_recall_at_8": model_article_rate,
        "article_recall_gain": article_gain,
        "model": model.model_dump(mode="json"),
        "parameter_count": model.parameter_count,
        "int8_model_bytes": model.int8_model_bytes,
        "macs_per_candidate": model.macs_per_record,
        "eligible_for_primary_runtime": eligible,
        "decision": "RETAIN_FOR_FULL_ABLATION" if eligible else "REMOVE_NO_MEASURABLE_VALUE",
        "elapsed_seconds": time.time() - started,
        "rejected_linear_ranker_reconstructed": False,
    }
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(serialized, encoding="utf-8")
    print(f"output={args.output}")
    print(f"sha256={hashlib.sha256(serialized.encode()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
