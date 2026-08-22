#!/usr/bin/env python3
"""Build the V14 ESP32-P4 deployment knowledge pack from the verified v12-final substrate.

Produces a packs.py-compliant immutable knowledge pack with page-addressable
binary regions sized for the accessory P4 (4,096 B pages):

  regions/addressing-index.bin   paged trigram posting index over all distinct
                                 normalized 397k title surfaces, with resident
                                 gram directory and paged surface directory
  regions/canonical-objects.bin  canonical entity table (u64 key, title pool)
  regions/evidence.bin           copied-span occurrence evidence, grouped by
                                 entity, paged, with a paged entity directory
  regions/policy.json            the frozen selected V14 int8 policy artifact

Determinism: all tables are sorted; all integers little-endian; surface IDs are
1-based positions in byte-sorted surface order, matching the
PagedPostingIndex convention of cumulative unpacked posting offsets.

The v12-final outer manifest is verified before any input is read.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import struct
import sys
from pathlib import Path

PAGE = 4096
IDX_MAGIC = b"ACP1IDX1"
ENT_MAGIC = b"ACP1ENT1"
EVD_MAGIC = b"ACP1EVD1"
FORMAT_VERSION = 1

STATE_CODES = {"canonical": 0, "ambiguous": 1, "missing": 2, "redirect_cycle": 3}
SPLIT_CODES = {"fit": 0, "calibration": 1, "holdout": 2}
NO_ENTITY = 0xFFFFFFFF


def _trigrams(value: str) -> tuple[str, ...]:
    """Exact copy of edge_runtime.layout._trigrams semantics."""

    normalized = " ".join(value.casefold().replace("_", " ").split())
    padded = f"  {normalized}  "
    return tuple(sorted({padded[index : index + 3] for index in range(len(padded) - 2)}))


def _norm(value: str) -> str:
    return " ".join(value.casefold().replace("_", " ").split())


def _page_aligned(offset: int) -> int:
    return math.ceil(offset / PAGE) * PAGE


def _pad_to_page(blob: bytearray) -> None:
    blob.extend(b"\x00" * (_page_aligned(len(blob)) - len(blob)))


def verify_outer_manifest(root: Path) -> dict:
    manifest = json.loads((root / "semantic-address-v2-targeted.manifest.json").read_text())
    for entry in manifest["files"]:
        payload = (root / entry["file"]).read_bytes()
        if hashlib.sha256(payload).hexdigest() != entry["sha256"]:
            raise SystemExit(f"v12-final integrity failure: {entry['file']}")
    return manifest


def load_entities(path: Path) -> tuple[list[tuple[int, str]], dict[str, int]]:
    """Return (sorted [(entity_key, title)], v2_entity_id -> entity_idx)."""

    rows: dict[str, str] = {}
    with gzip.open(path, "rt", encoding="utf-8") as source:
        for line in source:
            record = json.loads(line)
            rows[record["entity_id"]] = record["title"]
    keyed: list[tuple[int, str, str]] = []
    seen: set[int] = set()
    for entity_id, title in rows.items():
        hex_part = entity_id.rsplit(":", 1)[-1]
        key = int(hex_part[:16], 16)
        if key in seen:
            raise SystemExit(f"entity key collision at {entity_id}; widen the key")
        seen.add(key)
        keyed.append((key, title, entity_id))
    keyed.sort(key=lambda item: item[0])
    entities = [(key, title) for key, title, _ in keyed]
    index = {entity_id: position for position, (_, _, entity_id) in enumerate(keyed)}
    return entities, index


def load_surfaces(path: Path, entity_index: dict[str, int]) -> list[tuple[str, int, int]]:
    """Return sorted [(normalized_surface, entity_idx_or_NO_ENTITY, state_code)]."""

    surfaces: dict[str, tuple[int, int]] = {}
    with gzip.open(path, "rt", encoding="utf-8") as source:
        for line in source:
            record = json.loads(line)
            surface = _norm(record["surface"])
            state = STATE_CODES[record["resolution_state"]]
            entity_id = record.get("canonical_entity_id") or ""
            entity_idx = entity_index.get(entity_id, NO_ENTITY)
            prior = surfaces.get(surface)
            if prior is None or (prior[0] == NO_ENTITY and entity_idx != NO_ENTITY):
                surfaces[surface] = (entity_idx, state)
    return sorted((surface, entity_idx, state) for surface, (entity_idx, state) in surfaces.items())


def build_address_index(surfaces: list[tuple[str, int, int]]) -> tuple[bytes, dict]:
    surface_count = len(surfaces)
    grams: dict[str, list[int]] = {}
    for position, (surface, _, _) in enumerate(surfaces):
        surface_id = position + 1
        for gram in _trigrams(surface):
            grams.setdefault(gram, []).append(surface_id)

    gram_order = sorted(grams)
    gram_pool = bytearray()
    gram_entries = bytearray()
    postings = bytearray()
    for gram in gram_order:
        encoded = gram.encode("utf-8")
        if len(encoded) > 0xFFFF:
            raise SystemExit("gram too long")
        ids = grams[gram]
        gram_entries += struct.pack(
            "<II I H H",
            len(postings),
            len(ids) * 4,
            len(gram_pool),
            len(encoded),
            0,
        )
        gram_pool += encoded
        postings += struct.pack(f"<{len(ids)}I", *ids)

    surface_pool = bytearray()
    surface_entries = bytearray()
    for surface, entity_idx, state in surfaces:
        encoded = surface.encode("utf-8")
        if len(encoded) > 0xFFFF:
            raise SystemExit("surface too long")
        surface_entries += struct.pack(
            "<I H H I I",
            entity_idx,
            state,
            len(encoded),
            len(surface_pool),
            0,
        )
        surface_pool += encoded

    header = bytearray(PAGE)
    gram_dir_bytes = len(gram_entries) + len(gram_pool)
    postings_off = _page_aligned(PAGE + gram_dir_bytes)
    surface_off = _page_aligned(postings_off + len(postings))
    gram_dir_off = PAGE

    blob = bytearray()
    blob += header
    blob += gram_entries
    blob += gram_pool
    _pad_to_page(blob)
    assert len(blob) == postings_off
    blob += postings
    _pad_to_page(blob)
    assert len(blob) == surface_off
    blob += surface_entries
    blob += surface_pool
    _pad_to_page(blob)

    struct.pack_into(
        "<8sIIIIQQQQQQQII",
        header,
        0,
        IDX_MAGIC,
        FORMAT_VERSION,
        PAGE,
        surface_count,
        len(gram_order),
        len(postings),
        gram_dir_off,
        gram_dir_bytes,
        postings_off,
        len(postings),
        surface_off,
        len(surface_entries) + len(surface_pool),
        16,
        16,
    )
    blob[:PAGE] = header
    stats = {
        "surface_count": surface_count,
        "gram_count": len(gram_order),
        "postings_bytes": len(postings),
        "gram_directory_bytes": gram_dir_bytes,
        "surface_directory_bytes": len(surface_entries) + len(surface_pool),
        "file_bytes": len(blob),
        "postings_pages": math.ceil(len(postings) / PAGE),
    }
    return bytes(blob), stats


def build_entity_table(entities: list[tuple[int, str]]) -> tuple[bytes, dict]:
    pool = bytearray()
    entries = bytearray()
    for key, title in entities:
        encoded = title.encode("utf-8")
        entries += struct.pack("<Q I H H I", key, len(pool), len(encoded), 0, 0)
        pool += encoded
    header = bytearray(PAGE)
    struct.pack_into(
        "<8sIIQQQQ",
        header,
        0,
        ENT_MAGIC,
        FORMAT_VERSION,
        len(entities),
        PAGE,
        len(entries),
        _page_aligned(PAGE + len(entries)),
        len(pool),
    )
    blob = bytearray(header)
    blob += entries
    _pad_to_page(blob)
    blob += pool
    _pad_to_page(blob)
    return bytes(blob), {"entity_count": len(entities), "file_bytes": len(blob)}


def build_evidence(
    path: Path, entity_index: dict[str, int]
) -> tuple[bytes, dict]:
    by_entity: dict[int, bytearray] = {}
    counts: dict[int, int] = {}
    docs: dict[str, int] = {}
    occurrence_count = 0
    with gzip.open(path, "rt", encoding="utf-8") as source:
        for line in source:
            record = json.loads(line)
            entity_idx = entity_index.get(record.get("canonical_entity_id") or "")
            if entity_idx is None:
                continue
            doc_id = record["source_document_id"]
            doc_idx = docs.setdefault(doc_id, len(docs))
            mention = record["mention"].encode("utf-8")[:0xFFFF]
            context = record["context"].encode("utf-8")[:0xFFFF]
            blob = by_entity.setdefault(entity_idx, bytearray())
            blob += struct.pack(
                "<I B B H H H",
                doc_idx,
                SPLIT_CODES.get(record["source_split"], 0),
                STATE_CODES.get(record["resolution_state"], 0),
                len(mention),
                len(context),
                0,
            )
            blob += mention
            blob += context
            counts[entity_idx] = counts.get(entity_idx, 0) + 1
            occurrence_count += 1

    doc_ids = sorted(docs, key=lambda item: docs[item])
    doc_pool = bytearray()
    doc_entries = bytearray()
    for doc_id in doc_ids:
        encoded = doc_id.encode("utf-8")
        doc_entries += struct.pack("<I H H", len(doc_pool), len(encoded), 0)
        doc_pool += encoded

    directory = bytearray()
    blobs = bytearray()
    for entity_idx in sorted(by_entity):
        blob = by_entity[entity_idx]
        directory += struct.pack("<IIII", entity_idx, len(blobs), len(blob), counts[entity_idx])
        blobs += blob

    directory_off = PAGE
    blobs_off = _page_aligned(directory_off + len(directory))
    doc_off = _page_aligned(blobs_off + len(blobs))
    header = bytearray(PAGE)
    struct.pack_into(
        "<8sI Q I QQQQQQ",
        header,
        0,
        EVD_MAGIC,
        FORMAT_VERSION,
        occurrence_count,
        len(doc_ids),
        directory_off,
        len(directory),
        blobs_off,
        len(blobs),
        doc_off,
        len(doc_entries) + len(doc_pool),
    )
    blob = bytearray(header)
    blob += directory
    _pad_to_page(blob)
    assert len(blob) == blobs_off
    blob += blobs
    _pad_to_page(blob)
    assert len(blob) == doc_off
    blob += doc_entries
    blob += doc_pool
    _pad_to_page(blob)
    stats = {
        "occurrence_count": occurrence_count,
        "entities_with_evidence": len(by_entity),
        "doc_count": len(doc_ids),
        "entity_directory_bytes": len(directory),
        "file_bytes": len(blob),
    }
    return bytes(blob), stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v12-final", type=Path, required=True)
    parser.add_argument("--tier", default="397k")
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo-head", required=True)
    arguments = parser.parse_args()

    outer = verify_outer_manifest(arguments.v12_final)
    print(f"v12-final outer manifest verified ({len(outer['files'])} files)", file=sys.stderr)

    address = arguments.v12_final / "address" / arguments.tier
    entities, entity_index = load_entities(address / "entities.jsonl.gz")
    print(f"entities: {len(entities)}", file=sys.stderr)
    surfaces = load_surfaces(address / "aliases.jsonl.gz", entity_index)
    print(f"distinct surfaces: {len(surfaces)}", file=sys.stderr)

    idx_blob, idx_stats = build_address_index(surfaces)
    print(f"address index: {json.dumps(idx_stats, sort_keys=True)}", file=sys.stderr)
    ent_blob, ent_stats = build_entity_table(entities)
    evd_blob, evd_stats = build_evidence(address / "occurrences.jsonl.gz", entity_index)
    print(f"evidence: {json.dumps(evd_stats, sort_keys=True)}", file=sys.stderr)

    policy_bytes = arguments.policy.read_bytes()

    pack_dir = arguments.output
    regions_dir = pack_dir / "regions"
    regions_dir.mkdir(parents=True, exist_ok=False)
    payloads = {
        "regions/addressing-index.bin": idx_blob,
        "regions/canonical-objects.bin": ent_blob,
        "regions/evidence.bin": evd_blob,
        "regions/policy.json": policy_bytes,
    }
    for relative, blob in payloads.items():
        (pack_dir / relative).write_bytes(blob)

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from aethersparse.edge_runtime.packs import (
        KnowledgePackManifest,
        PackRegion,
        RegionRole,
        SourceType,
        validate_pack_directory,
    )

    regions = tuple(
        PackRegion(
            role=role,
            path=relative,
            offset=0,
            length=len(blob),
            sha256=hashlib.sha256(blob).hexdigest(),
            page_bytes=PAGE,
        )
        for (relative, blob), role in zip(
            payloads.items(),
            (
                RegionRole.ADDRESSING_INDEX,
                RegionRole.CANONICAL_OBJECT_TABLE,
                RegionRole.EVIDENCE_STORE,
                RegionRole.POLICY_MODEL,
            ),
            strict=True,
        )
    )
    manifest = KnowledgePackManifest.create(
        source_namespace="simplewiki",
        source_type=SourceType.ENCYCLOPEDIA,
        source_version=(
            "simplewiki-20260701-pages-articles.xml.bz2 sha256:"
            "541a2547b6cc72e91449719226d05181234cfadb2531a69faca1969245c8cb5d"
        ),
        source_license_provenance=(
            "CC BY-SA 4.0 (Wikipedia contributors, Simple English Wikipedia dump 2026-07-01)",
        ),
        canonical_object_id_scheme="as:v050:entity (first 64 bits of hex digest as u64 key)",
        compiler_identity=f"aethersparse v14_p4_pack_build v1 @ {arguments.repo_head}",
        regions=regions,
    )
    (pack_dir / "manifest.json").write_text(
        manifest.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    provenance = {
        "schema_version": "aethersparse.v14-p4-pack-provenance.v1",
        "v12_final_outer_manifest_sha256": hashlib.sha256(
            (arguments.v12_final / "semantic-address-v2-targeted.manifest.json").read_bytes()
        ).hexdigest(),
        "source_tier": arguments.tier,
        "normalization_id": "nfkc-html-punctuation-whitespace-v050-v1",
        "series_id": "simplewiki_real_corpus_v050_20260701_e7a60c622d86dd01",
        "repo_head": arguments.repo_head,
        "policy_artifact_sha256": hashlib.sha256(policy_bytes).hexdigest(),
        "address_index": idx_stats,
        "canonical_objects": ent_stats,
        "evidence": evd_stats,
        "pack_id": manifest.pack_id,
    }
    (pack_dir / "provenance.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    validated = validate_pack_directory(pack_dir)
    print(json.dumps({"pack_id": validated.pack_id, **provenance}, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
