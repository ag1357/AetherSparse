"""Deterministic streaming MediaWiki-to-SQLite pack builder."""

from __future__ import annotations

import bz2
import hashlib
import html
import json
import re
import sqlite3
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PARSER_ID = "mediawiki-xml-v050-distinct-source-pages-v1"
NORMALIZATION_ID = "nfkc-html-punctuation-whitespace-v050-v1"
SCHEMA_VERSION = 500
LICENSE = "CC-BY-SA-4.0"
PACK_FORMAT_ID = "aethersparse-flat-structured-sqlite-v050-1"

LINK_RE = re.compile(r"\[\[([^]|#]+)(?:#[^]|]+)?(?:\|([^]]+))?\]\]")
SECTION_RE = re.compile(r"(?m)^(={2,6})\s*(.*?)\s*\1\s*$")
TAG_RE = re.compile(r"<[^>]+>")
TEMPLATE_RE = re.compile(r"\{\{[^{}]{0,1000}\}\}")
TOKEN_RE = re.compile(r"[\w'-]{2,}", re.UNICODE)


def _hash_file(path: Path, algorithm: str = "sha256") -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_text(value: str) -> str:
    value = html.unescape(unicodedata.normalize("NFKC", value))
    punctuation: dict[str | int, str | int | None] = {
        "\u201c": '"',
        "\u201d": '"',
        "\u2018": "'",
        "\u2019": "'",
        "\u2013": "-",
        "\u2014": "-",
    }
    value = value.translate(str.maketrans(punctuation))
    return re.sub(r"\s+", " ", value).strip()


def plain_text(wikitext: str) -> str:
    text = TEMPLATE_RE.sub(" ", wikitext)
    text = LINK_RE.sub(lambda match: match.group(2) or match.group(1), text)
    text = TAG_RE.sub(" ", text)
    text = re.sub(r"'{2,5}", "", text)
    text = re.sub(
        r"\[(?:https?://\S+)(?:\s+([^\]]+))?\]", lambda match: match.group(1) or "", text
    )
    return normalize_text(text)


@dataclass(frozen=True)
class SourcePage:
    page_id: str
    revision_id: str
    title: str
    raw: str
    redirect_target: str | None
    revision_sha1: str | None
    timestamp: str | None


@dataclass(frozen=True)
class PackSettings:
    article_limit: int
    chunk_chars: int = 480


def iter_pages(dump: Path, *, limit: int | None = None) -> Iterator[SourcePage]:
    opener = bz2.open if dump.suffix == ".bz2" else open
    accepted = 0
    with opener(dump, "rb") as source:
        for _event, element in ET.iterparse(source, events=("end",)):
            if element.tag.rsplit("}", 1)[-1] != "page":
                continue
            page_id = title = namespace = ""
            revision_id = raw = ""
            revision_sha1: str | None = None
            timestamp: str | None = None
            redirect_target: str | None = None
            for child in element:
                name = child.tag.rsplit("}", 1)[-1]
                if name == "title":
                    title = child.text or ""
                elif name == "ns":
                    namespace = child.text or ""
                elif name == "id" and not page_id:
                    page_id = child.text or ""
                elif name == "redirect":
                    redirect_target = child.attrib.get("title")
                elif name == "revision":
                    for part in child:
                        part_name = part.tag.rsplit("}", 1)[-1]
                        if part_name == "id":
                            revision_id = part.text or ""
                        elif part_name == "timestamp":
                            timestamp = part.text
                        elif part_name == "sha1":
                            revision_sha1 = part.text
                        elif part_name == "text":
                            raw = part.text or ""
            element.clear()
            if namespace != "0" or not page_id or not revision_id or not raw:
                continue
            yield SourcePage(
                page_id=page_id,
                revision_id=revision_id,
                title=title,
                raw=raw,
                redirect_target=redirect_target,
                revision_sha1=revision_sha1,
                timestamp=timestamp,
            )
            accepted += 1
            if limit is not None and accepted >= limit:
                return


