#!/usr/bin/env python3
"""Phase 0A.2/0A.3: parallel MediaWiki ingestion with sharded SQLite writes.

Design (bit-identity with the serial CorpusStore.ingest_mediawiki):

- Each worker re-reads the dump and processes the contiguous page-index range
  [start, end) — the same articles the serial loop would see, in dump order.
- Workers collect rows in memory (documents/chunks/fts/time/links/categories/
  aliases) plus the raw counters the serial manifest increments, and write ONE
  SQLite shard each with a single executemany transaction.
- The merge passes shard rows table-by-table in shard order (== serial page
  order), then runs the serial global steps with the serial code path:
  redirect folding, anchor-alias insertion over the sorted union, the links
  UPDATE, and the manifest — so row content AND insertion order match serial.
- shards get cache_size 64 MB; the merge connection gets 1 GB.  FTS is loaded
  as rows in the same order as serial (incremental-equivalent); secondary
  indexes are created after the bulk load; ANALYZE is skipped (serial packs
  never ran it — gate is exact metric reproduction).

Gate: same table counts, same corpus_meta, same benchmark metrics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aethersparse.traversal.corpus import (  # noqa: E402
    LINK_RE,
    SECTION_RE,
    TOKEN_RE,
    CorpusStore,
    _sha256_file,
    iter_mediawiki_pages,
    normalize_text,
    plain_text,
)

YEAR_RE = re.compile(r"\b(?:1[5-9]\d{2}|20\d{2}|2100)\b")

SCHEMA_NO_INDEXES = """
CREATE TABLE documents(
  document_id TEXT PRIMARY KEY, title TEXT NOT NULL, normalized_title TEXT NOT NULL,
  revision TEXT NOT NULL, source_url TEXT NOT NULL, license TEXT NOT NULL,
  provenance TEXT NOT NULL, content_hash TEXT NOT NULL, raw_text TEXT NOT NULL,
  normalized_text TEXT NOT NULL, redirect_target TEXT);
CREATE TABLE chunks(
  chunk_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, section_path TEXT NOT NULL,
  block_index INTEGER NOT NULL, raw_start INTEGER NOT NULL, raw_end INTEGER NOT NULL,
  raw_text TEXT NOT NULL, normalized_text TEXT NOT NULL, content_hash TEXT NOT NULL,
  summary TEXT NOT NULL, semantic_key TEXT NOT NULL);
CREATE VIRTUAL TABLE chunks_fts USING fts5(
  chunk_id UNINDEXED, title, section_path, body,
  tokenize='unicode61 remove_diacritics 2');
CREATE TABLE aliases(alias TEXT NOT NULL, document_id TEXT NOT NULL,
  PRIMARY KEY(alias, document_id));
CREATE TABLE links(source_document_id TEXT NOT NULL,
  target_title TEXT NOT NULL, target_document_id TEXT,
  PRIMARY KEY(source_document_id, target_title));
CREATE TABLE categories(document_id TEXT NOT NULL, category TEXT NOT NULL,
  PRIMARY KEY(document_id, category));
CREATE TABLE time_expressions(chunk_id TEXT NOT NULL, value TEXT NOT NULL,
  PRIMARY KEY(chunk_id, value));
