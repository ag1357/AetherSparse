from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from aethersparse.agent.knowledge import (
    CanonicalSourceObject,
    DeploymentPackRegistry,
    JsonLinesSourceAdapter,
    KnowledgePackManifest,
    PackRegion,
    PackRegionKind,
    SourceProvenance,
    SourceType,
)


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _manifest(pack_root: Path, version: str, lineage: list[str]) -> KnowledgePackManifest:
    index = (pack_root / "index.bin").read_bytes()
    evidence = (pack_root / "evidence.bin").read_bytes()
    fields: dict[str, object] = {
        "schema_version": "aethercore.knowledge-pack.v1",
        "pack_id": "technical",
        "pack_version": version,
        "source_namespace": "project-docs",
        "source_type": "software_documentation",
        "source_version": "docs-r1",
        "source_license_provenance": [
            {
                "license_id": "Apache-2.0",
                "origin": "https://example.invalid/docs",
                "revision": "r1",
                "content_digest": f"sha256:{_sha(b'source')}",
            }
        ],
        "canonical_object_id_scheme": "project-docs:{sha256}",
        "canonical_object_count": 2,
        "addressing_index": {
            "kind": "addressing_index",
            "relative_path": "index.bin",
            "logical_bytes": len(index),
            "sha256": _sha(index),
        },
        "content_evidence_store": {
            "kind": "evidence/content",
            "relative_path": "evidence.bin",
            "logical_bytes": len(evidence),
            "sha256": _sha(evidence),
        },
        "optional_cache": None,
        "compiler_identity": "factory:test",
        "update_lineage": lineage,
    }
    fields["manifest_sha256"] = KnowledgePackManifest.digest_fields(fields)
    return KnowledgePackManifest.model_validate(fields)


def test_all_required_source_adapters_share_one_provenance_contract(tmp_path: Path) -> None:
    provenance = SourceProvenance(
        license_id="CC-BY-4.0",
        origin="https://example.invalid/source",
        revision="r1",
        content_digest=f"sha256:{_sha(b'body')}",
    )
    for source_type in SourceType:
        path = tmp_path / f"{source_type.name}.jsonl"
        record = CanonicalSourceObject(
            canonical_object_id=f"test:{source_type.name}",
            source_namespace="test",
            source_type=source_type,
            source_version="r1",
            title="Object",
            body="body",
            provenance=provenance,
        )
        path.write_text(record.model_dump_json() + "\n", encoding="utf-8")
        assert list(JsonLinesSourceAdapter(path, source_type).iter_objects()) == [record]


def test_pack_manifest_regions_and_atomic_add_update_remove(tmp_path: Path) -> None:
    pack_v1 = tmp_path / "pack-v1"
    pack_v1.mkdir()
    (pack_v1 / "index.bin").write_bytes(b"index-v1")
    (pack_v1 / "evidence.bin").write_bytes(b"evidence-v1")
    manifest_v1 = _manifest(pack_v1, "1", [])

    registry = DeploymentPackRegistry(tmp_path / "registry")
    registry.add(manifest_v1, pack_v1)
    assert registry.active()["technical"]["identity"] == manifest_v1.identity()

    pack_v2 = tmp_path / "pack-v2"
    pack_v2.mkdir()
    (pack_v2 / "index.bin").write_bytes(b"index-v2")
    (pack_v2 / "evidence.bin").write_bytes(b"evidence-v2")
    manifest_v2 = _manifest(pack_v2, "2", [manifest_v1.identity()])
    registry.update(manifest_v2, pack_v2)
    assert registry.active()["technical"]["identity"] == manifest_v2.identity()

    registry.remove("technical")
    assert registry.active() == {}
    assert json.loads((tmp_path / "registry" / "active-packs.json").read_text()) == {}


def test_pack_digest_validation_fails_closed(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "index.bin").write_bytes(b"index")
    (pack / "evidence.bin").write_bytes(b"evidence")
    manifest = _manifest(pack, "1", [])
    (pack / "index.bin").write_bytes(b"tampered")
    with pytest.raises(ValueError, match=r"size mismatch|digest mismatch"):
        DeploymentPackRegistry(tmp_path / "registry").add(manifest, pack)


def test_region_paths_cannot_escape_pack() -> None:
    with pytest.raises(ValueError, match="relative"):
        PackRegion(
            kind=PackRegionKind.ADDRESSING_INDEX,
            relative_path="../outside",
            logical_bytes=0,
            sha256=_sha(b""),
        )
