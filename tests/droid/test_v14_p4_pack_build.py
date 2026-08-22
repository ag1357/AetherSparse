"""Round-trip contract test for the V14 P4 deployment pack builder."""

from __future__ import annotations

import gzip
import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from aethersparse.edge_runtime.packs import validate_pack_directory  # noqa: E402

PAGE = 4096


def _gz(path: Path, rows: list[dict]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as sink:
        for row in rows:
            sink.write(json.dumps(row) + "\n")


def _write_fake_v12_final(root: Path) -> None:
    address = root / "address" / "397k"
    address.mkdir(parents=True)
    entity_a = "as:v050:entity:" + "0a" * 12
    entity_b = "as:v050:entity:" + "0b" * 12
    _gz(
        address / "entities.jsonl.gz",
        [
            {"entity_id": entity_a, "title": "Alpha", "normalized_title": "alpha"},
            {"entity_id": entity_b, "title": "Beta", "normalized_title": "beta"},
        ],
    )
    _gz(
        address / "aliases.jsonl.gz",
        [
            {
                "surface": "Alpha",
                "canonical_entity_id": entity_a,
                "resolution_state": "canonical",
            },
            {
                "surface": "Beta",
                "canonical_entity_id": entity_b,
                "resolution_state": "canonical",
            },
            {
                "surface": "Missing Page",
                "canonical_entity_id": "",
                "resolution_state": "missing",
            },
        ],
    )
    _gz(
        address / "occurrences.jsonl.gz",
        [
            {
                "canonical_entity_id": entity_a,
                "source_document_id": "simplewiki:1:1",
                "source_split": "fit",
                "resolution_state": "canonical",
                "mention": "Alpha",
                "context": "Alpha is the first letter.",
            }
        ],
    )
    payload_files = {}
    for extra in ("x.txt",):
        (root / extra).write_text("unused\n", encoding="utf-8")
        payload_files[extra] = hashlib.sha256((root / extra).read_bytes()).hexdigest()
    manifest = {
        "schema_version": "aethersparse.semantic-address-v2-factory-handoff.v1",
        "file_count": len(payload_files),
        "files": [
            {
                "file": name,
                "bytes": (root / name).stat().st_size,
                "sha256": digest,
            }
            for name, digest in sorted(payload_files.items())
        ],
    }
    (root / "semantic-address-v2-targeted.manifest.json").write_text(json.dumps(manifest))


def test_pack_round_trip(tmp_path: Path) -> None:
    source = tmp_path / "v12-final"
    source.mkdir()
    _write_fake_v12_final(source)
    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps({"schema_version": "aethercore.cog-masked-linear-policy.int8.v1"}),
        encoding="utf-8",
    )
    output = tmp_path / "pack"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/droid/v14_p4_pack_build.py"),
            "--v12-final",
            str(source),
            "--tier",
            "397k",
            "--policy",
            str(policy),
            "--output",
            str(output),
            "--repo-head",
            "0" * 40,
        ],
        check=True,
        capture_output=True,
    )

    manifest = validate_pack_directory(output)
    roles = {region.role.value for region in manifest.regions}
    assert roles == {
        "addressing_index",
        "canonical_object_table",
        "evidence_store",
        "policy_model",
    }

    index_blob = (output / "regions/addressing-index.bin").read_bytes()
    assert index_blob[:8] == b"ACP1IDX1"
    (
        _magic,
        _version,
        page_bytes,
        surface_count,
        gram_count,
        postings_len,
        gram_off,
        _gram_len,
        postings_off,
        postings_len_again,
        surface_off,
        _surface_len,
        gram_entry_bytes,
        surface_entry_bytes,
    ) = struct.unpack_from("<8sIIIIQQQQQQQII", index_blob, 0)
    assert page_bytes == PAGE
    assert surface_count == 3  # alpha, beta, missing page
    assert gram_count > 0
    assert postings_len == postings_len_again
    assert gram_off % PAGE == 0 and postings_off % PAGE == 0 and surface_off % PAGE == 0
    assert gram_entry_bytes == 16 and surface_entry_bytes == 16

    entities_blob = (output / "regions/canonical-objects.bin").read_bytes()
    assert entities_blob[:8] == b"ACP1ENT1"
    (_m, _v, entity_count, entries_off, entries_len, _pool_off, _pool_len) = struct.unpack_from(
        "<8sIIQQQQ", entities_blob, 0
    )
    assert entity_count == 2
    assert entries_off == PAGE and entries_len == 2 * 20  # entity entry: <Q I H H I

    evidence_blob = (output / "regions/evidence.bin").read_bytes()
    assert evidence_blob[:8] == b"ACP1EVD1"
    (_m, _v, occurrence_count, _doc_count) = struct.unpack_from("<8sIQI", evidence_blob, 0)
    assert occurrence_count == 1

    provenance = json.loads((output / "provenance.json").read_text())
    assert provenance["pack_id"] == manifest.pack_id
    assert provenance["address_index"]["surface_count"] == 3