SCHEMA = """
PRAGMA user_version=500;
CREATE TABLE corpus_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE documents(
  document_id TEXT PRIMARY KEY,
  wiki_page_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  title TEXT NOT NULL,
  normalized_title TEXT NOT NULL,
  redirect_target TEXT,
  source_url TEXT NOT NULL,
  source_text_bytes INTEGER NOT NULL,
  source_text_sha256 TEXT NOT NULL,
  revision_sha1 TEXT,
  revision_timestamp TEXT,
  raw_wikitext TEXT NOT NULL,
  normalized_text TEXT NOT NULL,
  UNIQUE(wiki_page_id, revision_id)
) WITHOUT ROWID;
CREATE INDEX documents_title ON documents(normalized_title);
CREATE INDEX documents_source_hash ON documents(source_text_sha256);
CREATE TABLE chunks(
  chunk_id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL,
  section_path TEXT NOT NULL,
  block_index INTEGER NOT NULL,
  raw_start INTEGER NOT NULL,
  raw_end INTEGER NOT NULL,
  offset_unit TEXT NOT NULL CHECK(offset_unit='unicode_codepoint'),
  raw_text TEXT NOT NULL,
  normalized_text TEXT NOT NULL,
  source_span_sha256 TEXT NOT NULL,
  FOREIGN KEY(document_id) REFERENCES documents(document_id),
  UNIQUE(document_id, raw_start, raw_end)
) WITHOUT ROWID;
CREATE INDEX chunks_document ON chunks(document_id, raw_start);
CREATE VIRTUAL TABLE chunks_fts USING fts5(
  chunk_id UNINDEXED, title, section_path, body,
  tokenize='unicode61 remove_diacritics 2'
);
CREATE TABLE aliases(
  alias TEXT NOT NULL,
  document_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  PRIMARY KEY(alias, document_id, kind),
  FOREIGN KEY(document_id) REFERENCES documents(document_id)
) WITHOUT ROWID;
CREATE INDEX aliases_lookup ON aliases(alias);
CREATE TABLE redirects(
  source_document_id TEXT PRIMARY KEY,
  target_title TEXT NOT NULL,
  source_text_sha256 TEXT NOT NULL,
  FOREIGN KEY(source_document_id) REFERENCES documents(document_id)
) WITHOUT ROWID;
CREATE TABLE anchors(
  anchor_id TEXT PRIMARY KEY,
  source_document_id TEXT NOT NULL,
  target_title TEXT NOT NULL,
  anchor_text TEXT NOT NULL,
  raw_start INTEGER NOT NULL,
  raw_end INTEGER NOT NULL,
  raw_text TEXT NOT NULL,
  source_span_sha256 TEXT NOT NULL,
  FOREIGN KEY(source_document_id) REFERENCES documents(document_id)
) WITHOUT ROWID;
CREATE INDEX anchors_target ON anchors(target_title);
CREATE INDEX anchors_text ON anchors(anchor_text);
"""


def _boundaries(raw: str) -> list[tuple[int, int, str]]:
    sections = list(SECTION_RE.finditer(raw))
    result = [(0, sections[0].start() if sections else len(raw), "Lead")]
    for index, match in enumerate(sections):
        end = sections[index + 1].start() if index + 1 < len(sections) else len(raw)
        result.append((match.end(), end, normalize_text(match.group(2))))
    return result


