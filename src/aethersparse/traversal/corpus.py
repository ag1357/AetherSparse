"""Immutable MediaWiki corpus ingestion and bounded SQLite indexes."""

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

LINK_RE = re.compile(r"\[\[([^]|#]+)(?:#[^]|]+)?(?:\|([^]]+))?\]\]")
SECTION_RE = re.compile(r"(?m)^(={2,6})\s*(.*?)\s*\1\s*$")
TAG_RE = re.compile(r"<[^>]+>")
TEMPLATE_RE = re.compile(r"\{\{[^{}]{0,1000}\}\}")
TOKEN_RE = re.compile(r"[\w'-]{2,}", re.UNICODE)


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
    text = LINK_RE.sub(lambda m: m.group(2) or m.group(1), text)
    text = TAG_RE.sub(" ", text)
    text = re.sub(r"'{2,5}", "", text)
    text = re.sub(r"\[(?:https?://\S+)(?:\s+([^\]]+))?\]", lambda m: m.group(1) or "", text)
    return normalize_text(text)


@dataclass(frozen=True)
class Page:
    page_id: str
    title: str
    revision: str
    raw: str
    redirect: str | None


def iter_mediawiki_pages(path: Path, limit: int | None = None) -> Iterator[Page]:
    opener = bz2.open if path.suffix == ".bz2" else open
    count = 0
    with opener(path, "rb") as stream:
        for _event, elem in ET.iterparse(stream, events=("end",)):
            if elem.tag.rsplit("}", 1)[-1] != "page":
                continue
            fields: dict[str, str] = {}
            revision = ""
            raw = ""
            redirect = None
            for child in elem:
                name = child.tag.rsplit("}", 1)[-1]
                if name in {"title", "ns", "id"} and name not in fields:
                    fields[name] = child.text or ""
                elif name == "redirect":
                    redirect = child.attrib.get("title")
                elif name == "revision":
                    for part in child:
                        part_name = part.tag.rsplit("}", 1)[-1]
                        if part_name == "id":
                            revision = part.text or ""
                        elif part_name == "text":
                            raw = part.text or ""
            elem.clear()
            if fields.get("ns") != "0" or not raw:
                continue
            yield Page(fields.get("id", ""), fields.get("title", ""), revision, raw, redirect)
            count += 1
            if limit is not None and count >= limit:
                return