CREATE TABLE corpus_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def process_range(
    dump: str, start: int, end: int, chunk_chars: int, shard_path: str
) -> dict:
    """Process pages [start, end) into one shard SQLite; returns counters."""

    t0 = time.perf_counter()
    docs: list[tuple] = []
    doc_aliases: list[tuple] = []
    chunk_rows: list[tuple] = []
    fts_rows: list[tuple] = []
    time_rows: list[tuple] = []
    link_rows: list[tuple] = []
    category_rows: list[tuple] = []
    redirect_pages: list[tuple[str, str, str]] = []
    anchor_aliases: set[tuple[str, str]] = set()
    articles = chunks = links_found = 0

    for index, page in enumerate(iter_mediawiki_pages(Path(dump))):
        if index < start:
            continue
        if index >= end:
            break
        raw_hash = hashlib.sha256(page.raw.encode()).hexdigest()
        document_id = f"mw:{page.page_id}:{page.revision}:{raw_hash[:12]}"
        normalized = plain_text(page.raw)
        docs.append(
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
            )
        )
        articles += 1  # page_id makes document_id unique; serial INSERT never ignores
        doc_aliases.append((normalize_text(page.title).casefold(), document_id))
        if page.redirect:
            redirect_pages.append(
                (
                    document_id,
                    normalize_text(page.title).casefold(),
                    normalize_text(page.redirect).casefold(),
                )
            )
        sections = list(SECTION_RE.finditer(page.raw))
        boundaries = [
            (0, sections[0].start() if sections else len(page.raw), "Lead")
        ]
        for i, match in enumerate(sections):
            end_b = sections[i + 1].start() if i + 1 < len(sections) else len(page.raw)
            boundaries.append((match.end(), end_b, normalize_text(match.group(2))))
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
                    semantic_key = hashlib.blake2b(
                        " ".join(sorted(set(words))).encode(), digest_size=8
                    ).hexdigest()
                    summary = re.split(r"(?<=[.!?])\s+", body, maxsplit=1)[0][:240]
                    chunk_rows.append(
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
                        )
                    )
                    fts_rows.append((chunk_id, page.title, heading, body))
                    for year in set(YEAR_RE.findall(body)):
                        time_rows.append((chunk_id, year))
                    chunks += 1
                cursor = max(raw_end, cursor + 1)
                block += 1
        for target, label in LINK_RE.findall(page.raw):
            target = normalize_text(target)
            if target and not target.casefold().startswith(
                ("file:", "image:", "category:")
            ):
                link_rows.append((document_id, target, None))
                links_found += 1
                anchor_text = normalize_text(label or target).casefold()
                if (
                    label
                    and 4 <= len(anchor_text) <= 60
                    and anchor_text != target.casefold()
                    and any(character.isalpha() for character in anchor_text)
                ):
                    anchor_aliases.add((anchor_text, target.casefold()))
            if target.casefold().startswith("category:"):
                category_rows.append((document_id, target.split(":", 1)[1]))

    # Write the shard in one transaction.
    Path(shard_path).unlink(missing_ok=True)
    db = sqlite3.connect(shard_path)
    db.execute("PRAGMA journal_mode=OFF")
    db.execute("PRAGMA synchronous=OFF")
    db.execute("PRAGMA cache_size=-65536")  # 64 MB per worker (user correction)
    db.execute("PRAGMA temp_store=MEMORY")
    db.executescript(
        """
        CREATE TABLE documents(document_id TEXT, title TEXT, normalized_title TEXT,
          revision TEXT, source_url TEXT, license TEXT, provenance TEXT,
          content_hash TEXT, raw_text TEXT, normalized_text TEXT, redirect_target TEXT);
        CREATE TABLE chunks(chunk_id TEXT, document_id TEXT, section_path TEXT,
          block_index INTEGER, raw_start INTEGER, raw_end INTEGER, raw_text TEXT,
          normalized_text TEXT, content_hash TEXT, summary TEXT, semantic_key TEXT);
        CREATE TABLE fts(chunk_id TEXT, title TEXT, section_path TEXT, body TEXT);
        CREATE TABLE aliases(alias TEXT, document_id TEXT);
        CREATE TABLE links(source_document_id TEXT, target_title TEXT,
          target_document_id TEXT);
        CREATE TABLE categories(document_id TEXT, category TEXT);
        CREATE TABLE time_expressions(chunk_id TEXT, value TEXT);
        CREATE TABLE redirects(document_id TEXT, source_title TEXT, target_title TEXT);
        CREATE TABLE anchor_aliases(anchor_text TEXT, target_title TEXT);
        """
    )
    with db:
        db.executemany("INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?)", docs)
        db.executemany("INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?,?,?,?)", chunk_rows)
        db.executemany("INSERT INTO fts VALUES(?,?,?,?)", fts_rows)
        db.executemany("INSERT INTO aliases VALUES(?,?)", doc_aliases)
        db.executemany("INSERT INTO links VALUES(?,?,?)", link_rows)
        db.executemany("INSERT INTO categories VALUES(?,?)", category_rows)
        db.executemany("INSERT INTO time_expressions VALUES(?,?)", time_rows)
        db.executemany("INSERT INTO redirects VALUES(?,?,?)", redirect_pages)
        db.executemany(
            "INSERT INTO anchor_aliases VALUES(?,?)", sorted(anchor_aliases)
        )
    db.close()
    peak_kb = 0
    try:
        with open(f"/proc/{os.getpid()}/status") as fh:
            for line in fh:
                if line.startswith("VmHWM"):
                    peak_kb = int(line.split()[1])
    except OSError:
        pass
    return {
        "shard": shard_path,
        "start": start,
        "end": end,
        "articles": articles,
        "chunks": chunks,
        "links_found": links_found,
        "seconds": round(time.perf_counter() - t0, 2),
        "peak_rss_mb": round(peak_kb / 1024, 1),
    }


