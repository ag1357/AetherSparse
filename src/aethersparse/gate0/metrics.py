"""Gate 0 metrics, deterministic pack accounting, and honest BLOCKED states."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aethersparse.gate0.extractor import read_candidate_set
from aethersparse.gate0.models import (
    ExtractionRun,
    Gate0Report,
    GoldPartition,
    MetricResult,
    MetricStatus,
    ReviewAction,
    ReviewedGoldRecord,
    ReviewerKind,
    ReviewReason,
    ValidationRun,
)
from aethersparse.gate0.review import ReviewJournal
from aethersparse.gate0.sources import SourceRepository, stable_json
from aethersparse.gate0.validator import read_validation_set
from aethersparse.models import KeyClass

PILOT_TARGETS = {
    GoldPartition.CALIBRATION: 100,
    GoldPartition.DEVELOPMENT: 250,
    GoldPartition.SEALED_GATE0: 150,
}
KEY_BYTES = {
    KeyClass.K0: 0,
    KeyClass.K1: 16,
    KeyClass.K2: 32,
    KeyClass.K3: 128,
}


def _read_gold(path: Path) -> tuple[ReviewedGoldRecord, ...]:
    if not path.exists():
        return ()
    return tuple(
        ReviewedGoldRecord.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


def read_gold_partitions(root: Path) -> tuple[ReviewedGoldRecord, ...]:
    result: list[ReviewedGoldRecord] = []
    for partition in GoldPartition:
        result.extend(_read_gold(root / f"{partition.value}.jsonl"))
    return tuple(result)


def compile_reviewed_pack(
    records: tuple[ReviewedGoldRecord, ...],
    source_repository: SourceRepository,
    output_dir: Path,
) -> dict[str, Any]:
    """Serialize a deterministic reference pack and return byte-level accounting."""

    output_dir.mkdir(parents=True, exist_ok=True)
    ordered_records = tuple(sorted(records, key=lambda item: item.candidate_id))
    packet_lines = [
        stable_json(record.packet.model_dump(mode="json")) for record in ordered_records
    ]
    packet_blob = b"\n".join(packet_lines) + (b"\n" if packet_lines else b"")

    spans: dict[tuple[str, int, int], dict[str, Any]] = {}
    relation_index: dict[str, list[str]] = defaultdict(list)
    subject_index: dict[str, list[str]] = defaultdict(list)
    for record in ordered_records:
        packet = record.packet
        relation_index[packet.primary_relation].append(packet.candidate_id)
        subject_index[packet.primary_subject].append(packet.candidate_id)
        for claim in packet.atomic_claims:
            alignment = claim.alignment
            key = (
                alignment.source_doc_id,
                alignment.raw_char_start,
                alignment.raw_char_end,
            )
            spans[key] = alignment.model_dump(mode="json")
    span_lines = [stable_json(spans[key]) for key in sorted(spans)]
    span_blob = b"\n".join(span_lines) + (b"\n" if span_lines else b"")
    indexes = {
        "relations": {key: sorted(value) for key, value in sorted(relation_index.items())},
        "subjects": {key: sorted(value) for key, value in sorted(subject_index.items())},
    }
    index_blob = stable_json(indexes)

    payload_bytes = sum(len(stable_json(record.packet.payload)) for record in ordered_records)
    logical_header_bytes = len(ordered_records) * 128
    key_bytes = sum(KEY_BYTES[record.packet.key_class] for record in ordered_records)
    logical_span_bytes = sum(
        64 + len(str(span["raw_text"]).encode("utf-8")) for span in spans.values()
    )
    logical_fixed_overhead = 256
    logical_pack_bytes = (
        logical_header_bytes
        + payload_bytes
        + key_bytes
        + len(index_blob)
        + logical_span_bytes
        + logical_fixed_overhead
    )
    normalized_source_bytes = sum(
        len(snapshot.normalized_text.encode("utf-8")) for snapshot in source_repository.list()
    )
    raw_source_bytes = sum(snapshot.raw_byte_length for snapshot in source_repository.list())
    component_hashes = {
        "packets": f"sha256:{hashlib.sha256(packet_blob).hexdigest()}",
        "spans": f"sha256:{hashlib.sha256(span_blob).hexdigest()}",
        "indexes": f"sha256:{hashlib.sha256(index_blob).hexdigest()}",
    }
    unsigned_manifest = {
        "pack_id": "apollo_gate0_reviewed_v0",
        "schema_version": "0.3.0",
        "source_manifest_hash": source_repository.manifest_hash(),
        "reviewed_packet_count": len(ordered_records),
        "span_count": len(spans),
        "component_hashes": component_hashes,
        "logical_pack_bytes": logical_pack_bytes,
        "signature": "UNSIGNED_GATE0_QUALIFICATION",
    }
    manifest_hash = f"sha256:{hashlib.sha256(stable_json(unsigned_manifest)).hexdigest()}"
    manifest = {**unsigned_manifest, "manifest_hash": manifest_hash}
    manifest_blob = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    actual_serialized_pack_bytes = (
        len(packet_blob) + len(span_blob) + len(index_blob) + len(manifest_blob)
    )
    accounting = {
        "reviewed_packet_count": len(ordered_records),
        "span_count": len(spans),
        "actual_serialized_pack_bytes": actual_serialized_pack_bytes,
        "actual_packet_bytes": len(packet_blob),
        "actual_source_span_bytes": len(span_blob),
        "actual_index_bytes": len(index_blob),
        "actual_manifest_fixed_overhead_bytes": len(manifest_blob),
        "logical_pack_bytes": logical_pack_bytes,
        "logical_header_bytes": logical_header_bytes,
        "logical_payload_bytes": payload_bytes,
        "logical_key_bytes": key_bytes,
        "logical_index_bytes": len(index_blob),
        "logical_source_span_bytes": logical_span_bytes,
        "logical_fixed_overhead_bytes": logical_fixed_overhead,
        "raw_source_bytes": raw_source_bytes,
        "normalized_source_bytes": normalized_source_bytes,
        "actual_compiled_bytes_per_source_byte": (
            actual_serialized_pack_bytes / normalized_source_bytes
            if normalized_source_bytes
            else 0.0
        ),
        "logical_compiled_bytes_per_source_byte": (
            logical_pack_bytes / normalized_source_bytes if normalized_source_bytes else 0.0
        ),
        "manifest_hash": manifest_hash,
        "component_hashes": component_hashes,
    }
    (output_dir / "packets.jsonl").write_bytes(packet_blob)
    (output_dir / "source_spans.jsonl").write_bytes(span_blob)
    (output_dir / "indexes.json").write_bytes(index_blob + b"\n")
    (output_dir / "manifest.json").write_bytes(manifest_blob)
    (output_dir / "size_breakdown.json").write_text(
        json.dumps(accounting, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return accounting


def _blocked(metric_id: str, threshold: str, evidence: str) -> MetricResult:
    return MetricResult(
        metric_id=metric_id,
        status=MetricStatus.BLOCKED,
        threshold=threshold,
        evidence=evidence,
    )


def _metric(
    metric_id: str,
    value: float | int,
    *,
    passed: bool,
    threshold: str,
    evidence: str,
) -> MetricResult:
    return MetricResult(
        metric_id=metric_id,
        status=MetricStatus.PASS if passed else MetricStatus.FAIL,
        value=value,
        threshold=threshold,
        evidence=evidence,
    )


def evaluate_gate0(
    *,
    source_repository: SourceRepository,
    candidate_path: Path,
    validation_path: Path,
    journal: ReviewJournal,
    gold_root: Path,
    extraction_run_path: Path,
    validation_run_path: Path,
    freeze_lock_path: Path,
    compiled_output_dir: Path,
) -> tuple[Gate0Report, dict[str, Any]]:
    candidates = read_candidate_set(candidate_path)
    candidate_ids = {candidate.candidate_id for candidate in candidates}
    validations = read_validation_set(validation_path)
    validation_by_id = {result.candidate_id: result for result in validations}
    entries = journal.entries()
    human_entries = [entry for entry in entries if entry.reviewer_kind is ReviewerKind.HUMAN]
    latest_human: dict[str, Any] = {}
    for entry in human_entries:
        latest_human[entry.candidate_id] = entry
    gold = read_gold_partitions(gold_root)
    partition_counts = Counter(record.partition.value for record in gold)
    accounting = compile_reviewed_pack(gold, source_repository, compiled_output_dir)
    reproduced_accounting = compile_reviewed_pack(
        gold, source_repository, compiled_output_dir
    )
    deterministic_reproduction = (
        accounting["manifest_hash"] == reproduced_accounting["manifest_hash"]
        and accounting["component_hashes"] == reproduced_accounting["component_hashes"]
        and accounting["actual_serialized_pack_bytes"]
        == reproduced_accounting["actual_serialized_pack_bytes"]
    )

    extraction_run = (
        ExtractionRun.model_validate_json(extraction_run_path.read_text("utf-8"))
        if extraction_run_path.exists()
        else None
    )
    validation_run = (
        ValidationRun.model_validate_json(validation_run_path.read_text("utf-8"))
        if validation_run_path.exists()
        else None
    )
    freeze_lock = (
        json.loads(freeze_lock_path.read_text("utf-8")) if freeze_lock_path.exists() else None
    )

    counts_complete = all(
        partition_counts[partition.value] == target for partition, target in PILOT_TARGETS.items()
    )
    sealed_frozen = bool(
        freeze_lock
        and freeze_lock.get("extractor_configuration_hash")
        == (extraction_run.configuration_hash if extraction_run else None)
        and freeze_lock.get("validator_configuration_hash")
        == (validation_run.configuration_hash if validation_run else None)
        and freeze_lock.get("sealed_evaluation_permitted") is True
    )
    all_candidates_reviewed = bool(candidate_ids) and candidate_ids.issubset(latest_human.keys())
    inventory_status_path = gold_root / "gold_inventory_status.json"
    inventory = (
        json.loads(inventory_status_path.read_text("utf-8"))
        if inventory_status_path.exists()
        else {}
    )
    sealed_inventory_complete = inventory.get("sealed_gate0_complete") is True

    metrics: list[MetricResult] = []
    if not (counts_complete and sealed_frozen and all_candidates_reviewed):
        prerequisite = (
            f"partition counts={dict(partition_counts)}; frozen={sealed_frozen}; "
            f"all candidates human reviewed={all_candidates_reviewed}"
        )
        metrics.extend(
            [
                _blocked("tier1_packet_precision", ">= 0.95", prerequisite),
                _blocked(
                    "source_span_alignment_accuracy",
                    ">= 0.98",
                    prerequisite,
                ),
                _blocked("incorrect_entity_rate", "<= 0.02", prerequisite),
                _blocked("incorrect_relation_rate", "<= 0.03", prerequisite),
                _blocked(
                    "human_corrections_per_1000_tier1",
                    "<= 50",
                    prerequisite,
                ),
                _blocked("duplicate_rate", "<= 0.05", prerequisite),
            ]
        )
    else:
        correct = sum(
            entry.action in {ReviewAction.ACCEPT, ReviewAction.EDIT}
            for entry in latest_human.values()
        )
        precision = correct / len(candidate_ids) if candidate_ids else 0.0
        corrections = sum(
            entry.action
            in {
                ReviewAction.EDIT,
                ReviewAction.REJECT,
                ReviewAction.QUARANTINE,
                ReviewAction.MERGE_DUPLICATE,
            }
            for entry in human_entries
        )
        reason_counts = Counter(entry.reason_code for entry in human_entries if entry.reason_code)
        alignment_checks = [
            check
            for candidate_id in candidate_ids
            if candidate_id in validation_by_id
            for check in validation_by_id[candidate_id].checks
            if check.check_id == "source_alignment"
        ]
        alignment_correct = sum(
            check.status.value == "PASS" for check in alignment_checks
        )
        alignment_accuracy = (
            alignment_correct / len(alignment_checks) if alignment_checks else 0.0
        )
        entity_errors = reason_counts[ReviewReason.ENTITY_FIX]
        relation_errors = reason_counts[ReviewReason.RELATION_FIX]
        duplicate_merges = sum(
            entry.action is ReviewAction.MERGE_DUPLICATE for entry in human_entries
        )
        metrics.extend(
            [
                _metric(
                    "tier1_packet_precision",
                    precision,
                    passed=precision >= 0.95,
                    threshold=">= 0.95",
                    evidence="final human actions over all extractor candidates",
                ),
                _metric(
                    "source_span_alignment_accuracy",
                    alignment_accuracy,
                    passed=alignment_accuracy >= 0.98,
                    threshold=">= 0.98",
                    evidence="independent raw-offset/hash validation",
                ),
                _metric(
                    "incorrect_entity_rate",
                    entity_errors / len(candidate_ids),
                    passed=entity_errors / len(candidate_ids) <= 0.02,
                    threshold="<= 0.02",
                    evidence="human ENTITY_FIX actions",
                ),
                _metric(
                    "incorrect_relation_rate",
                    relation_errors / len(candidate_ids),
                    passed=relation_errors / len(candidate_ids) <= 0.03,
                    threshold="<= 0.03",
                    evidence="human RELATION_FIX actions",
                ),
                _metric(
                    "human_corrections_per_1000_tier1",
                    corrections / len(candidate_ids) * 1000,
                    passed=corrections / len(candidate_ids) * 1000 <= 50,
                    threshold="<= 50",
                    evidence="structured append-only review actions",
                ),
                _metric(
                    "duplicate_rate",
                    duplicate_merges / len(candidate_ids),
                    passed=duplicate_merges / len(candidate_ids) <= 0.05,
                    threshold="<= 0.05",
                    evidence="MERGE_DUPLICATE actions over extractor candidates",
                ),
            ]
        )

    if sealed_inventory_complete and counts_complete and sealed_frozen:
        accepted_fingerprints = {
            (
                record.packet.primary_subject,
                record.packet.primary_relation,
                record.packet.primary_object,
            )
            for record in gold
            if record.partition is GoldPartition.SEALED_GATE0
        }
        extracted_fingerprints = {
            (
                candidate.primary_subject,
                candidate.primary_relation,
                candidate.primary_object,
            )
            for candidate in candidates
        }
        recall = (
            len(accepted_fingerprints & extracted_fingerprints) / len(accepted_fingerprints)
            if accepted_fingerprints
            else 0.0
        )
        metrics.append(
            _metric(
                "tier1_packet_recall",
                recall,
                passed=recall >= 0.80,
                threshold=">= 0.80",
                evidence="sealed human-complete source fact inventory",
            )
        )
    else:
        metrics.append(
            _blocked(
                "tier1_packet_recall",
                ">= 0.80",
                "sealed source fact inventory is not human-certified complete",
            )
        )

    latest_actions = Counter(entry.action.value for entry in latest_human.values())
    correction_categories = Counter(
        entry.reason_code.value
        for entry in human_entries
        if entry.reason_code is not None
    )
    reviewed_denominator = max(1, len(latest_human))
    metrics.extend(
        [
            MetricResult(
                metric_id="human_reviewed_candidate_count",
                status=MetricStatus.INFORMATIONAL,
                value=len(latest_human),
                evidence="latest human journal actions",
            ),
            MetricResult(
                metric_id="quarantine_rate",
                status=MetricStatus.INFORMATIONAL,
                value=latest_actions[ReviewAction.QUARANTINE.value] / reviewed_denominator,
                evidence="latest human journal actions",
            ),
            MetricResult(
                metric_id="rejection_rate",
                status=MetricStatus.INFORMATIONAL,
                value=latest_actions[ReviewAction.REJECT.value] / reviewed_denominator,
                evidence="latest human journal actions",
            ),
            MetricResult(
                metric_id="extraction_wall_clock_ms",
                status=MetricStatus.INFORMATIONAL,
                value=extraction_run.wall_clock_ms if extraction_run else None,
                evidence="measured extraction run report",
            ),
            MetricResult(
                metric_id="validation_wall_clock_ms",
                status=MetricStatus.INFORMATIONAL,
                value=validation_run.wall_clock_ms if validation_run else None,
                evidence="measured independent validation run report",
            ),
            MetricResult(
                metric_id="teacher_tokens",
                status=MetricStatus.INFORMATIONAL,
                value=extraction_run.teacher_tokens if extraction_run else None,
                evidence="rule extractor uses no teacher model",
            ),
            MetricResult(
                metric_id="teacher_cost_usd",
                status=MetricStatus.INFORMATIONAL,
                value=extraction_run.teacher_cost_usd if extraction_run else None,
                evidence="recorded extraction run cost",
            ),
            MetricResult(
                metric_id="actual_serialized_pack_bytes",
                status=MetricStatus.INFORMATIONAL,
                value=accounting["actual_serialized_pack_bytes"],
                evidence="packets, spans, indexes, and manifest on disk",
            ),
            MetricResult(
                metric_id="logical_pack_bytes",
                status=MetricStatus.INFORMATIONAL,
                value=accounting["logical_pack_bytes"],
                evidence="defined logical accounting model",
            ),
            MetricResult(
                metric_id="deterministic_reproducibility",
                status=(
                    MetricStatus.PASS
                    if deterministic_reproduction
                    else MetricStatus.FAIL
                ),
                value=accounting["manifest_hash"],
                threshold="identical manifest, components, and byte count",
                evidence="two consecutive deterministic serializations",
            ),
        ]
    )
    for reason in ReviewReason:
        metrics.append(
            MetricResult(
                metric_id=f"correction_category_{reason.value.casefold()}",
                status=MetricStatus.INFORMATIONAL,
                value=correction_categories[reason.value],
                evidence="append-only human review journal",
            )
        )
    if counts_complete and all_candidates_reviewed:
        metrics.append(
            _metric(
                "logical_compiled_bytes_per_source_byte",
                accounting["logical_compiled_bytes_per_source_byte"],
                passed=accounting["logical_compiled_bytes_per_source_byte"] <= 8.0,
                threshold="<= 8.0",
                evidence="complete reviewed pack serialized component accounting",
            )
        )
    else:
        metrics.append(
            _blocked(
                "logical_compiled_bytes_per_source_byte",
                "<= 8.0",
                "economic ratio requires the complete 500-packet reviewed pack",
            )
        )

    mandatory = [metric for metric in metrics if metric.status is not MetricStatus.INFORMATIONAL]
    blockers = [metric.metric_id for metric in mandatory if metric.status is MetricStatus.BLOCKED]
    if blockers:
        overall_status = MetricStatus.BLOCKED
    elif any(metric.status is MetricStatus.FAIL for metric in mandatory):
        overall_status = MetricStatus.FAIL
    else:
        overall_status = MetricStatus.PASS
    report = Gate0Report(
        generated_at=datetime.now(UTC),
        source_manifest_hash=source_repository.manifest_hash(),
        extractor_configuration_hash=(
            extraction_run.configuration_hash if extraction_run else "BLOCKED"
        ),
        validator_configuration_hash=(
            validation_run.configuration_hash if validation_run else "BLOCKED"
        ),
        review_journal_hash=journal.journal_hash(),
        partition_counts={
            partition.value: partition_counts[partition.value] for partition in GoldPartition
        },
        metrics=tuple(metrics),
        overall_status=overall_status,
        blockers=tuple(blockers),
    )
    return report, accounting


def write_gate0_report(
    report: Gate0Report,
    accounting: dict[str, Any],
    json_path: Path,
    markdown_path: Path,
) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(
            {
                "gate0": report.model_dump(mode="json"),
                "pack_size": accounting,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Gate 0 compiler qualification",
        "",
        f"**Overall status:** {report.overall_status}",
        "",
        "## Partition counts",
        "",
    ]
    for partition, count in report.partition_counts.items():
        lines.append(f"- {partition}: {count}")
    lines.extend(
        [
            "",
            "## Metrics",
            "",
            "| Metric | Status | Value | Threshold |",
            "|---|---:|---:|---:|",
        ]
    )
    for metric in report.metrics:
        lines.append(
            f"| {metric.metric_id} | {metric.status} | "
            f"{metric.value if metric.value is not None else '—'} | "
            f"{metric.threshold or '—'} |"
        )
    if report.blockers:
        lines.extend(["", "## BLOCKED", ""])
        lines.extend(f"- {blocker}" for blocker in report.blockers)
    lines.extend(
        [
            "",
            "## Pack-size breakdown",
            "",
            "| Component | Bytes |",
            "|---|---:|",
            f"| Actual serialized total | {accounting['actual_serialized_pack_bytes']} |",
            f"| Actual packets | {accounting['actual_packet_bytes']} |",
            f"| Actual source spans | {accounting['actual_source_span_bytes']} |",
            f"| Actual indexes | {accounting['actual_index_bytes']} |",
            f"| Actual manifest/fixed overhead | "
            f"{accounting['actual_manifest_fixed_overhead_bytes']} |",
            f"| Logical total | {accounting['logical_pack_bytes']} |",
            f"| Logical headers | {accounting['logical_header_bytes']} |",
            f"| Logical payloads | {accounting['logical_payload_bytes']} |",
            f"| Logical keys | {accounting['logical_key_bytes']} |",
            f"| Logical indexes | {accounting['logical_index_bytes']} |",
            f"| Logical source spans | {accounting['logical_source_span_bytes']} |",
            f"| Logical fixed overhead | {accounting['logical_fixed_overhead_bytes']} |",
            "",
            "The report fails closed: absent human review, incomplete gold fact "
            "inventories, or an unfrozen sealed set cannot be converted into passing metrics.",
            "",
        ]
    )
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
