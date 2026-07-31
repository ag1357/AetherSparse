from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from aethersparse.gate0.extractor import (
    RuleCandidateExtractor,
    write_candidate_set,
)
from aethersparse.gate0.models import (
    GoldPartition,
    ReviewAction,
    ReviewerKind,
    ReviewReason,
    ReviewRequest,
)
from aethersparse.gate0.review import (
    ReviewDataStore,
    ReviewJournalError,
    materialize_human_gold,
)
from aethersparse.gate0.review_service import create_review_router
from aethersparse.gate0.sources import SourceRepository, freeze_source
from aethersparse.gate0.validator import IndependentValidator, write_validation_set
from aethersparse.service import create_app


def build_store(tmp_path: Path) -> tuple[ReviewDataStore, str]:
    source_repository = SourceRepository(tmp_path / "sources")
    source = freeze_source(
        source_doc_id="src_review_fixture",
        title="Review fixture",
        source_url="https://example.invalid/review",
        source_revision="v1",
        license="public_domain",
        source_group="fixture",
        raw_text="Apollo 15 launched on July 26, 1971.",
    )
    source_repository.add(source)
    candidates = RuleCandidateExtractor().extract_snapshot(source)
    candidate_path = tmp_path / "candidates.jsonl"
    write_candidate_set(candidates, candidate_path)
    validations = IndependentValidator().validate_all(candidates, source_repository)
    validation_path = tmp_path / "validation.jsonl"
    write_validation_set(validations, validation_path)
    return (
        ReviewDataStore(
            candidate_path=candidate_path,
            validation_path=validation_path,
            source_root=tmp_path / "sources",
            journal_path=tmp_path / "review.jsonl",
        ),
        candidates[0].candidate_id,
    )


def test_edit_and_rejection_require_structured_reason() -> None:
    with pytest.raises(ValidationError, match="reason_code"):
        ReviewRequest(
            candidate_id="candidate",
            action=ReviewAction.REJECT,
            reviewer_id="reviewer",
            reviewer_kind=ReviewerKind.HUMAN,
        )


def test_append_only_journal_hash_chain_detects_tampering(tmp_path: Path) -> None:
    store, candidate_id = build_store(tmp_path)
    entry = store.append(
        ReviewRequest(
            candidate_id=candidate_id,
            action=ReviewAction.ACCEPT,
            reviewer_id="human-1",
            reviewer_kind=ReviewerKind.HUMAN,
        )
    )
    assert entry.sequence == 1
    assert store.journal.journal_hash() == entry.entry_hash

    payload = json.loads((tmp_path / "review.jsonl").read_text(encoding="utf-8"))
    payload["reviewer_id"] = "tampered"
    (tmp_path / "review.jsonl").write_text(json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(ReviewJournalError, match="hash"):
        store.journal.entries()


def test_only_human_accept_or_edit_materializes_as_gold(tmp_path: Path) -> None:
    store, candidate_id = build_store(tmp_path)
    store.append(
        ReviewRequest(
            candidate_id=candidate_id,
            action=ReviewAction.ACCEPT,
            reviewer_id="agent-pre-review",
            reviewer_kind=ReviewerKind.AGENT,
        )
    )
    plan = {candidate_id: GoldPartition.CALIBRATION}
    assert (
        materialize_human_gold(
            candidate_path=store.candidate_path,
            journal=store.journal,
            partition_plan=plan,
        )
        == ()
    )

    store.append(
        ReviewRequest(
            candidate_id=candidate_id,
            action=ReviewAction.ACCEPT,
            reviewer_id="human-1",
            reviewer_kind=ReviewerKind.HUMAN,
        )
    )
    records = materialize_human_gold(
        candidate_path=store.candidate_path,
        journal=store.journal,
        partition_plan=plan,
    )
    assert len(records) == 1
    assert records[0].reviewer_kind is ReviewerKind.HUMAN


def test_review_api_returns_all_evidence_and_appends_action(tmp_path: Path) -> None:
    store, candidate_id = build_store(tmp_path)
    app = FastAPI()
    app.include_router(create_review_router(store))
    client = TestClient(app)

    view = client.get(f"/v1/review/candidates/{candidate_id}").json()
    assert view["source"]["raw_text"]
    assert view["candidate"]["atomic_claims"]
    assert view["validator"]["checks"]

    response = client.post(
        "/v1/review/actions",
        json={
            "candidate_id": candidate_id,
            "action": "QUARANTINE",
            "reviewer_id": "human-android",
            "reviewer_kind": "human",
            "reason_code": "UNSUPPORTED",
            "reason_detail": "Needs a clearer source span.",
        },
    )
    assert response.status_code == 200
    assert response.json()["entry"]["action"] == "QUARANTINE"
    assert client.get("/v1/review/journal/verify").json()["valid"] is True


def test_merge_requires_existing_target(tmp_path: Path) -> None:
    store, candidate_id = build_store(tmp_path)
    with pytest.raises(KeyError):
        store.append(
            ReviewRequest(
                candidate_id=candidate_id,
                action=ReviewAction.MERGE_DUPLICATE,
                reviewer_id="human-1",
                reviewer_kind=ReviewerKind.HUMAN,
                reason_code=ReviewReason.DUPLICATE,
                merge_target_candidate_id="missing",
            )
        )


def test_android_review_ui_is_transport_only() -> None:
    ui = Path("web/review_ui/index.html").read_text(encoding="utf-8")
    forbidden = (
        "import aethersparse",
        "from aethersparse",
        "RuleCandidateExtractor",
        "IndependentValidator",
        "KnowledgeStore",
    )
    assert "/v1/review/" in ui
    assert not any(item in ui for item in forbidden)


def test_android_review_ui_is_served_by_external_service() -> None:
    response = TestClient(create_app()).get("/review")

    assert response.status_code == 200
    assert "/v1/review/actions" in response.text
    assert "AetherSparse Gate 0 review" in response.text
