"""Deterministic content-addressed cognitive-cell pack and delta manifests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from aethersparse.cells.models import CognitiveCell


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def content_hash(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


class CellBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    block_hash: str
    cell: CognitiveCell


class CellPackManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: str = "0.4"
    topology: str
    cell_hashes: tuple[str, ...]
    entity_registry_hash: str
    routing_table_hash: str
    source_manifest_hash: str
    root_hash: str


class CellPackDelta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    from_root: str
    to_root: str
    added_blocks: tuple[str, ...]
    removed_blocks: tuple[str, ...]
    unchanged_blocks: int


class CognitiveCellPack:
    def __init__(self, manifest: CellPackManifest, blocks: tuple[CellBlock, ...]):
        self.manifest = manifest
        self.blocks = blocks

    @classmethod
    def compile(
        cls,
        cells: list[CognitiveCell],
        *,
        topology: str,
        source_manifest_hash: str,
    ) -> CognitiveCellPack:
        cell_ids = [cell.cell_id for cell in cells]
        if len(cell_ids) != len(set(cell_ids)):
            raise ValueError("cell IDs must be unique within a pack")
        blocks = tuple(
            CellBlock(block_hash=content_hash(cell.model_dump(mode="json")), cell=cell)
            for cell in sorted(cells, key=lambda item: item.cell_id)
        )
        entity_registry = sorted({alias for block in blocks for alias in block.cell.entity_aliases})
        routing_table = [
            {
                "cell_id": block.cell.cell_id,
                "block_hash": block.block_hash,
                "signature_hex": block.cell.signature_hex,
            }
            for block in blocks
        ]
        body = {
            "schema_version": "0.4",
            "topology": topology,
            "cell_hashes": [block.block_hash for block in blocks],
            "entity_registry_hash": content_hash(entity_registry),
            "routing_table_hash": content_hash(routing_table),
            "source_manifest_hash": source_manifest_hash,
        }
        manifest = CellPackManifest(
            topology=topology,
            cell_hashes=tuple(block.block_hash for block in blocks),
            entity_registry_hash=content_hash(entity_registry),
            routing_table_hash=content_hash(routing_table),
            source_manifest_hash=source_manifest_hash,
            root_hash=content_hash(body),
        )
        return cls(manifest, blocks)

    def verify(self) -> bool:
        for block in self.blocks:
            if content_hash(block.cell.model_dump(mode="json")) != block.block_hash:
                return False
        if tuple(block.block_hash for block in self.blocks) != self.manifest.cell_hashes:
            return False
        entity_registry = sorted(
            {alias for block in self.blocks for alias in block.cell.entity_aliases}
        )
        if content_hash(entity_registry) != self.manifest.entity_registry_hash:
            return False
        routing_table = [
            {
                "cell_id": block.cell.cell_id,
                "block_hash": block.block_hash,
                "signature_hex": block.cell.signature_hex,
            }
            for block in self.blocks
        ]
        if content_hash(routing_table) != self.manifest.routing_table_hash:
            return False
        body = self.manifest.model_dump(mode="json", exclude={"root_hash"})
        return content_hash(body) == self.manifest.root_hash

    def write(self, directory: Path) -> dict[str, int | str]:
        directory.mkdir(parents=True, exist_ok=True)
        block_dir = directory / "blocks"
        block_dir.mkdir(exist_ok=True)
        for block in self.blocks:
            digest = block.block_hash.split(":", 1)[1]
            (block_dir / f"{digest}.json").write_bytes(
                canonical_bytes(block.model_dump(mode="json")) + b"\n"
            )
        (directory / "manifest.json").write_bytes(
            canonical_bytes(self.manifest.model_dump(mode="json")) + b"\n"
        )
        return {
            "root_hash": self.manifest.root_hash,
            "cell_blocks": len(self.blocks),
            "serialized_bytes": sum(
                path.stat().st_size for path in directory.rglob("*") if path.is_file()
            ),
        }

    def delta(self, previous: CognitiveCellPack) -> CellPackDelta:
        old = set(previous.manifest.cell_hashes)
        new = set(self.manifest.cell_hashes)
        return CellPackDelta(
            from_root=previous.manifest.root_hash,
            to_root=self.manifest.root_hash,
            added_blocks=tuple(sorted(new - old)),
            removed_blocks=tuple(sorted(old - new)),
            unchanged_blocks=len(old & new),
        )
