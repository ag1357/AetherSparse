"""Regression tests for the v0.5 pack-normalization repairs.

The first real canonical-pack execution exposed two generic defects:

1. The canonical registry re-normalized raw titles with the generic surface
   contract instead of trusting the pack's stored ``normalized_title`` under
   the pack's declared ``normalization_id``.  The v0.5 normalization folds
   U+2013 EN DASH to hyphen; the generic contract does not, so nine real 10k
   entities (for example "Castilla–La Mancha") failed verification.
2. The v11 benchmark capture exporter aborted on the cross-tier hard-negative
   freeze instead of selecting the unique requested-tier replica per case.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from aethersparse.addressing.bundle_v2 import (
    compile_verified_exact_address_index,
    load_canonical_registry,
)
from aethersparse.addressing.compiler_v2 import (
    AddressArtifactError,
    canonical_entity_id,
    compile_address_pack,
    iter_jsonl_gzip,
    verify_address_export,
)
from aethersparse.addressing.contracts_v2 import (
    V050_PACK_NORMALIZATION_ID,
    pack_lookup_normalizer,
    with_stable_record_id,
)
from aethersparse.addressing.exact import ExactAddressIndex
from aethersparse.addressing.factory_export_v2 import export_v11_benchmark_capture

EN_DASH_TITLE = "Castilla–La Mancha"
EN_DASH_NORMALIZED = "castilla-la mancha"  # v0.5 folds U+2013 to hyphen


def _fit_document_id(stem: str) -> str:
    for index in range(10000):
        value = f"{stem}:{index}"
        bucket = int(hashlib.sha256(value.encode()).hexdigest()[:8], 16) % 100
        if bucket < 80:
            return value
    raise AssertionError("failed to construct fit document ID")


def _dash_pack(path: Path) -> None:
    """Minimal canonical-layout pack carrying the en-dash normalization class."""

    db = sqlite3.connect(path)
    db.executescript(
        """
        PRAGMA user_version=500;
        CREATE TABLE documents(
          document_id TEXT PRIMARY KEY,title TEXT,normalized_title TEXT,
          redirect_target TEXT,source_text_sha256 TEXT,raw_wikitext TEXT
        );
        CREATE TABLE aliases(alias TEXT,document_id TEXT,kind TEXT);
        CREATE TABLE redirects(
          source_document_id TEXT,target_title TEXT,source_text_sha256 TEXT
        );
        CREATE TABLE anchors(
          anchor_id TEXT,source_document_id TEXT,target_title TEXT,anchor_text TEXT,
          raw_start INTEGER,raw_end INTEGER,raw_text TEXT,source_span_sha256 TEXT
        );
        CREATE TABLE corpus_meta(key TEXT PRIMARY KEY,value TEXT);
        """
    )
    db.execute(
        "INSERT INTO corpus_meta VALUES('normalization_id',?)",
        (json.dumps(V050_PACK_NORMALIZATION_ID),),
    )
    dash_id = _fit_document_id("doc:dash")
    source_id = _fit_document_id("doc:source")
    dash_text = "Castilla–La Mancha is a region."
    source_text = "See [[Castilla–La Mancha|the region]] and [[Nowhere Real| ]] here."
    db.executemany(
        "INSERT INTO documents VALUES(?,?,?,?,?,?)",
        [
            (
                dash_id,
                EN_DASH_TITLE,
                EN_DASH_NORMALIZED,
                None,
                hashlib.sha256(dash_text.encode()).hexdigest(),
                dash_text,
            ),
            (
                source_id,
                "Region Article",
                "region article",
                None,
                hashlib.sha256(source_text.encode()).hexdigest(),
                source_text,
            ),
        ],
    )
    db.executemany(
        "INSERT INTO aliases VALUES(?,?,?)",
        [
            (EN_DASH_NORMALIZED, dash_id, "title"),
            ("region article", source_id, "title"),
        ],
    )
    raw_link = "[[Castilla–La Mancha|the region]]"
    start = source_text.index(raw_link)
    db.execute(
        "INSERT INTO anchors VALUES(?,?,?,?,?,?,?,?)",
        (
            "anchor:dash:1",
            source_id,
            EN_DASH_TITLE,
            "the region",
            start,
            start + len(raw_link),
            raw_link,
            hashlib.sha256(raw_link.encode()).hexdigest(),
        ),
    )
    # Whitespace-only piped-link display: a real corpus idiom whose normalized
    # mention is empty and which can never serve as an exact lookup key.
    empty_link = "[[Nowhere Real| ]]"
    empty_start = source_text.index(empty_link)
    db.execute(
        "INSERT INTO anchors VALUES(?,?,?,?,?,?,?,?)",
        (
            "anchor:empty:1",
            source_id,
            "Nowhere Real",
            "",
            empty_start,
            empty_start + len(empty_link),
            empty_link,
            hashlib.sha256(empty_link.encode()).hexdigest(),
        ),
    )
    db.commit()
    db.close()


def _rewrite_stream(
    directory: Path, name: str, mutate: object
) -> None:
    rows = list(iter_jsonl_gzip(directory / f"{name}.jsonl.gz"))
    mutate(rows)  # type: ignore[operator]
    rows = [with_stable_record_id(row) for row in rows]
    raw = b"".join(
        (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
        for row in rows
    )
    path = directory / f"{name}.jsonl.gz"
    with (
        path.open("wb") as raw_stream,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw_stream, mtime=0) as stream,
    ):
        stream.write(raw)
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["streams"][name] = {
        "file": path.name,
        "compressed_bytes": path.stat().st_size,
        "gzip_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "jsonl_bytes": len(raw),
        "jsonl_sha256": hashlib.sha256(raw).hexdigest(),
        "rows": len(rows),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def test_en_dash_entity_loads_and_resolves_canonical(tmp_path: Path) -> None:
    pack = tmp_path / "pack.sqlite"
    _dash_pack(pack)

    manifest = compile_address_pack(pack, tmp_path / "address", corpus_tier="fixture")

    assert manifest.normalization_id == V050_PACK_NORMALIZATION_ID
    assert manifest.counts["occurrence_resolution_canonical"] == 1
    assert manifest.counts["occurrence_resolution_missing"] == 1

    registry = load_canonical_registry(tmp_path / "address")
    by_id = {entry.entity_id: entry for entry in registry.entries}
    expected_id = canonical_entity_id(EN_DASH_NORMALIZED)
    assert expected_id in by_id
    assert by_id[expected_id].canonical_title == EN_DASH_TITLE
    assert by_id[expected_id].normalized_title == EN_DASH_NORMALIZED

    # The exact-index path that the defect blocked now compiles end to end,
    # skipping only the unqueryable empty-surface unresolved row.
    artifact = compile_verified_exact_address_index(
        tmp_path / "address", tmp_path / "exact.fst"
    )
    assert artifact.total_bytes == (tmp_path / "exact.fst").stat().st_size
    assert artifact.entity_count >= 2
    exact = ExactAddressIndex(tmp_path / "exact.fst")
    raw_lookup = exact.lookup(EN_DASH_TITLE)
    normalized_lookup = exact.lookup(EN_DASH_NORMALIZED)
    assert raw_lookup is not None
    assert normalized_lookup is not None
    assert raw_lookup.postings[0].entity_id == expected_id
    assert normalized_lookup.postings[0].entity_id == expected_id

    # Both occurrences remain fully preserved in the export stream.
    occurrences = list(iter_jsonl_gzip(tmp_path / "address" / "occurrences.jsonl.gz"))
    assert len(occurrences) == 2
    empty = next(row for row in occurrences if row["anchor_id"] == "anchor:empty:1")
    assert empty["mention"] == " "
    assert empty["normalized_mention"] == ""
    assert empty["resolution_state"] == "missing"


def test_exact_index_skips_resolved_empty_surface(tmp_path: Path) -> None:
    pack = tmp_path / "pack.sqlite"
    _dash_pack(pack)
    # Retarget the whitespace-display anchor at an existing entity: a resolved
    # occurrence with an empty normalized surface is a lawful corpus idiom at
    # larger tiers, still unqueryable, and must be skipped like any other
    # empty key while the entity stays reachable through its other surfaces.
    db = sqlite3.connect(pack)
    db.execute(
        "UPDATE anchors SET target_title=? WHERE anchor_id='anchor:empty:1'",
        ("Region Article",),
    )
    db.commit()
    db.close()
    manifest = compile_address_pack(pack, tmp_path / "address", corpus_tier="fixture")
    assert manifest.counts["occurrence_resolution_canonical"] == 2
    artifact = compile_verified_exact_address_index(
        tmp_path / "address", tmp_path / "exact.fst"
    )
    assert artifact.total_bytes == (tmp_path / "exact.fst").stat().st_size


def test_registry_rejects_wrong_derivation_and_tampered_ids(tmp_path: Path) -> None:
    pack = tmp_path / "pack.sqlite"
    _dash_pack(pack)
    compile_address_pack(pack, tmp_path / "address", corpus_tier="fixture")

    def derive_from_raw_title(rows: list[dict[str, object]]) -> None:
        for row in rows:
            if row["document_id"].startswith("doc:dash"):
                # The pre-repair derivation: canonical ID from the raw title.
                row["entity_id"] = canonical_entity_id(str(row["title"]))

    _rewrite_stream(tmp_path / "address", "entities", derive_from_raw_title)
    with pytest.raises(AddressArtifactError, match="canonical entity ID/title mismatch"):
        load_canonical_registry(tmp_path / "address")


def test_manifest_without_normalization_id_fails_closed(tmp_path: Path) -> None:
    pack = tmp_path / "pack.sqlite"
    _dash_pack(pack)
    compile_address_pack(pack, tmp_path / "address", corpus_tier="fixture")

    manifest_path = tmp_path / "address" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["normalization_id"]
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    with pytest.raises(AddressArtifactError, match="normalization_id"):
        verify_address_export(tmp_path / "address")


def test_pack_lookup_normalizer_rejects_undeclared_normalization() -> None:
    with pytest.raises(ValueError, match="undeclared source pack normalization"):
        pack_lookup_normalizer("some-other-normalization")


def test_export_selects_unique_requested_tier_replica(tmp_path: Path) -> None:
    pack = tmp_path / "pack.sqlite"
    _dash_pack(pack)
    entity_id = canonical_entity_id(EN_DASH_NORMALIZED)
    query = "Where is Castilla–La Mancha?"
    start = query.index(EN_DASH_TITLE)
    mention = {
        "surface": EN_DASH_TITLE,
        "char_start": start,
        "char_end": start + len(EN_DASH_TITLE),
        "selected_entity_id": entity_id,
        "candidates": [{"entity_id": entity_id}],
    }
    document = {
        "partition_counts": {"development": 2, "tuning": 1},
        "sealed_partitions_excluded": ["evaluation", "final_held"],
        "cases": [
            {
                "case_id": "case:unique",
                "partition": "development",
                "query": query,
                "correct_entity_ids": [entity_id],
                "replicas": [
                    {"corpus_tier": "25k", "mentions": []},
                    {"corpus_tier": "10k", "mentions": [mention]},
                ],
            },
            {
                "case_id": "case:absent",
                "partition": "tuning",
                "query": query,
                "correct_entity_ids": [entity_id],
                "replicas": [{"corpus_tier": "397k", "mentions": [mention]}],
            },
            {
                "case_id": "case:duplicate",
                "partition": "development",
                "query": query,
                "correct_entity_ids": [entity_id],
                "replicas": [
                    {"corpus_tier": "10k", "mentions": [mention]},
                    {"corpus_tier": "10k", "mentions": [mention]},
                ],
            },
        ],
    }
    hard_negatives = tmp_path / "hard-negatives.json.gz"
    with gzip.open(hard_negatives, "wt", encoding="utf-8") as stream:
        json.dump(document, stream)

    result = export_v11_benchmark_capture(
        pack=pack,
        hard_negatives=hard_negatives,
        corpus_tier="10k",
        output=tmp_path / "capture.jsonl.gz",
    )

    assert result["counts"]["cases"] == 1
    assert result["counts"]["cases_excluded_absent_requested_tier_replica"] == 1
    assert result["counts"]["cases_excluded_duplicate_requested_tier_replica"] == 1
    rows = list(iter_jsonl_gzip(tmp_path / "capture.jsonl.gz"))
    assert len(rows) == 1
    assert rows[0]["case_id"] == "case:unique"
    assert rows[0]["partition"] == "development"
    assert rows[0]["pre_cap_candidates"][0]["entity_id"] == entity_id
    assert rows[0]["pre_cap_candidates"][0]["channel"] == "title"