def merge_shards(
    shard_paths: list[str], output: Path, dump: Path, chunk_chars: int,
    fold_redirects: bool, counters: list[dict],
) -> dict:
    t0 = time.perf_counter()
    output.unlink(missing_ok=True)
    db = sqlite3.connect(output)
    db.execute("PRAGMA journal_mode=OFF")
    db.execute("PRAGMA synchronous=OFF")
    db.execute("PRAGMA cache_size=-1048576")  # 1 GB: merge/writer connection only
    db.execute("PRAGMA temp_store=MEMORY")
    db.execute("PRAGMA locking_mode=EXCLUSIVE")
    db.executescript(SCHEMA_NO_INDEXES)

    tables = (
        ("documents", 11), ("chunks", 11), ("fts", 4), ("aliases", 2),
        ("links", 3), ("categories", 2), ("time_expressions", 2),
    )
    for shard in shard_paths:
        db.execute("ATTACH DATABASE ? AS shard", (shard,))
        with db:
            for table, ncol in tables:
                target = "chunks_fts" if table == "fts" else table
                marks = ",".join("?" * ncol)
                db.execute(
                    f"INSERT OR IGNORE INTO {target} SELECT * FROM shard.{table}"
                )
            # OR IGNORE above is safe: shards are disjoint; IGNORE only matters
            # if the dump itself repeats a document_id/chunk_id (serial behaves
            # the same way).
        db.execute("DETACH DATABASE shard")

    # Redirect folding + anchor aliases + links resolution with the SERIAL code.
    store = CorpusStore.__new__(CorpusStore)
    store.path = output
    store.db = db

    redirect_rows: list[tuple[str, str, str]] = []
    anchor_set: set[tuple[str, str]] = set()
    for shard in shard_paths:
        sdb = sqlite3.connect(shard)
        redirect_rows.extend(sdb.execute("SELECT * FROM redirects").fetchall())
        anchor_set.update(sdb.execute("SELECT * FROM anchor_aliases").fetchall())
        sdb.close()

    if fold_redirects:
        resolved_titles = store._fold_redirects(
            [(d, s, t) for d, s, t in redirect_rows]
        )
    else:
        resolved_titles = {}
        for row in db.execute(
            "SELECT normalized_title, document_id, redirect_target FROM documents"
        ):
            if row[0] not in resolved_titles or row[2] is None:
                resolved_titles[str(row[0])] = str(row[1])

    anchor_alias_rows = 0
    with db:
        for anchor_text, target_title in sorted(anchor_set):
            target_doc = resolved_titles.get(target_title)
            if target_doc:
                cursor = db.execute(
                    "INSERT OR IGNORE INTO aliases VALUES(?,?)",
                    (anchor_text, target_doc),
                )
                anchor_alias_rows += cursor.rowcount
        db.execute(
            """UPDATE links SET target_document_id=(
                 SELECT document_id FROM aliases WHERE alias=lower(links.target_title) LIMIT 1)
               WHERE target_document_id IS NULL"""
        )

    # Secondary indexes AFTER the bulk load.
    with db:
        db.execute("CREATE INDEX aliases_name ON aliases(alias)")
        db.execute("CREATE INDEX links_source ON links(source_document_id)")
        db.execute("CREATE INDEX links_target ON links(target_title)")

    manifest = {
        "articles": sum(c["articles"] for c in counters),
        "chunks": sum(c["chunks"] for c in counters),
        "links": sum(c["links_found"] for c in counters),
        "redirects_folded": len(redirect_rows) if fold_redirects else 0,
        "fold_redirects": fold_redirects,
        "anchor_alias_rows": anchor_alias_rows,
        "dump_sha256": _sha256_file(dump),
        "chunk_chars": chunk_chars,
    }
    with db:
        for key, value in manifest.items():
            db.execute(
                "INSERT OR REPLACE INTO corpus_meta VALUES(?,?)",
                (key, json.dumps(value)),
            )
    # Restore shipping pragmas (0A.3: build-only pragmas must not ship).
    db.execute("PRAGMA locking_mode=NORMAL")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.commit()
    db.close()
    # journal_mode=WAL leaves -wal/-shm companions; checkpoint and remove.
    output.with_suffix(".sqlite-wal").unlink(missing_ok=True)
    output.with_suffix(".sqlite-shm").unlink(missing_ok=True)
    manifest["merge_seconds"] = round(time.perf_counter() - t0, 2)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=min(16, os.cpu_count() or 1))
    parser.add_argument("--chunk-chars", type=int, default=480)
    parser.add_argument("--fold-redirects", type=bool, default=True)
    parser.add_argument("--shard-dir", type=Path, default=Path("/tmp/v09-shards"))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    # Count pages once (also validates the dump path).
    limit = args.limit
    if limit is None:
        # Full dump: count pages with a quick pass (cheap vs the build).
        t0 = time.perf_counter()
        limit = sum(1 for _ in iter_mediawiki_pages(args.dump))
        print(f"page count {limit} in {time.perf_counter() - t0:.0f}s", flush=True)

    workers = max(1, min(args.workers, limit))
    bounds = [
        (i * limit // workers, (i + 1) * limit // workers) for i in range(workers)
    ]
    args.shard_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(
                process_range,
                str(args.dump),
                start,
                end,
                args.chunk_chars,
                str(args.shard_dir / f"shard-{i:03d}.sqlite"),
            )
            for i, (start, end) in enumerate(bounds)
        ]
        counters = [f.result() for f in futures]
    workers_seconds = round(time.perf_counter() - started, 2)

    counters.sort(key=lambda c: c["start"])
    manifest = merge_shards(
        [c["shard"] for c in counters],
        args.output,
        args.dump,
        args.chunk_chars,
        args.fold_redirects,
        counters,
    )
    total = round(time.perf_counter() - started, 2)
    report = {
        "workers": workers,
        "limit": limit,
        "worker_seconds_wall": workers_seconds,
        "worker_detail": counters,
        "total_seconds": total,
        "manifest": manifest,
        "worker_peak_rss_mb_max": max(c["peak_rss_mb"] for c in counters),
    }
    print(json.dumps({k: v for k, v in report.items() if k != "worker_detail"}, indent=2))
    if args.report:
        args.report.write_text(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
