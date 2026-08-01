"""Read-only streaming bridge from the v0.5 real-corpus SQLite packs."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Generator, Mapping, Sequence
from itertools import islice
from pathlib import Path

from aethersparse.substrate.builder import StructuredSubstrateBuilder, SubstrateBuildError
from aethersparse.substrate.models import (
    ClaimSeed,
    ExplicitAliasSeed,
    FlatStructuredPack,
    SourcePage,
    SubstrateMetadata,
)

REQUIRED_DOCUMENT_COLUMNS = frozenset(
    {
        "document_id",
        "wiki_page_id",
        "revision_id",
        "title",
        "source_url",
        "source_text_sha256",
        "revision_timestamp",
        "raw_wikitext",
    }
)


def _connect_read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _validate_schema(connection: sqlite3.Connection) -> None:
    columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(documents)")
    }
    missing = REQUIRED_DOCUMENT_COLUMNS - columns
    if missing:
        raise SubstrateBuildError(
            f"real-corpus SQLite documents schema lacks columns: {sorted(missing)}"
        )


def iter_source_pages_from_sqlite(
    database_path: Path,
    *,
    document_ids: Sequence[str] | None = None,
    batch_size: int = 256,
    license_name: str = "CC-BY-SA-4.0",
) -> Generator[SourcePage, None, None]:
    """Yield immutable pages without reading the full pack into memory.

    Filtering is performed while rows stream so this remains safe for a 50k pack.
    The source pack is never opened writable.
    """

    if batch_size < 1 or batch_size > 8192:
        raise ValueError("batch_size must be between 1 and 8192")
    selected = set(document_ids) if document_ids is not None else None
    connection = _connect_read_only(database_path)
    try:
        _validate_schema(connection)
        cursor = connection.execute(
            """SELECT document_id,wiki_page_id,revision_id,title,source_url,
                      source_text_sha256,revision_timestamp,raw_wikitext
                 FROM documents
                ORDER BY length(wiki_page_id),wiki_page_id,revision_id"""
        )
        while rows := cursor.fetchmany(batch_size):
            for row in rows:
                if selected is not None and str(row["document_id"]) not in selected:
                    continue
                raw_hash = str(row["source_text_sha256"])
                source_hash = raw_hash if raw_hash.startswith("sha256:") else f"sha256:{raw_hash}"
                timestamp = row["revision_timestamp"]
                yield SourcePage(
                    page_id=str(row["wiki_page_id"]),
                    namespace=0,
                    revision_id=str(row["revision_id"]),
                    revision_timestamp=str(timestamp) if timestamp else "unknown",
                    title=str(row["title"]),
                    source_url=str(row["source_url"]),
                    license=license_name,
                    text=str(row["raw_wikitext"]),
                    source_sha256=source_hash,
                )
    finally:
        connection.close()


def substrate_metadata_from_sqlite(
    database_path: Path,
    *,
    build_command: str,
    parent_pack_hash: str | None = None,
) -> SubstrateMetadata:
    """Translate checksum-pinned corpus metadata without inventing identities."""

    connection = _connect_read_only(database_path)
    try:
        rows = connection.execute("SELECT key,value FROM corpus_meta ORDER BY key")
        metadata = {str(row[0]): json.loads(str(row[1])) for row in rows}
    finally:
        connection.close()
    source = metadata.get("source")
    if not isinstance(source, dict):
        raise SubstrateBuildError("corpus_meta source object is missing")
    source_sha256 = source.get("sha256")
    if not isinstance(source_sha256, str) or not source_sha256:
        raise SubstrateBuildError("corpus_meta source.sha256 is missing")
    return SubstrateMetadata(
        series_id=str(metadata["series_id"]),
        source_dump_id=str(source.get("filename") or source.get("dump_date") or "unknown"),
        source_dump_sha256=(
            source_sha256
            if source_sha256.startswith("sha256:")
            else f"sha256:{source_sha256}"
        ),
        parser_identity=str(metadata["parser_id"]),
        normalization_identity=str(metadata["normalization_id"]),
        build_command=build_command,
        parent_pack_hash=parent_pack_hash,
    )


def build_selected_substrate_from_sqlite(
    database_path: Path,
    metadata: SubstrateMetadata,
    *,
    document_ids: Sequence[str] | None = None,
    max_documents: int | None = None,
    claim_seeds: Sequence[ClaimSeed] = (),
    explicit_aliases: Sequence[ExplicitAliasSeed] = (),
    entity_types: Mapping[str, str] | None = None,
    max_chunk_chars: int = 1024,
) -> FlatStructuredPack:
    """Build a bounded evaluation substrate from a selected SQLite document set.

    Callers must supply either explicit document IDs or an explicit maximum. This
    prevents an accidental full-corpus materialization in the host reference builder.
    """

    if document_ids is None and max_documents is None:
        raise ValueError("selected build requires document_ids or max_documents")
    if max_documents is not None and max_documents < 1:
        raise ValueError("max_documents must be positive")
    source_pages = iter_source_pages_from_sqlite(database_path, document_ids=document_ids)
    try:
        pages = (
            tuple(islice(source_pages, max_documents))
            if max_documents
            else tuple(source_pages)
        )
    finally:
        source_pages.close()
    if document_ids is not None and len(pages) != len(set(document_ids)):
        raise SubstrateBuildError("one or more selected document IDs were not found")
    return StructuredSubstrateBuilder(
        metadata, max_chunk_chars=max_chunk_chars
    ).build(
        pages,
        claim_seeds=claim_seeds,
        explicit_aliases=explicit_aliases,
        entity_types=entity_types,
    )
