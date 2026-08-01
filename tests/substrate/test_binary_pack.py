from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from aethersparse.substrate import (
    BinaryPackError,
    FlatBinaryPackReader,
    FlatStructuredPack,
    write_flat_binary_pack,
)


def test_binary_pack_is_reproducible_verified_and_query_bounded(
    tmp_path: Path,
    build_fixture_pack: Callable[[], FlatStructuredPack],
) -> None:
    pack = build_fixture_pack()
    first_path = tmp_path / "first.aeth"
    second_path = tmp_path / "second.aeth"
    first = write_flat_binary_pack(pack, first_path, shard_count=8)
    second = write_flat_binary_pack(pack, second_path, shard_count=8)

    assert first.file_sha256 == second.file_sha256
    assert first.manifest.root_sha256 == second.manifest.root_sha256
    assert first.manifest.metadata.series_id == "simplewiki_v050_fixture_r1"
    reader = FlatBinaryPackReader(first_path)
    verification = reader.verify_all()
    assert verification.bytes_read == first.total_bytes

    mercury_document = next(
        document for document in pack.documents if document.title == "Mercury"
    )
    mass_claim = next(claim for claim in pack.claims if claim.relation_family == "mass")
    query_read = reader.query_sections(
        text="What is Mercury mass?",
        relation_families=("mass",),
        entity_ids=(mass_claim.subject_entity_id,),
        document_ids=(mercury_document.document_id,),
        claim_ids=(mass_claim.claim_id,),
        max_sections=24,
    )

    assert query_read.trace.storage_reads <= 25  # selected shards plus one manifest read
    assert query_read.trace.bytes_read < first.total_bytes
    assert query_read.sections


def test_section_corruption_fails_closed(
    tmp_path: Path,
    build_fixture_pack: Callable[[], FlatStructuredPack],
) -> None:
    path = tmp_path / "corrupt.aeth"
    write_flat_binary_pack(build_fixture_pack(), path, shard_count=4)
    reader = FlatBinaryPackReader(path)
    descriptor = reader.manifest.sections[-1]
    with path.open("r+b") as handle:
        payload_offset = path.stat().st_size - reader.manifest.payload_bytes
        handle.seek(payload_offset + descriptor.relative_offset)
        byte = handle.read(1)
        handle.seek(payload_offset + descriptor.relative_offset)
        handle.write(bytes([byte[0] ^ 0x01]))

    with pytest.raises(BinaryPackError, match="checksum mismatch"):
        FlatBinaryPackReader(path).verify_all()


def test_declared_section_bound_fails_before_storage_reads(
    tmp_path: Path,
    build_fixture_pack: Callable[[], FlatStructuredPack],
) -> None:
    path = tmp_path / "bounded.aeth"
    write_flat_binary_pack(build_fixture_pack(), path, shard_count=8)

    with pytest.raises(BinaryPackError, match="exceeding bound"):
        FlatBinaryPackReader(path).query_sections(
            text="one two three four five six seven eight",
            max_sections=2,
        )
