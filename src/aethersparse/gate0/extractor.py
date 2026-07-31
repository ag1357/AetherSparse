"""Reproducible rule extractor that emits candidates and never canonical packets."""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aethersparse.gate0.models import (
    CandidateClaimUnit,
    CandidatePacket,
    ExtractionRun,
    ExtractorProvenance,
    FrozenSourceSnapshot,
    QuantityValue,
)
from aethersparse.gate0.sources import (
    SourceRepository,
    align_evidence,
    align_normalized_range,
    sha256_text,
    stable_json,
)
from aethersparse.models import KeyClass, PacketType

EXTRACTOR_IDENTITY = "aethersparse_explicit_rule_extractor"
EXTRACTOR_VERSION = "1.0.0"
RULE_VERSION = "tier1-explicit-rules-v1"

MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

DATE_PATTERN = re.compile(
    r"\b(?P<month>January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+"
    r"(?P<day>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<year>\d{4})\b",
    re.IGNORECASE,
)
MISSION_PATTERN = re.compile(r"\bApollo\s+\d+\b", re.IGNORECASE)
PERSON_PATTERN = re.compile(r"\b(?:[A-Z][A-Za-z.'-]*\s+){1,3}(?:[A-Z][A-Za-z.'-]*)\b")
QUANTITY_PATTERN = re.compile(
    r"\b(?P<value>\d+(?:,\d{3})*(?:\.\d+)?)\s*"
    r"(?P<unit>miles?|feet|hours?|minutes?|seconds?|pounds?|psi|kilometers?|meters?)\b",
    re.IGNORECASE,
)

EVENT_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("launched_on", re.compile(r"\b(?:launched|lifted off)\b", re.IGNORECASE), "launched"),
    (
        "landed_at",
        re.compile(r"\b(?:landed|touched down)\b", re.IGNORECASE),
        "landed",
    ),
    (
        "returned_on",
        re.compile(r"\b(?:splashed down|splashdown|returned to Earth)\b", re.IGNORECASE),
        "returned",
    ),
    (
        "entered_orbit",
        re.compile(r"\b(?:entered lunar orbit|orbited the moon)\b", re.IGNORECASE),
        "entered orbit",
    ),
    ("separated_from", re.compile(r"\bseparated\b", re.IGNORECASE), "separated"),
    ("conducted", re.compile(r"\bconducted\b", re.IGNORECASE), "conducted"),
)

ROLE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "commander",
        re.compile(r"\bCommander\s+(?P<person>[A-Z][A-Za-z.]+(?:\s+[A-Z][A-Za-z.'-]+){1,3})"),
    ),
    (
        "command_module_pilot",
        re.compile(
            r"\bCommand Module Pilot\s+(?P<person>"
            r"[A-Z][A-Za-z.]+(?:\s+[A-Z][A-Za-z.'\"-]+){1,4})"
        ),
    ),
    (
        "lunar_module_pilot",
        re.compile(
            r"\bLunar Module Pilot\s+(?P<person>"
            r"[A-Z][A-Za-z.]+(?:\s+[A-Z][A-Za-z.'\"-]+){1,4})"
        ),
    ),
)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_") or "unknown"


def stable_id(kind: str, label: str) -> str:
    digest = hashlib.sha256(f"{kind}:{label.casefold()}".encode()).hexdigest()[:12]
    return f"as:{kind}:{_slug(label)}:{digest}"


def relation_id(label: str) -> str:
    return stable_id("rel", label)


def entity_id(label: str) -> str:
    return stable_id("concept", label)


def _configuration_hash() -> str:
    configuration = {
        "extractor_identity": EXTRACTOR_IDENTITY,
        "extractor_version": EXTRACTOR_VERSION,
        "rule_version": RULE_VERSION,
        "event_rules": [rule_id for rule_id, _pattern, _label in EVENT_RULES],
        "role_rules": [role for role, _pattern in ROLE_PATTERNS],
    }
    return sha256_text(stable_json(configuration).decode("utf-8"))


def _cache_identity(snapshot: FrozenSourceSnapshot) -> str:
    return sha256_text(
        f"{snapshot.raw_content_hash}|{snapshot.source_revision}|{_configuration_hash()}"
    )


def _dates(sentence: str) -> tuple[str, ...]:
    values: list[str] = []
    for match in DATE_PATTERN.finditer(sentence):
        month = MONTHS[match.group("month").casefold()]
        values.append(f"{int(match.group('year')):04d}-{month:02d}-{int(match.group('day')):02d}")
    return tuple(values)


