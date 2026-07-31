"""Append-only human review journal and reviewed-gold materialization."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path

from aethersparse.gate0.extractor import read_candidate_set
from aethersparse.gate0.models import (
    CandidatePacket,
    CandidateState,
    GoldPartition,
    ReviewAction,
    ReviewedGoldRecord,
    ReviewerKind,
    ReviewJournalEntry,
    ReviewRequest,
    utc_now,
)
from aethersparse.gate0.sources import stable_json
from aethersparse.gate0.validator import read_validation_set

GENESIS_HASH = "sha256:" + "0" * 64


class ReviewJournalError(ValueError):
    """Raised when append-only journal integrity or action invariants fail."""


def _entry_hash(unsigned: dict[str, object]) -> str:
    return f"sha256:{hashlib.sha256(stable_json(unsigned)).hexdigest()}"


class ReviewJournal:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def entries(self) -> tuple[ReviewJournalEntry, ...]:
        if not self.path.exists():
            return ()
        entries = tuple(
            ReviewJournalEntry.model_validate_json(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
        self.verify(entries)
        return entries

    @staticmethod
    def verify(entries: tuple[ReviewJournalEntry, ...]) -> str:
        previous_hash = GENESIS_HASH
        for expected_sequence, entry in enumerate(entries, start=1):
            if entry.sequence != expected_sequence:
                raise ReviewJournalError("journal sequence is not contiguous")
            if entry.previous_entry_hash != previous_hash:
                raise ReviewJournalError("journal hash chain is broken")
            unsigned = entry.model_dump(mode="json", exclude={"entry_hash"})
            if _entry_hash(unsigned) != entry.entry_hash:
                raise ReviewJournalError("journal entry hash does not reproduce")
            previous_hash = entry.entry_hash
        return previous_hash

    def append(self, request: ReviewRequest) -> ReviewJournalEntry:
        self.path.touch(exist_ok=True)
        with self.path.open("a+", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                stream.seek(0)
                entries = tuple(
                    ReviewJournalEntry.model_validate_json(line)
                    for line in stream.read().splitlines()
                    if line.strip()
                )
                previous_hash = self.verify(entries)
                unsigned: dict[str, object] = {
                    "sequence": len(entries) + 1,
                    "occurred_at": utc_now().isoformat(),
                    "candidate_id": request.candidate_id,
                    "action": request.action,
                    "reviewer_id": request.reviewer_id,
                    "reviewer_kind": request.reviewer_kind,
                    "reason_code": request.reason_code,
                    "reason_detail": request.reason_detail,
                    "edited_candidate": (
                        request.edited_candidate.model_dump(mode="json")
                        if request.edited_candidate
                        else None
                    ),
                    "merge_target_candidate_id": request.merge_target_candidate_id,
                    "previous_entry_hash": previous_hash,
                }
                canonical_entry = ReviewJournalEntry.model_validate(
                    {**unsigned, "entry_hash": GENESIS_HASH}
                )
                canonical_unsigned = canonical_entry.model_dump(mode="json", exclude={"entry_hash"})
                entry = canonical_entry.model_copy(
                    update={"entry_hash": _entry_hash(canonical_unsigned)}
                )
                stream.seek(0, os.SEEK_END)
                stream.write(
                    json.dumps(entry.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
                    + "\n"
                )
                stream.flush()
                os.fsync(stream.fileno())
                return entry
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def latest_by_candidate(self) -> dict[str, ReviewJournalEntry]:
        latest: dict[str, ReviewJournalEntry] = {}
        for entry in self.entries():
            latest[entry.candidate_id] = entry
        return latest

    def journal_hash(self) -> str:
        return self.verify(self.entries())


def state_for(entry: ReviewJournalEntry | None) -> CandidateState:
    if entry is None:
        return CandidateState.CANDIDATE
    return {
        ReviewAction.ACCEPT: CandidateState.ACCEPTED,
        ReviewAction.EDIT: CandidateState.EDITED,
        ReviewAction.QUARANTINE: CandidateState.QUARANTINED,
        ReviewAction.REJECT: CandidateState.REJECTED,
        ReviewAction.MERGE_DUPLICATE: CandidateState.MERGED_DUPLICATE,
    }[entry.action]


def build_partition_plan(
    candidate_ids: tuple[str, ...],
    *,
    calibration_count: int = 100,
    development_count: int = 250,
    sealed_count: int = 150,
) -> dict[str, GoldPartition]:
    ordered = sorted(
        candidate_ids,
        key=lambda candidate_id: hashlib.sha256(candidate_id.encode()).hexdigest(),
    )
    plan: dict[str, GoldPartition] = {}
    cursor = 0
    for candidate_id in ordered[cursor : cursor + calibration_count]:
        plan[candidate_id] = GoldPartition.CALIBRATION
    cursor += calibration_count
    for candidate_id in ordered[cursor : cursor + development_count]:
        plan[candidate_id] = GoldPartition.DEVELOPMENT
    cursor += development_count
    for candidate_id in ordered[cursor : cursor + sealed_count]:
        plan[candidate_id] = GoldPartition.SEALED_GATE0
    return plan


def write_partition_plan(
    plan: dict[str, GoldPartition],
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "policy": {
            "calibration_target": 100,
            "compiler_development_target": 250,
            "sealed_gate0_target": 150,
            "assignment": "sha256(candidate_id) sort",
        },
        "assignments": {
            candidate_id: partition.value for candidate_id, partition in sorted(plan.items())
        },
    }
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def materialize_human_gold(
    *,
    candidate_path: Path,
    journal: ReviewJournal,
    partition_plan: dict[str, GoldPartition],
) -> tuple[ReviewedGoldRecord, ...]:
    candidates = {item.candidate_id: item for item in read_candidate_set(candidate_path)}
    latest = journal.latest_by_candidate()
    records: list[ReviewedGoldRecord] = []
    for candidate_id, partition in partition_plan.items():
        entry = latest.get(candidate_id)
        if entry is None or entry.reviewer_kind is not ReviewerKind.HUMAN:
            continue
        if entry.action not in {ReviewAction.ACCEPT, ReviewAction.EDIT}:
            continue
        packet: CandidatePacket
        if entry.action is ReviewAction.EDIT:
            if entry.edited_candidate is None:
                raise ReviewJournalError("EDIT entry lost its edited candidate")
            packet = entry.edited_candidate
        else:
            packet = candidates[candidate_id]
        records.append(
            ReviewedGoldRecord(
                candidate_id=candidate_id,
                partition=partition,
                review_entry_hash=entry.entry_hash,
                reviewer_id=entry.reviewer_id,
                reviewer_kind=entry.reviewer_kind,
                packet=packet,
            )
        )
    return tuple(sorted(records, key=lambda item: (item.partition.value, item.candidate_id)))


def write_gold_partitions(
    records: tuple[ReviewedGoldRecord, ...],
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for partition in GoldPartition:
        selected = [record for record in records if record.partition is partition]
        path = output_dir / f"{partition.value}.jsonl"
        rendered = "\n".join(
            json.dumps(record.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
            for record in selected
        )
        path.write_text(rendered + ("\n" if rendered else ""), encoding="utf-8")


class ReviewDataStore:
    """Join source, candidate, validator, and append-only review state for the API."""

    def __init__(
        self,
        *,
        candidate_path: Path,
        validation_path: Path,
        source_root: Path,
        journal_path: Path,
    ) -> None:
        from aethersparse.gate0.sources import SourceRepository

        self.candidate_path = candidate_path
        self.validation_path = validation_path
        self.sources = SourceRepository(source_root)
        self.journal = ReviewJournal(journal_path)

    def candidates(self) -> tuple[CandidatePacket, ...]:
        return read_candidate_set(self.candidate_path)

    def get_candidate(self, candidate_id: str) -> CandidatePacket:
        for candidate in self.candidates():
            if candidate.candidate_id == candidate_id:
                return candidate
        raise KeyError(candidate_id)

    def view(self, candidate_id: str) -> dict[str, object]:
        candidate = self.get_candidate(candidate_id)
        validations = {
            item.candidate_id: item for item in read_validation_set(self.validation_path)
        }
        latest = self.journal.latest_by_candidate().get(candidate_id)
        snapshot = self.sources.get(candidate.source_doc_id)
        return {
            "candidate": candidate.model_dump(mode="json"),
            "validator": (
                validations[candidate_id].model_dump(mode="json")
                if candidate_id in validations
                else None
            ),
            "source": snapshot.model_dump(mode="json"),
            "review": latest.model_dump(mode="json") if latest else None,
            "state": state_for(latest).value,
        }

    def append(self, request: ReviewRequest) -> ReviewJournalEntry:
        self.get_candidate(request.candidate_id)
        if request.merge_target_candidate_id:
            self.get_candidate(request.merge_target_candidate_id)
        return self.journal.append(request)
