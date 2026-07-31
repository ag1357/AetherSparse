"""Deterministic answer planning and realization with direct claim bindings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from aethersparse.models import (
    Citation,
    ClaimBinding,
    EventPayload,
    KnowledgePacket,
    ParseFrame,
    PropositionPayload,
    QuotationPayload,
    SourceSpan,
)
from aethersparse.parser import (
    REL_CREW_MEMBERS,
    REL_LANDING_PARTICIPANT,
    REL_LANDING_VEHICLE,
    REL_LAUNCHED_ON,
    REL_OCCURRED_ON,
    REL_REMAINED_IN_ORBIT,
    REL_RETURNED_ON,
    REL_SAID,
)


@dataclass(frozen=True)
class RealizedAnswer:
    sentence: str
    citations: tuple[Citation, ...]
    bindings: tuple[ClaimBinding, ...]


def display_date(value: date) -> str:
    return f"{value.strftime('%B')} {value.day}, {value.year}"


def _claim(packet: KnowledgePacket, relation: str, object_fragment: str | None = None) -> str:
    for claim in packet.atomic_claims:
        if claim.relation_id == relation and (
            object_fragment is None or object_fragment in claim.object_value
        ):
            return claim.claim_unit_id
    raise ValueError(f"packet {packet.header.packet_id} lacks required claim {relation}")


def _binding(
    sentence: str,
    surface: str,
    packet: KnowledgePacket,
    claim_id: str,
    *,
    occurrence: int = 0,
) -> ClaimBinding:
    start = -1
    cursor = 0
    for _ in range(occurrence + 1):
        start = sentence.find(surface, cursor)
        if start < 0:
            raise ValueError(f"surface {surface!r} not found in deterministic realization")
        cursor = start + len(surface)
    return ClaimBinding(
        surface=surface,
        start=start,
        end=start + len(surface),
        claim_unit_id=claim_id,
        packet_id=packet.header.packet_id,
        source_span_ids=packet.header.source_span_ids,
    )


def _citation(span: SourceSpan) -> Citation:
    return Citation(
        citation_id=f"cite:{span.source_span_id}",
        source_span_id=span.source_span_id,
        source_doc_id=span.source_doc_id,
        source_title=span.source_title,
        source_url=span.source_url,
        source_revision=span.source_revision,
        quoted_text=span.text,
        char_start=span.char_start,
        char_end=span.char_end,
    )


def realize(
    frame: ParseFrame,
    packet: KnowledgePacket,
    spans: dict[str, SourceSpan],
) -> RealizedAnswer:
    relation = frame.relation_id
    bindings: list[ClaimBinding] = []

    if relation == REL_OCCURRED_ON and isinstance(packet.payload, EventPayload):
        if packet.payload.occurred_on is None:
            raise ValueError("landing event is missing occurred_on")
        sentence = (
            f"Apollo 11 landed on the Moon on {display_date(packet.payload.occurred_on)}."
        )
        bindings.extend(
            [
                _binding(
                    sentence,
                    "Apollo 11",
                    packet,
                    _claim(packet, packet.header.primary_relation),
                ),
                _binding(
                    sentence,
                    "the Moon",
                    packet,
                    _claim(packet, packet.header.primary_relation),
                ),
                _binding(
                    sentence,
                    display_date(packet.payload.occurred_on),
                    packet,
                    _claim(packet, REL_OCCURRED_ON),
                ),
            ]
        )
    elif relation == REL_LANDING_PARTICIPANT and isinstance(packet.payload, EventPayload):
        names = " and ".join(packet.payload.participants)
        sentence = f"{names} landed on the Moon during Apollo 11."
        for name in packet.payload.participants:
            bindings.append(
                _binding(
                    sentence,
                    name,
                    packet,
                    _claim(packet, REL_LANDING_PARTICIPANT, name.split()[-1].casefold()),
                )
            )
        landing_claim = _claim(packet, packet.header.primary_relation)
        bindings.extend(
            [
                _binding(sentence, "the Moon", packet, landing_claim),
                _binding(sentence, "Apollo 11", packet, landing_claim),
            ]
        )
    elif relation == REL_LANDING_VEHICLE and isinstance(packet.payload, EventPayload):
        if packet.payload.vehicle is None:
            raise ValueError("landing event is missing a vehicle")
        sentence = (
            f"Apollo 11 landed on the Moon in the lunar module {packet.payload.vehicle}."
        )
        landing_claim = _claim(packet, packet.header.primary_relation)
        bindings.extend(
            [
                _binding(sentence, "Apollo 11", packet, landing_claim),
                _binding(sentence, "the Moon", packet, landing_claim),
                _binding(
                    sentence,
                    packet.payload.vehicle,
                    packet,
                    _claim(packet, REL_LANDING_VEHICLE),
                ),
            ]
        )
    elif relation in {REL_LAUNCHED_ON, REL_RETURNED_ON} and isinstance(
        packet.payload, PropositionPayload
    ):
        verb = "launched on" if relation == REL_LAUNCHED_ON else "returned to Earth on"
        sentence = f"Apollo 11 {verb} {packet.payload.object_label}."
        claim_id = _claim(packet, relation)
        bindings.extend(
            [
                _binding(sentence, "Apollo 11", packet, claim_id),
                _binding(sentence, packet.payload.object_label, packet, claim_id),
            ]
        )
    elif relation == REL_CREW_MEMBERS and isinstance(packet.payload, PropositionPayload):
        sentence = f"The Apollo 11 crew was {packet.payload.object_label}."
        claim_id = _claim(packet, relation)
        bindings.extend(
            [
                _binding(sentence, "Apollo 11", packet, claim_id),
                _binding(sentence, packet.payload.object_label, packet, claim_id),
            ]
        )
    elif relation == REL_REMAINED_IN_ORBIT and isinstance(
        packet.payload, PropositionPayload
    ):
        sentence = "Michael Collins remained in lunar orbit while Armstrong and Aldrin walked."
        claim_id = _claim(packet, relation)
        bindings.extend(
            [
                _binding(sentence, "Michael Collins", packet, claim_id),
                _binding(sentence, "lunar orbit", packet, claim_id),
            ]
        )
    elif relation == REL_SAID and isinstance(packet.payload, QuotationPayload):
        sentence = f"{packet.payload.speaker_label} said, “{packet.payload.quotation}”"
        bindings.extend(
            [
                _binding(
                    sentence,
                    packet.payload.speaker_label,
                    packet,
                    "cu:armstrong:quote_attribution",
                ),
                _binding(
                    sentence,
                    packet.payload.quotation,
                    packet,
                    "cu:armstrong:quote_text",
                ),
            ]
        )
    else:
        raise ValueError(f"no deterministic realization for relation {relation}")

    citation_spans = tuple(spans[span_id] for span_id in packet.header.source_span_ids)
    return RealizedAnswer(
        sentence=sentence,
        citations=tuple(_citation(span) for span in citation_spans),
        bindings=tuple(bindings),
    )

