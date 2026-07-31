"""Reproducible orchestration for Gate 0 qualification artifacts."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from aethersparse.gate0.extractor import (
    RuleCandidateExtractor,
    extract_repository,
    read_candidate_set,
)
from aethersparse.gate0.metrics import (
    evaluate_gate0,
    read_gold_partitions,
    write_gate0_report,
)
from aethersparse.gate0.models import GoldPartition
from aethersparse.gate0.query_authoring import build_pending_query_set
from aethersparse.gate0.query_eval import evaluate_sealed_queries, write_query_report
from aethersparse.gate0.review import (
    ReviewJournal,
    build_partition_plan,
    materialize_human_gold,
    write_gold_partitions,
    write_partition_plan,
)
from aethersparse.gate0.sources import SourceRepository, freeze_source, stable_json
from aethersparse.gate0.validator import (
    IndependentValidator,
    validate_repository,
)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA_ROOT = ROOT / "data" / "gate0"
DEFAULT_REPORT_ROOT = ROOT / "reports" / "gate0"


def paths(data_root: Path = DEFAULT_DATA_ROOT) -> dict[str, Path]:
    return {
        "source_seed": data_root / "source_seed.json",
        "source_root": data_root / "sources" / "snapshots",
        "source_manifest": data_root / "sources" / "frozen_manifest.json",
        "candidates": data_root / "candidates" / "candidates.jsonl",
        "extraction_run": data_root / "candidates" / "extraction_run.json",
        "validation": data_root / "validation" / "validation_results.jsonl",
        "validation_run": data_root / "validation" / "validation_run.json",
        "journal": data_root / "review" / "review_journal.jsonl",
        "partition_plan": data_root / "gold" / "partition_plan.json",
        "gold_root": data_root / "gold",
        "freeze_lock": data_root / "freeze" / "gate0_rules.lock.json",
        "sealed_queries": data_root / "eval" / "sealed_queries.candidates.json",
        "compiled": data_root / "compiled",
    }


def ingest_source_seed(
    seed_path: Path,
    data_root: Path = DEFAULT_DATA_ROOT,
) -> tuple[SourceRepository, dict[str, Any]]:
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    retrieved_at = datetime.fromisoformat(seed["retrieved_at"].replace("Z", "+00:00"))
    repository = SourceRepository(paths(data_root)["source_root"])
    for raw_source in seed["sources"]:
        snapshot = freeze_source(
            source_doc_id=raw_source["source_doc_id"],
            title=raw_source["title"],
            source_url=raw_source["source_url"],
            source_revision=raw_source["source_revision"],
            license=raw_source["license"],
            source_group=raw_source["source_group"],
            raw_text=raw_source["raw_text"],
            retrieved_at=retrieved_at,
        )
        repository.add(snapshot)
    snapshots = repository.list()
    manifest = {
        "manifest_id": seed["manifest_id"],
        "scope_warning": seed["scope_warning"],
        "source_count": len(snapshots),
        "source_manifest_hash": repository.manifest_hash(),
        "sources": [
            {
                "source_doc_id": snapshot.source_doc_id,
                "title": snapshot.title,
                "source_url": snapshot.source_url,
                "source_revision": snapshot.source_revision,
                "retrieved_at": snapshot.retrieved_at.isoformat(),
                "license": snapshot.license,
                "source_group": snapshot.source_group,
                "raw_content_hash": snapshot.raw_content_hash,
                "raw_byte_length": snapshot.raw_byte_length,
                "normalized_content_hash": snapshot.normalized_content_hash,
                "normalized_byte_length": len(snapshot.normalized_text.encode("utf-8")),
            }
            for snapshot in snapshots
        ],
    }
    manifest_path = paths(data_root)["source_manifest"]
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return repository, manifest


def _load_partition_plan(path: Path) -> dict[str, GoldPartition]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        candidate_id: GoldPartition(partition)
        for candidate_id, partition in payload["assignments"].items()
    }


def build_candidate_and_validation_sets(
    data_root: Path = DEFAULT_DATA_ROOT,
) -> dict[str, Any]:
    item_paths = paths(data_root)
    repository = SourceRepository(item_paths["source_root"])
    candidates, extraction_run = extract_repository(
        repository,
        item_paths["candidates"],
        item_paths["extraction_run"],
    )
    _validations, validation_run = validate_repository(
        candidates,
        repository,
        item_paths["validation"],
        item_paths["validation_run"],
    )
    plan = build_partition_plan(tuple(candidate.candidate_id for candidate in candidates))
    existing_plan = _load_partition_plan(item_paths["partition_plan"])
    if existing_plan and existing_plan != plan:
        raise ValueError(
            "candidate partition plan is already frozen and would change; "
            "create a new Gate 0 corpus version"
        )
    if not existing_plan:
        write_partition_plan(plan, item_paths["partition_plan"])
    item_paths["journal"].parent.mkdir(parents=True, exist_ok=True)
    item_paths["journal"].touch(exist_ok=True)
    item_paths["gold_root"].mkdir(parents=True, exist_ok=True)
    for partition in GoldPartition:
        partition_path = item_paths["gold_root"] / f"{partition.value}.jsonl"
        partition_path.touch(exist_ok=True)
    return {
        "source_count": len(repository.list()),
        "candidate_count": len(candidates),
        "validator_pass": validation_run.pass_count,
        "validator_review": validation_run.review_count,
        "validator_fail": validation_run.fail_count,
        "extraction_run_id": extraction_run.run_id,
        "validation_run_id": validation_run.run_id,
        "partition_assignments": {
            partition.value: sum(value is partition for value in plan.values())
            for partition in GoldPartition
        },
    }


def materialize_reviewed_gold(data_root: Path = DEFAULT_DATA_ROOT) -> dict[str, int]:
    item_paths = paths(data_root)
    plan = _load_partition_plan(item_paths["partition_plan"])
    journal = ReviewJournal(item_paths["journal"])
    records = materialize_human_gold(
        candidate_path=item_paths["candidates"],
        journal=journal,
        partition_plan=plan,
    )
    write_gold_partitions(records, item_paths["gold_root"])
    return {
        partition.value: sum(record.partition is partition for record in records)
        for partition in GoldPartition
    }


def freeze_rules(
    data_root: Path = DEFAULT_DATA_ROOT,
    *,
    sealed_evaluation_permitted: bool = False,
) -> dict[str, Any]:
    item_paths = paths(data_root)
    candidates = read_candidate_set(item_paths["candidates"])
    candidate_set_hash = (
        f"sha256:{hashlib.sha256(item_paths['candidates'].read_bytes()).hexdigest()}"
        if item_paths["candidates"].exists()
        else None
    )
    existing_lock = (
        json.loads(item_paths["freeze_lock"].read_text(encoding="utf-8"))
        if item_paths["freeze_lock"].exists()
        else None
    )
    if sealed_evaluation_permitted:
        if existing_lock is None:
            raise ValueError("create the initial rules lock before permitting sealed evaluation")
        immutable_fields = {
            "extractor_configuration_hash": RuleCandidateExtractor.configuration_hash,
            "validator_configuration_hash": IndependentValidator.configuration_hash,
            "candidate_set_hash": candidate_set_hash,
            "candidate_count": len(candidates),
        }
        if any(existing_lock.get(key) != value for key, value in immutable_fields.items()):
            raise ValueError(
                "extractor, validator, or candidate corpus changed after the rules lock"
            )
        materialize_reviewed_gold(data_root)
        reviewed = Counter(
            record.partition.value
            for record in read_gold_partitions(item_paths["gold_root"])
        )
        if (
            reviewed[GoldPartition.CALIBRATION.value] != 100
            or reviewed[GoldPartition.DEVELOPMENT.value] != 250
        ):
            raise ValueError(
                "sealed evaluation requires 100 calibration and 250 "
                "compiler-development human-reviewed packets"
            )
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "UNAVAILABLE"
    lock = {
        "lock_version": "1",
        "git_commit": commit,
        "extractor_configuration_hash": RuleCandidateExtractor.configuration_hash,
        "validator_configuration_hash": IndependentValidator.configuration_hash,
        "candidate_set_hash": candidate_set_hash,
        "candidate_count": len(candidates),
        "sealed_evaluation_permitted": sealed_evaluation_permitted,
        "policy": (
            "Set sealed_evaluation_permitted only after extractor and validator "
            "rules are frozen and calibration/development review is complete."
        ),
    }
    lock["lock_hash"] = f"sha256:{hashlib.sha256(stable_json(lock)).hexdigest()}"
    item_paths["freeze_lock"].parent.mkdir(parents=True, exist_ok=True)
    item_paths["freeze_lock"].write_text(
        json.dumps(lock, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return lock


def generate_gate0_report(
    data_root: Path = DEFAULT_DATA_ROOT,
    report_root: Path = DEFAULT_REPORT_ROOT,
) -> dict[str, Any]:
    item_paths = paths(data_root)
    repository = SourceRepository(item_paths["source_root"])
    report, accounting = evaluate_gate0(
        source_repository=repository,
        candidate_path=item_paths["candidates"],
        validation_path=item_paths["validation"],
        journal=ReviewJournal(item_paths["journal"]),
        gold_root=item_paths["gold_root"],
        extraction_run_path=item_paths["extraction_run"],
        validation_run_path=item_paths["validation_run"],
        freeze_lock_path=item_paths["freeze_lock"],
        compiled_output_dir=item_paths["compiled"],
    )
    write_gate0_report(
        report,
        accounting,
        report_root / "GATE0_REPORT.json",
        report_root / "GATE0_REPORT.md",
    )
    return {
        "overall_status": report.overall_status,
        "blockers": report.blockers,
        "partition_counts": report.partition_counts,
        "pack_size": accounting,
    }


def build_query_candidates(data_root: Path = DEFAULT_DATA_ROOT) -> dict[str, Any]:
    query_set = build_pending_query_set(paths(data_root)["sealed_queries"])
    return {
        "query_set_id": query_set.query_set_id,
        "candidate_question_count": len(query_set.cases),
        "human_reviewed_question_count": 0,
    }


def generate_sealed_query_report(
    data_root: Path = DEFAULT_DATA_ROOT,
    report_root: Path = DEFAULT_REPORT_ROOT,
) -> dict[str, Any]:
    item_paths = paths(data_root)
    report = evaluate_sealed_queries(
        query_path=item_paths["sealed_queries"],
        freeze_lock_path=item_paths["freeze_lock"],
        gold_root=item_paths["gold_root"],
    )
    write_query_report(
        report,
        report_root / "SEALED_QUERY_REPORT.json",
        report_root / "SEALED_QUERY_REPORT.md",
    )
    return report


def bootstrap_gate0(
    seed_path: Path,
    data_root: Path = DEFAULT_DATA_ROOT,
    report_root: Path = DEFAULT_REPORT_ROOT,
) -> dict[str, Any]:
    _repository, manifest = ingest_source_seed(seed_path, data_root)
    build = build_candidate_and_validation_sets(data_root)
    lock = freeze_rules(data_root, sealed_evaluation_permitted=False)
    materialized = materialize_reviewed_gold(data_root)
    query_candidates = build_query_candidates(data_root)
    report = generate_gate0_report(data_root, report_root)
    query_report = generate_sealed_query_report(data_root, report_root)
    return {
        "source_manifest": {
            "source_count": manifest["source_count"],
            "source_manifest_hash": manifest["source_manifest_hash"],
        },
        "build": build,
        "freeze": lock,
        "human_gold": materialized,
        "query_candidates": query_candidates,
        "report": report,
        "query_report": query_report,
    }
