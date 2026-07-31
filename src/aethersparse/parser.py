"""Deterministic provisional parser with explicit unknown-span copying."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from aethersparse.models import Intent, ParseFrame, UnknownSpan

ROOT = Path(__file__).resolve().parents[2]
ENTITY_CATALOG = ROOT / "data" / "normalized" / "entity_catalog.json"

APOLLO_11 = "as:concept:apollo_11:01JAS000000000000000001"
MOON = "as:concept:moon:01JAS000000000000000002"
NEIL_ARMSTRONG = "as:concept:neil_armstrong:01JAS000000000003"
MICHAEL_COLLINS = "as:concept:michael_collins:01JAS000000000005"

REL_LANDED_ON = "as:rel:landed_on:01JASR000000000000001"
REL_OCCURRED_ON = "as:rel:occurred_on:01JASR000000000000002"
REL_LANDING_PARTICIPANT = "as:rel:landing_participant:01JASR00000000003"
REL_LANDING_VEHICLE = "as:rel:landing_vehicle:01JASR0000000000004"
REL_LAUNCHED_ON = "as:rel:launched_on:01JASR000000000000005"
REL_RETURNED_ON = "as:rel:returned_on:01JASR000000000000006"
REL_CREW_MEMBERS = "as:rel:crew_members:01JASR000000000000007"
REL_REMAINED_IN_ORBIT = "as:rel:remained_in_lunar_orbit:01JASR000000008"
REL_SAID = "as:rel:said:01JASR000000000000009"

DOMAIN_TERMS = {
    "apollo",
    "moon",
    "lunar",
    "astronaut",
    "armstrong",
    "aldrin",
    "collins",
    "eagle",
    "one small step",
}


class DeterministicParser:
    """Small rule baseline; learned parsing is intentionally absent."""

    def __init__(self, catalog_path: Path = ENTITY_CATALOG) -> None:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        self.alias_to_id: dict[str, str] = {}
        for entity in catalog["entities"]:
            for alias in entity["aliases"]:
                self.alias_to_id[alias.casefold()] = str(entity["concept_id"])

    @staticmethod
    def normalize(text: str) -> str:
        return " ".join(unicodedata.normalize("NFC", text).strip().split())

    def _unknown_mission_spans(self, text: str) -> tuple[UnknownSpan, ...]:
        spans: list[UnknownSpan] = []
        for match in re.finditer(r"\bApollo\s+(?:\d+|[IVX]+)\b", text, re.IGNORECASE):
            surface = match.group(0)
            if surface.casefold() not in self.alias_to_id:
                spans.append(
                    UnknownSpan(
                        surface=surface,
                        char_start=match.start(),
                        char_end=match.end(),
                    )
                )
        part_pattern = re.compile(r"\b[A-Z]{2,}\d{2,}[A-Z0-9-]*\b")
        for match in part_pattern.finditer(text):
            spans.append(
                UnknownSpan(
                    surface=match.group(0),
                    char_start=match.start(),
                    char_end=match.end(),
                )
            )
        return tuple(spans)

    def parse(self, text: str) -> ParseFrame:
        normalized = self.normalize(text)
        folded = normalized.casefold()
        unknown = self._unknown_mission_spans(normalized)

        if unknown:
            return ParseFrame(
                intent=Intent.UNKNOWN,
                confidence=0.2,
                unknown_spans=unknown,
                ambiguity_flags=("unknown_entity",),
            )

        entity_id: str | None = None
        for alias, candidate_id in sorted(
            self.alias_to_id.items(), key=lambda item: len(item[0]), reverse=True
        ):
            if alias in folded:
                entity_id = candidate_id
                break
        if "apollo 11" in folded or "apollo xi" in folded:
            entity_id = APOLLO_11

        has_domain_signal = any(term in folded for term in DOMAIN_TERMS)
        if not has_domain_signal:
            return ParseFrame(intent=Intent.UNKNOWN, confidence=0.0)

        asks_when = "when" in folded or "what date" in folded or "which date" in folded
        if ("land" in folded or "touchdown" in folded) and asks_when:
            return ParseFrame(
                intent=Intent.TEMPORAL_WHEN,
                entity_id=APOLLO_11 if entity_id in {None, MOON} else entity_id,
                relation_id=REL_OCCURRED_ON,
                answer_slot="occurred_on",
                confidence=0.98,
            )
        if ("launch" in folded or "liftoff" in folded) and asks_when:
            return ParseFrame(
                intent=Intent.TEMPORAL_WHEN,
                entity_id=APOLLO_11,
                relation_id=REL_LAUNCHED_ON,
                answer_slot="date",
                confidence=0.98,
            )
        if any(term in folded for term in ("return", "splashdown", "back on earth")) and asks_when:
            return ParseFrame(
                intent=Intent.TEMPORAL_WHEN,
                entity_id=APOLLO_11,
                relation_id=REL_RETURNED_ON,
                answer_slot="date",
                confidence=0.98,
            )
        if "who" in folded and ("land" in folded or "walked" in folded):
            return ParseFrame(
                intent=Intent.FACT_LOOKUP,
                entity_id=APOLLO_11,
                relation_id=REL_LANDING_PARTICIPANT,
                answer_slot="participants",
                confidence=0.97,
            )
        if ("lunar module" in folded or "vehicle" in folded) and (
            "which" in folded or "what" in folded or "land" in folded
        ):
            return ParseFrame(
                intent=Intent.FACT_LOOKUP,
                entity_id=APOLLO_11,
                relation_id=REL_LANDING_VEHICLE,
                answer_slot="vehicle",
                confidence=0.97,
            )
        if "crew" in folded and ("who" in folded or "member" in folded):
            return ParseFrame(
                intent=Intent.FACT_LOOKUP,
                entity_id=APOLLO_11,
                relation_id=REL_CREW_MEMBERS,
                answer_slot="entities",
                confidence=0.98,
            )
        if "collins" in folded and (
            "where" in folded or "what did" in folded or "during" in folded
        ):
            return ParseFrame(
                intent=Intent.FACT_LOOKUP,
                entity_id=MICHAEL_COLLINS,
                relation_id=REL_REMAINED_IN_ORBIT,
                answer_slot="location",
                confidence=0.96,
            )
        if "who said" in folded or "one small step" in folded:
            return ParseFrame(
                intent=Intent.QUOTE_WHO_SAID,
                entity_id=NEIL_ARMSTRONG,
                relation_id=REL_SAID,
                answer_slot="speaker",
                confidence=0.99,
            )

        return ParseFrame(
            intent=Intent.UNKNOWN,
            entity_id=entity_id,
            confidence=0.35,
            ambiguity_flags=("unsupported_frame",),
        )

