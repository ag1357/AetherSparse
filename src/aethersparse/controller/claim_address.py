"""Exact entity/relation/type addresses over immutable evidence records.

This module is deliberately not a semantic retriever.  It indexes only claims
that already have exact source-span bindings and returns them by authoritative
canonical entity ID, relation address, and typed answer shape.  Approximate
retrieval may supply fallback evidence, but it may not manufacture or rewrite
an address in this index.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable

from pydantic import Field, model_validator

from aethersparse.controller.evidence import build_evidence_graph
from aethersparse.controller.models import (
    AnswerShape,
    EvidenceGraph,
    EvidenceRecord,
    ExactSourceSpan,
    FrozenModel,
    QueryFrame,
    StructuredClaim,
)
from aethersparse.controller.value_lattice import TypedValueLattice, lattice_from_evidence

CLAIM_ADDRESS_SCHEMA_VERSION = "aethersparse.claim-address.v1"


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _shape_matches(frame_shape: AnswerShape, claim_shape: AnswerShape) -> bool:
    if frame_shape is AnswerShape.UNKNOWN:
        return True
    if frame_shape is AnswerShape.COMPARISON:
        return claim_shape in {AnswerShape.COMPARISON, AnswerShape.QUANTITY}
    if frame_shape is AnswerShape.LIST:
        return claim_shape in {
            AnswerShape.LIST,
            AnswerShape.ENTITY,
            AnswerShape.DEFINITION,
            AnswerShape.DATE,
            AnswerShape.QUANTITY,
        }
    return frame_shape is claim_shape


def _record_key(record: EvidenceRecord) -> tuple[str, str, str, str]:
    claim = record.claim
    return (
        claim.subject_entity_id,
        claim.relation_family,
        claim.answer_shape.value,
        claim.claim_id,
    )


class ClaimAddressManifest(FrozenModel):
    schema_version: str = CLAIM_ADDRESS_SCHEMA_VERSION
    record_count: int = Field(ge=0)
    entity_count: int = Field(ge=0)
    relation_count: int = Field(ge=0)
    source_span_count: int = Field(ge=0)
    index_sha256: str
    serialized_bytes: int = Field(ge=0)
    posting_serialized_bytes: int = Field(ge=0)
    source_region_bytes: int = Field(ge=0)


class ClaimAddressLookup(FrozenModel):
    """One bounded direct-address result with logical accounting inputs."""

    records: tuple[EvidenceRecord, ...]
    candidate_count_before_cap: int = Field(ge=0)
    candidate_count_after_cap: int = Field(ge=0)
    entity_postings_touched: int = Field(ge=0)
    relation_postings_touched: int = Field(ge=0)
    posting_bytes_read: int = Field(ge=0)
    posting_region_payload_bytes: tuple[int, ...] = ()
    source_region_bytes_read: int = Field(ge=0)
    source_region_payload_bytes: tuple[int, ...] = ()
    unresolved_entity_ids: tuple[str, ...] = ()
    unresolved_relation_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def counts_match_records(self) -> ClaimAddressLookup:
        if self.candidate_count_after_cap != len(self.records):
            raise ValueError("post-cap count must equal returned records")
        if self.candidate_count_before_cap < self.candidate_count_after_cap:
            raise ValueError("pre-cap count may not be smaller than post-cap count")
        if any(size <= 0 for size in self.posting_region_payload_bytes):
            raise ValueError("posting regions must contain positive payload bytes")
        if sum(self.posting_region_payload_bytes) != self.posting_bytes_read:
            raise ValueError("posting-region payload bytes must match the aggregate")
        if any(size < 0 for size in self.source_region_payload_bytes):
            raise ValueError("source-region payload bytes may not be negative")
        if sum(self.source_region_payload_bytes) != self.source_region_bytes_read:
            raise ValueError("source-region payload bytes must match the aggregate")
        return self

    def value_lattice(self, *, capacity: int = 64) -> TypedValueLattice:
        """Lift the exact result into the existing bounded typed value lattice."""

        return lattice_from_evidence(self.records, capacity=capacity)

    def evidence_graph(self, query_id: str, frame: QueryFrame) -> EvidenceGraph:
        """Build the existing exact evidence graph without changing verification."""

        return build_evidence_graph(query_id, frame, self.records)


class ClaimAddressIndex:
    """Immutable in-memory view of a deterministic source-bound sidecar."""

    def __init__(self, records: Iterable[EvidenceRecord]) -> None:
        unique: dict[str, EvidenceRecord] = {}
        for record in records:
            existing = unique.get(record.claim.claim_id)
            if existing is not None and existing != record:
                raise ValueError(f"claim ID collision: {record.claim.claim_id}")
            unique[record.claim.claim_id] = record
        self._records = tuple(sorted(unique.values(), key=_record_key))
        postings: dict[tuple[str, str, str], list[EvidenceRecord]] = defaultdict(list)
        by_entity: dict[str, list[EvidenceRecord]] = defaultdict(list)
        by_entity_shape: dict[tuple[str, str], list[EvidenceRecord]] = defaultdict(list)
        for record in self._records:
            claim = record.claim
            postings[
                (
                    claim.subject_entity_id,
                    claim.relation_family,
                    claim.answer_shape.value,
                )
            ].append(record)
            by_entity[claim.subject_entity_id].append(record)
            by_entity_shape[(claim.subject_entity_id, claim.answer_shape.value)].append(record)
        self._postings = {key: tuple(value) for key, value in sorted(postings.items())}
        self._relation_addresses = {
            (entity_id, relation_id) for entity_id, relation_id, _ in self._postings
        }
        self._by_entity = {key: tuple(value) for key, value in sorted(by_entity.items())}
        self._by_entity_shape = {
            key: tuple(value) for key, value in sorted(by_entity_shape.items())
        }
        self._record_bytes = {
            record.claim.claim_id: len(_canonical_json(self._posting(record)))
            for record in self._records
        }
        payload = self.to_bytes()
        self.manifest = ClaimAddressManifest(
            record_count=len(self._records),
            entity_count=len(self._by_entity),
            relation_count=len({record.claim.relation_family for record in self._records}),
            source_span_count=len(
                {span.span_id for record in self._records for span in record.source_spans}
            ),
            index_sha256=hashlib.sha256(payload).hexdigest(),
            serialized_bytes=len(payload),
            posting_serialized_bytes=sum(self._record_bytes.values()),
            source_region_bytes=sum(
                len(span.text.encode("utf-8"))
                for record in self._records
                for span in record.source_spans
            ),
        )

    @property
    def records(self) -> tuple[EvidenceRecord, ...]:
        return self._records

    def to_bytes(self) -> bytes:
        """Canonical content-addressable serialization of the exact sidecar."""

        return _canonical_json(
            {
                "records": [record.model_dump(mode="json") for record in self._records],
                "schema_version": CLAIM_ADDRESS_SCHEMA_VERSION,
            }
        )

    @staticmethod
    def _posting(record: EvidenceRecord) -> dict[str, object]:
        """Exact pointer record separated from immutable source payload bytes."""

        claim = record.claim
        return {
            "answer_shape": claim.answer_shape.value,
            "claim_id": claim.claim_id,
            "confidence": claim.confidence,
            "relation_family": claim.relation_family,
            "source_regions": [
                {
                    "char_end": span.char_end,
                    "char_start": span.char_start,
                    "document_id": span.document_id,
                    "span_id": span.span_id,
                    "text_hash": span.text_hash,
                }
                for span in record.source_spans
            ],
            "subject_entity_id": claim.subject_entity_id,
        }

    @classmethod
    def from_bytes(cls, payload: bytes) -> ClaimAddressIndex:
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise ValueError("claim address payload must be an object")
        if decoded.get("schema_version") != CLAIM_ADDRESS_SCHEMA_VERSION:
            raise ValueError("claim address schema version mismatch")
        rows = decoded.get("records")
        if not isinstance(rows, list):
            raise ValueError("claim address payload lacks records")
        return cls(EvidenceRecord.model_validate(row) for row in rows)

    def lookup(self, frame: QueryFrame, *, limit: int = 16) -> ClaimAddressLookup:
        """Resolve an exact address union before applying one global cap.

        All entity/relation combinations are unioned first.  Missing relations
        never suppress an entity posting: when the frame has no relation address,
        the bounded entity posting is used and uncertainty remains downstream.
        """

        if limit < 1 or limit > 64:
            raise ValueError("claim address limit must be in [1,64]")
        entities = tuple(dict.fromkeys(frame.candidate_entity_ids))
        relations = tuple(dict.fromkeys(frame.requested_relation_families))
        candidates: dict[str, EvidenceRecord] = {}
        posting_regions: list[tuple[EvidenceRecord, ...]] = []
        entity_touches = 0
        relation_touches = 0
        unresolved_entities: list[str] = []
        unresolved_relations: set[str] = set()
        for entity_id in entities:
            entity_records = self._by_entity.get(entity_id)
            if entity_records is None:
                unresolved_entities.append(entity_id)
                continue
            entity_touches += 1
            if not relations:
                for claim_shape in AnswerShape:
                    if not _shape_matches(frame.answer_shape, claim_shape):
                        continue
                    posting = self._by_entity_shape.get((entity_id, claim_shape.value))
                    if posting is None:
                        continue
                    posting_regions.append(posting)
                    for record in posting:
                        candidates.setdefault(record.claim.claim_id, record)
                continue
            entity_matched = False
            for relation_id in relations:
                if (entity_id, relation_id) not in self._relation_addresses:
                    unresolved_relations.add(relation_id)
                    continue
                entity_matched = True
                for claim_shape in AnswerShape:
                    if not _shape_matches(frame.answer_shape, claim_shape):
                        continue
                    posting = self._postings.get((entity_id, relation_id, claim_shape.value))
                    if posting is None:
                        continue
                    relation_touches += 1
                    posting_regions.append(posting)
                    for record in posting:
                        candidates.setdefault(record.claim.claim_id, record)
            if not entity_matched:
                # Fail closed.  A missing relation address is not permission to
                # silently return every fact about the entity.
                continue
        eligible = [
            record
            for record in candidates.values()
            if _shape_matches(frame.answer_shape, record.claim.answer_shape)
        ]
        eligible.sort(
            key=lambda record: (
                -record.claim.confidence,
                record.claim.subject_entity_id,
                record.claim.relation_family,
                record.claim.claim_id,
            )
        )
        selected = tuple(eligible[:limit])
        eligible_ids = {record.claim.claim_id for record in eligible}
        posting_region_bytes = tuple(
            size
            for posting in posting_regions
            if (
                size := sum(
                    self._record_bytes[record.claim.claim_id]
                    for record in posting
                    if record.claim.claim_id in eligible_ids
                )
            )
        )
        posting_bytes = sum(posting_region_bytes)
        unique_source_spans: dict[str, ExactSourceSpan] = {}
        for record in selected:
            for span in record.source_spans:
                existing = unique_source_spans.get(span.span_id)
                if existing is not None and existing != span:
                    raise ValueError(f"source span ID collision: {span.span_id}")
                unique_source_spans[span.span_id] = span
        ordered_source_spans = sorted(
            unique_source_spans.values(),
            key=lambda span: (span.document_id, span.char_start, span.char_end, span.span_id),
        )
        source_region_bytes = tuple(len(span.text.encode("utf-8")) for span in ordered_source_spans)
        source_bytes = sum(source_region_bytes)
        return ClaimAddressLookup(
            records=selected,
            candidate_count_before_cap=len(eligible),
            candidate_count_after_cap=len(selected),
            entity_postings_touched=entity_touches,
            relation_postings_touched=relation_touches,
            posting_bytes_read=posting_bytes,
            posting_region_payload_bytes=posting_region_bytes,
            source_region_bytes_read=source_bytes,
            source_region_payload_bytes=source_region_bytes,
            unresolved_entity_ids=tuple(unresolved_entities),
            unresolved_relation_ids=tuple(sorted(unresolved_relations)),
        )


def evidence_records_from_replay(
    claims: Iterable[dict[str, object]],
    source_spans: Iterable[dict[str, object]],
) -> tuple[EvidenceRecord, ...]:
    """Strictly lift replay claims already bound to exact retained spans.

    This helper performs no extraction and reads no labels.  Invalid or
    incomplete records are omitted rather than repaired.
    """

    spans: dict[str, ExactSourceSpan] = {}
    for payload in source_spans:
        try:
            span = ExactSourceSpan.model_validate(payload)
        except ValueError:
            continue
        spans[span.span_id] = span
    records: list[EvidenceRecord] = []
    for payload in claims:
        try:
            claim = StructuredClaim.model_validate(payload)
        except ValueError:
            continue
        bound = tuple(spans[span_id] for span_id in claim.source_span_ids if span_id in spans)
        if len(bound) != len(claim.source_span_ids) or not bound:
            continue
        surface = (
            claim.quotation
            if claim.answer_shape is AnswerShape.QUOTATION and claim.quotation
            else claim.object_value
        )
        if not surface or not any(surface in span.text for span in bound):
            continue
        records.append(
            EvidenceRecord(
                claim=claim,
                source_spans=bound,
                entity_fit=1.0,
                relation_fit=1.0,
                answerability=1.0,
                answer_shape_fit=1.0,
                temporal_fit=1.0,
                attribution_fit=1.0,
                source_quality=1.0,
                facet_coverage=(),
            )
        )
    return tuple(records)
