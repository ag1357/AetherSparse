"""Bounded edit-distance <=2 spelling index (Mission 4, Lane C / Phase 7).

Mission 4 misspelling diagnosis (reports/droid/v09/v09-misspell-25k-v2.json,
v09-misspell-100k-v2.json): every displaced misspelling case has a
vocabulary correction within Levenshtein distance 2 (36/36); raw-surface
trigram dual-normalization recovers 0/36 and is falsified, as is the
redirect-folding hypothesis.  The distance metric is Damerau-OSA
(adjacent transposition = 1): measured on v09 laneC variants, the
benchmark's misspellings are transposition-heavy, and pure Levenshtein
ordering lets common wrong words outrank the intended correction.  The
existing distance-1 repair probe
(`EvidenceSelector._spelling_repairs`) cannot generate distance-2 variants
(a length-8 token has ~200k edit-2 variants), so the correction source is a
persisted symmetric-delete index: every vocabulary token is indexed under
all strings obtainable by deleting up to 2 characters, and a lookup deletes
up to 2 characters from the query term and intersects.  Candidates are
verified with a banded Levenshtein check so only true distance-<=2 pairs
survive.  Deterministic: the build is a pure function of the pack, and
correction ordering is (distance, -frequency, token).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path

from aethersparse.traversal.corpus import TOKEN_RE, normalize_text

BUILD_VERSION = 1
MAX_DISTANCE = 2


def levenshtein_leq(left: str, right: str, limit: int = MAX_DISTANCE) -> int | None:
    """Damerau-OSA distance when <= limit, else None.

    Optimal string alignment: adjacent transposition costs 1.  Measured
    (v09 laneC variants @10k): the benchmark's misspellings are
    transposition-heavy ('tehnic'->'ethnic', 'rgeen'->'green',
    'omnday'->'monday', 'amgenta'->'magenta'), and pure Levenshtein scores
    those as distance 2, letting frequency ordering prefer wrong common
    words ('tennis', 'been', 'sunday', 'agent').  With OSA the intended
    correction is distance 1 and wins the (distance, -frequency, token)
    ordering outright.

    Full DP with early exit; the symmetric-delete prefilter means this runs
    on a handful of candidates per lookup, so banding is unnecessary.
    """

    if abs(len(left) - len(right)) > limit:
        return None
    if left == right:
        return 0
    width = len(right) + 1
    before: list[int] | None = None
    previous = list(range(width))
    for i, char_left in enumerate(left, start=1):
        current = [i]
        for j in range(1, width):
            cost = 0 if char_left == right[j - 1] else 1
            value = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            if (
                before is not None
                and i > 1
                and j > 1
                and char_left == right[j - 2]
                and left[i - 2] == right[j - 1]
            ):
                value = min(value, before[j - 2] + 1)
            current.append(value)
        if min(current) > limit:
            return None
        before, previous = previous, current
    distance = previous[len(right)]
    return distance if distance <= limit else None


def _deletions(term: str, depth: int = MAX_DISTANCE) -> set[str]:
    """All strings obtainable from term by deleting up to depth characters."""

    variants = {term}
    frontier = {term}
    for _ in range(depth):
        next_frontier: set[str] = set()
        for item in frontier:
            for i in range(len(item)):
                deleted = item[:i] + item[i + 1 :]
                if deleted and deleted not in variants:
                    variants.add(deleted)
                    next_frontier.add(deleted)
        frontier = next_frontier
    return variants


def build_sidecar(
    pack_path: Path,
    out_path: Path,
    *,
    min_freq: int = 3,
    min_len: int = 4,
    vocab_cap: int = 120_000,
) -> dict[str, int | str]:
    """Build the edit-distance-<=2 index sidecar for a pack.  Pure function."""

    counts: Counter[str] = Counter()
    source = sqlite3.connect(f"file:{pack_path.resolve()}?mode=ro&immutable=1", uri=True)
    for (text,) in source.execute("SELECT normalized_text FROM chunks"):
        counts.update(
            token
            for token in TOKEN_RE.findall(normalize_text(text).casefold())
            if token.isalpha()
        )
    for (title,) in source.execute("SELECT title FROM documents"):
        counts.update(
            token
            for token in TOKEN_RE.findall(normalize_text(title).casefold())
            if token.isalpha()
        )
    source.close()
    vocabulary = sorted(
        (
            (-freq, token)
            for token, freq in counts.items()
            if freq >= min_freq and len(token) >= min_len
        )
    )[:vocab_cap]
    vocabulary = [(token, -neg_freq) for neg_freq, token in vocabulary]

    variants: dict[str, list[tuple[str, int]]] = {}
    for token, freq in vocabulary:
        for variant in _deletions(token):
            variants.setdefault(variant, []).append((token, freq))

    if out_path.exists():
        out_path.unlink()
    db = sqlite3.connect(out_path)
    db.execute("PRAGMA journal_mode=OFF")
    db.execute("PRAGMA synchronous=OFF")
    db.execute("CREATE TABLE vocab (token TEXT PRIMARY KEY, freq INTEGER NOT NULL)")
    db.execute("CREATE TABLE variants (variant TEXT PRIMARY KEY, tokens TEXT NOT NULL)")
    db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.executemany("INSERT INTO vocab VALUES (?, ?)", vocabulary)
    db.executemany(
        "INSERT INTO variants VALUES (?, ?)",
        (
            (variant, json.dumps(sorted(hits, key=lambda item: (-item[1], item[0]))))
            for variant, hits in sorted(variants.items())
        ),
    )
    pack_sha = hashlib.sha256(Path(pack_path).read_bytes()).hexdigest()
    db.executemany(
        "INSERT INTO meta VALUES (?, ?)",
        [
            ("build_version", str(BUILD_VERSION)),
            ("pack_sha256", pack_sha),
            ("pack_path", str(pack_path)),
            ("min_freq", str(min_freq)),
            ("min_len", str(min_len)),
            ("vocab_cap", str(vocab_cap)),
            ("vocab_size", str(len(vocabulary))),
            ("variant_rows", str(len(variants))),
        ],
    )
    db.commit()
    db.execute("PRAGMA optimize")
    db.close()
    return {
        "pack_sha256": pack_sha,
        "vocab_size": len(vocabulary),
        "variant_rows": len(variants),
        "sidecar_bytes": out_path.stat().st_size,
    }


class EditDistanceIndex:
    """Read-only lookup over a sidecar built by build_sidecar()."""

    def __init__(self, sidecar_path: Path):
        self.path = sidecar_path
        self.db = sqlite3.connect(
            f"file:{sidecar_path.resolve()}?mode=ro&immutable=1", uri=True
        )
        meta = dict(self.db.execute("SELECT key, value FROM meta"))
        self.pack_sha256 = str(meta.get("pack_sha256"))

    @classmethod
    def maybe_open(cls, pack_path: Path) -> "EditDistanceIndex | None":
        sidecar = pack_path.with_name(f"{pack_path.stem}.ed2.sqlite")
        if not sidecar.exists():
            return None
        try:
            return cls(sidecar)
        except sqlite3.Error:
            return None

    def __contains__(self, term: str) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM vocab WHERE token = ?", (term,)
        ).fetchone()
        return row is not None

    def corrections(self, term: str, *, limit: int = 1) -> list[tuple[str, int]]:
        """Best vocabulary corrections for an OOV term, distance <= 2.

        Returns [(token, distance)] ordered by (distance, -frequency, token).
        """

        if term in self:
            return []
        probes = sorted(_deletions(term))
        hits: dict[str, int] = {}
        marks = ",".join("?" for _ in probes)
        for (tokens_json,) in self.db.execute(
            f"SELECT tokens FROM variants WHERE variant IN ({marks})", probes
        ):
            for token, _freq in json.loads(tokens_json):
                if token in hits:
                    continue
                distance = levenshtein_leq(term, token)
                if distance is not None and distance > 0:
                    hits[token] = distance
        if not hits:
            return []
        freq_rows = self.db.execute(
            f"SELECT token, freq FROM vocab WHERE token IN ({','.join('?' for _ in hits)})",
            tuple(hits),
        ).fetchall()
        freq = {str(token): int(value) for token, value in freq_rows}
        ordered = sorted(hits, key=lambda token: (hits[token], -freq.get(token, 0), token))
        return [(token, hits[token]) for token in ordered[:limit]]