def _insert_page(
    db: sqlite3.Connection, page: SourcePage, settings: PackSettings
) -> tuple[int, int]:
    source_hash = hashlib.sha256(page.raw.encode()).hexdigest()
    document_id = f"simplewiki:{page.page_id}:{page.revision_id}"
    db.execute(
        "INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            document_id,
            page.page_id,
            page.revision_id,
            page.title,
            normalize_text(page.title).casefold(),
            page.redirect_target,
            f"https://simple.wikipedia.org/?curid={page.page_id}",
            len(page.raw.encode()),
            source_hash,
            page.revision_sha1,
            page.timestamp,
            page.raw,
            plain_text(page.raw),
        ),
    )
    db.execute(
        "INSERT INTO aliases VALUES(?,?,?)",
        (normalize_text(page.title).casefold(), document_id, "title"),
    )
    if page.redirect_target:
        db.execute(
            "INSERT INTO redirects VALUES(?,?,?)",
            (document_id, normalize_text(page.redirect_target), source_hash),
        )
    chunk_count = 0
    for section_start, section_end, heading in _boundaries(page.raw):
        cursor = section_start
        block = 0
        while cursor < section_end:
            raw_end = min(section_end, cursor + settings.chunk_chars)
            if raw_end < section_end:
                split = page.raw.rfind("\n", cursor, raw_end)
                if split > cursor + settings.chunk_chars // 2:
                    raw_end = split
            if raw_end <= cursor:
                raw_end = min(section_end, cursor + settings.chunk_chars)
            raw_block = page.raw[cursor:raw_end]
            body = plain_text(raw_block)
            if body:
                span_hash = hashlib.sha256(raw_block.encode()).hexdigest()
                chunk_id = "chunk:" + hashlib.sha256(
                    f"{document_id}:{cursor}:{raw_end}:{span_hash}".encode()
                ).hexdigest()[:32]
                db.execute(
                    "INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        chunk_id,
                        document_id,
                        heading,
                        block,
                        cursor,
                        raw_end,
                        "unicode_codepoint",
                        raw_block,
                        body,
                        span_hash,
                    ),
                )
                db.execute(
                    "INSERT INTO chunks_fts VALUES(?,?,?,?)", (chunk_id, page.title, heading, body)
                )
                chunk_count += 1
            cursor = raw_end
            block += 1
    anchor_count = 0
    for index, match in enumerate(LINK_RE.finditer(page.raw)):
        target = normalize_text(match.group(1))
        if target.casefold().startswith(("file:", "image:", "category:")):
            continue
        raw_link = match.group(0)
        anchor = normalize_text(match.group(2) or match.group(1))
        span_hash = hashlib.sha256(raw_link.encode()).hexdigest()
        anchor_id = "anchor:" + hashlib.sha256(
            f"{document_id}:{match.start()}:{match.end()}:{index}:{span_hash}".encode()
        ).hexdigest()[:32]
        db.execute(
            "INSERT INTO anchors VALUES(?,?,?,?,?,?,?,?)",
            (
                anchor_id,
                document_id,
                target,
                anchor.casefold(),
                match.start(),
                match.end(),
                raw_link,
                span_hash,
            ),
        )
        anchor_count += 1
    return chunk_count, anchor_count


