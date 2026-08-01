from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from aethersparse.substrate import (
    build_selected_substrate_from_sqlite,
    iter_source_pages_from_sqlite,
    sha256_text,
    substrate_metadata_from_sqlite,
)


def _make_corpus_database(path: Path) -> tuple[str, str]:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE corpus_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE documents(
          document_id TEXT PRIMARY KEY,
          wiki_page_id TEXT NOT NULL,
          revision_id TEXT NOT NULL,
          title TEXT NOT NULL,
          source_url TEXT NOT NULL,
          source_text_sha256 TEXT NOT NULL,
          revision_timestamp TEXT,
          raw_wikitext TEXT NOT NULL
        );
        """
    )
    rows = (
        (
            "simplewiki:1:r1",
            "1",
            "r1",
            "Mercury",
            "https://simple.wikipedia.org/?curid=1",
            "Mercury is a planet.",
        ),
        (
            "simplewiki:2:r2",
            "2",
            "r2",
            "Quick Silver",
            "https://simple.wikipedia.org/?curid=2",
            "#REDIRECT [[Mercury]]",
        ),
    )
    for document_id, page_id, revision_id, title, source_url, text in rows:
        source_hash = sha256_text(text).removeprefix("sha256:")
        connection.execute(
            "INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)",
            (
                document_id,
                page_id,
                revision_id,
                title,
                source_url,
                source_hash,
                "2026-08-01T00:00:00Z",
                text,
            ),
        )
    metadata = {
        "series_id": "simplewiki_real_corpus_v050_test",
        "parser_id": "mediawiki-xml-v050-distinct-source-pages",
        "normalization_id": "mediawiki-plain-v050",
        "source": {
            "filename": "simplewiki-test-pages-articles.xml.bz2",
            "sha256": "2" * 64,
        },
    }
    for key, value in metadata.items():
        connection.execute(
            "INSERT INTO corpus_meta VALUES(?,?)",
            (key, json.dumps(value, sort_keys=True, separators=(",", ":"))),
        )
    connection.commit()
    connection.close()
    return rows[0][0], rows[1][0]


def test_sqlite_bridge_streams_and_builds_only_selected_documents(tmp_path: Path) -> None:
    path = tmp_path / "corpus.sqlite"
    mercury_id, redirect_id = _make_corpus_database(path)

    pages = tuple(
        iter_source_pages_from_sqlite(path, document_ids=(redirect_id,), batch_size=1)
    )
    assert [page.title for page in pages] == ["Quick Silver"]
    assert pages[0].source_sha256 is not None
    assert pages[0].source_sha256.startswith("sha256:")

    metadata = substrate_metadata_from_sqlite(
        path, build_command="aethersparse substrate build --selected"
    )
    pack = build_selected_substrate_from_sqlite(
        path,
        metadata,
        document_ids=(mercury_id, redirect_id),
        max_chunk_chars=128,
    )

    assert len(pack.documents) == 2
    assert len(pack.entities) == 1
    assert len(pack.redirects) == 1
    assert pack.metadata.source_dump_sha256 == "sha256:" + "2" * 64