def _quantities(sentence: str, owner: str | None) -> tuple[QuantityValue, ...]:
    result: list[QuantityValue] = []
    for match in QUANTITY_PATTERN.finditer(sentence):
        value = float(match.group("value").replace(",", ""))
        unit = match.group("unit").casefold()
        if unit.endswith("s") and unit not in {"miles"}:
            unit = unit[:-1]
        result.append(
            QuantityValue(
                surface=match.group(0),
                normalized_value=value,
                normalized_unit=unit,
                owner_entity_id=entity_id(owner) if owner else None,
            )
        )
    return tuple(result)


def _entities(sentence: str) -> tuple[tuple[str, str], ...]:
    surfaces: list[str] = []
    surfaces.extend(match.group(0) for match in MISSION_PATTERN.finditer(sentence))
    for match in PERSON_PATTERN.finditer(sentence):
        surface = match.group(0).strip()
        if surface.casefold().startswith(
            ("the ", "on ", "after ", "before ", "mission ", "lunar ", "command ")
        ):
            continue
        if surface not in surfaces:
            surfaces.append(surface)
    return tuple((surface, entity_id(surface)) for surface in surfaces)


def _subject(sentence: str) -> tuple[str, str]:
    mission = MISSION_PATTERN.search(sentence)
    if mission:
        surface = mission.group(0)
        return surface, entity_id(surface)
    entities = _entities(sentence)
    if entities:
        return entities[0]
    first_words = " ".join(sentence.split()[:4]).strip(" ,.")
    return first_words, entity_id(first_words)


def _sentence_ranges(normalized_text: str) -> tuple[tuple[int, int, str], ...]:
    result: list[tuple[int, int, str]] = []
    for match in re.finditer(r"[^.!?]+(?:[.!?]+|$)", normalized_text):
        start, end = match.span()
        while start < end and normalized_text[start].isspace():
            start += 1
        while end > start and normalized_text[end - 1].isspace():
            end -= 1
        sentence = normalized_text[start:end]
        if len(sentence.split()) >= 4:
            result.append((start, end, sentence))
    return tuple(result)


def _candidate(
    *,
    snapshot: FrozenSourceSnapshot,
    normalized_start: int,
    normalized_end: int,
    sentence: str,
    rule_id: str,
    packet_type: PacketType,
    subject_surface: str,
    subject_id: str,
    relation_label: str,
    object_value: str,
    payload: dict[str, Any],
    confidence: float,
    temporal_values: tuple[str, ...] = (),
    quantities: tuple[QuantityValue, ...] = (),
    attribution: str | None = None,
    claim_alignment_override: Any | None = None,
) -> CandidatePacket:
    relation = relation_id(relation_label)
    alignment = claim_alignment_override or align_normalized_range(
        snapshot, normalized_start, normalized_end
    )
    cache_identity = _cache_identity(snapshot)
    candidate_seed = {
        "source_hash": snapshot.raw_content_hash,
        "normalized_start": normalized_start,
        "normalized_end": normalized_end,
        "rule_id": rule_id,
        "subject": subject_id,
        "relation": relation,
        "object": object_value,
        "payload": payload,
    }
    candidate_id = f"as:candidate:{hashlib.sha256(stable_json(candidate_seed)).hexdigest()}"
    claim_id = f"as:claim:{hashlib.sha256((candidate_id + relation).encode()).hexdigest()[:24]}"
    entity_pairs = _entities(sentence)
    entity_ids = {identifier for _surface, identifier in entity_pairs}
    entity_ids.add(subject_id)
    if attribution:
        entity_ids.add(entity_id(attribution))
    provenance = ExtractorProvenance(
        extractor_identity=EXTRACTOR_IDENTITY,
        extractor_version=EXTRACTOR_VERSION,
        configuration_hash=_configuration_hash(),
        prompt_or_rule_version=RULE_VERSION,
        source_revision=snapshot.source_revision,
        source_content_hash=snapshot.raw_content_hash,
        deterministic_cache_identity=cache_identity,
        estimated_rule_operations=max(1, len(sentence) * len(EVENT_RULES)),
    )
    return CandidatePacket(
        candidate_id=candidate_id,
        packet_type=packet_type,
        key_class=KeyClass.K0 if packet_type is PacketType.PROPOSITION else KeyClass.K1,
        source_doc_id=snapshot.source_doc_id,
        source_revision=snapshot.source_revision,
        source_content_hash=snapshot.raw_content_hash,
        primary_subject=subject_id,
        primary_relation=relation,
        primary_object=object_value,
        entity_ids=tuple(sorted(entity_ids)),
        temporal_values=temporal_values,
        quantities=quantities,
        polarity=(
            "negative"
            if re.search(r"\b(?:not|no|never|without|didn't|did not)\b", sentence, re.I)
            else "positive"
        ),
        attribution=attribution,
        payload=payload,
        atomic_claims=(
            CandidateClaimUnit(
                claim_unit_id=claim_id,
                subject_id=subject_id,
                relation_id=relation,
                object_value=object_value,
                evidence_surface=alignment.normalized_text,
                alignment=alignment,
                extractor_confidence=confidence,
            ),
        ),
        extractor_confidence=confidence,
        extractor=provenance,
    )


