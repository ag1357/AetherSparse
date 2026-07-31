"""Review API router; all Android UI actions still cross the accessory boundary."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from aethersparse.gate0.models import ReviewRequest
from aethersparse.gate0.review import ReviewDataStore, state_for
from aethersparse.gate0.validator import read_validation_set

ROOT = Path(__file__).resolve().parents[3]


def default_store() -> ReviewDataStore:
    data_root = Path(os.environ.get("AETHERSPARSE_GATE0_DATA", ROOT / "data" / "gate0"))
    return ReviewDataStore(
        candidate_path=data_root / "candidates" / "candidates.jsonl",
        validation_path=data_root / "validation" / "validation_results.jsonl",
        source_root=data_root / "sources" / "snapshots",
        journal_path=data_root / "review" / "review_journal.jsonl",
    )


def create_review_router(store: ReviewDataStore | None = None) -> APIRouter:
    review_store = store or default_store()
    router = APIRouter(prefix="/v1/review", tags=["gate0-review"])

    @router.get("/status")
    def status() -> dict[str, object]:
        candidates = review_store.candidates()
        latest = review_store.journal.latest_by_candidate()
        state_counts: dict[str, int] = {}
        for candidate in candidates:
            state = state_for(latest.get(candidate.candidate_id)).value
            state_counts[state] = state_counts.get(state, 0) + 1
        return {
            "candidate_count": len(candidates),
            "state_counts": state_counts,
            "journal_entries": len(review_store.journal.entries()),
            "journal_hash": review_store.journal.journal_hash(),
            "human_reviewed_count": sum(
                entry.reviewer_kind.value == "human" for entry in latest.values()
            ),
        }

    @router.get("/candidates")
    def candidates(
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=25, ge=1, le=100),
        state: str | None = None,
    ) -> dict[str, object]:
        all_candidates = review_store.candidates()
        latest = review_store.journal.latest_by_candidate()
        filtered = [
            candidate
            for candidate in all_candidates
            if state is None or state_for(latest.get(candidate.candidate_id)).value == state
        ]
        validations = {
            item.candidate_id: item for item in read_validation_set(review_store.validation_path)
        }
        items = []
        for candidate in filtered[offset : offset + limit]:
            validation = validations.get(candidate.candidate_id)
            items.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "packet_type": candidate.packet_type,
                    "source_doc_id": candidate.source_doc_id,
                    "primary_relation": candidate.primary_relation,
                    "primary_object": candidate.primary_object,
                    "extractor_confidence": candidate.extractor_confidence,
                    "validator_decision": validation.decision if validation else None,
                    "state": state_for(latest.get(candidate.candidate_id)),
                }
            )
        return {
            "total": len(filtered),
            "offset": offset,
            "limit": limit,
            "items": items,
        }

    @router.get("/candidates/{candidate_id:path}")
    def candidate(candidate_id: str) -> dict[str, object]:
        try:
            return review_store.view(candidate_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="candidate not found") from error

    @router.post("/actions")
    def action(request: ReviewRequest) -> dict[str, object]:
        try:
            entry = review_store.append(request)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="candidate not found") from error
        return {
            "entry": entry.model_dump(mode="json"),
            "journal_hash": review_store.journal.journal_hash(),
        }

    @router.get("/journal/verify")
    def verify_journal() -> dict[str, object]:
        entries = review_store.journal.entries()
        return {
            "valid": True,
            "entry_count": len(entries),
            "journal_hash": review_store.journal.verify(entries),
        }

    return router