class CorpusStore:
    """SQLite store whose source rows are content-addressed and never updated."""

    def __init__(self, path: Path, *, read_only: bool = False):
        self.path = path
        if read_only:
            if not path.is_file():
                raise FileNotFoundError(path)
            self.db = sqlite3.connect(f"file:{path.resolve()}?mode=ro&immutable=1", uri=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        if not read_only:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            self._schema()

    def close(self) -> None:
        self.db.close()

    def _schema(self) -> None:
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS documents(
              document_id TEXT PRIMARY KEY, title TEXT NOT NULL, normalized_title TEXT NOT NULL,
              revision TEXT NOT NULL, source_url TEXT NOT NULL, license TEXT NOT NULL,
              provenance TEXT NOT NULL, content_hash TEXT NOT NULL UNIQUE, raw_text TEXT NOT NULL,
              normalized_text TEXT NOT NULL, redirect_target TEXT);
            CREATE TABLE IF NOT EXISTS chunks(
              chunk_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, section_path TEXT NOT NULL,
              block_index INTEGER NOT NULL, raw_start INTEGER NOT NULL, raw_end INTEGER NOT NULL,
              raw_text TEXT NOT NULL, normalized_text TEXT NOT NULL, content_hash TEXT NOT NULL,
              summary TEXT NOT NULL, semantic_key TEXT NOT NULL,
              FOREIGN KEY(document_id) REFERENCES documents(document_id));
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
              chunk_id UNINDEXED, title, section_path, body,
              tokenize='unicode61 remove_diacritics 2');
            CREATE TABLE IF NOT EXISTS aliases(alias TEXT NOT NULL, document_id TEXT NOT NULL,
              PRIMARY KEY(alias, document_id));
            CREATE INDEX IF NOT EXISTS aliases_name ON aliases(alias);
            CREATE TABLE IF NOT EXISTS links(source_document_id TEXT NOT NULL,
              target_title TEXT NOT NULL, target_document_id TEXT,
              PRIMARY KEY(source_document_id, target_title));
            CREATE INDEX IF NOT EXISTS links_source ON links(source_document_id);
            CREATE INDEX IF NOT EXISTS links_target ON links(target_title);
            CREATE TABLE IF NOT EXISTS categories(document_id TEXT NOT NULL, category TEXT NOT NULL,
              PRIMARY KEY(document_id, category));
            CREATE TABLE IF NOT EXISTS time_expressions(chunk_id TEXT NOT NULL, value TEXT NOT NULL,
              PRIMARY KEY(chunk_id, value));
            CREATE TABLE IF NOT EXISTS corpus_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """
        )
        self.db.commit()

    def ingest_mediawiki(
        self, dump: Path, *, limit: int | None = None, chunk_chars: int = 480
    ) -> dict[str, object]:
        articles = chunks = links = 0
        for page in iter_mediawiki_pages(dump, limit):
            raw_hash = hashlib.sha256(page.raw.encode()).hexdigest()
            document_id = f"mw:{page.page_id}:{page.revision}:{raw_hash[:12]}"
            normalized = plain_text(page.raw)
            self.db.execute(
                "INSERT OR IGNORE INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    document_id,
                    page.title,
                    normalize_text(page.title).casefold(),
                    page.revision,
                    f"https://simple.wikipedia.org/?curid={page.page_id}",
                    "CC-BY-SA-4.0",
                    "Wikimedia Simple English Wikipedia XML dump",
                    raw_hash,
                    page.raw,
                    normalized,
                    page.redirect,
                ),
            )
            if self.db.execute("SELECT changes()").fetchone()[0] == 0:
                continue
            articles += 1
            self.db.execute(
                "INSERT OR IGNORE INTO aliases VALUES(?,?)",
                (normalize_text(page.title).casefold(), document_id),
            )
            if page.redirect:
                self.db.execute(
                    "INSERT OR IGNORE INTO aliases VALUES(?,?)",
                    (normalize_text(page.redirect).casefold(), document_id),
                )
            sections = list(SECTION_RE.finditer(page.raw))
            boundaries = [(0, sections[0].start() if sections else len(page.raw), "Lead")]
            for index, match in enumerate(sections):
                end = sections[index + 1].start() if index + 1 < len(sections) else len(page.raw)
                boundaries.append((match.end(), end, normalize_text(match.group(2))))
            for section_start, section_end, heading in boundaries:
                cursor = section_start
                block = 0
                while cursor < section_end:
                    raw_end = min(section_end, cursor + chunk_chars)
                    if raw_end < section_end:
                        split = page.raw.rfind("\n", cursor, raw_end)
                        if split > cursor + chunk_chars // 2:
                            raw_end = split
                    raw_block = page.raw[cursor:raw_end]
                    body = plain_text(raw_block)
                    if body:
                        digest = hashlib.sha256(
                            f"{document_id}:{cursor}:{raw_end}:{raw_block}".encode()
                        ).hexdigest()
                        chunk_id = f"chunk:{digest[:24]}"
                        words = TOKEN_RE.findall(body.casefold())
                        semantic_key = hashlib.blake2s(
                            " ".join(sorted(set(words))).encode(), digest_size=8
                        ).hexdigest()
                        summary = re.split(r"(?<=[.!?])\s+", body, maxsplit=1)[0][:240]
                        self.db.execute(
                            "INSERT OR IGNORE INTO chunks VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                chunk_id,
                                document_id,
                                heading,
                                block,
                                cursor,
                                raw_end,
                                raw_block,
                                body,
                                hashlib.sha256(raw_block.encode()).hexdigest(),
                                summary,
                                semantic_key,
                            ),
                        )
                        self.db.execute(
                            "INSERT INTO chunks_fts VALUES(?,?,?,?)",
                            (chunk_id, page.title, heading, body),
                        )
                        for year in set(re.findall(r"\b(?:1[5-9]\d{2}|20\d{2}|2100)\b", body)):
                            self.db.execute(
                                "INSERT OR IGNORE INTO time_expressions VALUES(?,?)",
                                (chunk_id, year),
                            )
                        chunks += 1
                    cursor = max(raw_end, cursor + 1)
                    block += 1
            for target, _label in LINK_RE.findall(page.raw):
                target = normalize_text(target)
                if target and not target.casefold().startswith(("file:", "image:", "category:")):
                    self.db.execute(
                        "INSERT OR IGNORE INTO links VALUES(?,?,NULL)", (document_id, target)
                    )
                    links += 1
                if target.casefold().startswith("category:"):
                    self.db.execute(
                        "INSERT OR IGNORE INTO categories VALUES(?,?)",
                        (document_id, target.split(":", 1)[1]),
                    )
            if articles % 500 == 0:
                self.db.commit()
        self.db.execute(
            """UPDATE links SET target_document_id=(
                 SELECT document_id FROM aliases WHERE alias=lower(links.target_title) LIMIT 1)
               WHERE target_document_id IS NULL"""
        )
        manifest = {
            "articles": articles,
            "chunks": chunks,
            "links": links,
            "dump_sha256": hashlib.sha256(dump.read_bytes()).hexdigest(),
            "chunk_chars": chunk_chars,
        }
        for key, value in manifest.items():
            self.db.execute(
                "INSERT OR REPLACE INTO corpus_meta VALUES(?,?)", (key, json.dumps(value))
            )
        self.db.commit()
        return manifest

    def stats(self) -> dict[str, int | str]:
        result: dict[str, int | str] = {}
        for table in ("documents", "chunks", "links", "aliases", "categories"):
            result[table] = self.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        result["database_bytes"] = self.path.stat().st_size if self.path.exists() else 0
        return result

    def search(self, query: str, limit: int = 12) -> list[sqlite3.Row]:
        terms = [term for term in TOKEN_RE.findall(query.casefold()) if len(term) > 2]
        if not terms:
            return []
        # Longer distinct terms provide a deterministic, cheap approximation of IDF.
        selected = sorted(set(terms), key=lambda term: (-len(term), term))[:7]
        fts_query = " OR ".join(f'"{term}"' for term in selected)
        return list(
            self.db.execute(
                """SELECT c.*, d.title, d.revision, d.source_url,
                          bm25(chunks_fts, 1.8, 1.2, 1.0) AS rank
                   FROM chunks_fts f JOIN chunks c ON c.chunk_id=f.chunk_id
                   JOIN documents d ON d.document_id=c.document_id
                   WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?""",
                (fts_query, limit),
            )
        )

    def title_search(self, query: str, limit: int = 10) -> list[sqlite3.Row]:
        key = f"%{normalize_text(query).casefold()}%"
        return list(
            self.db.execute(
                """SELECT d.* FROM documents d LEFT JOIN aliases a ON a.document_id=d.document_id
                   WHERE d.normalized_title LIKE ? OR a.alias LIKE ?
                   GROUP BY d.document_id ORDER BY length(d.title) LIMIT ?""",
                (key, key, limit),
            )
        )

    def chunks_for_documents(self, document_ids: list[str], limit: int) -> list[sqlite3.Row]:
        if not document_ids:
            return []
        marks = ",".join("?" for _ in document_ids)
        return list(
            self.db.execute(
                f"""SELECT c.*, d.title, d.revision, d.source_url, 0.0 AS rank
                    FROM chunks c JOIN documents d ON d.document_id=c.document_id
                    WHERE c.document_id IN ({marks})
                    ORDER BY c.document_id, c.block_index LIMIT ?""",
                (*document_ids, limit),
            )
        )

    def linked_documents(self, document_ids: list[str], limit: int) -> list[str]:
        if not document_ids:
            return []
        marks = ",".join("?" for _ in document_ids)
        rows = self.db.execute(
            f"""SELECT target_document_id, count(*) n FROM links
                WHERE source_document_id IN ({marks}) AND target_document_id IS NOT NULL
                GROUP BY target_document_id ORDER BY n DESC LIMIT ?""",
            (*document_ids, limit),
        )
        return [row[0] for row in rows]
