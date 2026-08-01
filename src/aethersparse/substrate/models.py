"""Typed contracts for the v0.5 flat structured knowledge substrate.

The substrate deliberately models source documents independently of their content
hash.  Two pages may contain byte-identical text while remaining distinct source
objects with distinct page and revision identities.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from aethersparse.models import StrictModel


class SourcePage(StrictModel):
    """Immutable input page produced by a streaming corpus parser."""

    page_id: str = Field(min_length=1)
    namespace: int = 0
    revision_id: str = Field(min_length=1)
    revision_timestamp: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    license: str = Field(min_length=1)
    text: str
    source_sha256: str | None = None


class DocumentRecord(StrictModel):
    document_id: str
    page_id: str
    namespace: int
    revision_id: str
    revision_timestamp: str
    title: str
    normalized_title: str
    source_url: str
    license: str
    source_sha256: str
    source_bytes: int = Field(ge=0)
    text: str
    canonical_entity_id: str | None = None
    is_redirect: bool = False


class SourceBinding(StrictModel):
    """Exact character and UTF-8 byte coordinates within one immutable page."""

    binding_id: str
    document_id: str
    page_id: str
    revision_id: str
    source_sha256: str
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    byte_start: int = Field(ge=0)
    byte_end: int = Field(ge=0)
    surface_sha256: str
    surface: str

    @model_validator(mode="after")
    def valid_interval(self) -> SourceBinding:
        if self.char_end <= self.char_start:
            raise ValueError("source binding char interval must be non-empty")
        if self.byte_end <= self.byte_start:
            raise ValueError("source binding byte interval must be non-empty")
        return self


class EntityRecord(StrictModel):
    entity_id: str
    canonical_title: str
    normalized_title: str
    document_id: str
    entity_type: str = "unknown"


class AliasKind(StrEnum):
    TITLE = "title"
    REDIRECT = "redirect"
    ANCHOR = "anchor"
    EXPLICIT = "explicit"


class AliasRecord(StrictModel):
    alias_id: str
    surface: str
    normalized_surface: str
    entity_id: str
    kind: AliasKind
    support_binding_ids: tuple[str, ...] = ()


class ExplicitAliasSeed(StrictModel):
    surface: str = Field(min_length=1)
    target_title: str = Field(min_length=1)


class RedirectRecord(StrictModel):
    redirect_id: str
    source_document_id: str
    surface_title: str
    target_title: str
    target_entity_id: str
    binding_id: str


class AnchorRecord(StrictModel):
    anchor_id: str
    source_document_id: str
    surface: str
    normalized_surface: str
    target_title: str
    target_entity_id: str
    binding_id: str


class HeadingRecord(StrictModel):
    heading_id: str
    document_id: str
    level: int = Field(ge=1, le=6)
    text: str
    normalized_text: str
    binding_id: str


class ChunkRecord(StrictModel):
    chunk_id: str
    document_id: str
    ordinal: int = Field(ge=0)
    heading: str | None = None
    text: str
    binding_id: str


class ClaimKind(StrEnum):
    PROPOSITION = "proposition"
    EVENT = "event"
    DATE = "date"
    QUANTITY = "quantity"
    QUOTATION = "quotation"


class ObjectKind(StrEnum):
    ENTITY = "entity"
    TEXT = "text"
    DATE = "date"
    QUANTITY = "quantity"
    QUOTATION = "quotation"
    LOCATION = "location"
    EVENT = "event"


class ClaimAttribute(StrictModel):
    key: str
    value: str


class ClaimSeed(StrictModel):
    """Adjudicated claim input; evidence must resolve to exactly one source span."""

    page_id: str
    subject_title: str
    relation_family: str
    object_value: str
    object_kind: ObjectKind = ObjectKind.TEXT
    claim_kind: ClaimKind = ClaimKind.PROPOSITION
    evidence_text: str | None = None
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)
    attributes: tuple[ClaimAttribute, ...] = ()

    @model_validator(mode="after")
    def evidence_is_identifiable(self) -> ClaimSeed:
        has_offsets = self.char_start is not None or self.char_end is not None
        if has_offsets and (self.char_start is None or self.char_end is None):
            raise ValueError("claim evidence requires both char_start and char_end")
        if not has_offsets and not self.evidence_text:
            raise ValueError("claim evidence requires exact offsets or evidence_text")
        if (
            self.char_start is not None
            and self.char_end is not None
            and self.char_end <= self.char_start
        ):
            raise ValueError("claim evidence interval must be non-empty")
        return self


class StructuredClaim(StrictModel):
    claim_id: str
    subject_entity_id: str
    relation_family: str
    object_value: str
    object_entity_id: str | None = None
    object_kind: ObjectKind
    claim_kind: ClaimKind
    source_binding_ids: tuple[str, ...] = Field(min_length=1)
    source_document_ids: tuple[str, ...] = Field(min_length=1)
    attributes: tuple[ClaimAttribute, ...] = ()


class Posting(StrictModel):
    key: str
    document_ids: tuple[str, ...] = ()
    chunk_ids: tuple[str, ...] = ()
    claim_ids: tuple[str, ...] = ()


class FlatIndexes(StrictModel):
    lexical: tuple[Posting, ...]
    title: tuple[Posting, ...]
    heading: tuple[Posting, ...]
    phrase: tuple[Posting, ...]
    relation: tuple[Posting, ...]
    entity: tuple[Posting, ...]


class SubstrateMetadata(StrictModel):
    schema_version: Literal["0.5.0"] = "0.5.0"
    series_id: str
    source_dump_id: str
    source_dump_sha256: str
    parser_identity: str
    normalization_identity: str
    build_command: str
    parent_pack_hash: str | None = None


class FlatStructuredPack(StrictModel):
    metadata: SubstrateMetadata
    documents: tuple[DocumentRecord, ...]
    source_bindings: tuple[SourceBinding, ...]
    entities: tuple[EntityRecord, ...]
    aliases: tuple[AliasRecord, ...]
    redirects: tuple[RedirectRecord, ...]
    anchors: tuple[AnchorRecord, ...]
    headings: tuple[HeadingRecord, ...]
    chunks: tuple[ChunkRecord, ...]
    claims: tuple[StructuredClaim, ...]
    indexes: FlatIndexes
    manifest_sha256: str


class RetrievalRequest(StrictModel):
    text: str = Field(min_length=1)
    entity_ids: tuple[str, ...] = ()
    relation_families: tuple[str, ...] = ()
    answer_kind: ObjectKind | None = None
    temporal_constraint: str | None = None
    max_candidates: int = Field(default=128, ge=1, le=1024)
    top_k: int = Field(default=8, ge=1, le=64)


class FusionFeatures(StrictModel):
    lexical_hits: int = Field(ge=0)
    title_hits: int = Field(ge=0)
    heading_hits: int = Field(ge=0)
    phrase_hits: int = Field(ge=0)
    proximity: int = Field(ge=0, le=1)
    alias_fit: int = Field(ge=0, le=1)
    redirect_fit: int = Field(ge=0, le=1)
    anchor_fit: int = Field(ge=0, le=1)
    entity_fit: int = Field(ge=0, le=1)
    relation_fit: int = Field(ge=0, le=1)
    answer_type_fit: int = Field(ge=0, le=1)
    temporal_fit: int = Field(ge=0, le=1)


class RetrievedEvidence(StrictModel):
    rank: int = Field(ge=1)
    score: int
    document_id: str
    chunk_id: str
    binding_id: str
    matched_claim_ids: tuple[str, ...]
    features: FusionFeatures


class RetrievalResult(StrictModel):
    request: RetrievalRequest
    evidence: tuple[RetrievedEvidence, ...]
    considered_candidates: int = Field(ge=0)
    truncated: bool


class BinarySection(StrictModel):
    name: str
    relative_offset: int = Field(ge=0)
    length: int = Field(ge=0)
    sha256: str


class BinaryPackManifest(StrictModel):
    format_id: Literal["AETHERSPARSE_FLAT_STRUCTURED_PACK_V1"]
    pack_manifest_sha256: str
    metadata: SubstrateMetadata
    shard_count: int = Field(ge=1, le=256)
    sections: tuple[BinarySection, ...]
    payload_bytes: int = Field(ge=0)
    root_sha256: str


class BinaryPackArtifact(StrictModel):
    path: str
    manifest: BinaryPackManifest
    file_sha256: str
    total_bytes: int = Field(ge=0)


class PackReadTrace(StrictModel):
    section_names: tuple[str, ...]
    storage_reads: int = Field(ge=0)
    bytes_read: int = Field(ge=0)


class BinaryQueryRead(StrictModel):
    sections: tuple[tuple[str, bytes], ...]
    trace: PackReadTrace
