"""Transparent sparse symbolic retrieval and evidence selection."""

from __future__ import annotations

from collections import defaultdict

from aethersparse.models import CompiledPack, KnowledgePacket, PacketStatus, ParseFrame


class KnowledgeStore:
    """In-memory host reference for indexes that become packed tables later."""

    def __init__(self, pack: CompiledPack) -> None:
        self.pack = pack
        self.by_relation: dict[str, list[KnowledgePacket]] = defaultdict(list)
        self.by_packet_id: dict[str, KnowledgePacket] = {}
        self.span_by_id = {span.source_span_id: span for span in pack.source_spans}
        for packet in pack.packets:
            self.by_packet_id[packet.header.packet_id] = packet
            indexed_relations = {packet.header.primary_relation}
            indexed_relations.update(claim.relation_id for claim in packet.atomic_claims)
            for relation in indexed_relations:
                self.by_relation[relation].append(packet)

    def retrieve(self, frame: ParseFrame) -> tuple[KnowledgePacket, ...]:
        if frame.relation_id is None:
            return ()
        candidates = self.by_relation.get(frame.relation_id, [])
        result = [
            packet
            for packet in candidates
            if packet.header.status is PacketStatus.CANONICAL
            and (
                frame.entity_id is None
                or frame.entity_id in packet.header.concept_ids
                or frame.entity_id == packet.header.primary_subject
            )
        ]
        return tuple(
            sorted(
                result,
                key=lambda packet: (-packet.header.packet_quality, packet.header.packet_id),
            )
        )

    def select_evidence(
        self,
        candidates: tuple[KnowledgePacket, ...],
        *,
        max_packets: int = 3,
    ) -> tuple[KnowledgePacket, ...]:
        """Choose independent canonical evidence families deterministically."""

        selected: list[KnowledgePacket] = []
        seen_checksums: set[str] = set()
        for packet in candidates:
            if packet.header.checksum in seen_checksums:
                continue
            selected.append(packet)
            seen_checksums.add(packet.header.checksum)
            if len(selected) >= max_packets:
                break
        return tuple(selected)

