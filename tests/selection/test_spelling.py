"""Unit tests for the bounded edit-distance <=2 spelling index (Lane C)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from aethersparse.selection.spelling import (
    EditDistanceIndex,
    _deletions,
    build_sidecar,
    levenshtein_leq,
)


def test_levenshtein_boundaries() -> None:
    assert levenshtein_leq("cat", "cat") == 0
    assert levenshtein_leq("cat", "cut") == 1
    assert levenshtein_leq("cat", "coat") == 1  # single insertion
    assert levenshtein_leq("cat", "coax") == 2  # insertion + substitution
    assert levenshtein_leq("cat", "coatt") == 2  # insert + insert
    assert levenshtein_leq("teh", "the") == 1  # OSA: adjacent transposition = 1
    assert levenshtein_leq("tehnic", "ethnic") == 1
    assert levenshtein_leq("rgeen", "green") == 1
    assert levenshtein_leq("cat", "dog") is None
    assert levenshtein_leq("recieve", "receive") == 1  # OSA transposition
    assert levenshtein_leq("adress", "address") == 1
    assert levenshtein_leq("schwarzeneger", "schwarzenegger") == 1


def test_deletions() -> None:
    assert _deletions("ab", 2) == {"ab", "a", "b"}
    assert "ct" in _deletions("cat", 2)
    assert "at" in _deletions("cat", 2)
    assert "" not in _deletions("ab", 2)


def _toy_pack(path: Path) -> None:
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE chunks (normalized_text TEXT)")
    db.execute("CREATE TABLE documents (title TEXT)")
    for _ in range(5):
        db.execute(
            "INSERT INTO chunks VALUES (?)",
            ("Paris is the capital of France. Napoleon died in 1821 on Helena.",),
        )
        db.execute(
            "INSERT INTO chunks VALUES (?)",
            ("London is the capital of England. The Thames flows through it.",),
        )
    db.execute("INSERT INTO documents VALUES ('Paris')")
    db.execute("INSERT INTO documents VALUES ('London')")
    db.commit()
    db.close()


def test_build_and_lookup(tmp_path: Path) -> None:
    pack = tmp_path / "toy-p3.sqlite"
    _toy_pack(pack)
    sidecar = tmp_path / "toy-p3.ed2.sqlite"
    stats = build_sidecar(pack, sidecar, min_freq=1, min_len=3, vocab_cap=1000)
    assert stats["vocab_size"] > 0

    index = EditDistanceIndex.maybe_open(pack)
    assert index is not None
    assert "paris" in index
    # Exact member: no corrections.
    assert index.corrections("paris") == []
    # Distance-1 substitution.
    assert index.corrections("parix") == [("paris", 1)]
    # Distance-2 (transposition counts as 2).
    assert index.corrections("paisr") == [("paris", 2)]
    # Beyond distance 2: no hit.
    assert index.corrections("paxxxis") == []
    # Frequency ordering: 'capital' appears more often than 'napoleon'.
    assert index.corrections("capitol")[0][0] == "capital"


def test_maybe_open_missing_sidecar(tmp_path: Path) -> None:
    pack = tmp_path / "toy-p3.sqlite"
    _toy_pack(pack)
    assert EditDistanceIndex.maybe_open(pack) is None


def test_lookup_is_deterministic(tmp_path: Path) -> None:
    pack = tmp_path / "toy-p3.sqlite"
    _toy_pack(pack)
    build_sidecar(pack, tmp_path / "toy-p3.ed2.sqlite", min_freq=1, min_len=3)
    index = EditDistanceIndex.maybe_open(pack)
    assert index is not None
    first = index.corrections("franse")
    second = index.corrections("franse")
    assert first == second == [("france", 1)]
