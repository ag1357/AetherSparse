"""Shared deterministic helpers for v0.5 benchmark authoring.

The author, adjudicator, evaluator, and auditor execute this module from distinct
processes.  Authors receive only the immutable corpus and never receive runtime
outputs or grades.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, cast

BENCHMARK_IDENTITY = "INDEPENDENT_NATURAL_QUERY_SET_V050_R1"
SCHEMA_VERSION = "1.0"

REQUIRED_CATEGORIES = frozenset(
    {
        "direct_fact",
        "alias",
        "redirect",
        "misspelling",
        "quotation",
        "date",
        "quantity",
        "incorrect_premise",
        "comparison",
        "two_source",
        "three_to_six_source",
        "ambiguous_entity",
        "unknown_entity",
        "out_of_corpus",
        "pronoun",
        "follow_up",
        "incomplete",
        "clarification",
        "abstention",
    }
)

AUTHOR_IDENTITIES = {
    "alpha": ("v050_r1_author_alpha", "v050-r1-author-alpha-process"),
    "beta": ("v050_r1_author_beta", "v050-r1-author-beta-process"),
    "gamma": ("v050_r1_author_gamma", "v050-r1-author-gamma-process"),
}
ADJUDICATOR_IDENTITY = "v050_r1_source_evidence_adjudicator"
ADJUDICATOR_PROCESS = "v050-r1-source-adjudicator-process"
EVALUATOR_IDENTITY = "v050_r1_blind_runtime_evaluator"
EVALUATOR_PROCESS = "v050-r1-runtime-evaluator-process"
AUDITOR_IDENTITY = "v050_r1_provenance_auditor"
AUDITOR_PROCESS = "v050-r1-provenance-auditor-process"

DEFINITION_RE = re.compile(
    r"'''(?P<subject>[^'\n]{1,100})'''\s+(?:is|are|was|were)\s+"
    r"(?P<answer>[^\n.]{15,260})\.",
    re.IGNORECASE,
)
DATE_RE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+\d{1,2}(?:st|nd|rd|th)?(?:,\s*\d{3,4})?\b|"
    r"\b(?:1[0-9]{3}|20[0-2][0-9])\b"
)
QUANTITY_RE = re.compile(
    r"\b(?P<value>\d+(?:[.,]\d+)?)\s*(?P<unit>kilomet(?:er|re)s?|km|"
    r"met(?:er|re)s?|miles?|kilograms?|kg|percent|%|million|billion|people|"
    r"years?|days?|months?|degrees?|°C|°F)\b",
    re.IGNORECASE,
)
QUOTATION_RE = re.compile(r"[\"“]([^\"“”\n]{12,160})[\"”]")
PLAIN_DEFINITION_REJECT = re.compile(r"[\[\]{}<>|]")
NATURAL_SURFACE_RE = re.compile(r"[\w .,'()&\-]{2,80}", re.UNICODE)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path, *, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(block_size):
            digest.update(block)
    return digest.hexdigest()


def stable_id(prefix: str, *parts: object) -> str:
    material = "\x1f".join(str(part) for part in parts)
    return f"{prefix}:{sha256_text(material)[:24]}"


def normalize_surface(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value.replace("_", " "))
    return " ".join(normalized.strip().split()).casefold()


def canonical_entity_id(title: str) -> str:
    return f"as:v050:entity:{sha256_text(normalize_surface(title))[:24]}"


def partition_for_documents(document_ids: Iterable[str]) -> str:
    unique = tuple(sorted(set(document_ids)))
    if not unique:
        bucket = int(sha256_text("no-source")[:8], 16) % 100
    else:
        # Every source document owns one partition. Multi-source authors must group
        # documents with an identical partition to preserve article isolation.
        partitions = {partition_for_document(item) for item in unique}
        if len(partitions) != 1:
            raise ValueError("multi-source draft crosses frozen document partitions")
        return next(iter(partitions))
    return partition_for_bucket(bucket)


def partition_for_document(document_id: str) -> str:
    return partition_for_bucket(int(sha256_text(document_id)[:8], 16) % 100)


def partition_for_case(case_id: str) -> str:
    return partition_for_bucket(int(sha256_text(case_id)[:8], 16) % 100)


def partition_for_bucket(bucket: int) -> str:
    if bucket < 20:
        return "tuning"
    if bucket < 35:
        return "development"
    if bucket < 80:
        return "evaluation"
    return "final_held"


def connect_read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def corpus_identity(connection: sqlite3.Connection) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for row in connection.execute("SELECT key,value FROM corpus_meta ORDER BY key"):
        try:
            values[str(row[0])] = json.loads(str(row[1]))
        except json.JSONDecodeError:
            values[str(row[0])] = str(row[1])
    return values


def iter_definition_candidates(connection: sqlite3.Connection) -> Iterator[dict[str, Any]]:
    seen_titles: set[str] = set()
    rows = connection.execute(
        """SELECT d.document_id,d.wiki_page_id,d.revision_id,d.title,
                  d.normalized_title,d.source_url,d.source_text_sha256,d.raw_wikitext,
                  c.chunk_id,c.raw_start,c.raw_end,c.raw_text,c.source_span_sha256
             FROM documents d
             JOIN chunks c USING(document_id)
            WHERE d.redirect_target IS NULL
            ORDER BY d.normalized_title,c.block_index,c.chunk_id"""
    )
    for row in rows:
        normalized_title = str(row["normalized_title"])
        if normalized_title in seen_titles:
            continue
        match = DEFINITION_RE.search(str(row["raw_text"]))
        if match is None or PLAIN_DEFINITION_REJECT.search(match.group("answer")):
            continue
        subject = normalize_surface(match.group("subject"))
        title = normalize_surface(str(row["title"]))
        if subject != title and not title.startswith(f"{subject} ("):
            continue
        answer = match.group("answer").strip()
        if len(answer.split()) < 3:
            continue
        seen_titles.add(normalized_title)
        yield row_to_candidate(
            row,
            "definition",
            match.start("answer"),
            match.end("answer"),
        )


def row_to_candidate(
    row: sqlite3.Row,
    extractor: str,
    match_start: int | None = None,
    match_end: int | None = None,
) -> dict[str, Any]:
    candidate = {
        "document_id": str(row["document_id"]),
        "wiki_page_id": str(row["wiki_page_id"]),
        "revision_id": str(row["revision_id"]),
        "title": str(row["title"]),
        "normalized_title": str(row["normalized_title"]),
        "source_url": str(row["source_url"]),
        "document_hash": str(row["source_text_sha256"]),
        "chunk_id": str(row["chunk_id"]),
        "chunk_start": int(row["raw_start"]),
        "chunk_end": int(row["raw_end"]),
        "chunk_hash": str(row["source_span_sha256"]),
        "extractor": extractor,
    }
    if match_start is not None and match_end is not None:
        # Coordinates identify a candidate within evidence. They are not an answer;
        # only the independent adjudicator may copy and accept the surface.
        candidate["candidate_start"] = int(match_start)
        candidate["candidate_end"] = int(match_end)
    return candidate


def load_chunk(connection: sqlite3.Connection, chunk_id: str) -> sqlite3.Row:
    row = connection.execute(
        """SELECT d.document_id,d.wiki_page_id,d.revision_id,d.title,
                  d.normalized_title,d.source_url,d.source_text_sha256,d.raw_wikitext,
                  c.chunk_id,c.raw_start,c.raw_end,c.raw_text,c.source_span_sha256
             FROM chunks c JOIN documents d USING(document_id)
            WHERE c.chunk_id=?""",
        (chunk_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"unknown source chunk: {chunk_id}")
    return cast(sqlite3.Row, row)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    path.write_text(serialized, encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_cases_payload(cases: list[dict[str, Any]]) -> str:
    return json.dumps(
        sorted(cases, key=lambda item: str(item["case_id"])),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
