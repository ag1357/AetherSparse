"""Bounded fuzzy mention and canonical-address proposal indexes.

The fuzzy plane is deliberately a proposal mechanism.  It never mints an
entity ID from an approximate string: every emitted ID was supplied by the
compiled address records.  Mention hypotheses are retained separately from
the globally capped entity candidates so qualification can distinguish span
recovery from address-cap loss.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from aethersparse.controller.semantic_address import (
    CORPUS_ENTITY_ID_PREFIX,
    canonical_entity_id,
    normalize_mention,
)
from aethersparse.selection.spelling import levenshtein_leq

FUZZY_ADDRESS_SCHEMA_VERSION = "aethersparse.fuzzy-address-index.v12"
FUZZY_ADDRESS_MANIFEST_SCHEMA_VERSION = "aethersparse.fuzzy-address-manifest.v12"
FUZZY_CANONICAL_REGISTRY_ID = "v050.normalized-title-sha256-96bit"
_TOKEN_RE = re.compile(r"[^\W_]+(?:['\N{RIGHT SINGLE QUOTATION MARK}-][^\W_]+)*", re.UNICODE)


class FuzzyAddressDataError(ValueError):
    """Raised when compiled fuzzy-address data fails validation."""


class FuzzyChannel(StrEnum):
    """Independent mention-generation channels qualified by Mission 7."""

    EXACT = "exact"
    CHAR_NGRAM = "char_ngram"
    EDIT_DISTANCE = "edit_distance"
    SIMHASH_LSH = "simhash_lsh"


class CanonicalAddressRegistry(Protocol):
    """Validate a canonical entity/title pair against an authoritative registry."""

    def entity_id_for_title(self, canonical_title: str) -> str:
        """Return the only canonical ID authorized for ``canonical_title``."""


class HashCanonicalAddressRegistry:
    """Current corpus-band registry contract pending the shared compiler registry."""

    def entity_id_for_title(self, canonical_title: str) -> str:
        """Return the v0.5 content-derived canonical ID."""

        return str(canonical_entity_id(canonical_title))


@dataclass(frozen=True)
class AddressSurfaceRecord:
    """One compiled surface-to-authoritative-address observation.

    ``entity_id=None`` explicitly represents an unresolved target.  Such a
    surface may support mention detection but can never become an entity
    candidate.
    """

    surface: str
    entity_id: str | None
    canonical_title: str | None
    support_count: int = 1
    source_document_count: int = 1
    source_document_ids: tuple[str, ...] = ()
    support_provenance_ids: tuple[str, ...] = ()
    source_channels: tuple[str, ...] = ()
    source_provenance: tuple[str, ...] = ()


@dataclass(frozen=True)
class MentionHypothesis:
    """A copied query span associated with one compiled address surface."""

    char_start: int
    char_end: int
    observed_text: str
    exact_normalized_mention: str
    fuzzy_lookup_normalization: str
    matched_surface: str
    entity_ids: tuple[str, ...]
    resolved_entity_probabilities: tuple[tuple[str, float], ...]
    unresolved_support_count: int
    total_support_count: int
    unresolved_probability_mass: float
    omitted_probability_mass: float
    probability_provenance: str
    source_document_ids: tuple[str, ...]
    source_document_count: int
    source_diversity: float
    source_channels: tuple[str, ...]
    source_provenance: tuple[str, ...]
    exact_subchannels: tuple[str, ...]
    channel: FuzzyChannel
    score: float

    @property
    def unresolved_target(self) -> bool:
        """Compatibility view; numeric mass/support are the authoritative fields."""

        return self.unresolved_probability_mass > 0.0


@dataclass(frozen=True)
class AddressProposal:
    """One exact canonical address proposed by an independent generator."""

    entity_id: str
    canonical_title: str
    matched_surface: str
    observed_text: str
    char_start: int
    char_end: int
    channel: FuzzyChannel
    score: float
    channel_pre_cap_rank: int
    mention_probability: float
    support_count: int
    support_provenance_ids: tuple[str, ...]
    support_aggregation: str
    source_document_count: int
    source_document_ids: tuple[str, ...]
    source_diversity: float
    unresolved_probability_mass: float
    omitted_probability_mass: float
    probability_provenance: str
    source_channels: tuple[str, ...]
    source_provenance: tuple[str, ...]
    exact_subchannels: tuple[str, ...]


@dataclass(frozen=True)
class ChannelCapAccounting:
    """Separate mention, proposal, and retrieval-work caps for one channel."""

    mention_cap: int
    channel_address_cap: int
    max_spans: int
    postings_cap: int
    per_span_cap: int
    pre_cap_mention_count: int
    retained_mention_count: int
    pruned_mention_count: int
    pre_cap_address_count: int
    retained_address_count: int
    pruned_address_count: int


@dataclass(frozen=True)
class LookupCost:
    """Deterministic logical work and bytes touched by a lookup."""

    spans_considered: int
    posting_list_lookups: int
    postings_read: int
    surface_scores: int
    peak_accumulator_entries: int
    distance_evaluations: int
    integer_ops: int
    xor_popcount_ops: int
    estimated_bytes_read: int


@dataclass(frozen=True)
class FuzzyLookupResult:
    """Bounded per-channel result with pre-cap mention state preserved."""

    channel: FuzzyChannel
    mention_hypotheses: tuple[MentionHypothesis, ...]
    address_proposals: tuple[AddressProposal, ...]
    pre_cap_address_proposals: tuple[AddressProposal, ...]
    pruned_address_proposals: tuple[AddressProposal, ...]
    pruned_address_ids: tuple[str, ...]
    pre_cap_address_count: int
    mention_cap_saturated: bool
    address_cap_saturated: bool
    cap_accounting: ChannelCapAccounting
    cost: LookupCost


@dataclass(frozen=True)
class UnionAddressProposal:
    """Canonical-ID union with every generating fuzzy channel retained."""

    entity_id: str
    canonical_title: str
    best_score: float
    channels: tuple[FuzzyChannel, ...]
    matched_surfaces: tuple[str, ...]
    channel_proposals: tuple[AddressProposal, ...]
    support_count: int
    support_provenance_ids: tuple[str, ...]
    support_aggregation: str
    source_document_count: int
    source_document_ids: tuple[str, ...]
    source_diversity: float
    source_channels: tuple[str, ...]
    source_provenance: tuple[str, ...]
    exact_subchannels: tuple[str, ...]
    unresolved_probability_mass: float
    omitted_probability_mass: float


@dataclass(frozen=True)
class UnionAddressResult:
    """One global address cap applied after complete per-channel proposals."""

    address_proposals: tuple[UnionAddressProposal, ...]
    pre_cap_address_proposals: tuple[UnionAddressProposal, ...]
    pruned_address_proposals: tuple[UnionAddressProposal, ...]
    pruned_address_ids: tuple[str, ...]
    pre_cap_address_count: int
    global_address_cap: int
    global_cap_saturated: bool
    channel_pre_cap_counts: tuple[tuple[FuzzyChannel, int], ...]
    channel_locally_pruned_counts: tuple[tuple[FuzzyChannel, int], ...]

    def __iter__(self) -> Iterator[UnionAddressProposal]:
        return iter(self.address_proposals)

    def __len__(self) -> int:
        return len(self.address_proposals)

    def __getitem__(self, index: int) -> UnionAddressProposal:
        return self.address_proposals[index]


@dataclass(frozen=True)
class _Surface:
    normalized: str
    records: tuple[AddressSurfaceRecord, ...]
    tokens: tuple[str, ...]
    grams: tuple[str, ...]
    simhash: int


@dataclass(frozen=True)
class _Span:
    char_start: int
    char_end: int
    text: str
    exact_normalized: str
    fuzzy_normalized: str
    token_count: int


def normalize_fuzzy_surface(value: str) -> str:
    """NFKC/casefold a surface and make punctuation/tokenization explicit."""

    replaced = value.replace("_", " ").replace("\N{RIGHT SINGLE QUOTATION MARK}", "'")
    normalized = unicodedata.normalize("NFKC", replaced).casefold()
    return " ".join(match.group(0) for match in _TOKEN_RE.finditer(normalized))


def _char_ngrams(value: str, size: int) -> tuple[str, ...]:
    compact = f"^{value}$"
    if len(compact) <= size:
        return (compact,)
    return tuple(
        sorted({compact[index : index + size] for index in range(len(compact) - size + 1)})
    )


def _hash64(value: str) -> int:
    return int.from_bytes(hashlib.blake2b(value.encode("utf-8"), digest_size=8).digest(), "big")


def _simhash(grams: Sequence[str]) -> int:
    weights = [0] * 64
    for gram in grams:
        digest = _hash64(gram)
        for bit in range(64):
            weights[bit] += 1 if digest & (1 << bit) else -1
    result = 0
    for bit, weight in enumerate(weights):
        if weight >= 0:
            result |= 1 << bit
    return result


def _hamming(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def _deletions(term: str, depth: int = 2) -> tuple[str, ...]:
    variants = {term}
    frontier = {term}
    for _ in range(depth):
        following: set[str] = set()
        for value in frontier:
            for index in range(len(value)):
                candidate = value[:index] + value[index + 1 :]
                if candidate and candidate not in variants:
                    variants.add(candidate)
                    following.add(candidate)
        frontier = following
    return tuple(sorted(variants))


def _stable_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        + b"\n"
    )


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _gzip(value: bytes) -> bytes:
    return gzip.compress(value, compresslevel=9, mtime=0)


def _integer(value: object, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise FuzzyAddressDataError(f"{field} must be an integer >= {minimum}")
    return value


def _strings(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise FuzzyAddressDataError(f"{field} must be a list of non-empty strings")
    result = tuple(value)
    if len(set(result)) != len(result):
        raise FuzzyAddressDataError(f"{field} must not contain duplicates")
    return result


def _parse_record(value: Mapping[str, object]) -> AddressSurfaceRecord:
    required = {
        "surface",
        "entity_id",
        "canonical_title",
        "support_count",
        "source_document_count",
        "source_document_ids",
        "support_provenance_ids",
        "source_channels",
        "source_provenance",
    }
    if set(value) != required:
        raise FuzzyAddressDataError("address record fields do not match the v12 schema")
    surface = str(value["surface"])
    if not normalize_fuzzy_surface(surface):
        raise FuzzyAddressDataError("address surface must contain a normalized token")
    entity_value = value["entity_id"]
    title_value = value["canonical_title"]
    entity_id = None if entity_value is None else str(entity_value)
    canonical_title = None if title_value is None else str(title_value)
    if (entity_id is None) != (canonical_title is None):
        raise FuzzyAddressDataError("entity ID and canonical title must resolve together")
    if entity_id is not None:
        if not entity_id.startswith(CORPUS_ENTITY_ID_PREFIX):
            raise FuzzyAddressDataError("fuzzy proposals may only reference corpus entity IDs")
        assert canonical_title is not None
        if entity_id != canonical_entity_id(canonical_title):
            raise FuzzyAddressDataError("entity ID does not match its canonical title")
    support = _integer(value["support_count"], "support_count", minimum=1)
    source_count = _integer(value["source_document_count"], "source_document_count", minimum=1)
    if source_count > support:
        raise FuzzyAddressDataError("source support exceeds occurrence support")
    source_ids = _strings(value["source_document_ids"], "source_document_ids")
    if source_ids and len(source_ids) != source_count:
        raise FuzzyAddressDataError("source document IDs disagree with source_document_count")
    support_ids = _strings(value["support_provenance_ids"], "support_provenance_ids")
    if support_ids and len(support_ids) != support:
        raise FuzzyAddressDataError("support provenance IDs disagree with support_count")
    return AddressSurfaceRecord(
        surface,
        entity_id,
        canonical_title,
        support,
        source_count,
        source_ids,
        support_ids,
        _strings(value["source_channels"], "source_channels"),
        _strings(value["source_provenance"], "source_provenance"),
    )


class FuzzyAddressIndex:
    """Immutable compact lookup over exact address surfaces.

    Character n-gram postings, token-level symmetric-delete edit expansion,
    and 4x16-bit SimHash LSH are independent retrieval paths.  The edit index
    verifies every proposal with Damerau-OSA distance <=2; delete hashes never
    authorize an address by themselves.
    """

    def __init__(
        self,
        records: Iterable[AddressSurfaceRecord],
        *,
        ngram_size: int = 3,
        lsh_bands: int = 4,
        edit_distance: int = 2,
        registry: CanonicalAddressRegistry | None = None,
    ) -> None:
        if ngram_size not in {2, 3, 4}:
            raise ValueError("ngram_size must be 2, 3, or 4")
        if lsh_bands != 4:
            raise ValueError("the v12 compact baseline uses four 16-bit LSH bands")
        if edit_distance not in {1, 2}:
            raise ValueError("edit_distance must be one or two")
        active_registry = registry or HashCanonicalAddressRegistry()
        entity_titles: dict[str, str] = {}
        merged: dict[tuple[str, str | None, str | None], AddressSurfaceRecord] = {}
        for record in records:
            normalized = normalize_fuzzy_surface(record.surface)
            if not normalized:
                raise FuzzyAddressDataError("address surface must contain a token")
            if (record.entity_id is None) != (record.canonical_title is None):
                raise FuzzyAddressDataError("entity ID and canonical title must resolve together")
            if record.entity_id is not None:
                assert record.canonical_title is not None
                if not record.entity_id.startswith(CORPUS_ENTITY_ID_PREFIX):
                    raise FuzzyAddressDataError(
                        "fuzzy proposals may only reference corpus entity IDs"
                    )
                if active_registry.entity_id_for_title(record.canonical_title) != record.entity_id:
                    raise FuzzyAddressDataError("entity ID does not match its canonical title")
                prior_title = entity_titles.setdefault(record.entity_id, record.canonical_title)
                if prior_title != record.canonical_title:
                    raise FuzzyAddressDataError(
                        "one canonical entity ID cannot carry conflicting titles"
                    )
            if (
                record.support_count < 1
                or not 1 <= record.source_document_count <= record.support_count
            ):
                raise FuzzyAddressDataError("invalid support counts")
            if record.source_document_ids and (
                len(set(record.source_document_ids)) != len(record.source_document_ids)
                or len(record.source_document_ids) != record.source_document_count
                or any(not item for item in record.source_document_ids)
            ):
                raise FuzzyAddressDataError("invalid source document IDs")
            if record.support_provenance_ids and (
                len(set(record.support_provenance_ids)) != len(record.support_provenance_ids)
                or len(record.support_provenance_ids) != record.support_count
                or any(not item for item in record.support_provenance_ids)
            ):
                raise FuzzyAddressDataError("invalid support provenance IDs")
            if any(not item for item in (*record.source_channels, *record.source_provenance)):
                raise FuzzyAddressDataError("source channel/provenance values must be non-empty")
            key = (normalized, record.entity_id, record.canonical_title)
            previous = merged.get(key)
            if previous is not None and (
                not previous.source_document_ids
                or not record.source_document_ids
                or not previous.support_provenance_ids
                or not record.support_provenance_ids
            ):
                raise FuzzyAddressDataError(
                    "duplicate evidence requires document and support provenance IDs"
                )
            source_ids = tuple(
                sorted(
                    set(record.source_document_ids)
                    | (set(previous.source_document_ids) if previous else set())
                )
            )
            support_ids = tuple(
                sorted(
                    set(record.support_provenance_ids)
                    | (set(previous.support_provenance_ids) if previous else set())
                )
            )
            support_count = len(support_ids) if support_ids else record.support_count
            merged[key] = AddressSurfaceRecord(
                surface=normalized,
                entity_id=record.entity_id,
                canonical_title=record.canonical_title,
                support_count=support_count,
                source_document_count=(
                    len(source_ids) if source_ids else record.source_document_count
                ),
                source_document_ids=source_ids,
                support_provenance_ids=support_ids,
                source_channels=tuple(
                    sorted(
                        set(record.source_channels)
                        | (set(previous.source_channels) if previous else set())
                    )
                ),
                source_provenance=tuple(
                    sorted(
                        set(record.source_provenance)
                        | (set(previous.source_provenance) if previous else set())
                    )
                ),
            )
        grouped: dict[str, list[AddressSurfaceRecord]] = defaultdict(list)
        for record in merged.values():
            grouped[record.surface].append(record)
        self.ngram_size = ngram_size
        self.lsh_bands = lsh_bands
        self.edit_distance = edit_distance
        self._surfaces = tuple(
            _Surface(
                normalized=surface,
                records=tuple(
                    sorted(
                        group,
                        key=lambda item: (item.entity_id or "", item.canonical_title or ""),
                    )
                ),
                tokens=tuple(surface.split()),
                grams=_char_ngrams(surface, ngram_size),
                simhash=_simhash(_char_ngrams(surface, ngram_size)),
            )
            for surface, group in sorted(grouped.items())
        )
        self._surface_lookup = {
            surface.normalized: index for index, surface in enumerate(self._surfaces)
        }
        ngrams: dict[str, list[int]] = defaultdict(list)
        token_surfaces: dict[str, list[int]] = defaultdict(list)
        tokens: set[str] = set()
        lsh: dict[tuple[int, int], list[int]] = defaultdict(list)
        for surface_index, surface in enumerate(self._surfaces):
            for gram in surface.grams:
                ngrams[gram].append(surface_index)
            for token in set(surface.tokens):
                token_surfaces[token].append(surface_index)
                tokens.add(token)
            for band in range(lsh_bands):
                lsh[(band, (surface.simhash >> (band * 16)) & 0xFFFF)].append(surface_index)
        token_list = tuple(sorted(tokens))
        token_ids = {token: index for index, token in enumerate(token_list)}
        deletes: dict[int, set[int]] = defaultdict(set)
        for token, token_id in token_ids.items():
            for variant in _deletions(token, edit_distance):
                deletes[_hash64(variant)].add(token_id)
        self._ngrams = {key: tuple(value) for key, value in sorted(ngrams.items())}
        self._tokens = token_list
        self._token_ids = token_ids
        self._token_surfaces = {key: tuple(value) for key, value in sorted(token_surfaces.items())}
        self._delete_postings = {
            key: tuple(sorted(value)) for key, value in sorted(deletes.items())
        }
        self._lsh = {key: tuple(value) for key, value in sorted(lsh.items())}
        self._max_surface_tokens = max((len(item.tokens) for item in self._surfaces), default=1)

    @property
    def surface_count(self) -> int:
        return len(self._surfaces)

    @property
    def address_count(self) -> int:
        return sum(
            sum(record.entity_id is not None for record in item.records) for item in self._surfaces
        )

    @property
    def unresolved_record_count(self) -> int:
        return sum(
            sum(record.entity_id is None for record in item.records) for item in self._surfaces
        )

    def entity_ids(self) -> frozenset[str]:
        """Return the exact authoritative IDs present in the compiled index."""

        return frozenset(
            record.entity_id
            for surface in self._surfaces
            for record in surface.records
            if record.entity_id is not None
        )

    def _document(self) -> dict[str, object]:
        return {
            "schema_version": FUZZY_ADDRESS_SCHEMA_VERSION,
            "canonical_registry": FUZZY_CANONICAL_REGISTRY_ID,
            "parameters": {
                "ngram_size": self.ngram_size,
                "lsh_bands": self.lsh_bands,
                "edit_distance": self.edit_distance,
            },
            "records": [asdict(record) for surface in self._surfaces for record in surface.records],
            "runtime": {
                "surface_order": [surface.normalized for surface in self._surfaces],
                "ngram_postings": [[key, list(value)] for key, value in self._ngrams.items()],
                "tokens": list(self._tokens),
                "token_surface_postings": [
                    [key, list(value)] for key, value in self._token_surfaces.items()
                ],
                "delete_postings": [
                    [f"{key:016x}", list(value)] for key, value in self._delete_postings.items()
                ],
                "simhash": [f"{surface.simhash:016x}" for surface in self._surfaces],
                "lsh_postings": [
                    [band, bucket, list(value)] for (band, bucket), value in self._lsh.items()
                ],
            },
        }

    def to_bytes(self) -> bytes:
        """Return deterministic, fully compiled runtime bytes."""

        return _stable_json(self._document())

    @classmethod
    def from_bytes(cls, payload: bytes) -> FuzzyAddressIndex:
        """Load and prove that serialized runtime tables are derivable."""

        try:
            document = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise FuzzyAddressDataError("invalid fuzzy-address payload") from error
        if not isinstance(document, Mapping):
            raise FuzzyAddressDataError("fuzzy-address payload must be an object")
        if document.get("schema_version") != FUZZY_ADDRESS_SCHEMA_VERSION:
            raise FuzzyAddressDataError("unsupported fuzzy-address schema")
        if document.get("canonical_registry") != FUZZY_CANONICAL_REGISTRY_ID:
            raise FuzzyAddressDataError("unsupported fuzzy-address canonical registry")
        parameters = document.get("parameters")
        rows = document.get("records")
        if not isinstance(parameters, Mapping) or not isinstance(rows, list):
            raise FuzzyAddressDataError("fuzzy-address payload lacks parameters or records")
        records = tuple(_parse_record(row) for row in rows if isinstance(row, Mapping))
        if len(records) != len(rows):
            raise FuzzyAddressDataError("every fuzzy-address record must be an object")
        result = cls(
            records,
            ngram_size=_integer(parameters.get("ngram_size"), "ngram_size"),
            lsh_bands=_integer(parameters.get("lsh_bands"), "lsh_bands"),
            edit_distance=_integer(parameters.get("edit_distance"), "edit_distance"),
        )
        if result.to_bytes() != payload:
            raise FuzzyAddressDataError("serialized runtime tables are not derivable from records")
        return result

    def write_artifact(self, payload_path: Path, manifest_path: Path) -> dict[str, object]:
        """Write a deterministic gzip and content-addressed manifest."""

        raw = self.to_bytes()
        compressed = _gzip(raw)
        payload_path.write_bytes(compressed)
        manifest: dict[str, object] = {
            "schema_version": FUZZY_ADDRESS_MANIFEST_SCHEMA_VERSION,
            "canonical_registry": FUZZY_CANONICAL_REGISTRY_ID,
            "payload_file": payload_path.name,
            "payload_gzip_sha256": _sha256(compressed),
            "payload_json_sha256": _sha256(raw),
            "compressed_bytes": len(compressed),
            "uncompressed_bytes": len(raw),
            "surface_count": self.surface_count,
            "address_count": self.address_count,
            "unresolved_record_count": self.unresolved_record_count,
        }
        manifest_path.write_bytes(_stable_json(manifest))
        return manifest

    @classmethod
    def from_artifact(cls, payload_path: Path, manifest_path: Path) -> FuzzyAddressIndex:
        """Verify manifest, hashes, counts, and all derived runtime tables."""

        try:
            manifest = json.loads(manifest_path.read_bytes())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise FuzzyAddressDataError("invalid fuzzy-address manifest") from error
        if not isinstance(manifest, Mapping):
            raise FuzzyAddressDataError("fuzzy-address manifest must be an object")
        if manifest.get("schema_version") != FUZZY_ADDRESS_MANIFEST_SCHEMA_VERSION:
            raise FuzzyAddressDataError("unsupported fuzzy-address manifest schema")
        if manifest.get("canonical_registry") != FUZZY_CANONICAL_REGISTRY_ID:
            raise FuzzyAddressDataError("unsupported fuzzy-address canonical registry")
        compressed = payload_path.read_bytes()
        if manifest.get("payload_file") != payload_path.name:
            raise FuzzyAddressDataError("fuzzy-address payload filename mismatch")
        if manifest.get("payload_gzip_sha256") != _sha256(compressed):
            raise FuzzyAddressDataError("fuzzy-address gzip hash mismatch")
        try:
            raw = gzip.decompress(compressed)
        except OSError as error:
            raise FuzzyAddressDataError("invalid fuzzy-address gzip") from error
        if manifest.get("payload_json_sha256") != _sha256(raw):
            raise FuzzyAddressDataError("fuzzy-address JSON hash mismatch")
        if manifest.get("compressed_bytes") != len(compressed) or manifest.get(
            "uncompressed_bytes"
        ) != len(raw):
            raise FuzzyAddressDataError("fuzzy-address byte count mismatch")
        result = cls.from_bytes(raw)
        counts = {
            "surface_count": result.surface_count,
            "address_count": result.address_count,
            "unresolved_record_count": result.unresolved_record_count,
        }
        for field, observed in counts.items():
            if manifest.get(field) != observed:
                raise FuzzyAddressDataError(f"fuzzy-address {field} mismatch")
        return result

    def _spans(self, query: str, *, max_spans: int) -> tuple[_Span, ...]:
        # Tokenize the original string so copied offsets can never drift when
        # NFKC expands or contracts a code point. Lookup normalization is
        # computed from each copied span and retained alongside the exact form.
        tokens = tuple(_TOKEN_RE.finditer(query))
        candidates: list[_Span] = []
        max_tokens = min(self._max_surface_tokens + 1, 8)
        for start_index, start in enumerate(tokens):
            for width in range(1, min(max_tokens, len(tokens) - start_index) + 1):
                end = tokens[start_index + width - 1]
                text = query[start.start() : end.end()]
                fuzzy_normalized = normalize_fuzzy_surface(text)
                if len(fuzzy_normalized) < 3:
                    continue
                candidates.append(
                    _Span(
                        start.start(),
                        end.end(),
                        text,
                        normalize_mention(text),
                        fuzzy_normalized,
                        width,
                    )
                )
        ordered = sorted(
            candidates,
            key=lambda item: (
                -item.token_count,
                item.char_start,
                item.char_end,
                item.fuzzy_normalized,
            ),
        )
        return tuple(ordered[:max_spans])

    def _surface_hypothesis(
        self,
        span: _Span,
        surface_index: int,
        channel: FuzzyChannel,
        score: float,
    ) -> MentionHypothesis:
        surface = self._surfaces[surface_index]
        total_support = sum(record.support_count for record in surface.records)
        unresolved_support = sum(
            record.support_count for record in surface.records if record.entity_id is None
        )
        resolved_support: dict[str, int] = defaultdict(int)
        for record in surface.records:
            if record.entity_id is not None:
                resolved_support[record.entity_id] += record.support_count
        source_ids = tuple(
            sorted(
                {
                    source_id
                    for record in surface.records
                    for source_id in record.source_document_ids
                }
            )
        )
        source_count = len(source_ids) or max(
            (record.source_document_count for record in surface.records), default=0
        )
        return MentionHypothesis(
            char_start=span.char_start,
            char_end=span.char_end,
            observed_text=span.text,
            exact_normalized_mention=span.exact_normalized,
            fuzzy_lookup_normalization=span.fuzzy_normalized,
            matched_surface=surface.normalized,
            entity_ids=tuple(sorted(resolved_support)),
            resolved_entity_probabilities=tuple(
                (entity_id, support / total_support)
                for entity_id, support in sorted(resolved_support.items())
            ),
            unresolved_support_count=unresolved_support,
            total_support_count=total_support,
            unresolved_probability_mass=unresolved_support / total_support,
            omitted_probability_mass=0.0,
            probability_provenance="compiled_surface_support",
            source_document_ids=source_ids,
            source_document_count=source_count,
            source_diversity=(source_count / total_support),
            source_channels=tuple(
                sorted({item for record in surface.records for item in record.source_channels})
            ),
            source_provenance=tuple(
                sorted({item for record in surface.records for item in record.source_provenance})
            ),
            exact_subchannels=("fuzzy_normalized_title_surface",)
            if channel is FuzzyChannel.EXACT
            else (),
            channel=channel,
            score=score,
        )

    def _exact_matches(self, spans: Sequence[_Span]) -> tuple[list[MentionHypothesis], LookupCost]:
        hypotheses: list[MentionHypothesis] = []
        for span in spans:
            surface_index = self._surface_lookup.get(span.fuzzy_normalized)
            if surface_index is not None:
                hypotheses.append(
                    self._surface_hypothesis(span, surface_index, FuzzyChannel.EXACT, 1.0)
                )
        return hypotheses, LookupCost(
            spans_considered=len(spans),
            posting_list_lookups=0,
            postings_read=0,
            surface_scores=len(spans),
            peak_accumulator_entries=1 if spans else 0,
            distance_evaluations=0,
            integer_ops=len(spans),
            xor_popcount_ops=0,
            estimated_bytes_read=sum(
                len(span.fuzzy_normalized.encode("utf-8")) + 4 for span in spans
            ),
        )

    def _char_matches(
        self,
        spans: Sequence[_Span],
        *,
        score_threshold: float,
        postings_cap: int,
        per_span_cap: int,
    ) -> tuple[list[MentionHypothesis], LookupCost]:
        hypotheses: list[MentionHypothesis] = []
        postings_read = 0
        posting_list_lookups = 0
        surface_scores = 0
        peak_accumulator_entries = 0
        integer_ops = 0
        estimated_bytes = 0
        for span in spans:
            grams = _char_ngrams(span.fuzzy_normalized, self.ngram_size)
            counts: dict[int, int] = defaultdict(int)
            for gram in grams:
                posting_list_lookups += 1
                postings = self._ngrams.get(gram, ())
                room = max(0, postings_cap - postings_read)
                selected = postings[:room]
                postings_read += len(selected)
                estimated_bytes += len(gram.encode("utf-8")) + 4 * len(selected)
                for surface_index in selected:
                    counts[surface_index] += 1
                if postings_read >= postings_cap:
                    break
            peak_accumulator_entries = max(peak_accumulator_entries, len(counts))
            scored: list[tuple[float, int]] = []
            gram_count = len(grams)
            for surface_index, overlap in counts.items():
                surface_scores += 1
                target_count = len(self._surfaces[surface_index].grams)
                dice = 2.0 * overlap / (gram_count + target_count)
                containment = overlap / max(1, min(gram_count, target_count))
                score = 0.7 * dice + 0.3 * containment
                integer_ops += gram_count + target_count + 6
                if score >= score_threshold:
                    scored.append((score, surface_index))
            for score, surface_index in sorted(
                scored,
                key=lambda item: (-item[0], self._surfaces[item[1]].normalized, item[1]),
            )[:per_span_cap]:
                hypotheses.append(
                    self._surface_hypothesis(span, surface_index, FuzzyChannel.CHAR_NGRAM, score)
                )
            if postings_read >= postings_cap:
                break
        return hypotheses, LookupCost(
            spans_considered=len(spans),
            posting_list_lookups=posting_list_lookups,
            postings_read=postings_read,
            surface_scores=surface_scores,
            peak_accumulator_entries=peak_accumulator_entries,
            distance_evaluations=0,
            integer_ops=integer_ops,
            xor_popcount_ops=0,
            estimated_bytes_read=estimated_bytes,
        )

    def _edit_matches(
        self,
        query: str,
        *,
        postings_cap: int,
        per_token_cap: int,
    ) -> tuple[list[MentionHypothesis], LookupCost]:
        hypotheses: list[MentionHypothesis] = []
        postings_read = 0
        posting_list_lookups = 0
        surface_scores = 0
        peak_accumulator_entries = 0
        distance_evaluations = 0
        integer_ops = 0
        estimated_bytes = 0
        for match in _TOKEN_RE.finditer(query):
            observed = match.group(0)
            token = normalize_fuzzy_surface(observed)
            if len(token) < 4 or token in self._token_ids:
                continue
            token_candidates: set[int] = set()
            for variant in _deletions(token, self.edit_distance):
                posting_list_lookups += 1
                posting = self._delete_postings.get(_hash64(variant), ())
                room = max(0, postings_cap - postings_read)
                selected = posting[:room]
                postings_read += len(selected)
                estimated_bytes += 8 + 4 * len(selected)
                token_candidates.update(selected)
                if postings_read >= postings_cap:
                    break
            peak_accumulator_entries = max(peak_accumulator_entries, len(token_candidates))
            verified: list[tuple[int, str]] = []
            for token_id in sorted(token_candidates):
                candidate = self._tokens[token_id]
                distance_evaluations += 1
                distance = levenshtein_leq(token, candidate, self.edit_distance)
                integer_ops += 3 * (len(token) + 1) * (len(candidate) + 1)
                if distance is not None and distance > 0:
                    verified.append((distance, candidate))
            for distance, corrected in sorted(verified, key=lambda item: (item[0], item[1]))[
                :per_token_cap
            ]:
                surfaces = self._token_surfaces[corrected]
                for surface_index in surfaces[:per_token_cap]:
                    surface_scores += 1
                    surface = self._surfaces[surface_index]
                    coverage = 1.0 / len(surface.tokens)
                    score = 0.65 * (1.0 - distance / (self.edit_distance + 1)) + 0.35 * coverage
                    span = _Span(
                        match.start(),
                        match.end(),
                        observed,
                        normalize_mention(observed),
                        token,
                        1,
                    )
                    hypotheses.append(
                        self._surface_hypothesis(
                            span, surface_index, FuzzyChannel.EDIT_DISTANCE, score
                        )
                    )
                    estimated_bytes += len(corrected.encode("utf-8")) + 4
            if postings_read >= postings_cap:
                break
        return hypotheses, LookupCost(
            spans_considered=sum(1 for _ in _TOKEN_RE.finditer(query)),
            posting_list_lookups=posting_list_lookups,
            postings_read=postings_read,
            surface_scores=surface_scores,
            peak_accumulator_entries=peak_accumulator_entries,
            distance_evaluations=distance_evaluations,
            integer_ops=integer_ops,
            xor_popcount_ops=0,
            estimated_bytes_read=estimated_bytes,
        )

    def _simhash_matches(
        self,
        spans: Sequence[_Span],
        *,
        max_hamming: int,
        postings_cap: int,
        per_span_cap: int,
    ) -> tuple[list[MentionHypothesis], LookupCost]:
        hypotheses: list[MentionHypothesis] = []
        postings_read = 0
        posting_list_lookups = 0
        surface_scores = 0
        peak_accumulator_entries = 0
        integer_ops = 0
        xor_ops = 0
        estimated_bytes = 0
        for span in spans:
            grams = _char_ngrams(span.fuzzy_normalized, self.ngram_size)
            fingerprint = _simhash(grams)
            candidates: set[int] = set()
            integer_ops += len(grams) * 64
            for band in range(self.lsh_bands):
                bucket = (fingerprint >> (band * 16)) & 0xFFFF
                probes = [bucket, *(bucket ^ (1 << bit) for bit in range(16))]
                for probe in probes:
                    posting_list_lookups += 1
                    posting = self._lsh.get((band, probe), ())
                    room = max(0, postings_cap - postings_read)
                    selected = posting[:room]
                    postings_read += len(selected)
                    candidates.update(selected)
                    estimated_bytes += 4 + 4 * len(selected)
                    if postings_read >= postings_cap:
                        break
                if postings_read >= postings_cap:
                    break
            peak_accumulator_entries = max(peak_accumulator_entries, len(candidates))
            scored: list[tuple[float, int]] = []
            for surface_index in sorted(candidates):
                distance = _hamming(fingerprint, self._surfaces[surface_index].simhash)
                xor_ops += 1
                surface_scores += 1
                if distance <= max_hamming:
                    scored.append((1.0 - distance / 64.0, surface_index))
            for score, surface_index in sorted(
                scored,
                key=lambda item: (-item[0], self._surfaces[item[1]].normalized, item[1]),
            )[:per_span_cap]:
                hypotheses.append(
                    self._surface_hypothesis(span, surface_index, FuzzyChannel.SIMHASH_LSH, score)
                )
            if postings_read >= postings_cap:
                break
        return hypotheses, LookupCost(
            spans_considered=len(spans),
            posting_list_lookups=posting_list_lookups,
            postings_read=postings_read,
            surface_scores=surface_scores,
            peak_accumulator_entries=peak_accumulator_entries,
            distance_evaluations=0,
            integer_ops=integer_ops,
            xor_popcount_ops=xor_ops,
            estimated_bytes_read=estimated_bytes,
        )

    def lookup(
        self,
        query: str,
        channel: FuzzyChannel,
        *,
        mention_cap: int = 64,
        address_cap: int = 16,
        max_spans: int = 128,
        postings_cap: int = 4096,
        per_span_cap: int = 8,
        char_score_threshold: float = 0.52,
        simhash_max_hamming: int = 12,
    ) -> FuzzyLookupResult:
        """Generate bounded mention hypotheses and authoritative addresses."""

        if mention_cap < 1 or address_cap < 1 or max_spans < 1 or postings_cap < 1:
            raise ValueError("lookup caps must be positive")
        if per_span_cap < 1:
            raise ValueError("per_span_cap must be positive")
        if not 0.0 <= char_score_threshold <= 1.0:
            raise ValueError("char_score_threshold must be between zero and one")
        if not 0 <= simhash_max_hamming <= 64:
            raise ValueError("simhash_max_hamming must be between zero and 64")
        spans = self._spans(query, max_spans=max_spans)
        if channel is FuzzyChannel.EXACT:
            hypotheses, cost = self._exact_matches(spans)
        elif channel is FuzzyChannel.CHAR_NGRAM:
            hypotheses, cost = self._char_matches(
                spans,
                score_threshold=char_score_threshold,
                postings_cap=postings_cap,
                per_span_cap=per_span_cap,
            )
        elif channel is FuzzyChannel.EDIT_DISTANCE:
            hypotheses, cost = self._edit_matches(
                query,
                postings_cap=postings_cap,
                per_token_cap=per_span_cap,
            )
        elif channel is FuzzyChannel.SIMHASH_LSH:
            hypotheses, cost = self._simhash_matches(
                spans,
                max_hamming=simhash_max_hamming,
                postings_cap=postings_cap,
                per_span_cap=per_span_cap,
            )
        else:  # pragma: no cover - StrEnum exhaustiveness guard
            raise ValueError(f"unsupported fuzzy channel: {channel}")
        deduplicated: dict[tuple[int, int, str], MentionHypothesis] = {}
        for hypothesis in hypotheses:
            key = (hypothesis.char_start, hypothesis.char_end, hypothesis.matched_surface)
            previous = deduplicated.get(key)
            if previous is None or hypothesis.score > previous.score:
                deduplicated[key] = hypothesis
        ordered_mentions = tuple(
            sorted(
                deduplicated.values(),
                key=lambda item: (
                    -item.score,
                    item.char_start,
                    item.char_end,
                    item.matched_surface,
                ),
            )
        )
        retained_mentions = ordered_mentions[:mention_cap]
        proposals: dict[str, list[AddressProposal]] = defaultdict(list)
        # Address proposals are derived from every channel hypothesis before
        # the independent mention-display cap.  Retrieval work caps remain
        # explicit in ChannelCapAccounting, but no local presentation cap can
        # silently remove an entity before the cross-channel union.
        for hypothesis in ordered_mentions:
            surface = self._surfaces[self._surface_lookup[hypothesis.matched_surface]]
            probability_by_id = dict(hypothesis.resolved_entity_probabilities)
            for record in surface.records:
                if record.entity_id is None or record.canonical_title is None:
                    continue
                candidate = AddressProposal(
                    entity_id=record.entity_id,
                    canonical_title=record.canonical_title,
                    matched_surface=hypothesis.matched_surface,
                    observed_text=hypothesis.observed_text,
                    char_start=hypothesis.char_start,
                    char_end=hypothesis.char_end,
                    channel=channel,
                    score=hypothesis.score,
                    channel_pre_cap_rank=0,
                    mention_probability=probability_by_id[record.entity_id],
                    support_count=record.support_count,
                    support_provenance_ids=record.support_provenance_ids,
                    support_aggregation=(
                        "deduplicated_support_provenance_ids"
                        if record.support_provenance_ids
                        else "record_support_without_occurrence_ids"
                    ),
                    source_document_count=record.source_document_count,
                    source_document_ids=record.source_document_ids,
                    source_diversity=record.source_document_count / record.support_count,
                    unresolved_probability_mass=hypothesis.unresolved_probability_mass,
                    omitted_probability_mass=hypothesis.omitted_probability_mass,
                    probability_provenance=hypothesis.probability_provenance,
                    source_channels=record.source_channels,
                    source_provenance=record.source_provenance,
                    exact_subchannels=hypothesis.exact_subchannels,
                )
                proposals[record.entity_id].append(candidate)
        aggregated: list[AddressProposal] = []
        for candidates in proposals.values():
            best = min(
                candidates,
                key=lambda item: (-item.score, item.matched_surface, item.entity_id),
            )
            support_ids = tuple(
                sorted(
                    {item for candidate in candidates for item in candidate.support_provenance_ids}
                )
            )
            complete_support_ids = all(
                candidate.support_provenance_ids
                and len(candidate.support_provenance_ids) == candidate.support_count
                for candidate in candidates
            )
            support_count = (
                len(support_ids)
                if complete_support_ids
                else max(candidate.support_count for candidate in candidates)
            )
            document_ids = tuple(
                sorted({item for candidate in candidates for item in candidate.source_document_ids})
            )
            complete_document_ids = all(
                candidate.source_document_ids
                and len(candidate.source_document_ids) == candidate.source_document_count
                for candidate in candidates
            )
            document_count = (
                len(document_ids)
                if complete_document_ids
                else max(candidate.source_document_count for candidate in candidates)
            )
            aggregated.append(
                replace(
                    best,
                    support_count=support_count,
                    support_provenance_ids=support_ids,
                    support_aggregation=(
                        "deduplicated_support_provenance_ids"
                        if complete_support_ids
                        else "maximum_lower_bound_without_complete_occurrence_ids"
                    ),
                    source_document_count=document_count,
                    source_document_ids=document_ids,
                    source_diversity=document_count / support_count,
                    source_channels=tuple(
                        sorted({item for row in candidates for item in row.source_channels})
                    ),
                    source_provenance=tuple(
                        sorted({item for row in candidates for item in row.source_provenance})
                    ),
                    exact_subchannels=tuple(
                        sorted({item for row in candidates for item in row.exact_subchannels})
                    ),
                )
            )
        ordered_proposals = tuple(
            replace(item, channel_pre_cap_rank=rank)
            for rank, item in enumerate(
                sorted(
                    aggregated,
                    key=lambda item: (-item.score, item.matched_surface, item.entity_id),
                ),
                start=1,
            )
        )
        retained_proposals = ordered_proposals[:address_cap]
        pruned_proposals = ordered_proposals[address_cap:]
        cap_accounting = ChannelCapAccounting(
            mention_cap=mention_cap,
            channel_address_cap=address_cap,
            max_spans=max_spans,
            postings_cap=postings_cap,
            per_span_cap=per_span_cap,
            pre_cap_mention_count=len(ordered_mentions),
            retained_mention_count=len(retained_mentions),
            pruned_mention_count=len(ordered_mentions) - len(retained_mentions),
            pre_cap_address_count=len(ordered_proposals),
            retained_address_count=len(retained_proposals),
            pruned_address_count=len(pruned_proposals),
        )
        return FuzzyLookupResult(
            channel=channel,
            mention_hypotheses=retained_mentions,
            address_proposals=retained_proposals,
            pre_cap_address_proposals=ordered_proposals,
            pruned_address_proposals=pruned_proposals,
            pruned_address_ids=tuple(item.entity_id for item in pruned_proposals),
            pre_cap_address_count=len(ordered_proposals),
            mention_cap_saturated=len(ordered_mentions) > mention_cap,
            address_cap_saturated=len(ordered_proposals) > address_cap,
            cap_accounting=cap_accounting,
            cost=cost,
        )


def union_address_results(
    results: Sequence[FuzzyLookupResult], *, address_cap: int
) -> UnionAddressResult:
    """Union independent channels by exact canonical ID before one global cap."""

    if address_cap < 1:
        raise ValueError("address_cap must be positive")
    channels = [result.channel for result in results]
    if len(set(channels)) != len(channels):
        raise ValueError("address union accepts at most one result per channel")
    grouped: dict[str, list[AddressProposal]] = defaultdict(list)
    titles: dict[str, str] = {}
    for result in results:
        # The local address_cap is an output/presentation boundary only. The
        # global union consumes every channel proposal generated inside the
        # explicit retrieval-work caps, then applies exactly one K cap.
        for proposal in result.pre_cap_address_proposals:
            prior_title = titles.setdefault(proposal.entity_id, proposal.canonical_title)
            if prior_title != proposal.canonical_title:
                raise FuzzyAddressDataError(
                    "one canonical entity ID cannot carry conflicting titles"
                )
            grouped[proposal.entity_id].append(proposal)
    union: list[UnionAddressProposal] = []
    for entity_id, candidates in grouped.items():
        best = min(
            candidates,
            key=lambda item: (-item.score, item.matched_surface, item.channel, item.entity_id),
        )
        # A surface record returned through several generators is one evidence
        # record, not several occurrences. Deduplicate it before aggregating
        # support/source identities across genuinely different surfaces.
        evidence_rows: dict[tuple[str, str], AddressProposal] = {}
        for candidate in candidates:
            key = (candidate.entity_id, candidate.matched_surface)
            previous = evidence_rows.get(key)
            if previous is None or candidate.channel < previous.channel:
                evidence_rows[key] = candidate
        evidence = tuple(evidence_rows.values())
        support_ids = tuple(
            sorted({item for row in evidence for item in row.support_provenance_ids})
        )
        complete_support_ids = all(
            row.support_provenance_ids and len(row.support_provenance_ids) == row.support_count
            for row in evidence
        )
        support_count = (
            len(support_ids)
            if complete_support_ids
            else max((row.support_count for row in evidence), default=0)
        )
        document_ids = tuple(sorted({item for row in evidence for item in row.source_document_ids}))
        complete_document_ids = all(
            row.source_document_ids and len(row.source_document_ids) == row.source_document_count
            for row in evidence
        )
        document_count = (
            len(document_ids)
            if complete_document_ids
            else max((row.source_document_count for row in evidence), default=0)
        )
        union.append(
            UnionAddressProposal(
                entity_id=entity_id,
                canonical_title=best.canonical_title,
                best_score=best.score,
                channels=tuple(sorted({item.channel for item in candidates}, key=str)),
                matched_surfaces=tuple(sorted({item.matched_surface for item in candidates})),
                channel_proposals=tuple(
                    sorted(
                        candidates,
                        key=lambda item: (
                            item.channel,
                            item.channel_pre_cap_rank,
                            item.matched_surface,
                        ),
                    )
                ),
                support_count=support_count,
                support_provenance_ids=support_ids,
                support_aggregation=(
                    "deduplicated_support_provenance_ids"
                    if complete_support_ids
                    else "maximum_lower_bound_without_complete_occurrence_ids"
                ),
                source_document_count=document_count,
                source_document_ids=document_ids,
                source_diversity=(document_count / support_count if support_count else 0.0),
                source_channels=tuple(
                    sorted({item for row in evidence for item in row.source_channels})
                ),
                source_provenance=tuple(
                    sorted({item for row in evidence for item in row.source_provenance})
                ),
                exact_subchannels=tuple(
                    sorted({item for row in candidates for item in row.exact_subchannels})
                ),
                unresolved_probability_mass=max(
                    item.unresolved_probability_mass for item in candidates
                ),
                omitted_probability_mass=max(item.omitted_probability_mass for item in candidates),
            )
        )
    ordered = tuple(
        sorted(
            union,
            key=lambda item: (
                -len(item.channels),
                -item.best_score,
                item.canonical_title,
                item.entity_id,
            ),
        )
    )
    retained = ordered[:address_cap]
    pruned = ordered[address_cap:]
    return UnionAddressResult(
        address_proposals=retained,
        pre_cap_address_proposals=ordered,
        pruned_address_proposals=pruned,
        pruned_address_ids=tuple(item.entity_id for item in pruned),
        pre_cap_address_count=len(ordered),
        global_address_cap=address_cap,
        global_cap_saturated=bool(pruned),
        channel_pre_cap_counts=tuple(
            (result.channel, len(result.pre_cap_address_proposals)) for result in results
        ),
        channel_locally_pruned_counts=tuple(
            (result.channel, len(result.pruned_address_proposals)) for result in results
        ),
    )


def logical_index_bytes(index: FuzzyAddressIndex) -> dict[str, int]:
    """Return exact serialized bytes plus deterministic table-level estimates."""

    document = index._document()
    runtime = document["runtime"]
    if not isinstance(runtime, Mapping):  # pragma: no cover - internal invariant
        raise AssertionError("runtime serialization must be a mapping")
    address_surface_bytes = len(
        _stable_json(
            {
                "records": document["records"],
                "surface_order": runtime["surface_order"],
            }
        )
    )
    token_dictionary_bytes = len(_stable_json(runtime["tokens"]))
    ngram_posting_bytes = len(_stable_json(runtime["ngram_postings"]))
    edit_posting_bytes = len(_stable_json(runtime["delete_postings"]))
    token_surface_posting_bytes = len(_stable_json(runtime["token_surface_postings"]))
    simhash_lsh_bytes = len(
        _stable_json(
            {
                "simhash": runtime["simhash"],
                "lsh_postings": runtime["lsh_postings"],
            }
        )
    )
    return {
        "serialized_json_bytes": len(index.to_bytes()),
        "serialized_gzip_bytes": len(_gzip(index.to_bytes())),
        "address_surface_bytes": address_surface_bytes,
        "token_dictionary_bytes": token_dictionary_bytes,
        "ngram_posting_bytes": ngram_posting_bytes,
        "edit_posting_bytes": edit_posting_bytes,
        "token_surface_posting_bytes": token_surface_posting_bytes,
        "simhash_lsh_bytes": simhash_lsh_bytes,
        "fuzzy_normalized_exact_char_standalone_bytes": (
            address_surface_bytes + ngram_posting_bytes
        ),
        "fuzzy_normalized_exact_char_edit_standalone_bytes": (
            address_surface_bytes
            + ngram_posting_bytes
            + token_dictionary_bytes
            + edit_posting_bytes
            + token_surface_posting_bytes
        ),
        "fuzzy_all_channel_standalone_bytes": (
            address_surface_bytes
            + ngram_posting_bytes
            + token_dictionary_bytes
            + edit_posting_bytes
            + token_surface_posting_bytes
            + simhash_lsh_bytes
        ),
    }
