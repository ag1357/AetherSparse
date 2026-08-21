"""Streaming, source-bound Semantic Address Plane v2 compiler.

The compiler opens a canonical v0.5 corpus pack read-only and immutable.  It
emits deterministic gzip JSONL streams rather than copying the pack into the
repository.  Approximate channels may consume these records later, but only
the canonical IDs and exact source offsets emitted here are authoritative.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import re
import sqlite3
import tempfile
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from aethersparse.addressing.contracts_v2 import (
    ADDRESS_EXPORT_SCHEMA_VERSION as ADDRESS_EXPORT_SCHEMA_VERSION,
)
from aethersparse.addressing.contracts_v2 import (
    ADDRESS_MANIFEST_SCHEMA_VERSION as ADDRESS_MANIFEST_SCHEMA_VERSION,
)
from aethersparse.addressing.contracts_v2 import (
    V050_PACK_NORMALIZATION_ID as V050_PACK_NORMALIZATION_ID,
)
from aethersparse.addressing.contracts_v2 import (
    canonical_entity_id as canonical_entity_id,
)
from aethersparse.addressing.contracts_v2 import (
    normalize_surface as normalize_surface,
)
from aethersparse.addressing.contracts_v2 import (
    pack_lookup_normalizer,
    validate_record_contract,
    with_stable_record_id,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_TABLES = frozenset({"documents", "aliases", "redirects", "anchors"})
_STREAM_NAMES = (
    "entities",
    "aliases",
    "redirects",
    "occurrences",
    "surface_statistics",
    "quarantine",
)
_STATISTICS_VIEWS = (
    ("fit", ("fit",), "fit_and_selection"),
    ("fit+calibration", ("fit", "calibration"), "holdout_qualification_only"),
    ("all", ("fit", "calibration", "holdout"), "descriptive_only"),
)


class AddressArtifactError(ValueError):
    """Raised when an address source or compiled artifact is not trustworthy."""


@dataclass(frozen=True)
class StreamIdentity:
    """Content identity and row count for one deterministic JSONL stream."""

    file: str
    compressed_bytes: int
    gzip_sha256: str
    jsonl_bytes: int
    jsonl_sha256: str
    rows: int


@dataclass(frozen=True)
class AddressExportManifest:
    """Manifest returned after a successful pack compile or verification."""

    schema_version: str
    source_pack_sha256: str
    source_pack_bytes: int
    corpus_tier: str
    sqlite_user_version: int
    normalization_id: str
    split_policy: Mapping[str, object]
    counts: Mapping[str, int]
    views: Mapping[str, object]
    streams: Mapping[str, StreamIdentity]


@dataclass(frozen=True)
class _Document:
    document_id: str
    title: str
    normalized_title: str
    redirect_target: str | None
    source_text_sha256: str


@dataclass(frozen=True)
class _ResolvedTarget:
    entity_id: str | None
    canonical_title: str | None
    state: Literal["canonical", "missing", "ambiguous", "redirect_cycle"]
    redirect_path: tuple[str, ...]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_line(value: Mapping[str, object]) -> bytes:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return encoded + b"\n"


class _DeterministicGzipWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._raw = path.open("wb")
        self._gzip = gzip.GzipFile(filename="", mode="wb", fileobj=self._raw, mtime=0)
        self._json_hash = hashlib.sha256()
        self._json_bytes = 0
        self.rows = 0

    def write(self, row: Mapping[str, object]) -> None:
        identified = with_stable_record_id(row)
        validate_record_contract(identified)
        raw = _json_line(identified)
        self._gzip.write(raw)
        self._json_hash.update(raw)
        self._json_bytes += len(raw)
        self.rows += 1

    def close(self) -> StreamIdentity:
        self._gzip.close()
        self._raw.close()
        return StreamIdentity(
            file=self.path.name,
            compressed_bytes=self.path.stat().st_size,
            gzip_sha256=_sha256_file(self.path),
            jsonl_bytes=self._json_bytes,
            jsonl_sha256=self._json_hash.hexdigest(),
            rows=self.rows,
        )

    def abort(self) -> None:
        """Close a partially written stream before removing it."""

        try:
            self._gzip.close()
        finally:
            self._raw.close()


def _open_source(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    missing = _REQUIRED_TABLES - tables
    if missing:
        connection.close()
        raise AddressArtifactError(f"source pack lacks canonical tables: {sorted(missing)}")
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version != 500:
        connection.close()
        raise AddressArtifactError(f"source pack user_version must be 500, got {version}")
    integrity = str(connection.execute("PRAGMA quick_check").fetchone()[0])
    if integrity != "ok":
        connection.close()
        raise AddressArtifactError(f"source pack quick_check failed: {integrity}")
    return connection


def pack_normalization_id(connection: sqlite3.Connection) -> str:
    """Return the source pack's declared corpus_meta normalization_id.

    The declared normalization is required: without it the compiler cannot
    prove that lookup-side normalization matches the pack's stored
    normalized columns, so any pack that cannot declare it fails closed.
    """

    try:
        row = connection.execute(
            "SELECT value FROM corpus_meta WHERE key='normalization_id'"
        ).fetchone()
    except sqlite3.Error as error:
        raise AddressArtifactError(
            "source pack lacks corpus_meta normalization_id"
        ) from error
    if row is None:
        raise AddressArtifactError("source pack lacks corpus_meta normalization_id")
    try:
        value = json.loads(str(row[0]))
    except json.JSONDecodeError as error:
        raise AddressArtifactError("corpus_meta normalization_id is not JSON") from error
    if not isinstance(value, str) or not value:
        raise AddressArtifactError("corpus_meta normalization_id is not a string")
    return value


def pack_lookup_normalize(connection: sqlite3.Connection) -> Callable[[str], str]:
    """Return the pack-declared lookup normalizer, failing closed if undeclared."""

    try:
        return pack_lookup_normalizer(pack_normalization_id(connection))
    except ValueError as error:
        raise AddressArtifactError(str(error)) from error


def _documents(connection: sqlite3.Connection) -> Iterator[_Document]:
    rows = connection.execute(
        """SELECT document_id,title,normalized_title,redirect_target,
                  source_text_sha256
             FROM documents ORDER BY normalized_title,document_id"""
    )
    for row in rows:
        yield _Document(
            document_id=str(row["document_id"]),
            title=str(row["title"]),
            normalized_title=str(row["normalized_title"]),
            redirect_target=(str(row["redirect_target"]) if row["redirect_target"] else None),
            source_text_sha256=str(row["source_text_sha256"]),
        )


def _resolution_index(
    documents: Iterable[_Document],
) -> tuple[dict[str, tuple[_Document, ...]], dict[str, _Document]]:
    by_title: dict[str, list[_Document]] = {}
    by_id: dict[str, _Document] = {}
    for document in documents:
        by_title.setdefault(document.normalized_title, []).append(document)
        by_id[document.document_id] = document
    return ({key: tuple(value) for key, value in by_title.items()}, by_id)


def _resolve_target(
    title: str,
    by_title: Mapping[str, tuple[_Document, ...]],
    normalize_lookup: Callable[[str], str],
) -> _ResolvedTarget:
    current = normalize_lookup(title.split("#", 1)[0])
    path: list[str] = []
    seen: set[str] = set()
    while True:
        if current in seen:
            return _ResolvedTarget(None, None, "redirect_cycle", (*path, current))
        seen.add(current)
        path.append(current)
        rows = by_title.get(current, ())
        if not rows:
            return _ResolvedTarget(None, None, "missing", tuple(path))
        if len(rows) != 1:
            return _ResolvedTarget(None, None, "ambiguous", tuple(path))
        document = rows[0]
        if document.redirect_target:
            current = normalize_lookup(document.redirect_target.split("#", 1)[0])
            continue
        return _ResolvedTarget(
            canonical_entity_id(document.normalized_title),
            document.title,
            "canonical",
            tuple(path),
        )


def _source_split(document_id: str) -> Literal["fit", "calibration", "holdout"]:
    bucket = int(hashlib.sha256(document_id.encode()).hexdigest()[:8], 16) % 100
    if bucket < 80:
        return "fit"
    if bucket < 90:
        return "calibration"
    return "holdout"


def _mention_offsets(raw_start: int, raw_link: str) -> tuple[int, int, str]:
    """Locate copied display text inside one exact ``[[...]]`` source link."""

    if not raw_link.startswith("[[") or not raw_link.endswith("]]"):
        raise AddressArtifactError("anchor raw_text is not a complete source link")
    content = raw_link[2:-2]
    if "|" in content:
        before, display = content.split("|", 1)
        relative_start = 2 + len(before) + 1
    else:
        display = content.split("#", 1)[0]
        relative_start = 2
    relative_end = relative_start + len(display)
    if not display:
        raise AddressArtifactError("anchor source link has an empty display span")
    return raw_start + relative_start, raw_start + relative_end, display


def _validate_sha256(value: str, label: str) -> None:
    if not _SHA256.fullmatch(value):
        raise AddressArtifactError(f"{label} is not a lowercase SHA-256 value")


def _surface_bin(support: int) -> Literal["head", "torso", "tail"]:
    if support >= 100:
        return "head"
    if support >= 10:
        return "torso"
    return "tail"


def _write_manifest(path: Path, manifest: AddressExportManifest) -> None:
    document = asdict(manifest)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def compile_address_pack(
    source_pack: Path,
    output_directory: Path,
    *,
    corpus_tier: str,
    context_characters: int = 96,
) -> AddressExportManifest:
    """Compile a v0.5 SQLite pack into deterministic Semantic Address v2 streams.

    The source connection uses ``mode=ro&immutable=1``.  Surface aggregation is
    disk-backed in a temporary SQLite database so occurrence volume does not
    determine compiler RAM.  Existing output files are refused rather than
    overwritten.
    """

    if not corpus_tier or any(character.isspace() for character in corpus_tier):
        raise ValueError("corpus_tier must be a non-empty token")
    if context_characters < 0 or context_characters > 2048:
        raise ValueError("context_characters must be between 0 and 2048")
    output_directory.mkdir(parents=True, exist_ok=True)
    expected = [output_directory / f"{name}.jsonl.gz" for name in _STREAM_NAMES]
    manifest_path = output_directory / "manifest.json"
    existing = [path for path in (*expected, manifest_path) if path.exists()]
    if existing:
        raise FileExistsError(f"refusing to overwrite address artifacts: {existing}")

    source_hash = _sha256_file(source_pack)
    source_bytes = source_pack.stat().st_size
    connection = _open_source(source_pack)
    writers: dict[str, _DeterministicGzipWriter] = {}
    try:
        normalization_id = pack_normalization_id(connection)
        normalize_lookup = pack_lookup_normalize(connection)
        by_title, by_id = _resolution_index(_documents(connection))
        writers = {
            name: _DeterministicGzipWriter(output_directory / f"{name}.jsonl.gz")
            for name in _STREAM_NAMES
        }
        counts: Counter[str] = Counter()
        split_counts: Counter[str] = Counter()
        with tempfile.TemporaryDirectory(prefix="aethersparse-address-v2-") as temporary:
            aggregate = sqlite3.connect(str(Path(temporary) / "aggregate.sqlite"))
            aggregate.row_factory = sqlite3.Row
            aggregate.executescript(
                """
                PRAGMA journal_mode=OFF;
                PRAGMA synchronous=OFF;
                CREATE TABLE support(
                  surface TEXT NOT NULL,
                  entity_key TEXT NOT NULL,
                  source_split TEXT NOT NULL,
                  canonical_entity_id TEXT,
                  canonical_title TEXT,
                  resolution_state TEXT NOT NULL,
                  occurrence_count INTEGER NOT NULL DEFAULT 0,
                  PRIMARY KEY(surface,entity_key,source_split)
                ) WITHOUT ROWID;
                CREATE TABLE sources(
                  surface TEXT NOT NULL,
                  entity_key TEXT NOT NULL,
                  source_document_id TEXT NOT NULL,
                  source_split TEXT NOT NULL,
                  PRIMARY KEY(surface,entity_key,source_document_id)
                ) WITHOUT ROWID;
                """
            )

            for normalized_title in sorted(by_title):
                rows = by_title[normalized_title]
                if len(rows) != 1:
                    writers["quarantine"].write(
                        {
                            "schema_version": ADDRESS_EXPORT_SCHEMA_VERSION,
                            "record_type": "duplicate_title",
                            "normalized_title": normalized_title,
                            "source_document_ids": sorted(item.document_id for item in rows),
                        }
                    )
                    counts["duplicate_title_groups"] += 1
                    continue
                document = rows[0]
                resolved = _resolve_target(document.normalized_title, by_title, normalize_lookup)
                if resolved.entity_id is None:
                    if document.redirect_target:
                        writers["quarantine"].write(
                            {
                                "schema_version": ADDRESS_EXPORT_SCHEMA_VERSION,
                                "record_type": "unresolved_redirect",
                                "source_document_id": document.document_id,
                                "source_title": document.title,
                                "target_title": document.redirect_target,
                                "resolution_state": resolved.state,
                                "redirect_path": list(resolved.redirect_path),
                            }
                        )
                    continue
                if not document.redirect_target:
                    writers["entities"].write(
                        {
                            "schema_version": ADDRESS_EXPORT_SCHEMA_VERSION,
                            "record_type": "entity",
                            "entity_id": resolved.entity_id,
                            "title": document.title,
                            "normalized_title": document.normalized_title,
                            "document_id": document.document_id,
                            "source_text_sha256": document.source_text_sha256,
                        }
                    )

            alias_rows = connection.execute(
                "SELECT alias,document_id,kind FROM aliases ORDER BY alias,document_id,kind"
            )
            for row in alias_rows:
                document = by_id[str(row["document_id"])]
                resolved = _resolve_target(document.normalized_title, by_title, normalize_lookup)
                writers["aliases"].write(
                    {
                        "schema_version": ADDRESS_EXPORT_SCHEMA_VERSION,
                        "record_type": "alias",
                        "surface": normalize_surface(str(row["alias"])),
                        "kind": str(row["kind"]),
                        "source_document_id": document.document_id,
                        "canonical_entity_id": resolved.entity_id,
                        "canonical_title": resolved.canonical_title,
                        "resolution_state": resolved.state,
                        "redirect_path": list(resolved.redirect_path),
                    }
                )

            redirect_rows = connection.execute(
                """SELECT source_document_id,target_title,source_text_sha256
                     FROM redirects ORDER BY source_document_id"""
            )
            for row in redirect_rows:
                resolved = _resolve_target(str(row["target_title"]), by_title, normalize_lookup)
                writers["redirects"].write(
                    {
                        "schema_version": ADDRESS_EXPORT_SCHEMA_VERSION,
                        "record_type": "redirect",
                        "source_document_id": str(row["source_document_id"]),
                        "source_title": by_id[str(row["source_document_id"])].title,
                        "target_title": str(row["target_title"]),
                        "canonical_entity_id": resolved.entity_id,
                        "canonical_title": resolved.canonical_title,
                        "resolution_state": resolved.state,
                        "redirect_path": list(resolved.redirect_path),
                        "source_text_sha256": str(row["source_text_sha256"]),
                    }
                )

            anchor_rows = connection.execute(
                """SELECT anchor_id,source_document_id,target_title,anchor_text,
                          raw_start,raw_end,raw_text,source_span_sha256
                     FROM anchors ORDER BY source_document_id,raw_start,anchor_id"""
            )
            source_rows = iter(
                connection.execute(
                    """SELECT document_id,source_text_sha256,raw_wikitext
                         FROM documents ORDER BY document_id"""
                )
            )
            source_row = next(source_rows, None)
            for row in anchor_rows:
                source = by_id[str(row["source_document_id"])]
                while (
                    source_row is not None and str(source_row["document_id"]) < source.document_id
                ):
                    source_row = next(source_rows, None)
                if source_row is None or str(source_row["document_id"]) != source.document_id:
                    raise AddressArtifactError(
                        f"anchor source document is absent: {row['anchor_id']}"
                    )
                raw_wikitext = str(source_row["raw_wikitext"])
                if str(source_row["source_text_sha256"]) != source.source_text_sha256:
                    raise AddressArtifactError(f"source document hash drift: {source.document_id}")
                raw_start = int(row["raw_start"])
                raw_end = int(row["raw_end"])
                raw_link = str(row["raw_text"])
                if raw_wikitext[raw_start:raw_end] != raw_link:
                    raise AddressArtifactError(
                        f"anchor offsets do not copy source text: {row['anchor_id']}"
                    )
                mention_start, mention_end, exact_mention = _mention_offsets(raw_start, raw_link)
                if raw_wikitext[mention_start:mention_end] != exact_mention:
                    raise AddressArtifactError(
                        f"mention offsets do not copy source text: {row['anchor_id']}"
                    )
                normalized_mention = normalize_surface(str(row["anchor_text"] or exact_mention))
                resolved = _resolve_target(str(row["target_title"]), by_title, normalize_lookup)
                split = _source_split(source.document_id)
                context_start = max(0, mention_start - context_characters)
                context_end = min(len(raw_wikitext), mention_end + context_characters)
                writers["occurrences"].write(
                    {
                        "schema_version": ADDRESS_EXPORT_SCHEMA_VERSION,
                        "record_type": "hyperlink_occurrence",
                        "corpus_tier": corpus_tier,
                        "anchor_id": str(row["anchor_id"]),
                        "source_document_id": source.document_id,
                        "source_text_sha256": source.source_text_sha256,
                        "source_split": split,
                        "mention": exact_mention,
                        "normalized_mention": normalized_mention,
                        "mention_start": mention_start,
                        "mention_end": mention_end,
                        "link_start": raw_start,
                        "link_end": raw_end,
                        "offset_unit": "unicode_codepoint",
                        "context": raw_wikitext[context_start:context_end],
                        "context_start": context_start,
                        "context_end": context_end,
                        "raw_target_title": str(row["target_title"]),
                        "canonical_entity_id": resolved.entity_id,
                        "canonical_title": resolved.canonical_title,
                        "resolution_state": resolved.state,
                        "redirect_path": list(resolved.redirect_path),
                        "source_span_sha256": str(row["source_span_sha256"]),
                    }
                )
                normalized_target = normalize_surface(str(row["target_title"]))
                entity_key = resolved.entity_id or f"unresolved:{normalized_target}"
                aggregate.execute(
                    """INSERT INTO support VALUES(?,?,?,?,?,?,1)
                       ON CONFLICT(surface,entity_key,source_split) DO UPDATE SET
                         occurrence_count=occurrence_count+1""",
                    (
                        normalized_mention,
                        entity_key,
                        split,
                        resolved.entity_id,
                        resolved.canonical_title,
                        resolved.state,
                    ),
                )
                aggregate.execute(
                    "INSERT OR IGNORE INTO sources VALUES(?,?,?,?)",
                    (normalized_mention, entity_key, source.document_id, split),
                )
                counts["occurrences"] += 1
                counts[f"occurrence_resolution_{resolved.state}"] += 1
                split_counts[split] += 1
            aggregate.commit()

            support_bins: Counter[str] = Counter()
            entropy_bins: Counter[str] = Counter()
            unseen_holdout_surfaces = 0
            view_rows: dict[str, int] = {}
            for view_name, included_splits, usage in _STATISTICS_VIEWS:
                placeholders = ",".join("?" for _ in included_splits)
                surface_rows = aggregate.execute(
                    f"""SELECT surface,SUM(occurrence_count) AS total
                          FROM support WHERE source_split IN ({placeholders})
                          GROUP BY surface ORDER BY surface""",
                    included_splits,
                ).fetchall()
                view_rows[view_name] = len(surface_rows)
                counts[f"surface_statistics_{view_name.replace('+', '_')}_rows"] = len(surface_rows)
                for summary in surface_rows:
                    surface = str(summary["surface"])
                    total = int(summary["total"])
                    alternatives = aggregate.execute(
                        f"""WITH support_agg AS (
                                   SELECT surface,entity_key,canonical_entity_id,
                                          canonical_title,resolution_state,
                                          SUM(occurrence_count) AS occurrence_count
                                     FROM support
                                    WHERE surface=? AND source_split IN ({placeholders})
                                    GROUP BY surface,entity_key
                               ), source_agg AS (
                                   SELECT surface,entity_key,
                                          COUNT(DISTINCT source_document_id)
                                          AS source_document_count
                                     FROM sources
                                    WHERE surface=? AND source_split IN ({placeholders})
                                    GROUP BY surface,entity_key
                               )
                               SELECT s.entity_key,s.canonical_entity_id,s.canonical_title,
                                      s.resolution_state,s.occurrence_count,
                                      src.source_document_count
                                 FROM support_agg AS s JOIN source_agg AS src
                                   ON src.surface=s.surface AND src.entity_key=s.entity_key
                                ORDER BY s.occurrence_count DESC,s.entity_key""",
                        (surface, *included_splits, surface, *included_splits),
                    ).fetchall()
                    probabilities = [int(item["occurrence_count"]) / total for item in alternatives]
                    entropy = -sum(
                        value * math.log(value) for value in probabilities if value > 0.0
                    )
                    unresolved_mass = sum(
                        int(item["occurrence_count"]) / total
                        for item in alternatives
                        if item["canonical_entity_id"] is None
                    )
                    source_splits = {
                        str(item[0])
                        for item in aggregate.execute(
                            f"""SELECT DISTINCT source_split FROM sources
                                  WHERE surface=? AND source_split IN ({placeholders})""",
                            (surface, *included_splits),
                        )
                    }
                    all_source_splits = {
                        str(item[0])
                        for item in aggregate.execute(
                            "SELECT DISTINCT source_split FROM sources WHERE surface=?",
                            (surface,),
                        )
                    }
                    unseen_holdout = (
                        view_name == "all"
                        and "holdout" in all_source_splits
                        and "fit" not in all_source_splits
                    )
                    unseen_holdout_surfaces += unseen_holdout
                    support_bin = _surface_bin(total)
                    support_bins[f"{view_name}:{support_bin}"] += 1
                    entropy_bin = "zero" if entropy == 0.0 else "positive"
                    entropy_bins[f"{view_name}:{entropy_bin}"] += 1
                    writers["surface_statistics"].write(
                        {
                            "schema_version": ADDRESS_EXPORT_SCHEMA_VERSION,
                            "record_type": "surface_statistics",
                            "statistics_view": view_name,
                            "included_source_splits": list(included_splits),
                            "usage": usage,
                            "surface": surface,
                            "occurrence_count": total,
                            "ambiguity_count": len(alternatives),
                            "entropy_nats": entropy,
                            "unresolved_probability_mass": unresolved_mass,
                            "support_bin": support_bin,
                            "source_splits_present": sorted(source_splits),
                            "unseen_surface_in_holdout": unseen_holdout,
                            "candidates": [
                                {
                                    "canonical_entity_id": item["canonical_entity_id"],
                                    "canonical_title": item["canonical_title"],
                                    "resolution_state": item["resolution_state"],
                                    "occurrence_count": int(item["occurrence_count"]),
                                    "source_document_count": int(item["source_document_count"]),
                                    "probability": int(item["occurrence_count"]) / total,
                                    "source_diversity": (
                                        int(item["source_document_count"])
                                        / int(item["occurrence_count"])
                                    ),
                                }
                                for item in alternatives
                            ],
                        }
                    )
            counts["surfaces"] = view_rows["all"]
            counts["unseen_holdout_surfaces"] = unseen_holdout_surfaces
            aggregate.close()

        identities = {name: writers[name].close() for name in _STREAM_NAMES}
        counts.update({f"{name}_rows": identity.rows for name, identity in identities.items()})
        manifest = AddressExportManifest(
            schema_version=ADDRESS_MANIFEST_SCHEMA_VERSION,
            source_pack_sha256=source_hash,
            source_pack_bytes=source_bytes,
            corpus_tier=corpus_tier,
            sqlite_user_version=500,
            normalization_id=normalization_id,
            split_policy={
                "unit": "source_document_id",
                "hash": "sha256-first-32-bits-mod-100",
                "fit_buckets": "0-79",
                "calibration_buckets": "80-89",
                "holdout_buckets": "90-99",
                "benchmark_partitions_used": False,
            },
            counts=dict(sorted(counts.items())),
            views={
                "source_split_occurrences": dict(sorted(split_counts.items())),
                "support_bins": dict(sorted(support_bins.items())),
                "entropy_bins": dict(sorted(entropy_bins.items())),
                "unseen_holdout_surfaces": unseen_holdout_surfaces,
                "surface_statistics": {
                    view_name: {
                        "included_source_splits": list(included_splits),
                        "usage": usage,
                        "rows": view_rows[view_name],
                    }
                    for view_name, included_splits, usage in _STATISTICS_VIEWS
                },
            },
            streams=identities,
        )
        _write_manifest(manifest_path, manifest)
        return manifest
    except Exception:
        for writer in writers.values():
            with suppress(OSError):
                writer.abort()
        for path in (*expected, manifest_path):
            path.unlink(missing_ok=True)
        raise
    finally:
        connection.close()


def _stream_identity(path: Path) -> StreamIdentity:
    gzip_hash = _sha256_file(path)
    raw_hash = hashlib.sha256()
    raw_bytes = 0
    rows = 0
    try:
        with gzip.open(path, "rb") as stream:
            for line in stream:
                raw_hash.update(line)
                raw_bytes += len(line)
                try:
                    document = json.loads(line)
                except json.JSONDecodeError as error:
                    raise AddressArtifactError(f"invalid JSONL in {path.name}") from error
                if not isinstance(document, dict):
                    raise AddressArtifactError(f"non-object JSONL row in {path.name}")
                if document.get("schema_version") != ADDRESS_EXPORT_SCHEMA_VERSION:
                    raise AddressArtifactError(f"wrong row schema in {path.name}")
                rows += 1
    except (EOFError, gzip.BadGzipFile, OSError) as error:
        raise AddressArtifactError(f"corrupt gzip stream: {path.name}") from error
    return StreamIdentity(
        file=path.name,
        compressed_bytes=path.stat().st_size,
        gzip_sha256=gzip_hash,
        jsonl_bytes=raw_bytes,
        jsonl_sha256=raw_hash.hexdigest(),
        rows=rows,
    )


def verify_address_export(directory: Path) -> AddressExportManifest:
    """Recompute every stream identity and return the verified manifest."""

    manifest_path = directory / "manifest.json"
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AddressArtifactError("address manifest is missing or invalid") from error
    if not isinstance(raw, dict) or raw.get("schema_version") != ADDRESS_MANIFEST_SCHEMA_VERSION:
        raise AddressArtifactError("unsupported address manifest schema")
    source_hash = str(raw.get("source_pack_sha256", ""))
    _validate_sha256(source_hash, "source_pack_sha256")
    stream_rows = raw.get("streams")
    if not isinstance(stream_rows, dict) or set(stream_rows) != set(_STREAM_NAMES):
        raise AddressArtifactError("manifest stream set is incomplete")
    streams: dict[str, StreamIdentity] = {}
    for name in _STREAM_NAMES:
        path = directory / f"{name}.jsonl.gz"
        observed = _stream_identity(path)
        expected = stream_rows[name]
        if not isinstance(expected, dict) or asdict(observed) != expected:
            raise AddressArtifactError(f"stream identity mismatch: {name}")
        streams[name] = observed
    counts = raw.get("counts")
    views = raw.get("views")
    split_policy = raw.get("split_policy")
    if (
        not isinstance(counts, dict)
        or not isinstance(views, dict)
        or not isinstance(split_policy, dict)
    ):
        raise AddressArtifactError("manifest summaries are malformed")
    for name, identity in streams.items():
        if counts.get(f"{name}_rows") != identity.rows:
            raise AddressArtifactError(f"manifest row count mismatch: {name}")
    source_bytes = raw.get("source_pack_bytes")
    sqlite_version = raw.get("sqlite_user_version")
    if (
        isinstance(source_bytes, bool)
        or not isinstance(source_bytes, int)
        or source_bytes < 1
        or sqlite_version != 500
    ):
        raise AddressArtifactError("manifest source dimensions are malformed")
    normalization_id = raw.get("normalization_id")
    if normalization_id != V050_PACK_NORMALIZATION_ID:
        raise AddressArtifactError(
            f"manifest normalization_id must be {V050_PACK_NORMALIZATION_ID!r}: "
            f"{normalization_id!r}"
        )
    return AddressExportManifest(
        schema_version=ADDRESS_MANIFEST_SCHEMA_VERSION,
        source_pack_sha256=source_hash,
        source_pack_bytes=source_bytes,
        corpus_tier=str(raw["corpus_tier"]),
        sqlite_user_version=500,
        normalization_id=V050_PACK_NORMALIZATION_ID,
        split_policy=split_policy,
        counts={str(key): int(value) for key, value in counts.items()},
        views=views,
        streams=streams,
    )


def iter_jsonl_gzip(path: Path) -> Iterator[dict[str, Any]]:
    """Yield object rows from a compiled stream without loading it into RAM."""

    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            value = json.loads(line)
            if not isinstance(value, dict):
                raise AddressArtifactError(f"non-object JSONL row in {path.name}")
            yield value
