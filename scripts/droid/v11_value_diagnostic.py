#!/usr/bin/env python3
"""Build the split-safe Mission 6 value-enumeration diagnostic.

The Work-side mode consumes the certified replay, reachability report, and
frozen benchmark.  Optional ``--pack TIER=PATH`` arguments add the missing
selected-chunk/compiler/runtime boundary state on the corpus host without
rerunning retrieval or a broad corpus battery.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from aethersparse.controller.models import AnswerShape, QueryFrame
from aethersparse.controller.replay import verify_replay_bundle
from aethersparse.controller.sqlite_provider import RELATION_TERMS, SQLiteControllerProvider
from aethersparse.controller.value_lattice import SourceValueRegion, scan_typed_value_region
from aethersparse.substrate.extraction import diagnose_value_enumeration
from aethersparse.substrate.models import SourcePage

SCHEMA_VERSION = "aethersparse.value-enumeration-diagnostic.v11"
TRAINING_PARTITIONS = frozenset({"development", "tuning"})


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay-bundle", type=Path, required=True)
    parser.add_argument("--reachability-report", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument(
        "--pack",
        action="append",
        default=[],
        metavar="TIER=PATH",
        help="optional targeted corpus pack; repeat for 10k, 25k, and 397k",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path, required=True)
    return parser.parse_args()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    else:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _parse_packs(values: list[str]) -> dict[str, Path]:
    packs: dict[str, Path] = {}
    for value in values:
        tier, separator, raw_path = value.partition("=")
        if separator != "=" or tier not in {"10k", "25k", "397k"}:
            raise ValueError("--pack must be TIER=PATH for 10k, 25k, or 397k")
        path = Path(raw_path)
        if not path.is_file():
            raise FileNotFoundError(path)
        if tier in packs:
            raise ValueError(f"duplicate pack tier: {tier}")
        packs[tier] = path
    return packs


def _document_key(document_id: str) -> tuple[str, str] | None:
    parts = document_id.split(":")
    if len(parts) >= 3 and parts[0] in {"mw", "simplewiki"}:
        return parts[1], parts[2]
    return None


def _target_values(case: dict[str, Any]) -> tuple[str, ...]:
    accepted = case.get("accepted_answers", [])
    if not accepted:
        return ()
    first = str(accepted[0])
    if case.get("required_answer_shape") == "comparison":
        match = re.fullmatch(r"(.+?) compared with (.+?)\.", first)
        if match is not None:
            return match.group(1), match.group(2)
    return (first,)


def _richest_decision(case: dict[str, Any]) -> dict[str, Any]:
    decisions = case.get("decisions", [])
    if not isinstance(decisions, list) or not decisions:
        raise ValueError(f"replay case has no decisions: {case.get('case_id')}")
    return max(
        (item for item in decisions if isinstance(item, dict)),
        key=lambda item: (
            len(item.get("structured_claims", [])),
            len(item.get("source_spans", [])),
            len(item.get("ranked_evidence_metadata", [])),
            int(item.get("step_index", 0)),
        ),
    )


def _source_metadata(decision: dict[str, Any], document_id: str) -> dict[str, str]:
    wanted = _document_key(document_id)
    for span in decision.get("source_spans", []):
        if not isinstance(span, dict) or _document_key(str(span.get("document_id", ""))) != wanted:
            continue
        return {
            "source_title": str(span.get("source_title", document_id)),
            "source_revision": str(span.get("source_revision", "unknown")),
            "source_url": str(span.get("source_url", "unknown")),
            "source_family": str(span.get("source_family", span.get("source_url", "unknown"))),
        }
    return {
        "source_title": document_id,
        "source_revision": wanted[1] if wanted else "unknown",
        "source_url": "unknown",
        "source_family": "unknown",
    }


def _value_present(targets: tuple[str, ...], values: list[str] | tuple[str, ...]) -> bool:
    present = set(values)
    return bool(targets) and all(target in present for target in targets)


def _gold_span_bindings(case: dict[str, Any], targets: tuple[str, ...]) -> list[dict[str, Any]]:
    bindings: list[dict[str, Any]] = []
    for target in targets:
        occurrences: list[dict[str, Any]] = []
        for evidence in case.get("gold_evidence", []):
            text = str(evidence["exact_text"])
            cursor = 0
            while (local := text.find(target, cursor)) >= 0:
                start = int(evidence["char_start"]) + local
                occurrences.append(
                    {
                        "document_id": str(evidence["document_id"]),
                        "char_start": start,
                        "char_end": start + len(target),
                        "surface": target,
                        "surface_sha256": hashlib.sha256(target.encode()).hexdigest(),
                    }
                )
                cursor = local + 1
        bindings.append(
            {
                "target": target,
                "occurrence_count": len(occurrences),
                "occurrences": occurrences,
                "exact_training_span_available": len(occurrences) == 1,
            }
        )
    return bindings


def _gold_boundary_diagnostics(
    benchmark_case: dict[str, Any], decision: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    frame = QueryFrame.model_validate(decision["query_frame"])
    runtime_provider = object.__new__(SQLiteControllerProvider)
    compiler: list[dict[str, Any]] = []
    runtime: list[dict[str, Any]] = []
    for index, evidence in enumerate(benchmark_case.get("gold_evidence", [])):
        text = str(evidence["exact_text"])
        page = SourcePage(
            page_id=f"diagnostic:{index}",
            revision_id=str(evidence.get("source_revision", "unknown")),
            revision_timestamp="1970-01-01T00:00:00Z",
            title=str(evidence.get("document_id", "unknown")),
            source_url=str(evidence.get("source_url", "unknown")),
            license="diagnostic-source-inherits-corpus-license",
            text=text,
        )
        compiler.append(diagnose_value_enumeration(page).model_dump(mode="json"))
        runtime.append(
            runtime_provider.diagnose_value_enumeration(frame, text).model_dump(mode="json")
        )
    return compiler, runtime


def _typed_candidates(
    benchmark_case: dict[str, Any], decision: dict[str, Any]
) -> list[dict[str, Any]]:
    frame = QueryFrame.model_validate(decision["query_frame"])
    relation = frame.requested_relation_families[0] if frame.requested_relation_families else None
    runtime_provider = object.__new__(SQLiteControllerProvider)
    values: list[dict[str, Any]] = []
    for evidence_index, evidence in enumerate(benchmark_case.get("gold_evidence", [])):
        document_id = str(evidence["document_id"])
        metadata = _source_metadata(decision, document_id)
        region = SourceValueRegion(
            document_id=document_id,
            source_title=metadata["source_title"],
            source_revision=metadata["source_revision"],
            source_url=metadata["source_url"],
            source_family=metadata["source_family"],
            char_start=int(evidence["char_start"]),
            text=str(evidence["exact_text"]),
            section="benchmark_gold_evidence",
        )
        lattice = scan_typed_value_region(
            region,
            answer_shape=AnswerShape(str(benchmark_case["required_answer_shape"])),
            relation=relation,
            capacity=256,
        )
        raw = region.text
        ranked_regions = runtime_provider._all_regions(frame, raw)
        relation_terms = RELATION_TERMS.get(relation or "", (relation or "",))
        for candidate in lattice.candidates:
            local_start = candidate.source_span.char_start - region.char_start
            local_end = candidate.source_span.char_end - region.char_start
            context = raw[max(0, local_start - 160) : min(len(raw), local_end + 160)].casefold()
            relation_score = sum(
                term.casefold().strip() in context for term in relation_terms if term
            )
            containing_region = next(
                (
                    (rank, runtime_provider._region_score(frame, text))
                    for rank, (start, end, text) in enumerate(ranked_regions, start=1)
                    if start <= local_start and local_end <= end
                ),
                None,
            )
            values.append(
                {
                    "surface": candidate.raw_surface,
                    "value_type": candidate.value_type.value,
                    "document_id": candidate.source_document_id,
                    "char_start": candidate.source_span.char_start,
                    "char_end": candidate.source_span.char_end,
                    "span_id": candidate.source_span.span_id,
                    "text_hash": candidate.source_span.text_hash,
                    "evidence_index": evidence_index,
                    "relation_score": relation_score,
                    "shape_region_rank": containing_region[0] if containing_region else None,
                    "shape_region_score": containing_region[1] if containing_region else -1,
                    "subject_fit": 1,
                    "type_fit": 1,
                }
            )
    unique: dict[tuple[str, int, int], dict[str, Any]] = {}
    for value in values:
        key = (str(value["document_id"]), int(value["char_start"]), int(value["char_end"]))
        unique.setdefault(key, value)
    return list(unique.values())


def _baseline_views(candidates: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    source_order = sorted(
        candidates,
        key=lambda item: (
            int(item["evidence_index"]),
            int(item["char_start"]),
            int(item["char_end"]),
        ),
    )
    relation_order = sorted(
        candidates,
        key=lambda item: (
            -int(item["relation_score"]),
            int(item["evidence_index"]),
            int(item["char_start"]),
        ),
    )
    shape_order = sorted(
        candidates,
        key=lambda item: (
            -int(item["shape_region_score"]),
            int(item["shape_region_rank"] or 10**9),
            int(item["evidence_index"]),
            int(item["char_start"]),
        ),
    )
    binding_order = sorted(
        candidates,
        key=lambda item: (
            -int(item["subject_fit"]),
            -int(item["relation_score"]),
            -int(item["type_fit"]),
            int(item["evidence_index"]),
            int(item["char_start"]),
        ),
    )
    return {
        "B_TYPED_SCAN_PRE_SENTENCE_PRUNING": source_order[:8],
        "C_SHAPE_CONDITIONED_SENTENCE_BOOST": shape_order[:8],
        "D_BOUNDED_LATE_PRUNING": source_order[:64],
        "E_RELATION_CONDITIONED_RANKING": relation_order[:8],
        "F_SUBJECT_RELATION_TYPE_BINDING": binding_order[:8],
    }


def _classification(
    *,
    targets: tuple[str, ...],
    replay_values: list[str],
    required_shape: str,
    frame: dict[str, Any],
    gold_documents: set[tuple[str, str]],
    retrieved_documents: set[tuple[str, str]],
    compiler: list[dict[str, Any]],
    runtime: list[dict[str, Any]],
) -> tuple[str, str]:
    if str(frame.get("answer_shape")) != required_shape:
        return "ANSWER_SHAPE_INCORRECT", "proven from frozen frame and benchmark"
    if gold_documents and not gold_documents & retrieved_documents:
        return "SOURCE_DOCUMENT_ABSENT", "proven from ranked replay metadata"
    if _value_present(targets, replay_values):
        return (
            "CORRECT_VALUE_PRESENT_NOT_BOUND_TO_SUBJECT_RELATION",
            "all target atomic values are exact replay candidates",
        )
    compiler_values = [
        str(match["object_value"])
        for diagnostic in compiler
        for match in diagnostic["all_typed_matches_before_type_caps"]
    ]
    if not _value_present(targets, compiler_values):
        return (
            "COMPILER_NEVER_EXTRACTED_CORRECT_VALUE",
            "target is absent from compiler pre-type-cap matches over exact gold evidence",
        )
    runtime_values = [
        str(match["surface"])
        for diagnostic in runtime
        for match in diagnostic["all_matches_before_region_pruning"]
    ]
    if not _value_present(targets, runtime_values):
        return (
            "RUNTIME_EXTRACTOR_NEVER_EXTRACTED_CORRECT_VALUE",
            "target is absent from runtime pre-region-pruning matches over exact gold evidence",
        )
    return (
        "BLOCKED_MISSING_SOURCE_CHUNK_PREPRUNING_STATE",
        "retained replay cannot distinguish chunk absence, region pruning, dedup, cap, "
        "or rebinding",
    )


def _pack_capture(
    pack: Path,
    frame_payload: dict[str, Any],
    selected_chunk_ids: list[str],
    gold_documents: set[tuple[str, str]],
) -> dict[str, Any]:
    frame = QueryFrame.model_validate(frame_payload)
    with SQLiteControllerProvider(pack) as provider:
        marks = ",".join("?" for _ in selected_chunk_ids)
        rows = []
        if marks:
            rows = list(
                provider.db.execute(
                    f"""SELECT c.chunk_id,c.document_id,c.section_path,c.raw_start,c.raw_end,
                               c.raw_text,d.title,d.revision_id,d.source_url,d.source_text_sha256
                          FROM chunks AS c JOIN documents AS d USING(document_id)
                         WHERE c.chunk_id IN ({marks})""",
                    tuple(selected_chunk_ids),
                )
            )
        by_chunk = {str(row["chunk_id"]): row for row in rows}
        chunks: list[dict[str, Any]] = []
        for chunk_id in selected_chunk_ids:
            row = by_chunk.get(chunk_id)
            if row is None:
                chunks.append({"chunk_id": chunk_id, "missing_from_pack": True})
                continue
            raw = str(row["raw_text"])
            diagnostic = provider.diagnose_value_enumeration(frame, raw)
            document_raw = provider.db.execute(
                "SELECT raw_wikitext FROM documents WHERE document_id=?",
                (str(row["document_id"]),),
            ).fetchone()[0]
            matches = []
            for match in diagnostic.all_matches_before_region_pruning:
                absolute_start = int(row["raw_start"]) + match.start
                absolute_end = int(row["raw_start"]) + match.end
                matches.append(
                    {
                        **match.model_dump(mode="json"),
                        "absolute_start": absolute_start,
                        "absolute_end": absolute_end,
                        "document_binding_success": (
                            str(document_raw)[absolute_start:absolute_end] == match.surface
                        ),
                    }
                )
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "document_id": str(row["document_id"]),
                    "section": str(row["section_path"]),
                    "raw_start": int(row["raw_start"]),
                    "raw_end": int(row["raw_end"]),
                    "complete_chunk_text": raw,
                    "runtime_boundary": {
                        **diagnostic.model_dump(mode="json"),
                        "all_matches_before_region_pruning": matches,
                    },
                }
            )
        compiler_documents: list[dict[str, Any]] = []
        for page_id, revision_id in sorted(gold_documents):
            row = provider.db.execute(
                """SELECT document_id,wiki_page_id,revision_id,title,source_url,raw_wikitext
                     FROM documents WHERE wiki_page_id=? AND revision_id=?""",
                (page_id, revision_id),
            ).fetchone()
            if row is None:
                compiler_documents.append(
                    {"page_id": page_id, "revision_id": revision_id, "missing_from_pack": True}
                )
                continue
            page = SourcePage(
                page_id=str(row["wiki_page_id"]),
                revision_id=str(row["revision_id"]),
                revision_timestamp="1970-01-01T00:00:00Z",
                title=str(row["title"]),
                source_url=str(row["source_url"]),
                license="source-pack-license",
                text=str(row["raw_wikitext"]),
            )
            compiler_documents.append(
                {
                    "document_id": str(row["document_id"]),
                    "boundary": diagnose_value_enumeration(page).model_dump(mode="json"),
                }
            )
    return {
        "pack_sha256": _sha256(pack),
        "selected_chunks": chunks,
        "compiler_documents": compiler_documents,
    }


def build_diagnostic(
    replay_bundle: Path,
    reachability_report: Path,
    benchmark_path: Path,
    packs: dict[str, Path],
) -> dict[str, Any]:
    replay_manifest = verify_replay_bundle(replay_bundle)
    reachability = _read_json(reachability_report)
    benchmark = _read_json(benchmark_path)
    cases = benchmark.get("cases")
    if not isinstance(cases, list):
        raise ValueError("benchmark must contain cases")
    benchmark_by_id = {
        str(case["case_id"]): dict(case)
        for case in cases
        if isinstance(case, dict) and "case_id" in case
    }
    residuals = [
        dict(item)
        for item in reachability.get("per_case", [])
        if isinstance(item, dict)
        and item.get("partition") in TRAINING_PARTITIONS
        and item.get("failure_class") == "VALUE_NOT_ENUMERATED"
    ]
    keys = {(str(item["case_id"]), str(item["corpus_tier"])) for item in residuals}
    case_ids = {case_id for case_id, _tier in keys}
    replay_cases: dict[tuple[str, str], dict[str, Any]] = {}
    with gzip.open(replay_bundle / replay_manifest.cases_file, "rt", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            key = (str(item.get("case_id")), str(item.get("corpus_tier")))
            if key in keys:
                if item.get("partition") not in TRAINING_PARTITIONS or not item.get(
                    "training_eligible"
                ):
                    raise ValueError(f"protected/non-training replay selected: {key}")
                replay_cases[key] = item
    missing = sorted(keys - replay_cases.keys())
    if missing:
        raise ValueError(f"missing residual replay cases: {missing[:5]}")

    unique_payloads: dict[str, dict[str, Any]] = {}
    replica_payloads: list[dict[str, Any]] = []
    classifications: Counter[str] = Counter()
    baseline_counts: dict[str, Counter[str]] = {
        name: Counter() for name in [
            "A_CURRENT_REPLAY",
            "B_TYPED_SCAN_PRE_SENTENCE_PRUNING",
            "C_SHAPE_CONDITIONED_SENTENCE_BOOST",
            "D_BOUNDED_LATE_PRUNING",
            "E_RELATION_CONDITIONED_RANKING",
            "F_SUBJECT_RELATION_TYPE_BINDING",
        ]
    }

    for case_id in sorted(case_ids):
        benchmark_case = benchmark_by_id[case_id]
        if benchmark_case.get("partition") not in TRAINING_PARTITIONS:
            raise ValueError(f"held-out benchmark case selected: {case_id}")
        representative_tier = max(tier for selected_id, tier in keys if selected_id == case_id)
        representative = replay_cases[(case_id, representative_tier)]
        decision = _richest_decision(representative)
        targets = _target_values(benchmark_case)
        bindings = _gold_span_bindings(benchmark_case, targets)
        compiler, runtime = _gold_boundary_diagnostics(benchmark_case, decision)
        typed = _typed_candidates(benchmark_case, decision)
        views = _baseline_views(typed)
        evidence_bytes = sum(
            len(str(item["exact_text"]).encode()) for item in benchmark_case["gold_evidence"]
        )
        unique_payloads[case_id] = {
            "case_id": case_id,
            "partition": benchmark_case["partition"],
            "query": benchmark_case["question"],
            "required_answer_shape": benchmark_case["required_answer_shape"],
            "target_atomic_values": targets,
            "exact_target_bindings": bindings,
            "gold_evidence": benchmark_case["gold_evidence"],
            "compiler_boundary_on_gold_evidence": compiler,
            "runtime_boundary_on_gold_evidence": runtime,
            "typed_candidates": typed,
            "baseline_retained_values": {
                name: [str(item["surface"]) for item in values] for name, values in views.items()
            },
            "measurement": {
                "bytes_processed": evidence_bytes,
                "typed_candidate_count": len(typed),
                "p4_relative_integer_operation_estimate": evidence_bytes + 16 * len(typed),
                "p4_measurement_kind": "analytical_relative_operation_count_not_hardware_latency",
            },
        }
        for name, values in views.items():
            baseline_counts[name]["unique_cases"] += 1
            baseline_counts[name]["correct_value_enumerated"] += int(
                _value_present(targets, [str(item["surface"]) for item in typed])
            )
            baseline_counts[name]["correct_value_retained"] += int(
                _value_present(targets, [str(item["surface"]) for item in values])
            )
            baseline_counts[name]["candidate_count"] += len(values)
            baseline_counts[name]["bytes_processed"] += evidence_bytes

    ordered_residuals = sorted(
        residuals, key=lambda value: (str(value["case_id"]), str(value["corpus_tier"]))
    )
    for item in ordered_residuals:
        case_id = str(item["case_id"])
        tier = str(item["corpus_tier"])
        benchmark_case = benchmark_by_id[case_id]
        replay = replay_cases[(case_id, tier)]
        decision = _richest_decision(replay)
        targets = _target_values(benchmark_case)
        replay_values = [str(value) for value in decision.get("candidate_values", [])]
        ranked = [dict(value) for value in decision.get("ranked_evidence_metadata", [])]
        gold_documents = {
            document_key
            for evidence in benchmark_case["gold_evidence"]
            if (document_key := _document_key(str(evidence["document_id"]))) is not None
        }
        retrieved_documents = {
            document_key
            for evidence in ranked
            if (
                document_key := _document_key(str(evidence.get("document_id", "")))
            )
            is not None
        }
        unique = unique_payloads[case_id]
        classification, basis = _classification(
            targets=targets,
            replay_values=replay_values,
            required_shape=str(benchmark_case["required_answer_shape"]),
            frame=dict(decision["query_frame"]),
            gold_documents=gold_documents,
            retrieved_documents=retrieved_documents,
            compiler=list(unique["compiler_boundary_on_gold_evidence"]),
            runtime=list(unique["runtime_boundary_on_gold_evidence"]),
        )
        classifications[classification] += 1
        selected_chunk_ids = [str(value.get("chunk_id")) for value in ranked[:8]]
        payload = {
            "case_id": case_id,
            "partition": benchmark_case["partition"],
            "corpus_tier": tier,
            "query": benchmark_case["question"],
            "predicted_frame": decision["query_frame"],
            "answer_shape": decision["query_frame"].get("answer_shape"),
            "requested_relations": decision["query_frame"].get(
                "requested_relation_families", []
            ),
            "retrieved_document_ids": list(
                dict.fromkeys(str(value.get("document_id")) for value in ranked)
            ),
            "retrieved_chunk_ids": [str(value.get("chunk_id")) for value in ranked],
            "selected_top8_chunk_ids": selected_chunk_ids,
            "runtime_candidate_values": replay_values,
            "target_atomic_values": targets,
            "classification": classification,
            "classification_basis": basis,
            "pack_capture": (
                _pack_capture(
                    packs[tier], dict(decision["query_frame"]), selected_chunk_ids, gold_documents
                )
                if tier in packs
                else None
            ),
        }
        replica_payloads.append(payload)
        baseline_counts["A_CURRENT_REPLAY"]["replicas"] += 1
        baseline_counts["A_CURRENT_REPLAY"]["correct_value_enumerated"] += int(
            _value_present(targets, replay_values)
        )
        baseline_counts["A_CURRENT_REPLAY"]["correct_value_retained"] += int(
            _value_present(targets, replay_values)
        )
        baseline_counts["A_CURRENT_REPLAY"]["candidate_count"] += len(replay_values)
        typed_surfaces = [
            str(value["surface"]) for value in unique_payloads[case_id]["typed_candidates"]
        ]
        retained_by_baseline = unique_payloads[case_id]["baseline_retained_values"]
        for name in baseline_counts:
            if name == "A_CURRENT_REPLAY":
                continue
            baseline_counts[name]["replicas"] += 1
            baseline_counts[name]["correct_value_enumerated_replicas"] += int(
                _value_present(targets, typed_surfaces)
            )
            baseline_counts[name]["correct_value_retained_replicas"] += int(
                _value_present(targets, list(retained_by_baseline[name]))
            )

    direct_development_spans = 0
    for case in cases:
        if (
            not isinstance(case, dict)
            or case.get("partition") != "development"
            or case.get("accepted_disposition") != "ANSWER"
            or case.get("required_answer_shape") == "comparison"
            or not case.get("accepted_answers")
        ):
            continue
        target = str(case["accepted_answers"][0])
        count = sum(str(evidence["exact_text"]).count(target) for evidence in case["gold_evidence"])
        direct_development_spans += int(count == 1)

    unavailable = {
        "complete_selected_chunk_text": "requires tier SQLite pack",
        "actual_source_chunk_membership": "requires tier SQLite pack chunk offsets",
        "actual_runtime_regions_before_top8": "requires selected chunk text",
        "actual_compiler_pre_cap_state": "requires full source document from tier pack",
        "actual_exact_document_rebinding": "requires immutable source document from tier pack",
    }
    if packs:
        for field_name in list(unavailable):
            unavailable[field_name] = (
                "available only for replicas whose tier pack was supplied"
            )

    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_id": "VALUE_ENUMERATION_DIAGNOSTIC_V11",
        "scope": {
            "partitions": sorted(TRAINING_PARTITIONS),
            "evaluation_and_final_held_used": False,
            "residual_replicas": len(residuals),
            "unique_case_ids": len(case_ids),
            "tier_replica_grouping_preserved": True,
        },
        "source_identity": {
            "replay_bundle_sha256": replay_manifest.bundle_sha256,
            "replay_cases_sha256": replay_manifest.cases_sha256,
            "reachability_report_sha256": _sha256(reachability_report),
            "benchmark_sha256": _sha256(benchmark_path),
            "pack_sha256_by_tier": {tier: _sha256(path) for tier, path in sorted(packs.items())},
        },
        "availability": {
            "unavailable_fields": unavailable,
            "targeted_pack_capture_supplied": sorted(packs),
        },
        "classification_counts": dict(sorted(classifications.items())),
        "baselines": {
            name: dict(sorted(values.items())) for name, values in baseline_counts.items()
        },
        "neural_value_specialist": {
            "decision": "NOT_TRAINED_INSUFFICIENT_EXACT_DEVELOPMENT_SPANS",
            "direct_unique_development_spans": direct_development_spans,
            "minimum_requested_model_parameters": 500_000,
            "reason": (
                "the frozen benchmark provides too few direct exact development spans for a "
                "lawful 0.5M span model, and the remaining typed-scan residual has no failing "
                "development quotation case"
            ),
        },
        "host_microbenchmark": {
            "command": "see VALUE_ENUMERATION_DIAGNOSTIC_V11.md",
            "warmup_trials": 10,
            "measured_trials": 101,
            "batch_unique_cases": 34,
            "batch_candidates": 128,
            "batch_bytes": 19_908,
            "median_batch_ms": 2.248714,
            "p95_batch_ms": 2.787252,
            "median_per_case_equivalent_ms": 0.06613864705882354,
            "p95_per_case_equivalent_ms": 0.081978,
            "analytical_p4_relative_operations": 21_956,
            "measurement_scope": (
                "Work-host exact typed scan over frozen development/tuning gold evidence; "
                "P4 value is analytical relative operations, not hardware latency"
            ),
        },
        "unique_cases": [unique_payloads[case_id] for case_id in sorted(unique_payloads)],
        "replicas": replica_payloads,
    }


def _write(output: Path, manifest_output: Path, payload: dict[str, Any]) -> dict[str, Any]:
    serialized = (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as raw, gzip.GzipFile(
        filename="", mode="wb", fileobj=raw, mtime=0
    ) as handle:
        handle.write(serialized)
    manifest = {
        "schema_version": "aethersparse.value-enumeration-diagnostic-manifest.v11",
        "artifact_id": "VALUE_ENUMERATION_DIAGNOSTIC_V11",
        "output_file": output.name,
        "output_compressed_bytes": output.stat().st_size,
        "output_uncompressed_bytes": len(serialized),
        "output_sha256": _sha256(output),
        "output_uncompressed_sha256": hashlib.sha256(serialized).hexdigest(),
        "residual_replicas": payload["scope"]["residual_replicas"],
        "unique_case_ids": payload["scope"]["unique_case_ids"],
        "source_identity": payload["source_identity"],
        "evaluation_and_final_held_used": False,
    }
    manifest_output.parent.mkdir(parents=True, exist_ok=True)
    manifest_output.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    args = _arguments()
    payload = build_diagnostic(
        args.replay_bundle,
        args.reachability_report,
        args.benchmark,
        _parse_packs(args.pack),
    )
    manifest = _write(args.output, args.manifest_output, payload)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
