#!/usr/bin/env python3
"""Recompile the Pack-v2 derived evidence-directory table for a P4 pack.

Parses the evidence region's authoritative entity directory, re-encodes the
DIRECT_COMPACT_RESIDENT device image via the edge_runtime codec, and writes
the descriptor JSON that pack_v2.cpp validates at boot (schema, layout,
capacity, image bytes, image/directory/region sha256, pack_id binding).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

PAGE = 4096
EVD_MAGIC = b"AEVD0501"


def sha256_span(path: Path, offset: int, length: int) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        source.seek(offset)
        left = length
        while left:
            block = source.read(min(1 << 24, left))
            if not block:
                break
            digest.update(block)
            left -= len(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", type=Path, required=True)
    args = parser.parse_args()
    pack = args.pack

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from aethersparse.edge_runtime.deployment_v15 import (
        EvidenceDirectory,
        EvidenceEntry,
        EvidenceLayout,
    )

    manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
    provenance = json.loads((pack / "provenance.json").read_text(encoding="utf-8"))
    pack_id = manifest["pack_id"]
    capacity = int(provenance["canonical_objects"]["entity_count"])

    evidence = pack / "regions" / "evidence.bin"
    with open(evidence, "rb") as source:
        header = source.read(PAGE)
    (magic, _version, _occ, _docs, dir_off, dir_len, _bo, _bl, _do, _dl) = (
        struct.unpack("<8sI Q I QQQQQQ", header[:72])
    )
    if magic != EVD_MAGIC:
        raise SystemExit(f"bad evidence magic: {magic!r}")
    entries: list[EvidenceEntry] = []
    with open(evidence, "rb") as source:
        source.seek(dir_off)
        raw = source.read(dir_len)
    for pos in range(0, dir_len, 16):
        entity_idx, blob_off, blob_len, count = struct.unpack_from("<IIII", raw, pos)
        entries.append(
            EvidenceEntry(
                entity_index=entity_idx,
                blob_offset=blob_off,
                blob_length=blob_len,
                occurrence_count=count,
            )
        )

    directory = EvidenceDirectory(
        tuple(entries),
        entity_capacity=capacity,
        layout=EvidenceLayout.DIRECT_COMPACT_RESIDENT,
    )
    image = directory.encode()
    if len(image) != capacity * 12:
        raise SystemExit("image size does not match capacity * record")

    derived = pack / "derived"
    derived.mkdir(exist_ok=True)
    (derived / "evidence-directory-v2.bin").write_bytes(image)
    descriptor = {
        "schema_version": "aethersparse.deployment-pack-v2.v1",
        "layout": "direct_compact_resident",
        "entity_capacity": capacity,
        "evidence_entries": len(entries),
        "image_bytes": len(image),
        "record_bytes": 12,
        "page_bytes": PAGE,
        "resident_bytes": directory.resident_bytes,
        "cold_bytes": directory.cold_bytes,
        "image_sha256": hashlib.sha256(image).hexdigest(),
        "source_directory_sha256": sha256_span(evidence, dir_off, dir_len),
        "source_evidence_region_sha256": sha256_span(
            evidence, 0, evidence.stat().st_size
        ),
        "source_pack_id": pack_id,
        "compiler_identity": "aethersparse 0.15.0 aethercore pack",
        "device_time_repack_required": False,
    }
    (derived / "evidence-directory-v2.bin.json").write_text(
        json.dumps(descriptor, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(descriptor, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