class RuleCandidateExtractor:
    """Explicit-fact baseline with deterministic cache identities."""

    configuration_hash = _configuration_hash()

    def extract_snapshot(self, snapshot: FrozenSourceSnapshot) -> tuple[CandidatePacket, ...]:
        candidates: dict[str, CandidatePacket] = {}
        for start, end, sentence in _sentence_ranges(snapshot.normalized_text):
            subject_surface, subject_identifier = _subject(sentence)
            dates = _dates(sentence)
            quantities = _quantities(sentence, subject_surface)

            for rule_id, pattern, relation_label in EVENT_RULES:
                if not pattern.search(sentence):
                    continue
                object_value = dates[0] if dates else sentence
                packet = _candidate(
                    snapshot=snapshot,
                    normalized_start=start,
                    normalized_end=end,
                    sentence=sentence,
                    rule_id=rule_id,
                    packet_type=PacketType.EVENT,
                    subject_surface=subject_surface,
                    subject_id=subject_identifier,
                    relation_label=relation_label,
                    object_value=object_value,
                    temporal_values=dates,
                    quantities=quantities,
                    confidence=0.9 if dates else 0.78,
                    payload={
                        "event_label": relation_label,
                        "subject_label": subject_surface,
                        "sentence": sentence,
                        "occurred_on": dates[0] if dates else None,
                    },
                )
                candidates[packet.candidate_id] = packet

            for role, pattern in ROLE_PATTERNS:
                for match in pattern.finditer(sentence):
                    person = match.group("person").strip(" ,.")
                    packet = _candidate(
                        snapshot=snapshot,
                        normalized_start=start,
                        normalized_end=end,
                        sentence=sentence,
                        rule_id=f"role:{role}:{person}",
                        packet_type=PacketType.PROPOSITION,
                        subject_surface=person,
                        subject_id=entity_id(person),
                        relation_label=f"served_as_{role}",
                        object_value=subject_identifier,
                        temporal_values=dates,
                        confidence=0.88,
                        payload={
                            "subject_label": person,
                            "predicate_label": f"served as {role.replace('_', ' ')}",
                            "object_label": subject_surface,
                        },
                    )
                    candidates[packet.candidate_id] = packet

            for index, quantity in enumerate(quantities):
                packet = _candidate(
                    snapshot=snapshot,
                    normalized_start=start,
                    normalized_end=end,
                    sentence=sentence,
                    rule_id=f"quantity:{index}:{quantity.normalized_unit}",
                    packet_type=PacketType.PROPOSITION,
                    subject_surface=subject_surface,
                    subject_id=subject_identifier,
                    relation_label="has_explicit_quantity",
                    object_value=f"{quantity.normalized_value:g} {quantity.normalized_unit}",
                    temporal_values=dates,
                    quantities=(quantity,),
                    confidence=0.82,
                    payload={
                        "subject_label": subject_surface,
                        "predicate_label": "has explicit quantity",
                        "object_label": quantity.surface,
                        "normalized_value": quantity.normalized_value,
                        "normalized_unit": quantity.normalized_unit,
                    },
                )
                candidates[packet.candidate_id] = packet

            property_match = re.match(
                r"(?P<subject>.+?)\s+(?:was|were|is|are)\s+(?P<object>.+?)[.!?]?$",
                sentence,
                re.IGNORECASE,
            )
            if property_match and len(property_match.group("object").split()) <= 40:
                property_subject = property_match.group("subject").strip(" ,")
                object_value = property_match.group("object").strip(" ,.")
                packet = _candidate(
                    snapshot=snapshot,
                    normalized_start=start,
                    normalized_end=end,
                    sentence=sentence,
                    rule_id="explicit_copula",
                    packet_type=PacketType.PROPOSITION,
                    subject_surface=property_subject,
                    subject_id=entity_id(property_subject),
                    relation_label="explicit_description",
                    object_value=object_value,
                    temporal_values=dates,
                    quantities=quantities,
                    confidence=0.72,
                    payload={
                        "subject_label": property_subject,
                        "predicate_label": "was",
                        "object_label": object_value,
                    },
                )
                candidates[packet.candidate_id] = packet

        for quote_match in re.finditer(
            r"[“\"](?P<quote>[^”\"]{4,500})[”\"]",
            snapshot.raw_text,
        ):
            quote = quote_match.group("quote")
            context_start = max(0, quote_match.start() - 140)
            context = snapshot.raw_text[context_start : quote_match.start()]
            speaker_match = re.search(
                r"(?P<speaker>[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,3})"
                r"(?:\s+(?:said|responded|commented|replied|greeted))?[\s,:]*$",
                context,
            )
            speaker = speaker_match.group("speaker") if speaker_match else "UNKNOWN_SPEAKER"
            alignment = align_evidence(snapshot, quote, direct_quotation=True)
            normalized_start = snapshot.normalized_text.find(alignment.normalized_text)
            packet = _candidate(
                snapshot=snapshot,
                normalized_start=max(0, normalized_start),
                normalized_end=max(0, normalized_start) + len(alignment.normalized_text),
                sentence=alignment.normalized_text,
                rule_id=f"quotation:{quote_match.start()}",
                packet_type=PacketType.QUOTATION,
                subject_surface=speaker,
                subject_id=entity_id(speaker),
                relation_label="said",
                object_value=quote,
                attribution=speaker,
                confidence=0.92 if speaker != "UNKNOWN_SPEAKER" else 0.55,
                claim_alignment_override=alignment,
                payload={
                    "speaker_label": speaker,
                    "speaker_id": entity_id(speaker),
                    "quotation": quote,
                },
            )
            candidates[packet.candidate_id] = packet

        return tuple(sorted(candidates.values(), key=lambda item: item.candidate_id))


