"""Immutable exact-surface address dictionary with content-addressed postings.

The dictionary is a path-compressed bytewise acyclic finite-state transducer:
normalized UTF-8 surface byte strings are transitions and terminal states
output a posting-group index.
The output is always a distribution over exact canonical entity IDs (plus
explicit unresolved mass), never an implicitly selected entity.

Empirical priors are stored losslessly as integer ``support / total_support``.
This is both smaller and more faithful than quantizing probabilities; no prior
quantization is used by this format.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import IntFlag, StrEnum
from pathlib import Path
from typing import Any

MAGIC = b"AESAFS12"
FORMAT_VERSION = 1
SCHEMA_VERSION = "aethersparse.exact-address-index.v12"
MANIFEST_SCHEMA_VERSION = "aethersparse.exact-address-index-manifest.v12"
PREFIX = struct.Struct(">8sII")
STATE = struct.Struct(">IIi")
ARC = struct.Struct(">IHI")
GROUP = struct.Struct(">QIQQIddII")
POSTING = struct.Struct(">IQIIIIIBII")
UINT32 = struct.Struct(">I")
NO_GROUP = -1
SEALED_PARTITIONS = frozenset({"evaluation", "final_held"})


class AddressIndexError(ValueError):
    """The address input, serialized index, or manifest is invalid."""


class AddressChannel(StrEnum):
    """Auditable deterministic evidence channels."""

    TITLE = "title"
    REDIRECT = "redirect"
    ALIAS = "alias"
    ANCHOR = "anchor"


class _ChannelFlag(IntFlag):
    TITLE = 1
    REDIRECT = 2
    ALIAS = 4
    ANCHOR = 8


_CHANNEL_FLAG = {
    AddressChannel.TITLE: _ChannelFlag.TITLE,
    AddressChannel.REDIRECT: _ChannelFlag.REDIRECT,
    AddressChannel.ALIAS: _ChannelFlag.ALIAS,
    AddressChannel.ANCHOR: _ChannelFlag.ANCHOR,
}


@dataclass(frozen=True)
class AddressEvidence:
    """One exact surface-to-address or unresolved occurrence aggregate.

    ``source_document_ids`` are used only for exact source-diversity counting.
    ``provenance_ids`` are retained in postings and should name immutable source
    records or hashes. An unresolved row has no ``entity_id``/canonical title
    and must carry a stable ``unresolved_key`` so distinct unresolved targets
    remain distinct for ambiguity entropy.
    """

    surface: str
    entity_id: str | None
    canonical_title: str | None
    support_count: int
    source_document_ids: tuple[str, ...]
    channel: AddressChannel
    provenance_ids: tuple[str, ...]
    unresolved_key: str | None = None

    def __post_init__(self) -> None:
        if not normalize_surface(self.surface):
            raise AddressIndexError("address surface normalizes to empty")
        if self.support_count < 1:
            raise AddressIndexError("support_count must be positive")
        if not self.source_document_ids or any(not value for value in self.source_document_ids):
            raise AddressIndexError("source_document_ids must be non-empty strings")
        if len(set(self.source_document_ids)) != len(self.source_document_ids):
            raise AddressIndexError("source_document_ids contain duplicates")
        if len(self.source_document_ids) > self.support_count:
            raise AddressIndexError("source-document diversity exceeds support")
        if not self.provenance_ids or any(not value for value in self.provenance_ids):
            raise AddressIndexError("provenance_ids must be non-empty strings")
        if len(set(self.provenance_ids)) != len(self.provenance_ids):
            raise AddressIndexError("provenance_ids contain duplicates")
        resolved = self.entity_id is not None
        if resolved != (self.canonical_title is not None):
            raise AddressIndexError(
                "entity_id and canonical_title must both be set or both be null"
            )
        if resolved and self.unresolved_key is not None:
            raise AddressIndexError("resolved evidence cannot carry unresolved_key")
        if not resolved and not self.unresolved_key:
            raise AddressIndexError("unresolved evidence requires unresolved_key")
        if self.entity_id == "" or self.canonical_title == "":
            raise AddressIndexError("canonical entity fields must be non-empty")


@dataclass(frozen=True)
class AddressPosting:
    """One authoritative address hypothesis returned by exact lookup."""

    entity_id: str
    canonical_title: str
    prior: float
    support_count: int
    source_document_count: int
    source_diversity: float
    title_support_count: int
    redirect_support_count: int
    alias_support_count: int
    anchor_support_count: int
    channels: tuple[AddressChannel, ...]
    provenance_ids: tuple[str, ...]


@dataclass(frozen=True)
class ExactAddressLookup:
    """A surface distribution, including any explicit cap loss."""

    surface: str
    normalized_surface: str
    posting_offset: int
    postings: tuple[AddressPosting, ...]
    total_candidate_count: int
    omitted_candidate_count: int
    omitted_probability_mass: float
    unresolved_probability_mass: float
    unresolved_provenance_ids: tuple[str, ...]
    total_support: int
    ambiguity_count: int
    ambiguity_entropy_nats: float

    @property
    def returned_probability_mass(self) -> float:
        return sum(posting.prior for posting in self.postings)

    @property
    def probability_mass(self) -> float:
        return (
            self.returned_probability_mass
            + self.omitted_probability_mass
            + self.unresolved_probability_mass
        )

    @property
    def truncated(self) -> bool:
        return self.omitted_candidate_count > 0


@dataclass(frozen=True)
class AddressIndexArtifact:
    """Identity and measured footprint of a serialized exact-address index."""

    path: str
    manifest_path: str
    root_sha256: str
    file_sha256: str
    manifest_sha256: str
    total_bytes: int
    header_bytes: int
    dictionary_bytes: int
    posting_bytes: int
    provenance_bytes: int
    address_core_bytes_excluding_provenance: int
    surface_count: int
    entity_count: int
    posting_count: int


@dataclass
class _PostingAccumulator:
    canonical_title: str
    support: int
    source_documents: set[str]
    channel_support: Counter[AddressChannel]
    provenance: set[str]


@dataclass
class _SurfaceAccumulator:
    postings: dict[str, _PostingAccumulator]
    unresolved_support: dict[str, int]
    unresolved_provenance: set[str]


@dataclass
class _TrieNode:
    children: dict[int, _TrieNode]
    group_index: int


@dataclass
class _RadixNode:
    children: dict[int, tuple[bytes, _RadixNode]]
    group_index: int


def normalize_surface(value: str) -> str:
    """Return the exact-channel lookup form without lossy transliteration."""

    return " ".join(unicodedata.normalize("NFKC", value.replace("_", " ")).casefold().split())


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        + b"\n"
    )


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise AddressIndexError(f"{field} must be a SHA-256 hex digest")
    try:
        int(value, 16)
    except ValueError as error:
        raise AddressIndexError(f"{field} must be a SHA-256 hex digest") from error
    return value


def _pack_strings(values: Sequence[str]) -> bytes:
    parts = [UINT32.pack(len(values))]
    for value in values:
        encoded = value.encode("utf-8")
        if len(encoded) > 0xFFFFFFFF:
            raise AddressIndexError("serialized string exceeds uint32 length")
        parts.extend((UINT32.pack(len(encoded)), encoded))
    return b"".join(parts)


def _unpack_strings(payload: bytes, section: str) -> tuple[str, ...]:
    if len(payload) < UINT32.size:
        raise AddressIndexError(f"{section} string table is truncated")
    count = UINT32.unpack_from(payload, 0)[0]
    cursor = UINT32.size
    output: list[str] = []
    for _ in range(count):
        if cursor + UINT32.size > len(payload):
            raise AddressIndexError(f"{section} string length is truncated")
        length = UINT32.unpack_from(payload, cursor)[0]
        cursor += UINT32.size
        if cursor + length > len(payload):
            raise AddressIndexError(f"{section} string content is truncated")
        try:
            output.append(payload[cursor : cursor + length].decode("utf-8"))
        except UnicodeDecodeError as error:
            raise AddressIndexError(f"{section} contains invalid UTF-8") from error
        cursor += length
    if cursor != len(payload):
        raise AddressIndexError(f"{section} has trailing bytes")
    return tuple(output)


def _encode_varint(value: int) -> bytes:
    if value < 0:
        raise AddressIndexError("cannot encode a negative varint")
    output = bytearray()
    while value >= 0x80:
        output.append((value & 0x7F) | 0x80)
        value >>= 7
    output.append(value)
    return bytes(output)


def _decode_varint(payload: bytes, offset: int, section: str) -> tuple[int, int]:
    value = 0
    shift = 0
    cursor = offset
    while cursor < len(payload) and shift <= 63:
        byte = payload[cursor]
        cursor += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, cursor
        shift += 7
    raise AddressIndexError(f"{section} contains a truncated or oversized varint")


def _pack_front_coded_strings(values: Sequence[str]) -> bytes:
    parts = [UINT32.pack(len(values))]
    prior = b""
    for value in values:
        encoded = value.encode("utf-8")
        common = 0
        common_limit = min(len(prior), len(encoded))
        while common < common_limit and prior[common] == encoded[common]:
            common += 1
        suffix = encoded[common:]
        parts.extend((_encode_varint(common), _encode_varint(len(suffix)), suffix))
        prior = encoded
    return b"".join(parts)


def _unpack_front_coded_strings(payload: bytes, section: str) -> tuple[str, ...]:
    if len(payload) < UINT32.size:
        raise AddressIndexError(f"{section} string table is truncated")
    count = UINT32.unpack_from(payload, 0)[0]
    cursor = UINT32.size
    prior = b""
    output: list[str] = []
    for _ in range(count):
        common, cursor = _decode_varint(payload, cursor, section)
        suffix_length, cursor = _decode_varint(payload, cursor, section)
        if common > len(prior) or cursor + suffix_length > len(payload):
            raise AddressIndexError(f"{section} front-coded entry is invalid")
        encoded = prior[:common] + payload[cursor : cursor + suffix_length]
        cursor += suffix_length
        try:
            output.append(encoded.decode("utf-8"))
        except UnicodeDecodeError as error:
            raise AddressIndexError(f"{section} contains invalid UTF-8") from error
        prior = encoded
    if cursor != len(payload):
        raise AddressIndexError(f"{section} has trailing bytes")
    return tuple(output)


def _aggregate(evidence: Iterable[AddressEvidence]) -> dict[str, _SurfaceAccumulator]:
    surfaces: dict[str, _SurfaceAccumulator] = {}
    entity_titles: dict[str, str] = {}
    row_count = 0
    for row in evidence:
        row_count += 1
        surface = normalize_surface(row.surface)
        group = surfaces.setdefault(surface, _SurfaceAccumulator({}, {}, set()))
        if row.entity_id is None:
            assert row.unresolved_key is not None
            group.unresolved_support[row.unresolved_key] = (
                group.unresolved_support.get(row.unresolved_key, 0) + row.support_count
            )
            group.unresolved_provenance.update(row.provenance_ids)
            continue
        assert row.canonical_title is not None
        prior_title = entity_titles.setdefault(row.entity_id, row.canonical_title)
        if prior_title != row.canonical_title:
            raise AddressIndexError(f"canonical entity {row.entity_id!r} has conflicting titles")
        posting = group.postings.get(row.entity_id)
        if posting is None:
            posting = _PostingAccumulator(row.canonical_title, 0, set(), Counter(), set())
            group.postings[row.entity_id] = posting
        elif posting.canonical_title != row.canonical_title:
            raise AddressIndexError("surface posting has conflicting canonical titles")
        posting.support += row.support_count
        posting.source_documents.update(row.source_document_ids)
        posting.channel_support[row.channel] += row.support_count
        posting.provenance.update(row.provenance_ids)
    if row_count == 0:
        raise AddressIndexError("cannot compile an empty exact-address index")
    for surface, group in surfaces.items():
        if not group.postings and not group.unresolved_support:
            raise AddressIndexError(f"surface {surface!r} contains no evidence")
    return surfaces


def _compress_trie(node: _TrieNode) -> _RadixNode:
    children: dict[int, tuple[bytes, _RadixNode]] = {}
    for first_label, first_child in sorted(node.children.items()):
        labels = bytearray((first_label,))
        child = first_child
        while child.group_index == NO_GROUP and len(child.children) == 1:
            label, next_child = next(iter(child.children.items()))
            labels.append(label)
            child = next_child
        if len(labels) > 0xFFFF:
            raise AddressIndexError("normalized address surface exceeds radix arc bound")
        children[first_label] = (bytes(labels), _compress_trie(child))
    return _RadixNode(children, node.group_index)


def _build_trie(surfaces: Sequence[str]) -> tuple[bytes, bytes, bytes]:
    root = _TrieNode({}, NO_GROUP)
    for group_index, surface in enumerate(surfaces):
        trie_node = root
        for label in surface.encode("utf-8"):
            trie_node = trie_node.children.setdefault(label, _TrieNode({}, NO_GROUP))
        if trie_node.group_index != NO_GROUP:
            raise AddressIndexError("normalized duplicate surface entered dictionary")
        trie_node.group_index = group_index

    radix_root = _compress_trie(root)
    nodes: list[_RadixNode] = [radix_root]
    node_index = {id(radix_root): 0}
    cursor = 0
    while cursor < len(nodes):
        radix_node = nodes[cursor]
        for _, (_, child) in sorted(radix_node.children.items()):
            if id(child) not in node_index:
                node_index[id(child)] = len(nodes)
                nodes.append(child)
        cursor += 1

    state_parts: list[bytes] = []
    arc_parts: list[bytes] = []
    label_parts: list[bytes] = []
    arc_cursor = 0
    label_cursor = 0
    for radix_node in nodes:
        children = sorted(radix_node.children.items())
        state_parts.append(STATE.pack(arc_cursor, len(children), radix_node.group_index))
        for _, (labels, child) in children:
            arc_parts.append(ARC.pack(label_cursor, len(labels), node_index[id(child)]))
            label_parts.append(labels)
            label_cursor += len(labels)
        arc_cursor += len(children)
    return b"".join(state_parts), b"".join(arc_parts), b"".join(label_parts)


def _manifest_seed(
    *,
    source_artifact_sha256: str,
    source_partitions: Sequence[str],
    sealed_partitions_excluded: Sequence[str],
    sections: Sequence[Mapping[str, object]],
    counts: Mapping[str, int],
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "format_version": FORMAT_VERSION,
        "prior_encoding": "lossless_integer_support_ratio",
        "source_artifact_sha256": source_artifact_sha256,
        "source_partitions": list(source_partitions),
        "sealed_partitions_excluded": list(sealed_partitions_excluded),
        "sections": list(sections),
        "counts": dict(sorted(counts.items())),
    }


def compile_exact_address_index(
    evidence: Iterable[AddressEvidence],
    output_path: Path,
    *,
    source_artifact_sha256: str,
    source_partitions: Sequence[str] = (),
    sealed_partitions_excluded: Sequence[str] = ("evaluation", "final_held"),
) -> AddressIndexArtifact:
    """Compile deterministic exact-address evidence into an immutable index."""

    source_digest = _require_sha256(source_artifact_sha256, "source_artifact_sha256")
    partitions = tuple(sorted(set(source_partitions)))
    excluded = tuple(sorted(set(sealed_partitions_excluded)))
    if set(partitions) & SEALED_PARTITIONS:
        raise AddressIndexError("sealed evaluation/final-held partition entered exact index")
    if not SEALED_PARTITIONS.issubset(excluded):
        raise AddressIndexError("manifest must explicitly exclude evaluation/final-held")

    grouped = _aggregate(evidence)
    surfaces = tuple(sorted(grouped))
    states, arcs, labels = _build_trie(surfaces)
    entity_pairs = sorted(
        {
            (entity_id, posting.canonical_title)
            for group in grouped.values()
            for entity_id, posting in group.postings.items()
        }
    )
    entity_index = {entity_id: index for index, (entity_id, _) in enumerate(entity_pairs)}
    entity_strings: list[str] = []
    for entity_id, title in entity_pairs:
        entity_strings.extend((entity_id, title))

    all_provenance = sorted(
        {
            value
            for group in grouped.values()
            for value in (
                set(group.unresolved_provenance)
                | {item for posting in group.postings.values() for item in posting.provenance}
            )
        }
    )
    provenance_index = {value: index for index, value in enumerate(all_provenance)}
    provenance_refs = bytearray()
    provenance_ref_count = 0
    posting_parts: list[bytes] = []
    group_parts: list[bytes] = []
    posting_offset = 0
    posting_count = 0
    for surface in surfaces:
        group = grouped[surface]
        resolved_support = sum(posting.support for posting in group.postings.values())
        unresolved_support = sum(group.unresolved_support.values())
        total_support = resolved_support + unresolved_support
        outcomes = [posting.support for posting in group.postings.values()]
        outcomes.extend(group.unresolved_support.values())
        entropy = -sum(
            (support / total_support) * math.log(support / total_support) for support in outcomes
        )
        ordered_postings = sorted(
            group.postings.items(), key=lambda item: (-item[1].support, item[0])
        )
        group_posting_offset = posting_offset
        for entity_id, posting in ordered_postings:
            channels = posting.channel_support
            flags = _ChannelFlag(0)
            for channel in channels:
                flags |= _CHANNEL_FLAG[channel]
            references = sorted(provenance_index[value] for value in posting.provenance)
            reference_start = len(provenance_refs)
            for reference in references:
                provenance_refs.extend(_encode_varint(reference))
            provenance_ref_count += len(references)
            payload = POSTING.pack(
                entity_index[entity_id],
                posting.support,
                len(posting.source_documents),
                channels[AddressChannel.TITLE],
                channels[AddressChannel.REDIRECT],
                channels[AddressChannel.ALIAS],
                channels[AddressChannel.ANCHOR],
                int(flags),
                reference_start,
                len(references),
            )
            posting_parts.append(payload)
            posting_offset += len(payload)
            posting_count += 1
        unresolved_refs = sorted(provenance_index[value] for value in group.unresolved_provenance)
        unresolved_ref_start = len(provenance_refs)
        for reference in unresolved_refs:
            provenance_refs.extend(_encode_varint(reference))
        provenance_ref_count += len(unresolved_refs)
        group_parts.append(
            GROUP.pack(
                group_posting_offset,
                len(ordered_postings),
                total_support,
                unresolved_support,
                len(outcomes),
                entropy,
                unresolved_support / total_support,
                unresolved_ref_start,
                len(unresolved_refs),
            )
        )

    sections = {
        "arcs": arcs,
        "entities": _pack_strings(entity_strings),
        "groups": b"".join(group_parts),
        "labels": labels,
        "postings": b"".join(posting_parts),
        "provenance_refs": bytes(provenance_refs),
        "provenance_strings": _pack_front_coded_strings(all_provenance),
        "states": states,
    }
    descriptors: list[dict[str, object]] = []
    payloads: list[bytes] = []
    offset = 0
    for name, payload in sorted(sections.items()):
        descriptors.append(
            {
                "name": name,
                "relative_offset": offset,
                "length": len(payload),
                "sha256": _sha256(payload),
            }
        )
        payloads.append(payload)
        offset += len(payload)
    counts = {
        "arc_count": len(arcs) // ARC.size,
        "entity_count": len(entity_pairs),
        "group_count": len(surfaces),
        "posting_count": posting_count,
        "provenance_count": len(all_provenance),
        "provenance_ref_count": provenance_ref_count,
        "state_count": len(states) // STATE.size,
        "surface_count": len(surfaces),
    }
    seed = _manifest_seed(
        source_artifact_sha256=source_digest,
        source_partitions=partitions,
        sealed_partitions_excluded=excluded,
        sections=descriptors,
        counts=counts,
    )
    root_sha256 = _sha256(_canonical_json(seed))
    header = _canonical_json({**seed, "root_sha256": root_sha256})
    file_bytes = PREFIX.pack(MAGIC, FORMAT_VERSION, len(header)) + header + b"".join(payloads)
    file_sha256 = _sha256(file_bytes)
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "file": output_path.name,
        "file_sha256": file_sha256,
        "file_bytes": len(file_bytes),
        "header_sha256": _sha256(header),
        "root_sha256": root_sha256,
        "source_artifact_sha256": source_digest,
        "source_partitions": list(partitions),
        "sealed_partitions_excluded": list(excluded),
        "counts": counts,
    }
    manifest_bytes = _canonical_json(manifest)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(file_bytes)
    manifest_path = output_path.with_suffix(output_path.suffix + ".manifest.json")
    manifest_path.write_bytes(manifest_bytes)
    dictionary_bytes = len(states) + len(arcs) + len(labels) + len(sections["groups"])
    provenance_bytes = len(sections["provenance_refs"]) + len(sections["provenance_strings"])
    return AddressIndexArtifact(
        path=str(output_path),
        manifest_path=str(manifest_path),
        root_sha256=root_sha256,
        file_sha256=file_sha256,
        manifest_sha256=_sha256(manifest_bytes),
        total_bytes=len(file_bytes),
        header_bytes=PREFIX.size + len(header),
        dictionary_bytes=dictionary_bytes,
        posting_bytes=len(sections["postings"]) + len(sections["entities"]),
        provenance_bytes=provenance_bytes,
        address_core_bytes_excluding_provenance=(len(file_bytes) - provenance_bytes),
        surface_count=len(surfaces),
        entity_count=len(entity_pairs),
        posting_count=posting_count,
    )


class ExactAddressIndex:
    """Verified immutable reader for a compiled exact-address index."""

    def __init__(self, path: Path, manifest_path: Path | None = None) -> None:
        self.path = path
        raw = path.read_bytes()
        if len(raw) < PREFIX.size:
            raise AddressIndexError("exact-address file prefix is truncated")
        magic, version, header_length = PREFIX.unpack_from(raw, 0)
        if magic != MAGIC or version != FORMAT_VERSION:
            raise AddressIndexError("unsupported exact-address file format")
        header_start = PREFIX.size
        header_end = header_start + header_length
        if header_end > len(raw):
            raise AddressIndexError("exact-address header is truncated")
        header_bytes = raw[header_start:header_end]
        try:
            header: Any = json.loads(header_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AddressIndexError("exact-address header is invalid JSON") from error
        if not isinstance(header, dict) or header.get("schema_version") != SCHEMA_VERSION:
            raise AddressIndexError("unsupported exact-address schema")
        root = _require_sha256(header.get("root_sha256"), "root_sha256")
        seed = {key: value for key, value in header.items() if key != "root_sha256"}
        if _sha256(_canonical_json(seed)) != root:
            raise AddressIndexError("exact-address root hash mismatch")
        descriptors = header.get("sections")
        if not isinstance(descriptors, list):
            raise AddressIndexError("exact-address section directory is malformed")
        self._sections: dict[str, tuple[int, bytes]] = {}
        previous_end = 0
        for descriptor in descriptors:
            if not isinstance(descriptor, dict):
                raise AddressIndexError("exact-address section descriptor is malformed")
            name = descriptor.get("name")
            relative_offset = descriptor.get("relative_offset")
            length = descriptor.get("length")
            if (
                not isinstance(name, str)
                or isinstance(relative_offset, bool)
                or not isinstance(relative_offset, int)
                or isinstance(length, bool)
                or not isinstance(length, int)
                or relative_offset != previous_end
                or length < 0
            ):
                raise AddressIndexError("exact-address section layout is malformed")
            start = header_end + relative_offset
            end = start + length
            if end > len(raw):
                raise AddressIndexError("exact-address section is truncated")
            payload = raw[start:end]
            if _sha256(payload) != _require_sha256(descriptor.get("sha256"), "section sha256"):
                raise AddressIndexError(f"exact-address section checksum mismatch: {name}")
            if name in self._sections:
                raise AddressIndexError("duplicate exact-address section")
            self._sections[name] = (start, payload)
            previous_end += length
        if header_end + previous_end != len(raw):
            raise AddressIndexError("exact-address file has unaddressed trailing bytes")
        required = {
            "arcs",
            "entities",
            "groups",
            "labels",
            "postings",
            "provenance_refs",
            "provenance_strings",
            "states",
        }
        if set(self._sections) != required:
            raise AddressIndexError("exact-address section set is incomplete")
        counts = header.get("counts")
        if not isinstance(counts, dict):
            raise AddressIndexError("exact-address counts are malformed")
        self._counts = {
            key: self._count(counts, key)
            for key in (
                "arc_count",
                "entity_count",
                "group_count",
                "posting_count",
                "provenance_count",
                "provenance_ref_count",
                "state_count",
                "surface_count",
            )
        }
        self.root_sha256 = root
        self.source_artifact_sha256 = _require_sha256(
            header.get("source_artifact_sha256"), "source_artifact_sha256"
        )
        self.source_partitions = self._string_tuple(header, "source_partitions")
        self.sealed_partitions_excluded = self._string_tuple(header, "sealed_partitions_excluded")
        if set(self.source_partitions) & SEALED_PARTITIONS:
            raise AddressIndexError("sealed partition is declared in exact-address source")
        if not SEALED_PARTITIONS.issubset(self.sealed_partitions_excluded):
            raise AddressIndexError("exact-address header does not seal protected partitions")
        self._validate_lengths()
        self._entities = self._load_entities()
        self._provenance = _unpack_front_coded_strings(
            self._sections["provenance_strings"][1], "provenance_strings"
        )
        if len(self._provenance) != self._counts["provenance_count"]:
            raise AddressIndexError("provenance count disagrees with string table")
        self._validate_reference_stream()
        if manifest_path is not None:
            self._validate_manifest(manifest_path, raw, header_bytes)

    @staticmethod
    def _count(counts: Mapping[str, object], key: str) -> int:
        value = counts.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise AddressIndexError(f"exact-address count {key} is invalid")
        return value

    @staticmethod
    def _string_tuple(mapping: Mapping[str, object], key: str) -> tuple[str, ...]:
        value = mapping.get(key)
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise AddressIndexError(f"exact-address {key} is malformed")
        return tuple(value)

    def _validate_lengths(self) -> None:
        lengths = {
            "arcs": self._counts["arc_count"] * ARC.size,
            "groups": self._counts["group_count"] * GROUP.size,
            "postings": self._counts["posting_count"] * POSTING.size,
            "states": self._counts["state_count"] * STATE.size,
        }
        for name, expected in lengths.items():
            if len(self._sections[name][1]) != expected:
                raise AddressIndexError(f"exact-address {name} length disagrees with counts")
        if self._counts["surface_count"] != self._counts["group_count"]:
            raise AddressIndexError("surface and group counts disagree")

    def _validate_reference_stream(self) -> None:
        payload = self._sections["provenance_refs"][1]
        cursor = 0
        count = 0
        while cursor < len(payload):
            reference, cursor = _decode_varint(payload, cursor, "provenance_refs")
            if reference >= len(self._provenance):
                raise AddressIndexError("provenance reference points outside string table")
            count += 1
        if count != self._counts["provenance_ref_count"]:
            raise AddressIndexError("provenance reference count disagrees with stream")

    def _load_entities(self) -> tuple[tuple[str, str], ...]:
        values = _unpack_strings(self._sections["entities"][1], "entities")
        if len(values) != 2 * self._counts["entity_count"]:
            raise AddressIndexError("entity count disagrees with string table")
        return tuple(zip(values[::2], values[1::2], strict=True))

    def _validate_manifest(self, manifest_path: Path, raw: bytes, header: bytes) -> None:
        manifest_bytes = manifest_path.read_bytes()
        try:
            manifest: Any = json.loads(manifest_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AddressIndexError("exact-address manifest is invalid JSON") from error
        if not isinstance(manifest, dict):
            raise AddressIndexError("exact-address manifest must be an object")
        if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
            raise AddressIndexError("unsupported exact-address manifest schema")
        checks = {
            "file_sha256": _sha256(raw),
            "file_bytes": len(raw),
            "header_sha256": _sha256(header),
            "root_sha256": self.root_sha256,
            "source_artifact_sha256": self.source_artifact_sha256,
            "source_partitions": list(self.source_partitions),
            "sealed_partitions_excluded": list(self.sealed_partitions_excluded),
            "counts": self._counts,
        }
        for field, observed in checks.items():
            if manifest.get(field) != observed:
                raise AddressIndexError(f"exact-address manifest mismatch for {field}")

    @property
    def surface_count(self) -> int:
        return self._counts["surface_count"]

    @property
    def entity_count(self) -> int:
        return self._counts["entity_count"]

    def _transition(
        self, state_index: int, surface: bytes, position: int
    ) -> tuple[int, int] | None:
        states = self._sections["states"][1]
        arcs = self._sections["arcs"][1]
        labels = self._sections["labels"][1]
        if state_index >= self._counts["state_count"]:
            raise AddressIndexError("state transition points outside dictionary")
        first, count, _ = STATE.unpack_from(states, state_index * STATE.size)
        wanted = surface[position]
        low = 0
        high = count
        while low < high:
            middle = (low + high) // 2
            arc_index = first + middle
            if arc_index >= self._counts["arc_count"]:
                raise AddressIndexError("state arc range points outside dictionary")
            label_offset, label_length, _ = ARC.unpack_from(arcs, arc_index * ARC.size)
            if label_length < 1 or label_offset + label_length > len(labels):
                raise AddressIndexError("radix arc points outside label section")
            observed = labels[label_offset]
            if observed < wanted:
                low = middle + 1
            else:
                high = middle
        if low >= count:
            return None
        label_offset, label_length, target = ARC.unpack_from(arcs, (first + low) * ARC.size)
        if label_offset + label_length > len(labels):
            raise AddressIndexError("radix arc points outside label section")
        arc_label = labels[label_offset : label_offset + label_length]
        if not arc_label or arc_label[0] != wanted:
            return None
        if surface[position : position + label_length] != arc_label:
            return None
        return target, label_length

    def lookup(self, surface: str, *, max_postings: int | None = None) -> ExactAddressLookup | None:
        """Return the exact address distribution or ``None`` for an unknown surface.

        A caller cap is explicit: omitted candidate count and probability mass
        remain in the result, so truncation cannot masquerade as resolution.
        """

        if max_postings is not None and max_postings < 1:
            raise AddressIndexError("max_postings must be positive")
        normalized = normalize_surface(surface)
        if not normalized:
            return None
        state_index = 0
        encoded = normalized.encode("utf-8")
        position = 0
        while position < len(encoded):
            transition = self._transition(state_index, encoded, position)
            if transition is None:
                return None
            state_index, consumed = transition
            position += consumed
        states = self._sections["states"][1]
        if state_index >= self._counts["state_count"]:
            raise AddressIndexError("terminal state points outside dictionary")
        _, _, group_index = STATE.unpack_from(states, state_index * STATE.size)
        if group_index == NO_GROUP:
            return None
        if group_index < 0 or group_index >= self._counts["group_count"]:
            raise AddressIndexError("terminal group points outside dictionary")
        groups = self._sections["groups"][1]
        (
            relative_posting_offset,
            posting_count,
            total_support,
            unresolved_support,
            ambiguity_count,
            entropy,
            unresolved_mass,
            unresolved_provenance_start,
            unresolved_provenance_count,
        ) = GROUP.unpack_from(groups, group_index * GROUP.size)
        if relative_posting_offset + posting_count * POSTING.size > len(
            self._sections["postings"][1]
        ):
            raise AddressIndexError("posting group points outside posting section")
        limit = posting_count if max_postings is None else min(max_postings, posting_count)
        returned: list[AddressPosting] = []
        omitted_mass = 0.0
        for index in range(posting_count):
            posting = self._posting(relative_posting_offset + index * POSTING.size, total_support)
            if index < limit:
                returned.append(posting)
            else:
                omitted_mass += posting.prior
        if not math.isclose(
            sum(item.prior for item in returned) + omitted_mass + unresolved_mass,
            1.0,
            abs_tol=1e-12,
        ):
            raise AddressIndexError("posting distribution mass is inconsistent")
        absolute_posting_offset = self._sections["postings"][0] + relative_posting_offset
        return ExactAddressLookup(
            surface=surface,
            normalized_surface=normalized,
            posting_offset=absolute_posting_offset,
            postings=tuple(returned),
            total_candidate_count=posting_count,
            omitted_candidate_count=posting_count - limit,
            omitted_probability_mass=omitted_mass,
            unresolved_probability_mass=unresolved_support / total_support,
            unresolved_provenance_ids=self._provenance_slice(
                unresolved_provenance_start, unresolved_provenance_count
            ),
            total_support=total_support,
            ambiguity_count=ambiguity_count,
            ambiguity_entropy_nats=entropy,
        )

    def _posting(self, relative_offset: int, total_support: int) -> AddressPosting:
        postings = self._sections["postings"][1]
        (
            entity_index,
            support,
            source_document_count,
            title_support,
            redirect_support,
            alias_support,
            anchor_support,
            flags_value,
            provenance_start,
            provenance_count,
        ) = POSTING.unpack_from(postings, relative_offset)
        if entity_index >= len(self._entities):
            raise AddressIndexError("posting points outside entity registry")
        if support < 1 or source_document_count > support:
            raise AddressIndexError("posting support is invalid")
        channel_total = title_support + redirect_support + alias_support + anchor_support
        if channel_total != support:
            raise AddressIndexError("posting channel support disagrees with support")
        flags = _ChannelFlag(flags_value)
        expected_flags = _ChannelFlag(0)
        channel_counts = (
            (AddressChannel.TITLE, title_support),
            (AddressChannel.REDIRECT, redirect_support),
            (AddressChannel.ALIAS, alias_support),
            (AddressChannel.ANCHOR, anchor_support),
        )
        channels: list[AddressChannel] = []
        for channel, count in channel_counts:
            if count:
                expected_flags |= _CHANNEL_FLAG[channel]
                channels.append(channel)
        if flags != expected_flags:
            raise AddressIndexError("posting flags disagree with channel support")
        provenance = self._provenance_slice(provenance_start, provenance_count)
        entity_id, canonical_title = self._entities[entity_index]
        return AddressPosting(
            entity_id=entity_id,
            canonical_title=canonical_title,
            prior=support / total_support,
            support_count=support,
            source_document_count=source_document_count,
            source_diversity=source_document_count / support,
            title_support_count=title_support,
            redirect_support_count=redirect_support,
            alias_support_count=alias_support,
            anchor_support_count=anchor_support,
            channels=tuple(channels),
            provenance_ids=provenance,
        )

    def _provenance_slice(self, start: int, count: int) -> tuple[str, ...]:
        refs = self._sections["provenance_refs"][1]
        if start > len(refs):
            raise AddressIndexError("posting provenance points outside reference table")
        provenance: list[str] = []
        cursor = start
        for _ in range(count):
            string_index, cursor = _decode_varint(refs, cursor, "provenance_refs")
            if string_index >= len(self._provenance):
                raise AddressIndexError("provenance reference points outside string table")
            provenance.append(self._provenance[string_index])
        return tuple(provenance)
