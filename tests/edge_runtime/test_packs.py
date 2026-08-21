from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from aethersparse.edge_runtime.packs import (
    KnowledgePackManifest,
    PackContractError,
    PackRegion,
    PackRegistry,
    RegionRole,
    SourceType,
)


def _make_pack(root: Path, version: str) -> KnowledgePackManifest:
    root.mkdir()
    regions = []
    for role in (
        RegionRole.ADDRESSING_INDEX,
        RegionRole.CANONICAL_OBJECT_TABLE,
        RegionRole.EVIDENCE_STORE,
    ):
        payload = f"{role}:{version}".encode()
        path = f"{role}.bin"
        (root / path).write_bytes(payload)
        regions.append(
            PackRegion(
                role=role,
                path=path,
                offset=0,
                length=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
            )
        )
    manifest = KnowledgePackManifest.create(
        source_namespace="test.docs",
        source_type=SourceType.SOFTWARE_DOCUMENTATION,
        source_version=version,
        source_license_provenance=("Apache-2.0:test",),
        canonical_object_id_scheme="sha256-96bit",
        compiler_identity="test-compiler:v1",
        regions=tuple(regions),
    )
    (root / "manifest.json").write_text(
        json.dumps(manifest.model_dump(mode="json"), sort_keys=True), encoding="utf-8"
    )
    return manifest


def test_pack_activation_update_and_remove_are_atomic(tmp_path: Path) -> None:
    first = _make_pack(tmp_path / "first", "1")
    second = _make_pack(tmp_path / "second", "2")
    registry = PackRegistry(tmp_path / "deployment")
    assert registry.activate(tmp_path / "first")["packs"][0]["pack_id"] == first.pack_id
    updated = registry.activate(tmp_path / "second")
    assert updated["generation"] == 2
    assert updated["packs"][0]["pack_id"] == second.pack_id
    assert registry.deactivate(second.pack_id)["packs"] == []


def test_pack_digest_validation_rejects_mutation(tmp_path: Path) -> None:
    manifest = _make_pack(tmp_path / "pack", "1")
    target = tmp_path / "pack" / manifest.regions[0].path
    target.write_bytes(b"mutated")
    with pytest.raises(PackContractError, match=r"digest mismatch|truncated"):
        PackRegistry(tmp_path / "deployment").activate(tmp_path / "pack")