def write_candidate_set(
    candidates: tuple[CandidatePacket, ...],
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = "\n".join(
        json.dumps(candidate.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        for candidate in candidates
    )
    output_path.write_text(rendered + ("\n" if rendered else ""), encoding="utf-8")


def read_candidate_set(path: Path) -> tuple[CandidatePacket, ...]:
    if not path.exists():
        return ()
    return tuple(
        CandidatePacket.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


def extract_repository(
    source_repository: SourceRepository,
    output_path: Path,
    run_report_path: Path,
) -> tuple[tuple[CandidatePacket, ...], ExtractionRun]:
    started = datetime.now(UTC)
    start_ns = time.perf_counter_ns()
    extractor = RuleCandidateExtractor()
    all_candidates: dict[str, CandidatePacket] = {}
    operation_count = 0
    snapshots = source_repository.list()
    for snapshot in snapshots:
        for candidate in extractor.extract_snapshot(snapshot):
            all_candidates[candidate.candidate_id] = candidate
            operation_count += candidate.extractor.estimated_rule_operations
    candidates = tuple(sorted(all_candidates.values(), key=lambda item: item.candidate_id))
    write_candidate_set(candidates, output_path)
    candidate_set_hash = (
        f"sha256:{hashlib.sha256(output_path.read_bytes()).hexdigest()}"
        if output_path.exists()
        else sha256_text("")
    )
    completed = datetime.now(UTC)
    run = ExtractionRun(
        run_id=f"extract:{candidate_set_hash.removeprefix('sha256:')[:20]}",
        started_at=started,
        completed_at=completed,
        source_count=len(snapshots),
        candidate_count=len(candidates),
        cache_hits=0,
        wall_clock_ms=(time.perf_counter_ns() - start_ns) / 1_000_000,
        teacher_tokens=0,
        teacher_cost_usd=0.0,
        estimated_rule_operations=operation_count,
        configuration_hash=extractor.configuration_hash,
        candidate_set_hash=candidate_set_hash,
    )
    run_report_path.parent.mkdir(parents=True, exist_ok=True)
    run_report_path.write_text(
        json.dumps(run.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return candidates, run
