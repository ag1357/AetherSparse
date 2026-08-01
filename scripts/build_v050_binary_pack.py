#!/usr/bin/env python3
"""Build a bounded flat binary pack from a checksum-pinned v0.5 SQLite pack."""

from __future__ import annotations

import argparse
import hashlib
import json
from itertools import islice
from pathlib import Path

from aethersparse.substrate import (
    FlatBinaryPackReader,
    StructuredSubstrateBuilder,
    extract_claim_seeds,
    iter_source_pages_from_sqlite,
    substrate_metadata_from_sqlite,
    write_flat_binary_pack,
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--pack-manifest", type=Path, required=True)
    parser.add_argument("--documents", type=int, default=256)
    parser.add_argument("--chunk-chars", type=int, default=1024)
    parser.add_argument("--shards", type=int, default=32)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path, required=True)
    return parser.parse_args()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    args = _args()
    if not 1 <= args.documents <= 10_000:
        raise SystemExit("documents must be in [1,10000]")
    if not 1 <= args.shards <= 256:
        raise SystemExit("shards must be in [1,256]")
    expected = json.loads(args.pack_manifest.read_text(encoding="utf-8"))
    if args.pack.stat().st_size != int(expected["pack_bytes"]):
        raise SystemExit("SQLite pack byte count differs from frozen manifest")
    actual_pack_sha256 = _sha256_file(args.pack)
    if actual_pack_sha256 != expected["pack_sha256"]:
        raise SystemExit("SQLite pack SHA-256 differs from frozen manifest")

    pages = tuple(
        islice(iter_source_pages_from_sqlite(args.pack), args.documents)
    )
    if len(pages) != args.documents:
        raise SystemExit("SQLite pack ended before the requested document bound")
    build_command = (
        "scripts/build_v050_binary_pack.py "
        f"--documents {args.documents} --chunk-chars {args.chunk_chars} "
        f"--shards {args.shards}"
    )
    metadata = substrate_metadata_from_sqlite(
        args.pack,
        build_command=build_command,
        parent_pack_hash=f"sha256:{actual_pack_sha256}",
    )
    claims = extract_claim_seeds(pages)
    substrate = StructuredSubstrateBuilder(
        metadata, max_chunk_chars=args.chunk_chars
    ).build(pages, claim_seeds=claims)
    artifact = write_flat_binary_pack(substrate, args.output, shard_count=args.shards)
    verification = FlatBinaryPackReader(args.output).verify_all()
    report = {
        "artifact_id": "AETHERSPARSE_V050_FLAT_BINARY_PACK_R1",
        "eligible": True,
        "parent_pack": {
            "identity": expected["pack_identity"],
            "bytes": expected["pack_bytes"],
            "sha256": actual_pack_sha256,
            "verified": True,
        },
        "selection": {
            "documents": len(substrate.documents),
            "claims": len(substrate.claims),
            "source_bindings": len(substrate.source_bindings),
            "selection_order": "official_pack_page_id_revision_order_prefix",
        },
        "artifact": {
            "external_filename": args.output.name,
            "file_sha256": artifact.file_sha256,
            "total_bytes": artifact.total_bytes,
            "manifest": artifact.manifest.model_dump(mode="json"),
        },
        "verification": verification.model_dump(mode="json"),
    }
    args.manifest_output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.manifest_output.write_text(serialized, encoding="utf-8")
    print(f"output={args.output}")
    print(f"file_sha256={artifact.file_sha256}")
    print(f"manifest={args.manifest_output}")
    print(f"manifest_sha256={hashlib.sha256(serialized.encode()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
