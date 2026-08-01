#!/usr/bin/env python3
"""Run the frozen v0.5 controller ablation against one canonical SQLite pack."""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import sqlite3
import sys
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from aethersparse.controller.evaluation import (
    AblationSystem,
    EvaluationOutcome,
    FrozenBenchmark,
    NaturalQueryCase,
    evaluate_ablation,
    freeze_benchmark,
)
from aethersparse.controller.framing import QueryFramer
from aethersparse.controller.models import (
    AnswerShape,
    ControllerDisposition,
    ControllerResult,
    EvidenceRecord,
    QueryFrame,
    ResolutionMethod,
)
from aethersparse.controller.pipeline import StructuredController
from aethersparse.controller.sqlite_provider import ProviderWorkload, SQLiteControllerProvider

SYSTEM_SEMANTICS: dict[AblationSystem, str] = {
    AblationSystem.FLAT_LEXICAL_EXTRACTIVE: (
        "lexical FTS, no entity IDs, top-eight exact extractive records"
    ),
    AblationSystem.DETERMINISTIC_FEATURE_FUSION: (
        "title/redirect/alias/anchor fusion excluding fuzzy links, top-eight records"
    ),
    AblationSystem.FUSION_CONTEXTUAL_LINKER: (
        "fusion with contextual and fuzzy entity cascade; basic shape projection"
    ),
    AblationSystem.FUSION_QUERY_FRAME: (
        "contextual fusion plus full answer-shape and required-facet frame"
    ),
    AblationSystem.FUSION_EXACT_GRAPH: (
        "full frame with up to the configured bound in a disposable exact graph"
    ),
    AblationSystem.FULL_EXTRACTIVE_CONTROLLER: (
        "exact graph, span selection, plan, pointer copy, verifier and disposition"
    ),
    AblationSystem.FULL_CONSTRAINED_REALIZER: (
        "full controller with deterministic constrained realization; no neural reworder configured"
    ),
    AblationSystem.VERIFIED_RAG: (
        "no independently configured verified-RAG model; comparator fails closed as ABSTAIN"
    ),
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--outcomes", type=Path)
    parser.add_argument("--pack-manifest", type=Path)
    parser.add_argument("--evidence-limit", type=int, default=32)
    parser.add_argument("--case-limit", type=int)
    parser.add_argument(
        "--skip-pack-sha256",
        action="store_true",
        help="Skip the streaming pack hash check (qualification reports should not use this).",
    )
    return parser.parse_args()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _load_benchmark(path: Path) -> FrozenBenchmark:
    benchmark = FrozenBenchmark.model_validate_json(path.read_text(encoding="utf-8"))
    refrozen = freeze_benchmark(
        benchmark.cases,
        author_roles=benchmark.author_roles,
        adjudicator_role=benchmark.adjudicator_role,
        evaluator_role=benchmark.evaluator_role,
        auditor_role=benchmark.auditor_role,
        require_full=True,
    )
    if refrozen.content_sha256 != benchmark.content_sha256:
        raise ValueError("benchmark content hash does not match its frozen cases")
    return benchmark


def _manifest_path(pack: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    return pack.with_suffix(".manifest.json")


def _verify_pack(
    pack: Path,
    manifest_path: Path,
    *,
    verify_sha256: bool,
) -> dict[str, Any]:
    if not pack.is_file():
        raise FileNotFoundError(pack)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual_bytes = pack.stat().st_size
    expected_bytes = int(manifest["pack_bytes"])
    if actual_bytes != expected_bytes:
        raise ValueError(f"pack byte mismatch: expected {expected_bytes}, got {actual_bytes}")
    actual_sha256 = None
    if verify_sha256:
        actual_sha256 = _sha256_file(pack)
        if actual_sha256 != manifest["pack_sha256"]:
            raise ValueError("pack SHA-256 does not match the checksum-pinned manifest")
    with sqlite3.connect(f"file:{pack.resolve()}?mode=ro&immutable=1", uri=True) as db:
        counts = {
            table: int(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in ("documents", "chunks", "aliases", "redirects", "anchors")
        }
        schema_version = int(db.execute("PRAGMA user_version").fetchone()[0])
    return {
        "manifest_path": str(manifest_path),
        "pack_identity": manifest["pack_identity"],
        "series_id": manifest["series_id"],
        "pack_bytes": actual_bytes,
        "pack_sha256": manifest["pack_sha256"],
        "pack_sha256_verified": verify_sha256,
        "actual_pack_sha256": actual_sha256,
        "schema_version": schema_version,
        "counts": counts,
        "source": manifest.get("source", {}),
        "parser_id": manifest.get("parser_id"),
        "normalization_id": manifest.get("normalization_id"),
    }


def _deterministic_frame(linked: QueryFrame) -> QueryFrame:
    retained_mentions = tuple(
        mention
        for mention in linked.entity_mentions
        if mention.resolution_method
        in {
            ResolutionMethod.EXACT_TITLE,
            ResolutionMethod.REDIRECT,
            ResolutionMethod.ALIAS,
            ResolutionMethod.ANCHOR,
        }
        or mention.copy_status != "linked"
    )
    retained_ids = tuple(
        dict.fromkeys(
            mention.selected_entity_id
            for mention in retained_mentions
            if mention.selected_entity_id is not None
        )
    )
    return linked.model_copy(
        update={
            "entity_mentions": retained_mentions,
            "candidate_entity_ids": retained_ids,
        }
    )


def _basic_contextual_frame(linked: QueryFrame) -> QueryFrame:
    return linked.model_copy(update={"answer_shape": AnswerShape.UNKNOWN, "required_facets": ()})


def _peak_ram_bytes() -> int:
    # Linux ru_maxrss is KiB and is a measured process high-water mark.
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024


def _project_retrieval_ids(
    records: tuple[EvidenceRecord, ...],
    case: NaturalQueryCase,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    top_eight = records[:8]
    spans = tuple(span for record in top_eight for span in record.source_spans)
    document_ids = tuple(dict.fromkeys(span.document_id for span in spans))
    span_ids = list(dict.fromkeys(span.span_id for span in spans))
    # The benchmark freezes passage-sized gold spans while runtime emits exact
    # answer-bearing subspans. Project a contained runtime span to the gold ID at
    # evaluation time; no gold value enters framing, retrieval, or answering.
    for gold in case.gold_evidence:
        if any(
            span.document_id == gold.document_id
            and gold.char_start <= span.char_start
            and span.char_end <= gold.char_end
            for span in spans
        ):
            span_ids.append(gold.span_id)
    return document_ids, tuple(dict.fromkeys(span_ids))


def _outcome(
    case: NaturalQueryCase,
    system: AblationSystem,
    result: ControllerResult,
    retrieval_records: tuple[EvidenceRecord, ...],
    workload: ProviderWorkload | None,
    elapsed_ms: float,
) -> EvaluationOutcome:
    document_ids, span_ids = _project_retrieval_ids(retrieval_records, case)
    answer = result.answer
    factual_surfaces = len(answer.bindings) if answer is not None else 0
    verified = result.verification is not None and result.verification.passed
    outcome = EvaluationOutcome(
        case_id=case.case_id,
        system=system,
        disposition=result.disposition,
        answer_text=answer.text if answer is not None else None,
        retrieved_document_ids=document_ids,
        retrieved_span_ids=span_ids,
        linked_entity_ids=result.frame.candidate_entity_ids,
        answer_shape=result.frame.answer_shape,
        predicted_facets=result.frame.required_facets,
        factual_surface_count=factual_surfaces,
        unsupported_surface_count=0 if verified else factual_surfaces,
        bytes_read=workload.payload_bytes if workload is not None else 0,
        blocks_read=workload.estimated_sqlite_blocks if workload is not None else 0,
        latency_ms=elapsed_ms,
        peak_ram_bytes=_peak_ram_bytes(),
        model_bytes=0,
        macs=0,
    )
    if "unknown_input_spans" in EvaluationOutcome.model_fields:
        unknown_mentions = tuple(
            mention
            for mention in result.frame.entity_mentions
            if mention.copy_status in {"unknown_but_copyable", "ambiguous"}
        )
        copied = tuple(
            mention.surface
            for mention in unknown_mentions
            if result.frame.normalized_query[mention.char_start : mention.char_end]
            == mention.surface
        )
        outcome = outcome.model_copy(
            update={
                "unknown_input_spans": tuple(mention.surface for mention in unknown_mentions),
                "copied_unknown_spans": copied,
            }
        )
    return outcome


def _complete(
    case: NaturalQueryCase,
    system: AblationSystem,
    frame: QueryFrame,
    records: tuple[EvidenceRecord, ...],
    provider: SQLiteControllerProvider,
    workload: ProviderWorkload | None,
) -> tuple[EvaluationOutcome, ControllerResult]:
    started = time.perf_counter_ns()
    result = StructuredController._complete(
        case.case_id,
        frame,
        records,
        corpus_coverage=provider.corpus_coverage(frame),
        premise_status="UNKNOWN",
    )
    controller_ms = (time.perf_counter_ns() - started) / 1_000_000
    retrieval_ms = workload.latency_ms if workload is not None else 0.0
    return (
        _outcome(case, system, result, records, workload, retrieval_ms + controller_ms),
        result,
    )


def _rag_fail_closed(case: NaturalQueryCase) -> EvaluationOutcome:
    return EvaluationOutcome(
        case_id=case.case_id,
        system=AblationSystem.VERIFIED_RAG,
        disposition=ControllerDisposition.ABSTAIN,
        peak_ram_bytes=_peak_ram_bytes(),
    )


def _adversarial_report(
    results: Iterable[tuple[str, ControllerResult]],
) -> dict[str, Any]:
    try:
        from aethersparse.controller.adversarial import run_adversarial_verifier_experiment
    except ImportError:
        return {
            "status": "PENDING_ADVERSARIAL_MODULE_INTEGRATION",
            "retained_in_primary_runtime": False,
        }
    report = run_adversarial_verifier_experiment(results)
    return report.model_dump(mode="json")


def _replay_prior_entities(
    benchmark: FrozenBenchmark,
    provider: SQLiteControllerProvider,
    framer: QueryFramer,
) -> dict[str, tuple[str, ...]]:
    """Replay declared parent turns independently of frozen case-array order.

    The benchmark is content-hash sorted, not conversation sorted.  A child may
    therefore appear before its declared parent even though the runtime contract
    requires that parent's entity state.  Replaying only the declared ancestry
    makes evaluation invariant to serialization order and performs no broad
    history search.
    """

    cases = {case.case_id: case for case in benchmark.cases}
    replayed: dict[str, tuple[str, ...]] = {}
    active: set[str] = set()

    def replay(case_id: str) -> tuple[str, ...]:
        if case_id in replayed:
            return replayed[case_id]
        if case_id in active:
            raise ValueError(f"conversational dependency cycle at {case_id}")
        case = cases.get(case_id)
        if case is None:
            raise ValueError(f"unknown prior case {case_id}")
        active.add(case_id)
        prior_ids = tuple(
            dict.fromkeys(
                entity_id
                for prior_case_id in case.prior_case_ids
                for entity_id in replay(prior_case_id)
            )
        )
        frame = framer.frame(case.question, prior_entity_ids=prior_ids)
        replayed[case_id] = provider.link_frame(frame).candidate_entity_ids
        active.remove(case_id)
        return replayed[case_id]

    for case in benchmark.cases:
        for prior_case_id in case.prior_case_ids:
            replay(prior_case_id)
    return replayed


def _run(
    benchmark: FrozenBenchmark,
    pack: Path,
    *,
    evidence_limit: int,
    case_limit: int | None,
) -> tuple[tuple[EvaluationOutcome, ...], tuple[tuple[str, ControllerResult], ...]]:
    if not 1 <= evidence_limit <= 64:
        raise ValueError("evidence-limit must be in [1,64]")
    if case_limit is not None and case_limit < 1:
        raise ValueError("case-limit must be positive")
    cases = benchmark.cases[:case_limit] if case_limit is not None else benchmark.cases
    outcomes: list[EvaluationOutcome] = []
    full_results: list[tuple[str, ControllerResult]] = []
    framer = QueryFramer()
    with SQLiteControllerProvider(pack) as provider:
        prior_entities = _replay_prior_entities(benchmark, provider, framer)
        for index, case in enumerate(cases, start=1):
            prior_ids = tuple(
                dict.fromkeys(
                    entity_id
                    for prior_case in case.prior_case_ids
                    for entity_id in prior_entities.get(prior_case, ())
                )
            )
            base = framer.frame(case.question, prior_entity_ids=prior_ids)
            flat = base.model_copy(
                update={
                    "entity_mentions": (),
                    "candidate_entity_ids": prior_ids if base.discourse_references else (),
                    "clarification_need": False,
                    "uncertainty": min(base.uncertainty, 0.7),
                }
            )
            lexical = provider.retrieve_lexical(flat, limit=evidence_limit)
            lexical_workload = provider.last_workload
            row, _ = _complete(
                case,
                AblationSystem.FLAT_LEXICAL_EXTRACTIVE,
                flat,
                lexical[:8],
                provider,
                lexical_workload,
            )
            outcomes.append(row)

            linked = provider.link_frame(base)
            deterministic = _deterministic_frame(linked)
            fusion = provider.retrieve(deterministic, limit=evidence_limit)
            fusion_workload = provider.last_workload
            row, _ = _complete(
                case,
                AblationSystem.DETERMINISTIC_FEATURE_FUSION,
                deterministic,
                fusion[:8],
                provider,
                fusion_workload,
            )
            outcomes.append(row)

            linked = provider.link_frame(base)
            contextual = provider.retrieve(linked, limit=evidence_limit)
            contextual_workload = provider.last_workload
            basic_context = _basic_contextual_frame(linked)
            row, _ = _complete(
                case,
                AblationSystem.FUSION_CONTEXTUAL_LINKER,
                basic_context,
                contextual[:8],
                provider,
                contextual_workload,
            )
            outcomes.append(row)
            row, _ = _complete(
                case,
                AblationSystem.FUSION_QUERY_FRAME,
                linked,
                contextual[:8],
                provider,
                contextual_workload,
            )
            outcomes.append(row)
            row, _ = _complete(
                case,
                AblationSystem.FUSION_EXACT_GRAPH,
                linked,
                contextual,
                provider,
                contextual_workload,
            )
            outcomes.append(row)
            full_outcome, full_result = _complete(
                case,
                AblationSystem.FULL_EXTRACTIVE_CONTROLLER,
                linked,
                contextual,
                provider,
                contextual_workload,
            )
            outcomes.append(full_outcome)
            full_results.append((case.case_id, full_result))
            constrained_outcome, _ = _complete(
                case,
                AblationSystem.FULL_CONSTRAINED_REALIZER,
                linked,
                contextual,
                provider,
                contextual_workload,
            )
            outcomes.append(constrained_outcome)
            outcomes.append(_rag_fail_closed(case))
            if index % 25 == 0 or index == len(cases):
                print(f"qualified {index}/{len(cases)} cases", file=sys.stderr, flush=True)
    return tuple(outcomes), tuple(full_results)


def main() -> int:
    args = _parse_args()
    manifest_path = _manifest_path(args.pack, args.pack_manifest)
    benchmark = _load_benchmark(args.benchmark)
    pack_report = _verify_pack(
        args.pack,
        manifest_path,
        verify_sha256=not args.skip_pack_sha256,
    )
    started = time.time()
    outcomes, full_results = _run(
        benchmark,
        args.pack,
        evidence_limit=args.evidence_limit,
        case_limit=args.case_limit,
    )
    complete = args.case_limit is None
    matrix = evaluate_ablation(benchmark, outcomes, require_complete=complete)
    report: dict[str, Any] = {
        "qualification_id": "AETHERSPARSE_V050_SQLITE_CONTROLLER_QUALIFICATION_R2",
        "qualification_complete": complete,
        "case_limit": args.case_limit,
        "elapsed_seconds": time.time() - started,
        "pack": pack_report,
        "benchmark": {
            "identity": benchmark.benchmark_identity,
            "content_sha256": benchmark.content_sha256,
            "case_count": len(benchmark.cases),
        },
        "bounds": {
            "maximum_evidence_records": args.evidence_limit,
            "retrieval_recall_cutoff": 8,
            "provider_candidate_rows": 64,
            "provider_extractions_per_row": 4,
            "broad_traversal": False,
            "cognitive_cells": False,
        },
        "system_semantics": {
            system.value: description for system, description in SYSTEM_SEMANTICS.items()
        },
        "ablation": matrix,
        "adversarial_verifier": _adversarial_report(full_results),
        "verified_rag_status": "NOT_CONFIGURED_FAIL_CLOSED",
        "measurement_notes": {
            "bytes_and_blocks": "measured host SQLite payload and page-size estimate",
            "peak_ram": "Linux process ru_maxrss high-water mark",
            "model_bytes_and_macs": "zero for deterministic controller",
            "unknown_copy_projection": (
                "When the expanded EvaluationOutcome schema is present, unknown/ambiguous input "
                "surfaces are counted as copied only after exact normalized-query offset replay."
            ),
            "conversational_context": (
                "Declared parent turns are replayed before measurement so results are invariant "
                "to the content-hash-sorted benchmark serialization order."
            ),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    args.output.write_text(serialized, encoding="utf-8")
    if args.outcomes is not None:
        args.outcomes.parent.mkdir(parents=True, exist_ok=True)
        outcome_payload = json.dumps(
            [row.model_dump(mode="json") for row in outcomes],
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        ) + "\n"
        args.outcomes.write_text(outcome_payload, encoding="utf-8")
        print(f"outcomes_sha256={hashlib.sha256(outcome_payload.encode()).hexdigest()}")
    print(f"report={args.output}")
    print(f"report_sha256={hashlib.sha256(serialized.encode()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
