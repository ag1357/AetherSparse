from __future__ import annotations

from pathlib import Path

import pytest

from aethersparse.cells.models import CellKind, CognitiveCell
from aethersparse.cells.pack import CognitiveCellPack, content_hash


def _cell(label: str, document: str) -> CognitiveCell:
    return CognitiveCell(
        cell_id=f"cell:{label}",
        kind=CellKind.HYBRID,
        label=label,
        document_ids=(document,),
        relation_terms=(label,),
        signature_hex="00" * 128,
        source_bytes=100,
    )


def test_pack_is_deterministic_verified_and_delta_addressable(tmp_path: Path) -> None:
    first = CognitiveCellPack.compile(
        [_cell("moon", "doc:moon")],
        topology="hybrid",
        source_manifest_hash="sha256:source",
    )
    same = CognitiveCellPack.compile(
        [_cell("moon", "doc:moon")],
        topology="hybrid",
        source_manifest_hash="sha256:source",
    )
    assert first.manifest.root_hash == same.manifest.root_hash
    assert first.verify()
    stats = first.write(tmp_path / "pack")
    assert stats["serialized_bytes"] > 0

    second = CognitiveCellPack.compile(
        [_cell("moon", "doc:moon"), _cell("tide", "doc:tide")],
        topology="hybrid",
        source_manifest_hash="sha256:source",
    )
    delta = second.delta(first)
    assert delta.unchanged_blocks == 1
    assert len(delta.added_blocks) == 1
    assert not delta.removed_blocks


def test_pack_rejects_duplicate_ids_and_forged_derived_hashes() -> None:
    cell = _cell("moon", "doc:moon")
    with pytest.raises(ValueError, match="unique"):
        CognitiveCellPack.compile(
            [cell, cell], topology="hybrid", source_manifest_hash="sha256:source"
        )

    pack = CognitiveCellPack.compile(
        [cell], topology="hybrid", source_manifest_hash="sha256:source"
    )
    forged_body = pack.manifest.model_dump(mode="json", exclude={"root_hash"})
    forged_body["routing_table_hash"] = "sha256:" + "0" * 64
    forged = pack.manifest.model_copy(
        update={
            "routing_table_hash": forged_body["routing_table_hash"],
            "root_hash": content_hash(forged_body),
        }
    )
    assert not CognitiveCellPack(forged, pack.blocks).verify()
