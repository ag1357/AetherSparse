"""Checksum-pinned flat binary pack with bounded, instrumented section reads."""

from __future__ import annotations

import hashlib
import json
import struct
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from itertools import pairwise
from pathlib import Path
from typing import Any

from aethersparse.compiler import stable_json
from aethersparse.substrate.builder import normalize_surface, tokenize
from aethersparse.substrate.models import (
    BinaryPackArtifact,
    BinaryPackManifest,
    BinaryQueryRead,
    BinarySection,
    FlatStructuredPack,
    PackReadTrace,
    Posting,
)

MAGIC = b"AESFSP50"
PREFIX = struct.Struct(">8sI")
FORMAT_ID = "AETHERSPARSE_FLAT_STRUCTURED_PACK_V1"


class BinaryPackError(ValueError):
    """A pack is malformed, corrupt, or would exceed a declared read bound."""


def _sha256(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def shard_for_key(key: str, shard_count: int) -> int:
    return hashlib.sha256(key.encode("utf-8")).digest()[0] % shard_count


def _partition_models(
    values: Iterable[Any],
    *,
    key_name: str,
    shard_count: int,
) -> dict[int, list[dict[str, Any]]]:
    partitions: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for value in values:
        data = value.model_dump(mode="json")
        partitions[shard_for_key(str(data[key_name]), shard_count)].append(data)
    for partition in partitions.values():
        partition.sort(key=lambda item: str(item[key_name]))
    return partitions


def _partition_postings(
    postings: Sequence[Posting], shard_count: int
) -> dict[int, list[dict[str, Any]]]:
    return _partition_models(postings, key_name="key", shard_count=shard_count)


def _add_partitioned_sections(
    sections: dict[str, bytes],
    *,
    prefix: str,
    partitions: Mapping[int, list[dict[str, Any]]],
) -> None:
    for shard, records in sorted(partitions.items()):
        sections[f"{prefix}/{shard:02x}"] = stable_json(records)


def _manifest_seed(
    *,
    pack: FlatStructuredPack,
    shard_count: int,
    descriptors: tuple[BinarySection, ...],
    payload_bytes: int,
) -> dict[str, Any]:
    return {
        "format_id": FORMAT_ID,
        "pack_manifest_sha256": pack.manifest_sha256,
        "metadata": pack.metadata.model_dump(mode="json"),
        "shard_count": shard_count,
        "sections": [section.model_dump(mode="json") for section in descriptors],
        "payload_bytes": payload_bytes,
    }


def write_flat_binary_pack(
    pack: FlatStructuredPack,
    path: Path,
    *,
    shard_count: int = 64,
) -> BinaryPackArtifact:
    """Write stable JSON sections behind a compact binary directory.

    Source documents, chunks, bindings, claims, and every posting family are
    content-addressed into fixed shards. A query can therefore select bounded
    shards without reading a corpus-wide index or source block.
    """

    if not 1 <= shard_count <= 256:
        raise ValueError("shard_count must be between 1 and 256")
    sections: dict[str, bytes] = {
        "core/entities": stable_json(
            [item.model_dump(mode="json") for item in pack.entities]
        ),
        "core/aliases": stable_json(
            [item.model_dump(mode="json") for item in pack.aliases]
        ),
        "core/redirects": stable_json(
            [item.model_dump(mode="json") for item in pack.redirects]
        ),
        "core/anchors": stable_json(
            [item.model_dump(mode="json") for item in pack.anchors]
        ),
        "core/headings": stable_json(
            [item.model_dump(mode="json") for item in pack.headings]
        ),
    }
    _add_partitioned_sections(
        sections,
        prefix="documents",
        partitions=_partition_models(
            pack.documents, key_name="document_id", shard_count=shard_count
        ),
    )
    binding_partitions: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for binding in pack.source_bindings:
        binding_partitions[shard_for_key(binding.document_id, shard_count)].append(
            binding.model_dump(mode="json")
        )
    for records in binding_partitions.values():
        records.sort(key=lambda item: str(item["binding_id"]))
    _add_partitioned_sections(
        sections, prefix="bindings", partitions=binding_partitions
    )
    chunk_partitions: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for chunk in pack.chunks:
        chunk_partitions[shard_for_key(chunk.document_id, shard_count)].append(
            chunk.model_dump(mode="json")
        )
    for records in chunk_partitions.values():
        records.sort(key=lambda item: str(item["chunk_id"]))
    _add_partitioned_sections(sections, prefix="chunks", partitions=chunk_partitions)
    _add_partitioned_sections(
        sections,
        prefix="claims",
        partitions=_partition_models(pack.claims, key_name="claim_id", shard_count=shard_count),
    )
    for family in ("lexical", "title", "heading", "phrase", "relation", "entity"):
        postings = getattr(pack.indexes, family)
        _add_partitioned_sections(
            sections,
            prefix=f"index/{family}",
            partitions=_partition_postings(postings, shard_count),
        )

    descriptors: list[BinarySection] = []
    payload_parts: list[bytes] = []
    relative_offset = 0
    for name, payload in sorted(sections.items()):
        descriptors.append(
            BinarySection(
                name=name,
                relative_offset=relative_offset,
                length=len(payload),
                sha256=_sha256(payload),
            )
        )
        payload_parts.append(payload)
        relative_offset += len(payload)
    descriptor_tuple = tuple(descriptors)
    seed = _manifest_seed(
        pack=pack,
        shard_count=shard_count,
        descriptors=descriptor_tuple,
        payload_bytes=relative_offset,
    )
    manifest = BinaryPackManifest(
        **seed,
        root_sha256=_sha256(stable_json(seed)),
    )
    header = stable_json(manifest.model_dump(mode="json"))
    prefix = PREFIX.pack(MAGIC, len(header))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(prefix)
        handle.write(header)
        for payload in payload_parts:
            handle.write(payload)
    return BinaryPackArtifact(
        path=str(path),
        manifest=manifest,
        file_sha256=_sha256_file(path),
        total_bytes=path.stat().st_size,
    )


class FlatBinaryPackReader:
    """Random-access reader that verifies every selected section before returning it."""

    def __init__(self, path: Path) -> None:
        self.path = path
        with path.open("rb") as handle:
            prefix = handle.read(PREFIX.size)
            if len(prefix) != PREFIX.size:
                raise BinaryPackError("binary pack prefix is truncated")
            magic, header_length = PREFIX.unpack(prefix)
            if magic != MAGIC:
                raise BinaryPackError("binary pack magic mismatch")
            if header_length <= 0 or header_length > 64 * 1024 * 1024:
                raise BinaryPackError("binary pack header length is invalid")
            header = handle.read(header_length)
            if len(header) != header_length:
                raise BinaryPackError("binary pack manifest is truncated")
        try:
            self.manifest = BinaryPackManifest.model_validate_json(header)
        except (ValueError, json.JSONDecodeError) as exc:
            raise BinaryPackError("binary pack manifest is invalid") from exc
        seed = self.manifest.model_dump(mode="json", exclude={"root_sha256"})
        if _sha256(stable_json(seed)) != self.manifest.root_sha256:
            raise BinaryPackError("binary pack manifest root checksum mismatch")
        self._payload_offset = PREFIX.size + header_length
        self._manifest_read_bytes = self._payload_offset
        self._sections = {section.name: section for section in self.manifest.sections}
        if len(self._sections) != len(self.manifest.sections):
            raise BinaryPackError("binary pack has duplicate section names")
        file_size = path.stat().st_size
        if file_size != self._payload_offset + self.manifest.payload_bytes:
            raise BinaryPackError("binary pack byte count differs from manifest")

    def read_sections(
        self,
        section_names: Iterable[str],
        *,
        max_sections: int,
        include_manifest_read: bool = True,
    ) -> BinaryQueryRead:
        names = tuple(sorted(set(section_names)))
        if len(names) > max_sections:
            raise BinaryPackError(
                f"query requested {len(names)} sections, exceeding bound {max_sections}"
            )
        selected: list[tuple[str, bytes]] = []
        with self.path.open("rb") as handle:
            for name in names:
                descriptor = self._sections.get(name)
                if descriptor is None:
                    # Sparse shards are legitimately absent.
                    continue
                handle.seek(self._payload_offset + descriptor.relative_offset)
                payload = handle.read(descriptor.length)
                if len(payload) != descriptor.length:
                    raise BinaryPackError(f"binary section is truncated: {name}")
                if _sha256(payload) != descriptor.sha256:
                    raise BinaryPackError(f"binary section checksum mismatch: {name}")
                selected.append((name, payload))
        manifest_reads = int(include_manifest_read)
        manifest_bytes = self._manifest_read_bytes if include_manifest_read else 0
        return BinaryQueryRead(
            sections=tuple(selected),
            trace=PackReadTrace(
                section_names=tuple(name for name, _ in selected),
                storage_reads=len(selected) + manifest_reads,
                bytes_read=sum(len(payload) for _, payload in selected) + manifest_bytes,
            ),
        )

    def query_sections(
        self,
        *,
        text: str,
        relation_families: Sequence[str] = (),
        entity_ids: Sequence[str] = (),
        document_ids: Sequence[str] = (),
        claim_ids: Sequence[str] = (),
        max_sections: int = 32,
    ) -> BinaryQueryRead:
        """Resolve deterministic query keys to only their fixed posting/data shards."""

        shard_count = self.manifest.shard_count
        names: set[str] = set()
        terms = tuple(dict.fromkeys(tokenize(text)))
        phrases = tuple(
            dict.fromkeys(f"{left} {right}" for left, right in pairwise(terms))
        )
        for term in terms:
            shard = shard_for_key(term, shard_count)
            names.update(
                {
                    f"index/lexical/{shard:02x}",
                    f"index/title/{shard:02x}",
                    f"index/heading/{shard:02x}",
                }
            )
        for phrase in phrases:
            shard = shard_for_key(phrase, shard_count)
            names.add(f"index/phrase/{shard:02x}")
        for relation in relation_families:
            key = normalize_surface(relation)
            names.add(f"index/relation/{shard_for_key(key, shard_count):02x}")
        for entity_id in entity_ids:
            names.add(f"index/entity/{shard_for_key(entity_id, shard_count):02x}")
        for document_id in document_ids:
            shard = shard_for_key(document_id, shard_count)
            names.update(
                {
                    f"documents/{shard:02x}",
                    f"bindings/{shard:02x}",
                    f"chunks/{shard:02x}",
                }
            )
        for claim_id in claim_ids:
            names.add(f"claims/{shard_for_key(claim_id, shard_count):02x}")
        return self.read_sections(names, max_sections=max_sections)

    def verify_all(self) -> PackReadTrace:
        result = self.read_sections(
            self._sections, max_sections=len(self._sections), include_manifest_read=True
        )
        return result.trace
