from __future__ import annotations

import json
from pathlib import Path

from aethersparse.gate0.query_authoring import build_pending_query_set
from aethersparse.gate0.query_eval import evaluate_sealed_queries


def test_pending_query_set_is_independent_but_not_falsely_reviewed(
    tmp_path: Path,
) -> None:
    query_path = tmp_path / "sealed_queries.candidates.json"
    query_set = build_pending_query_set(query_path)

    assert len(query_set.cases) == 50
    assert all(case.author_identity != "aethersparse-rule-extractor" for case in query_set.cases)
    assert all(case.reviewer_id is None for case in query_set.cases)


def test_query_evidence_ids_belong_to_the_frozen_source_seed(tmp_path: Path) -> None:
    query_set = build_pending_query_set(
        tmp_path / "sealed_queries.candidates.json"
    )
    seed = json.loads(
        Path("data/gate0/source_seed.json").read_text(encoding="utf-8")
    )
    source_ids = {source["source_doc_id"] for source in seed["sources"]}

    assert all(
        evidence_id in source_ids
        for case in query_set.cases
        for evidence_id in case.evidence_source_ids
    )


def test_sealed_evaluation_fails_closed_before_human_review(tmp_path: Path) -> None:
    query_path = tmp_path / "sealed_queries.candidates.json"
    build_pending_query_set(query_path)
    freeze_path = tmp_path / "freeze.json"
    freeze_path.write_text(
        json.dumps({"sealed_evaluation_permitted": False}),
        encoding="utf-8",
    )

    report = evaluate_sealed_queries(
        query_path=query_path,
        freeze_lock_path=freeze_path,
        gold_root=tmp_path / "gold",
    )

    assert report["status"] == "BLOCKED"
    assert report["top1_matched_baseline"] is None
    assert report["compiled_program"] is None
    assert report["evidence_arbitration"]["status"] == "DISABLED"
    assert any("human review" in blocker for blocker in report["blockers"])
