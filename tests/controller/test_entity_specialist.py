from __future__ import annotations

import math
import sqlite3
from pathlib import Path

import pytest

from aethersparse.controller.entity_specialist import (
    LinearEntityRanker,
    WeightedCandidate,
    classify_entity_residual,
    extract_anchor_statistics,
    fit_linear_entity_ranker,
)
from aethersparse.controller.models import EntityCandidate, EntityMention, ResolutionMethod
from aethersparse.real_corpus.builder import SCHEMA


def _candidate(entity_id: str, confidence: float, *, context: float = 0.0) -> EntityCandidate:
    return EntityCandidate(
        entity_id=entity_id,
        title=entity_id,
        method=ResolutionMethod.ALIAS,
        name_score=0.97,
        type_score=1.0,
        relation_score=1.0,
        context_score=context,
        confidence=confidence,
    )


def _mention(*candidates: EntityCandidate, selected: str | None = None) -> EntityMention:
    return EntityMention(
        surface="Mercury",
        char_start=0,
        char_end=7,
        candidates=candidates,
        selected_entity_id=selected,
        selected_confidence=candidates[0].confidence if candidates else 0.0,
        resolution_method=ResolutionMethod.ALIAS if candidates else ResolutionMethod.UNKNOWN,
        copy_status=(
            "linked" if selected else ("ambiguous" if candidates else "unknown_but_copyable")
        ),
    )


def test_residual_classifier_uses_only_observable_replay_state() -> None:
    wrong = _candidate("entity:wrong", 0.9)
    right = _candidate("entity:right", 0.8)
    assert classify_entity_residual(("entity:right",), ()) == "mention_not_detected"
    assert (
        classify_entity_residual(("entity:right",), (_mention(wrong, selected=wrong.entity_id),))
        == "correct_entity_not_generated"
    )
    assert (
        classify_entity_residual(("entity:right",), (_mention(right),))
        == "correct_entity_top_ranked_but_rejected"
    )
    assert (
        classify_entity_residual(("entity:right",), (_mention(wrong, right),))
        == "correct_entity_present_but_misranked"
    )


def test_linear_ranker_is_deterministic_and_learns_context_signal() -> None:
    positive = _candidate("entity:right", 0.8, context=1.0)
    negative = _candidate("entity:wrong", 0.9, context=0.0)
    observations = (
        WeightedCandidate(positive, True, 1.0),
        WeightedCandidate(negative, False, 1.0),
    )
    first = fit_linear_entity_ranker(observations, epochs=400)
    second = fit_linear_entity_ranker(observations, epochs=400)
    assert first == second
    assert first.probability(positive) > first.probability(negative)
    assert LinearEntityRanker(first.weights).probability(positive) == first.probability(positive)


def _insert_document(
    connection: sqlite3.Connection,
    *,
    document_id: str,
    title: str,
    redirect_target: str | None = None,
) -> None:
    normalized = title.casefold()
    connection.execute(
        "INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            document_id,
            document_id,
            "1",
            title,
            normalized,
            redirect_target,
            f"https://example.test/{document_id}",
            1,
            document_id,
            None,
            None,
            "x",
            "x",
        ),
    )
    connection.execute("INSERT INTO aliases VALUES(?,?,?)", (normalized, document_id, "title"))


def _insert_anchor(
    connection: sqlite3.Connection,
    *,
    anchor_id: str,
    source_document_id: str,
    target_title: str,
    anchor_text: str,
    offset: int,
) -> None:
    connection.execute(
        "INSERT INTO anchors VALUES(?,?,?,?,?,?,?,?)",
        (
            anchor_id,
            source_document_id,
            target_title,
            anchor_text,
            offset,
            offset + 1,
            "x",
            anchor_id,
        ),
    )


def test_anchor_statistics_preserve_occurrence_and_document_counts(tmp_path: Path) -> None:
    pack = tmp_path / "pack.sqlite"
    connection = sqlite3.connect(pack)
    connection.executescript(SCHEMA)
    _insert_document(connection, document_id="source:a", title="Source A")
    _insert_document(connection, document_id="source:b", title="Source B")
    _insert_document(connection, document_id="planet", title="Mercury")
    _insert_document(connection, document_id="element", title="Mercury (element)")
    _insert_document(
        connection,
        document_id="redirect",
        title="Quick silver",
        redirect_target="Mercury (element)",
    )
    connection.execute(
        "INSERT INTO redirects VALUES(?,?,?)", ("redirect", "Mercury (element)", "h")
    )
    _insert_anchor(
        connection,
        anchor_id="anchor:1",
        source_document_id="source:a",
        target_title="Mercury",
        anchor_text="mercury",
        offset=0,
    )
    _insert_anchor(
        connection,
        anchor_id="anchor:2",
        source_document_id="source:a",
        target_title="Mercury",
        anchor_text="mercury",
        offset=2,
    )
    _insert_anchor(
        connection,
        anchor_id="anchor:3",
        source_document_id="source:b",
        target_title="Mercury (element)",
        anchor_text="mercury",
        offset=0,
    )
    connection.commit()
    connection.close()

    stats = extract_anchor_statistics(pack, alpha=1.0)
    assert len(stats) == 2
    planet = next(item for item in stats if item.target_title == "mercury")
    element = next(item for item in stats if item.target_title == "mercury (element)")
    assert planet.occurrence_count == 2
    assert planet.source_document_count == 1
    assert planet.probability == pytest.approx(3 / 5)
    assert element.probability == pytest.approx(2 / 5)
    assert planet.ambiguity_count == 2
    assert planet.title_indicator
    expected_entropy = -(3 / 5) * math.log(3 / 5) - (2 / 5) * math.log(2 / 5)
    assert planet.entropy_nats == pytest.approx(expected_entropy)
