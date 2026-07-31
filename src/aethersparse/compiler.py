"""Deterministic compiler for manually reviewed Tier 1 packet definitions."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from aethersparse.models import (
    AtomicClaim,
    CompiledPack,
    EventPayload,
    KeyClass,
    KnowledgePacket,
    PacketHeader,
    PacketStatus,
    PacketType,
    PackManifest,
    PropositionPayload,
    QuotationPayload,
    SourceDocument,
    SourceSpan,
)

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"
GOLD_FILE = ROOT / "data" / "gold_packets" / "tier1_reviewed.json"
COMPILED_FILE = ROOT / "data" / "compiled" / "apollo_smoke_pack.json"


class CompilationError(ValueError):
    """Raised when provenance or canonicalization invariants fail."""


def stable_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _load_sources(raw_dir: Path) -> dict[str, SourceDocument]:
    adapter = TypeAdapter(SourceDocument)
    sources: dict[str, SourceDocument] = {}
    for path in sorted(raw_dir.glob("*.json")):
        source = adapter.validate_json(path.read_text(encoding="utf-8"))
        if source.source_doc_id in sources:
            raise CompilationError(f"duplicate source_doc_id: {source.source_doc_id}")
        sources[source.source_doc_id] = source
    if not sources:
        raise CompilationError("no source snapshots found")
    return sources


def _span_for(
    source: SourceDocument,
    evidence_text: str,
    cache: dict[tuple[str, int, int], SourceSpan],
) -> SourceSpan:
    first = source.text.find(evidence_text)
    if first < 0:
        raise CompilationError(
            f"evidence is not an exact substring of {source.source_doc_id}: {evidence_text!r}"
        )
    if source.text.find(evidence_text, first + 1) >= 0:
        raise CompilationError(
            f"evidence occurs more than once in {source.source_doc_id}; alignment is ambiguous"
        )
    end = first + len(evidence_text)
    key = (source.source_doc_id, first, end)
    if key not in cache:
        span_digest = hashlib.sha256(
            f"{source.source_doc_id}:{first}:{end}".encode()
        ).hexdigest()[:16]
        cache[key] = SourceSpan(
            source_span_id=f"as:span:{span_digest}",
            source_doc_id=source.source_doc_id,
            source_title=source.title,
            source_revision=source.source_revision,
            source_url=source.source_url,
            source_group=source.source_group,
            license=source.license,
            char_start=first,
            char_end=end,
            text_hash=sha256_bytes(evidence_text.encode("utf-8")),
            text=evidence_text,
        )
    return cache[key]


def _payload(packet_type: PacketType, raw: dict[str, Any]) -> Any:
    if packet_type is PacketType.PROPOSITION:
        return PropositionPayload.model_validate(raw)
    if packet_type is PacketType.EVENT:
        return EventPayload.model_validate(raw)
    if packet_type is PacketType.QUOTATION:
        return QuotationPayload.model_validate(raw)
    raise CompilationError(f"unsupported packet type in first milestone: {packet_type}")


def _validate_claim_surface(claim: dict[str, Any], evidence_text: str) -> None:
    surface = str(claim["evidence_surface"])
    if surface not in evidence_text:
        raise CompilationError(
            f"atomic claim surface {surface!r} is not present in its aligned evidence"
        )


def compile_pack(
    raw_dir: Path = RAW_DIR,
    gold_file: Path = GOLD_FILE,
    output_file: Path | None = COMPILED_FILE,
) -> CompiledPack:
    """Compile reviewed definitions into a deterministic, hash-bound pack."""

    sources = _load_sources(raw_dir)
    gold = json.loads(gold_file.read_text(encoding="utf-8"))
    if gold.get("review_status") != "human_reviewed":
        raise CompilationError("gold definitions must be explicitly human_reviewed")

    spans: dict[tuple[str, int, int], SourceSpan] = {}
    packets: list[KnowledgePacket] = []
    packet_ids: set[str] = set()

    for raw_packet in sorted(gold["packets"], key=lambda item: item["packet_id"]):
        packet_id = str(raw_packet["packet_id"])
        if packet_id in packet_ids:
            raise CompilationError(f"duplicate packet_id: {packet_id}")
        packet_ids.add(packet_id)

        if raw_packet["status"] != PacketStatus.CANONICAL:
            continue
        if raw_packet["tier"] != 1:
            raise CompilationError("first canonical pack may contain Tier 1 only")
        if raw_packet["derivation"] == "teacher_candidate":
            raise CompilationError("teacher candidates cannot be canonical")

        source = sources[str(raw_packet["source_doc_id"])]
        evidence_text = str(raw_packet["evidence_text"])
        span = _span_for(source, evidence_text, spans)
        packet_type = PacketType(raw_packet["packet_type"])
        packet_payload = _payload(packet_type, raw_packet["payload"])

        claims: list[AtomicClaim] = []
        for raw_claim in raw_packet["atomic_claims"]:
            _validate_claim_surface(raw_claim, evidence_text)
            claim_data = {
                key: value for key, value in raw_claim.items() if key != "evidence_surface"
            }
            claims.append(
                AtomicClaim(
                    **claim_data,
                    aligned_span_ids=(span.source_span_id,),
                )
            )

        header_seed = {
            "packet_id": packet_id,
            "packet_type": packet_type,
            "primary_subject": raw_packet["primary_subject"],
            "primary_relation": raw_packet["primary_relation"],
            "primary_object": raw_packet["primary_object"],
            "source_span_id": span.source_span_id,
            "payload": packet_payload.model_dump(mode="json"),
            "claims": [claim.model_dump(mode="json") for claim in claims],
        }
        checksum = sha256_bytes(stable_json(header_seed))
        header = PacketHeader(
            packet_id=packet_id,
            packet_type=packet_type,
            status=PacketStatus.CANONICAL,
            tier=1,
            primary_subject=raw_packet["primary_subject"],
            primary_relation=raw_packet["primary_relation"],
            primary_object=raw_packet["primary_object"],
            concept_ids=tuple(raw_packet["concept_ids"]),
            bucket_id=raw_packet["bucket_id"],
            valid_from=raw_packet.get("valid_from"),
            valid_to=raw_packet.get("valid_to"),
            recorded_at=raw_packet.get("recorded_at"),
            source_span_ids=(span.source_span_id,),
            derivation=raw_packet["derivation"],
            perspective=raw_packet.get("perspective", "asserted_fact"),
            polarity=raw_packet.get("polarity", "positive"),
            modality=raw_packet.get("modality", "asserted"),
            packet_quality=raw_packet["packet_quality"],
            license=source.license,
            checksum=checksum,
            key_class=KeyClass(raw_packet["key_class"]),
        )
        packets.append(
            KnowledgePacket(
                header=header,
                payload=packet_payload,
                atomic_claims=tuple(claims),
            )
        )

    ordered_spans = tuple(sorted(spans.values(), key=lambda span: span.source_span_id))
    ordered_packets = tuple(sorted(packets, key=lambda packet: packet.header.packet_id))
    source_manifest = [
        {
            **source.model_dump(mode="json", exclude={"text"}),
            "content_hash": sha256_bytes(source.text.encode("utf-8")),
        }
        for source in sorted(sources.values(), key=lambda item: item.source_doc_id)
    ]
    source_manifest_hash = sha256_bytes(stable_json(source_manifest))
    normalized_source_bytes = sum(len(source.text.encode("utf-8")) for source in sources.values())

    payload_bytes = sum(
        len(stable_json(packet.payload.model_dump(mode="json"))) for packet in ordered_packets
    )
    key_bytes = sum(
        {
            KeyClass.K0: 0,
            KeyClass.K1: 16,
            KeyClass.K2: 32,
            KeyClass.K3: 128,
        }[packet.header.key_class]
        for packet in ordered_packets
    )
    index_bytes = len(ordered_packets) * 40
    span_bytes = sum(64 + len(span.text.encode("utf-8")) for span in ordered_spans)
    logical_query_pack_bytes = len(ordered_packets) * 128 + payload_bytes + key_bytes
    logical_query_pack_bytes += index_bytes + span_bytes

    unsigned_content = {
        "pack_id": gold["pack_id"],
        "ontology_version": gold["ontology_version"],
        "compiler_version": gold["compiler_version"],
        "source_manifest_hash": source_manifest_hash,
        "extraction_config_hash": sha256_bytes(stable_json(gold["extraction_config"])),
        "validator_config_hash": sha256_bytes(stable_json(gold["validator_config"])),
        "packet_count": len(ordered_packets),
        "span_count": len(ordered_spans),
        "normalized_source_bytes": normalized_source_bytes,
        "logical_query_pack_bytes": logical_query_pack_bytes,
        "source_spans": [span.model_dump(mode="json") for span in ordered_spans],
        "packets": [packet.model_dump(mode="json") for packet in ordered_packets],
    }
    manifest_hash = sha256_bytes(stable_json(unsigned_content))
    manifest = PackManifest(
        pack_id=gold["pack_id"],
        ontology_version=gold["ontology_version"],
        compiler_version=gold["compiler_version"],
        source_manifest_hash=source_manifest_hash,
        extraction_config_hash=unsigned_content["extraction_config_hash"],
        validator_config_hash=unsigned_content["validator_config_hash"],
        packet_count=len(ordered_packets),
        span_count=len(ordered_spans),
        normalized_source_bytes=normalized_source_bytes,
        logical_query_pack_bytes=logical_query_pack_bytes,
        logical_compiled_bytes_per_source_byte=(
            logical_query_pack_bytes / normalized_source_bytes
            if normalized_source_bytes
            else 0.0
        ),
        manifest_hash=manifest_hash,
        signature="UNSIGNED_HOST_EMULATOR",
    )
    pack = CompiledPack(
        manifest=manifest,
        source_spans=ordered_spans,
        packets=ordered_packets,
    )
    if output_file is not None:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(
            json.dumps(pack.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
    return pack


def load_pack(path: Path = COMPILED_FILE) -> CompiledPack:
    if not path.exists():
        return compile_pack(output_file=path)
    return CompiledPack.model_validate_json(path.read_text(encoding="utf-8"))