def series_identity(source: dict[str, Any], settings: PackSettings) -> str:
    identity = {
        "dump_sha256": source["sha256"],
        "normalization": NORMALIZATION_ID,
        "pack_format": PACK_FORMAT_ID,
        "parser": PARSER_ID,
        "schema_version": SCHEMA_VERSION,
        "chunk_chars": settings.chunk_chars,
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"simplewiki_real_corpus_v050_{source['dump_date']}_{digest[:16]}"


def _canonical_source(source: dict[str, Any]) -> dict[str, object]:
    """Remove transfer-path state so pack bytes do not depend on cache history."""

    keys = (
        "dump_date",
        "filename",
        "url",
        "status_url",
        "compressed_bytes",
        "official_sha1",
        "official_md5",
        "sha1",
        "sha256",
        "md5",
    )
    return {key: source[key] for key in keys}


def _integrity(db: sqlite3.Connection) -> dict[str, object]:
    integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
    foreign_keys = db.execute("PRAGMA foreign_key_check").fetchall()
    bad_chunks = db.execute(
        """SELECT COUNT(*) FROM chunks AS c JOIN documents AS d USING(document_id)
           WHERE substr(d.raw_wikitext,c.raw_start+1,c.raw_end-c.raw_start) != c.raw_text"""
    ).fetchone()[0]
    bad_hashes = 0
    for raw_text, expected in db.execute("SELECT raw_text,source_span_sha256 FROM chunks"):
        if hashlib.sha256(str(raw_text).encode()).hexdigest() != expected:
            bad_hashes += 1
    return {
        "sqlite_integrity": integrity,
        "foreign_key_violations": len(foreign_keys),
        "source_binding_failures": bad_chunks + bad_hashes,
    }


def build_pack(
    dump: Path,
    output: Path,
    *,
    source: dict[str, Any],
    settings: PackSettings,
) -> dict[str, object]:
    """Stream a dump into a new deterministic SQLite pack and return its manifest."""

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing pack: {output}")
    temporary = output.with_suffix(output.suffix + ".building")
    if temporary.exists():
        temporary.unlink()
    db = sqlite3.connect(temporary)
    try:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=OFF")
        db.execute("PRAGMA synchronous=OFF")
        db.execute("PRAGMA temp_store=MEMORY")
        db.execute("PRAGMA cache_size=-131072")
        db.executescript(SCHEMA)
        articles = chunks = anchors = redirects = 0
        for page in iter_pages(dump, limit=settings.article_limit):
            page_chunks, page_anchors = _insert_page(db, page, settings)
            articles += 1
            chunks += page_chunks
            anchors += page_anchors
            redirects += int(page.redirect_target is not None)
            if articles % 500 == 0:
                db.commit()
        if articles != settings.article_limit:
            raise ValueError(
                f"dump ended after {articles} namespace-0 pages; expected {settings.article_limit}"
            )
        canonical_source = _canonical_source(source)
        series_id = series_identity(source, settings)
        metadata = {
            "series_id": series_id,
            "pack_format_id": PACK_FORMAT_ID,
            "parser_id": PARSER_ID,
            "normalization_id": NORMALIZATION_ID,
            "schema_version": SCHEMA_VERSION,
            "source_project": "Simple English Wikipedia",
            "namespace": 0,
            "selection_order": "official_dump_order_namespace_0_nonempty_revision_text",
            "license_spdx": LICENSE,
            "license_terms_url": "https://creativecommons.org/licenses/by-sa/4.0/",
            "article_limit": settings.article_limit,
            "chunk_chars": settings.chunk_chars,
            "source": canonical_source,
        }
        for key, value in sorted(metadata.items()):
            db.execute(
                "INSERT INTO corpus_meta VALUES(?,?)",
                (key, json.dumps(value, sort_keys=True, separators=(",", ":"))),
            )
        db.commit()
        integrity = _integrity(db)
        if integrity != {
            "sqlite_integrity": "ok",
            "foreign_key_violations": 0,
            "source_binding_failures": 0,
        }:
            raise ValueError(f"pack integrity failed: {integrity}")
        duplicate_hash_groups = db.execute(
            """SELECT COUNT(*) FROM (
                 SELECT source_text_sha256 FROM documents
                 GROUP BY source_text_sha256 HAVING COUNT(*) > 1)"""
        ).fetchone()[0]
        duplicate_hash_documents = db.execute(
            """SELECT COALESCE(SUM(n),0) FROM (
                 SELECT COUNT(*) AS n FROM documents
                 GROUP BY source_text_sha256 HAVING COUNT(*) > 1)"""
        ).fetchone()[0]
        db.execute("PRAGMA optimize")
        db.execute("VACUUM")
        db.commit()
    finally:
        db.close()
    temporary.replace(output)
    manifest: dict[str, object] = {
        "series_id": series_identity(source, settings),
        "pack_identity": f"{series_identity(source, settings)}_{settings.article_limit // 1000}k",
        "pack_format_id": PACK_FORMAT_ID,
        "parser_id": PARSER_ID,
        "normalization_id": NORMALIZATION_ID,
        "schema_version": SCHEMA_VERSION,
        "article_limit": settings.article_limit,
        "chunk_chars": settings.chunk_chars,
        "documents": articles,
        "chunks": chunks,
        "anchors": anchors,
        "aliases": articles,
        "redirects": redirects,
        "exact_source_bound_records": articles + chunks + anchors + redirects,
        "duplicate_source_hash_groups_preserved": duplicate_hash_groups,
        "documents_in_duplicate_source_hash_groups": duplicate_hash_documents,
        "pack_bytes": output.stat().st_size,
        "pack_sha256": _hash_file(output),
        "source": _canonical_source(source),
        "integrity": integrity,
        "sqlite_version": sqlite3.sqlite_version,
    }
    return manifest


def inspect_pack(path: Path) -> dict[str, object]:
    db = sqlite3.connect(f"file:{path.resolve()}?mode=ro&immutable=1", uri=True)
    try:
        documents = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        chunks = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        anchors = db.execute("SELECT COUNT(*) FROM anchors").fetchone()[0]
        integrity = _integrity(db)
    finally:
        db.close()
    return {
        "documents": documents,
        "chunks": chunks,
        "anchors": anchors,
        "pack_bytes": path.stat().st_size,
        "pack_sha256": _hash_file(path),
        "integrity": integrity,
    }
