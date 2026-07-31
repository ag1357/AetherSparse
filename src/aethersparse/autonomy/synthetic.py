"""Deterministic synthetic-ground-truth worlds for autonomous qualification.

The structured world is authoritative. Prose, source conflicts, omissions, and
questions are derived artifacts. The generator stores only a one-way seed digest
and domain-separates development and hidden-evaluation random streams.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from aethersparse.models import StrictModel

SYNTHETIC_SCHEMA_VERSION = "aethersparse-synthetic-v1"
WorldPartition = Literal["development", "evaluation"]
PacketKind = Literal["PROPOSITION", "EVENT", "QUOTATION", "PERSPECTIVE"]
ExpectedDisposition = Literal["answer", "clarify", "abstain", "out_of_domain"]


class ScaleConfig(StrictModel):
    """A bounded, named synthetic-world size."""

    name: Literal["debug", "intermediate", "decisive"]
    entity_count: int = Field(ge=1)
    packet_count: int = Field(ge=1)
    question_count: int = Field(ge=1)
    source_chunk_size: int = Field(default=32, ge=8, le=128)


DEBUG_SCALE = ScaleConfig(
    name="debug",
    entity_count=100,
    packet_count=1_000,
    question_count=500,
)
INTERMEDIATE_SCALE = ScaleConfig(
    name="intermediate",
    entity_count=1_000,
    packet_count=10_000,
    question_count=5_000,
)
DECISIVE_SCALE = ScaleConfig(
    name="decisive",
    entity_count=5_000,
    packet_count=50_000,
    question_count=20_000,
    source_chunk_size=64,
)

SCALE_CONFIGS: dict[str, ScaleConfig] = {
    scale.name: scale for scale in (DEBUG_SCALE, INTERMEDIATE_SCALE, DECISIVE_SCALE)
}


class SyntheticEntity(StrictModel):
    entity_id: str
    canonical_name: str
    aliases: tuple[str, ...] = Field(min_length=2)
    entity_type: Literal["person", "place", "vehicle", "organization", "artifact"]
    ambiguous_aliases: tuple[str, ...] = ()
    unknown_aliases: tuple[str, ...] = ()


class SyntheticClaim(StrictModel):
    claim_id: str
    packet_type: PacketKind
    subject_id: str
    relation: str
    object_value: str
    object_is_entity: bool
    date_value: str | None = None
    quantity_value: float | None = None
    quantity_unit: str | None = None
    quantity_owner_id: str | None = None
    polarity: Literal["positive", "negative"] = "positive"
    attribution_id: str | None = None
    source_family: str
    lineage_id: str
    contradiction_of: str | None = None
    missing_evidence: bool = False
    ambiguous_entity: bool = False
    contains_unknown_term: bool = False
    domain: Literal["core", "out_of_domain"] = "core"

    @model_validator(mode="after")
    def quantity_fields_are_complete(self) -> SyntheticClaim:
        quantity_fields = (
            self.quantity_value,
            self.quantity_unit,
            self.quantity_owner_id,
        )
        if any(value is not None for value in quantity_fields) and not all(
            value is not None for value in quantity_fields
        ):
            raise ValueError("quantity value, unit, and owner must be set together")
        return self


class SyntheticSourceSpan(StrictModel):
    span_id: str
    claim_id: str
    source_doc_id: str
    raw_char_start: int = Field(ge=0)
    raw_char_end: int = Field(gt=0)
    raw_byte_start: int = Field(ge=0)
    raw_byte_end: int = Field(gt=0)
    raw_text: str
    raw_text_hash: str
    render_variant: int = Field(ge=0)


class SyntheticSourceDocument(StrictModel):
    source_doc_id: str
    revision_id: str
    source_family: str
    lineage_ids: tuple[str, ...]
    raw_text: str
    raw_content_hash: str
    spans: tuple[SyntheticSourceSpan, ...]


class SyntheticQuestion(StrictModel):
    question_id: str
    category: str
    question: str
    expected_disposition: ExpectedDisposition
    expected_answer: str | None = None
    evidence_claim_ids: tuple[str, ...] = ()
    session_id: str | None = None
    previous_question_id: str | None = None


class SyntheticManifest(StrictModel):
    schema_version: str
    generator_identity: str
    generator_version: str
    partition: WorldPartition
    scale_name: str
    seed_digest: str
    world_id: str
    entity_count: int
    packet_count: int
    question_count: int
    source_count: int
    artifact_hash: str


class SyntheticWorld(StrictModel):
    manifest: SyntheticManifest
    entities: tuple[SyntheticEntity, ...]
    claims: tuple[SyntheticClaim, ...]
    sources: tuple[SyntheticSourceDocument, ...]
    questions: tuple[SyntheticQuestion, ...]


def stable_json(value: object) -> bytes:
    """Return the canonical UTF-8 representation used for all identities."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def _identity(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256(stable_json(parts)).hexdigest()
    return f"{prefix}_{digest[:24]}"


def _derive_seed(master_seed: str, partition: WorldPartition) -> tuple[random.Random, str]:
    if not master_seed:
        raise ValueError("master_seed must be non-empty")
    material = hmac.new(
        master_seed.encode("utf-8"),
        f"{SYNTHETIC_SCHEMA_VERSION}:{partition}".encode(),
        hashlib.sha256,
    ).digest()
    return random.Random(int.from_bytes(material, "big")), sha256_bytes(material)


def _world_id(config: ScaleConfig, partition: WorldPartition, seed_digest: str) -> str:
    return _identity(
        "world",
        SYNTHETIC_SCHEMA_VERSION,
        config.model_dump(mode="json"),
        partition,
        seed_digest,
    )


_NAME_PREFIXES = (
    "Aster",
    "Bracken",
    "Cinder",
    "Dawn",
    "Ember",
    "Fathom",
    "Grove",
    "Harbor",
    "Ion",
    "Jade",
    "Kepler",
    "Lumen",
)
_NAME_SUFFIXES = (
    "Arc",
    "Beacon",
    "Crown",
    "Delta",
    "Echo",
    "Field",
    "Gate",
    "Haven",
    "Isle",
    "Junction",
)
_ENTITY_TYPES: tuple[Literal["person", "place", "vehicle", "organization", "artifact"], ...] = (
    "person",
    "place",
    "vehicle",
    "organization",
    "artifact",
)


def _generate_entities(
    config: ScaleConfig,
    partition: WorldPartition,
    rng: random.Random,
) -> tuple[SyntheticEntity, ...]:
    entities: list[SyntheticEntity] = []
    partition_prefix = "D" if partition == "development" else "E"
    for index in range(config.entity_count):
        prefix = _NAME_PREFIXES[(index + rng.randrange(len(_NAME_PREFIXES))) % len(_NAME_PREFIXES)]
        suffix = _NAME_SUFFIXES[
            (index * 3 + rng.randrange(len(_NAME_SUFFIXES))) % len(_NAME_SUFFIXES)
        ]
        canonical = f"{prefix} {suffix} {partition_prefix}{index:05d}"
        aliases = (f"{partition_prefix}-{index:05d}", f"{prefix}-{index:05d}")
        ambiguous = (
            (f"Relay-{partition_prefix}-{index // 2:04d}",)
            if index % 31 in {0, 1}
            else ()
        )
        unknown = (f"qx{partition_prefix.lower()}{index:05d}",) if index % 29 == 0 else ()
        entities.append(
            SyntheticEntity(
                entity_id=f"ent_{partition_prefix.lower()}_{index:06d}",
                canonical_name=canonical,
                aliases=aliases + ambiguous + unknown,
                entity_type=_ENTITY_TYPES[index % len(_ENTITY_TYPES)],
                ambiguous_aliases=ambiguous,
                unknown_aliases=unknown,
            )
        )
    return tuple(entities)


def _literal_status(index: int) -> str:
    return ("operational", "dormant", "calibrated", "restricted", "stable")[index % 5]


def _base_claim(
    index: int,
    entities: tuple[SyntheticEntity, ...],
    partition: WorldPartition,
    rng: random.Random,
) -> SyntheticClaim:
    entity_count = len(entities)
    subject = entities[(index * 17 + rng.randrange(entity_count)) % entity_count]
    other = entities[(index * 29 + 7 + rng.randrange(entity_count)) % entity_count]
    third = entities[(index * 37 + 11) % entity_count]
    mode = index % 10
    packet_type: PacketKind = "PROPOSITION"
    relation = "has_status"
    object_value = _literal_status(index)
    object_is_entity = False
    date_value: str | None = None
    quantity_value: float | None = None
    quantity_unit: str | None = None
    quantity_owner_id: str | None = None
    attribution_id: str | None = None
    if mode == 0:
        relation, object_value, object_is_entity = "located_in", other.entity_id, True
    elif mode == 1:
        relation, object_value, object_is_entity = "orbits", other.entity_id, True
    elif mode == 2:
        packet_type = "EVENT"
        relation, object_value, object_is_entity = "activated", other.entity_id, True
        date_value = f"{2030 + index % 30:04d}-{1 + index % 12:02d}-{1 + index % 27:02d}"
    elif mode == 3:
        relation = "has_mass"
        object_value = f"{5 + (index % 490) / 10:.1f} kg"
        quantity_value = 5 + (index % 490) / 10
        quantity_unit = "kg"
        quantity_owner_id = subject.entity_id
    elif mode == 4:
        relation, object_value, object_is_entity = "precedes", other.entity_id, True
    elif mode == 5:
        relation, object_value, object_is_entity = "causes", other.entity_id, True
    elif mode == 6:
        packet_type = "QUOTATION"
        relation = "said"
        object_value = f"cycle {index} remains {_literal_status(index)}"
        attribution_id = subject.entity_id
    elif mode == 7:
        packet_type = "PERSPECTIVE"
        relation = "reports_status"
        object_value = _literal_status(index + 1)
        attribution_id = third.entity_id
    elif mode == 8:
        relation = "has_status"
        object_value = _literal_status(index)
    else:
        relation, object_value, object_is_entity = "uses", other.entity_id, True

    contains_unknown = index % 43 == 0
    if contains_unknown:
        object_value = f"ux-term-{partition[0]}-{index:05d}"
        object_is_entity = False
        relation = "has_status"
        packet_type = "PROPOSITION"
        date_value = None
        quantity_value = None
        quantity_unit = None
        quantity_owner_id = None
        attribution_id = None

    return SyntheticClaim(
        claim_id=f"claim_{partition[0]}_{index:07d}",
        packet_type=packet_type,
        subject_id=subject.entity_id,
        relation=relation,
        object_value=object_value,
        object_is_entity=object_is_entity,
        date_value=date_value,
        quantity_value=quantity_value,
        quantity_unit=quantity_unit,
        quantity_owner_id=quantity_owner_id,
        polarity="negative" if index % 53 == 0 else "positive",
        attribution_id=attribution_id,
        source_family=f"family_{index % 7:02d}",
        lineage_id=f"lineage_{partition[0]}_{index // 13:06d}",
        missing_evidence=index % 37 == 0,
        ambiguous_entity=index % 41 == 0,
        contains_unknown_term=contains_unknown,
        domain="out_of_domain" if index % 59 == 0 else "core",
    )


def _generate_claims(
    config: ScaleConfig,
    entities: tuple[SyntheticEntity, ...],
    partition: WorldPartition,
    rng: random.Random,
) -> tuple[SyntheticClaim, ...]:
    claims: list[SyntheticClaim] = []
    for index in range(config.packet_count):
        claim = _base_claim(index, entities, partition, rng)
        if index > 0 and index % 100 == 99:
            target = claims[index - 1]
            claim = claim.model_copy(
                update={
                    "packet_type": target.packet_type,
                    "subject_id": target.subject_id,
                    "relation": target.relation,
                    "object_value": target.object_value,
                    "object_is_entity": target.object_is_entity,
                    "date_value": target.date_value,
                    "quantity_value": target.quantity_value,
                    "quantity_unit": target.quantity_unit,
                    "quantity_owner_id": target.quantity_owner_id,
                    "polarity": "negative" if target.polarity == "positive" else "positive",
                    "attribution_id": target.attribution_id,
                    "source_family": "family_conflict",
                    "lineage_id": target.lineage_id,
                    "contradiction_of": target.claim_id,
                    "missing_evidence": False,
                    "ambiguous_entity": False,
                    "contains_unknown_term": target.contains_unknown_term,
                    "domain": target.domain,
                }
            )
        claims.append(claim)
    return tuple(claims)


def _entity_surface(
    entity: SyntheticEntity,
    variant: int,
    *,
    force_ambiguous: bool = False,
) -> str:
    if force_ambiguous and entity.ambiguous_aliases:
        return entity.ambiguous_aliases[0]
    surfaces = (entity.canonical_name, *entity.aliases)
    return surfaces[variant % len(surfaces)]


def _negation(claim: SyntheticClaim, *, verb: bool = False) -> str:
    if claim.polarity == "positive":
        return ""
    return "does not " if verb else "not "


def _render_claim(
    claim: SyntheticClaim,
    entities: dict[str, SyntheticEntity],
    variant: int,
) -> str:
    subject = _entity_surface(
        entities[claim.subject_id],
        variant,
        force_ambiguous=claim.ambiguous_entity,
    )
    object_surface = (
        _entity_surface(entities[claim.object_value], variant + 1)
        if claim.object_is_entity
        else claim.object_value
    )
    if claim.packet_type == "QUOTATION":
        quote_open, quote_close = ("“", "”") if variant % 2 == 0 else ('"', '"')
        return f"{subject} said, {quote_open}{claim.object_value}{quote_close}."
    if claim.packet_type == "PERSPECTIVE":
        if claim.attribution_id is None:
            raise ValueError("perspective claim lacks attribution")
        attribution = _entity_surface(entities[claim.attribution_id], variant + 2)
        return (
            f"According to {attribution}, {subject} is "
            f"{_negation(claim)}{claim.object_value}."
        )
    if claim.packet_type == "EVENT":
        if claim.date_value is None:
            raise ValueError("event claim lacks date")
        if variant % 2:
            return (
                f"{subject} {_negation(claim, verb=True)}activated {object_surface} "
                f"on {claim.date_value}."
            )
        return (
            f"On {claim.date_value}, {subject} {_negation(claim, verb=True)}activated "
            f"{object_surface}."
        )
    if claim.relation == "has_mass":
        return f"{subject} has {_negation(claim)}a mass of {claim.object_value}."
    if claim.relation == "located_in" and variant % 2:
        return f"Within {object_surface}, {subject} is {_negation(claim)}located."
    if claim.relation == "located_in":
        return f"{subject} is {_negation(claim)}located in {object_surface}."
    if claim.relation == "orbits":
        return f"{subject} {_negation(claim, verb=True)}orbits {object_surface}."
    if claim.relation == "precedes":
        return (
            f"{subject} {_negation(claim, verb=True)}precedes {object_surface} "
            "in the recorded sequence."
        )
    if claim.relation == "causes":
        return f"{subject} {_negation(claim, verb=True)}causes {object_surface}."
    if claim.relation == "uses":
        return f"{subject} {_negation(claim, verb=True)}uses {object_surface}."
    return f"{subject} is {_negation(claim)}{claim.object_value}."


def _format_noise(sentence: str, claim_index: int, variant: int) -> str:
    if (claim_index + variant) % 17 == 0:
        sentence = sentence.replace(" ", "\u00a0", 1)
    if (claim_index + variant) % 23 == 0:
        sentence = sentence.replace(",", " ,")
    return sentence


def _render_sources(
    claims: tuple[SyntheticClaim, ...],
    entities: tuple[SyntheticEntity, ...],
    partition: WorldPartition,
    config: ScaleConfig,
) -> tuple[SyntheticSourceDocument, ...]:
    entity_index = {entity.entity_id: entity for entity in entities}
    records: list[tuple[str, SyntheticClaim, int]] = []
    for index, claim in enumerate(claims):
        if claim.missing_evidence:
            continue
        records.append((claim.source_family, claim, index % 4))
        if index % 13 == 0:
            records.append(("family_derived", claim, 1 + index % 4))

    grouped: dict[str, list[tuple[SyntheticClaim, int]]] = defaultdict(list)
    for family, claim, variant in records:
        grouped[family].append((claim, variant))

    documents: list[SyntheticSourceDocument] = []
    document_index = 0
    for family in sorted(grouped):
        family_records = grouped[family]
        for chunk_start in range(0, len(family_records), config.source_chunk_size):
            chunk = family_records[chunk_start : chunk_start + config.source_chunk_size]
            source_doc_id = f"src_{partition[0]}_{document_index:06d}"
            document_index += 1
            raw_parts: list[str] = []
            spans: list[SyntheticSourceSpan] = []
            current_chars = 0
            for local_index, (claim, variant) in enumerate(chunk):
                if local_index % 5 == 0:
                    distractor = (
                        f"Editorial note {local_index}: inter-\nnal formatting "
                        "is not evidence.&nbsp;\n"
                    )
                    raw_parts.append(distractor)
                    current_chars += len(distractor)
                sentence = _format_noise(
                    _render_claim(claim, entity_index, variant),
                    int(claim.claim_id.rsplit("_", 1)[-1]),
                    variant,
                )
                raw_start = current_chars
                raw_end = raw_start + len(sentence)
                prefix = "".join(raw_parts)
                raw_byte_start = len(prefix.encode("utf-8"))
                raw_byte_end = raw_byte_start + len(sentence.encode("utf-8"))
                spans.append(
                    SyntheticSourceSpan(
                        span_id=_identity(
                            "span",
                            source_doc_id,
                            claim.claim_id,
                            raw_start,
                            raw_end,
                        ),
                        claim_id=claim.claim_id,
                        source_doc_id=source_doc_id,
                        raw_char_start=raw_start,
                        raw_char_end=raw_end,
                        raw_byte_start=raw_byte_start,
                        raw_byte_end=raw_byte_end,
                        raw_text=sentence,
                        raw_text_hash=sha256_text(sentence),
                        render_variant=variant,
                    )
                )
                raw_parts.append(sentence)
                separator = "\n\n" if local_index % 3 == 0 else "\n"
                raw_parts.append(separator)
                current_chars = raw_end + len(separator)
            raw_text = "".join(raw_parts)
            documents.append(
                SyntheticSourceDocument(
                    source_doc_id=source_doc_id,
                    revision_id=_identity("revision", source_doc_id, sha256_text(raw_text)),
                    source_family=family,
                    lineage_ids=tuple(sorted({claim.lineage_id for claim, _ in chunk})),
                    raw_text=raw_text,
                    raw_content_hash=sha256_text(raw_text),
                    spans=tuple(spans),
                )
            )
    return tuple(documents)


_QUESTION_CATEGORIES = (
    "direct_fact",
    "unseen_paraphrase",
    "temporal_ordering",
    "quantity_unit",
    "causal_multihop",
    "quotation_vs_endorsement",
    "ambiguous_entity",
    "wrong_premise",
    "conflicting_sources",
    "duplicated_source_family",
    "missing_evidence",
    "unknown_term",
    "out_of_domain",
    "session_followup",
    "negation_mutation",
    "attribution_mutation",
)


def _question_text(
    category: str,
    claim: SyntheticClaim,
    entities: dict[str, SyntheticEntity],
    index: int,
) -> tuple[str, ExpectedDisposition, str | None]:
    subject = entities[claim.subject_id].canonical_name
    answer = (
        entities[claim.object_value].canonical_name
        if claim.object_is_entity
        else claim.object_value
    )
    if category == "direct_fact":
        return f"What is the {claim.relation} value for {subject}?", "answer", answer
    if category == "unseen_paraphrase":
        return (
            f"Tell me, in different words, what is established about {subject}.",
            "answer",
            answer,
        )
    if category == "temporal_ordering":
        return f"When did the recorded event involving {subject} occur?", "answer", claim.date_value
    if category == "quantity_unit":
        return f"What measured quantity belongs to {subject}, including its unit?", "answer", answer
    if category == "causal_multihop":
        return f"What follows through the causal chain beginning at {subject}?", "answer", answer
    if category == "quotation_vs_endorsement":
        return f"Was {answer!r} asserted as fact or attributed speech?", "answer", claim.packet_type
    if category == "ambiguous_entity":
        return "What is the status of Relay?", "clarify", None
    if category == "wrong_premise":
        return f"Why did {subject} have the opposite recorded value?", "clarify", None
    if category == "conflicting_sources":
        return f"Do all source families agree about {subject}?", "clarify", None
    if category == "duplicated_source_family":
        return f"What independent evidence supports the record for {subject}?", "answer", answer
    if category == "missing_evidence":
        return f"What undocumented property belongs to {subject}?", "abstain", None
    if category == "unknown_term":
        return f"What does ux-unseen-{index:05d} mean?", "abstain", None
    if category == "out_of_domain":
        return "Write a device driver for an unrelated accelerator.", "out_of_domain", None
    if category == "session_followup":
        return "What about its date and source?", "answer", claim.date_value or answer
    if category == "negation_mutation":
        return f"Is it false that {subject} has the recorded relation?", "answer", claim.polarity
    return f"Who should the statement about {subject} be attributed to?", "answer", (
        entities[claim.attribution_id].canonical_name if claim.attribution_id else None
    )


def _generate_questions(
    config: ScaleConfig,
    claims: tuple[SyntheticClaim, ...],
    entities: tuple[SyntheticEntity, ...],
    partition: WorldPartition,
) -> tuple[SyntheticQuestion, ...]:
    entity_index = {entity.entity_id: entity for entity in entities}
    questions: list[SyntheticQuestion] = []
    previous_by_session: dict[str, str] = {}
    for index in range(config.question_count):
        category = _QUESTION_CATEGORIES[index % len(_QUESTION_CATEGORIES)]
        claim = claims[(index * 47 + 3) % len(claims)]
        question, disposition, answer = _question_text(category, claim, entity_index, index)
        session_id = (
            f"session_{partition[0]}_{index // 16:06d}"
            if category == "session_followup"
            else None
        )
        previous = previous_by_session.get(session_id) if session_id is not None else None
        question_id = f"question_{partition[0]}_{index:07d}"
        if session_id is not None:
            previous_by_session[session_id] = question_id
        evidence = (
            ()
            if disposition in {"abstain", "out_of_domain"} or claim.missing_evidence
            else (claim.claim_id,)
        )
        questions.append(
            SyntheticQuestion(
                question_id=question_id,
                category=category,
                question=question,
                expected_disposition=disposition,
                expected_answer=answer,
                evidence_claim_ids=evidence,
                session_id=session_id,
                previous_question_id=previous,
            )
        )
    return tuple(questions)


def _artifact_payload(
    entities: tuple[SyntheticEntity, ...],
    claims: tuple[SyntheticClaim, ...],
    sources: tuple[SyntheticSourceDocument, ...],
    questions: tuple[SyntheticQuestion, ...],
) -> dict[str, Any]:
    return {
        "entities": [entity.model_dump(mode="json") for entity in entities],
        "claims": [claim.model_dump(mode="json") for claim in claims],
        "sources": [source.model_dump(mode="json") for source in sources],
        "questions": [question.model_dump(mode="json") for question in questions],
    }


def verify_world(world: SyntheticWorld) -> None:
    """Fail closed if identities, counts, hashes, or exact offsets changed."""

    manifest = world.manifest
    if len(world.entities) != manifest.entity_count:
        raise ValueError("entity count does not match manifest")
    if len(world.claims) != manifest.packet_count:
        raise ValueError("packet count does not match manifest")
    if len(world.questions) != manifest.question_count:
        raise ValueError("question count does not match manifest")
    if len(world.sources) != manifest.source_count:
        raise ValueError("source count does not match manifest")
    payload_hash = sha256_bytes(
        stable_json(_artifact_payload(world.entities, world.claims, world.sources, world.questions))
    )
    if payload_hash != manifest.artifact_hash:
        raise ValueError("synthetic artifact hash mismatch")
    claim_ids = {claim.claim_id for claim in world.claims}
    for source in world.sources:
        if sha256_text(source.raw_text) != source.raw_content_hash:
            raise ValueError(f"source content hash mismatch: {source.source_doc_id}")
        for span in source.spans:
            if span.claim_id not in claim_ids:
                raise ValueError(f"span references unknown claim: {span.claim_id}")
            raw = source.raw_text[span.raw_char_start : span.raw_char_end]
            if raw != span.raw_text or sha256_text(raw) != span.raw_text_hash:
                raise ValueError(f"raw character alignment mismatch: {span.span_id}")
            byte_start = len(source.raw_text[: span.raw_char_start].encode("utf-8"))
            byte_end = len(source.raw_text[: span.raw_char_end].encode("utf-8"))
            if (byte_start, byte_end) != (span.raw_byte_start, span.raw_byte_end):
                raise ValueError(f"raw byte alignment mismatch: {span.span_id}")


def _cache_path(cache_dir: Path, world_id: str) -> Path:
    return cache_dir / SYNTHETIC_SCHEMA_VERSION / f"{world_id}.json"


def save_world(world: SyntheticWorld, cache_dir: Path) -> Path:
    """Persist one compact, content-verified cache artifact atomically."""

    verify_world(world)
    destination = _cache_path(cache_dir, world.manifest.world_id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = stable_json(world.model_dump(mode="json"))
    if destination.exists():
        existing = SyntheticWorld.model_validate_json(destination.read_bytes())
        verify_world(existing)
        if existing.manifest.artifact_hash != world.manifest.artifact_hash:
            raise ValueError("cache identity collision with different artifact bytes")
        return destination
    temporary = destination.with_suffix(".tmp")
    temporary.write_bytes(encoded)
    temporary.replace(destination)
    return destination


def load_world(path: Path) -> SyntheticWorld:
    world = SyntheticWorld.model_validate_json(path.read_bytes())
    verify_world(world)
    return world


def generate_world(
    config: ScaleConfig,
    *,
    partition: WorldPartition,
    master_seed: str,
    cache_dir: Path | None = None,
) -> SyntheticWorld:
    """Generate a deterministic world, optionally reusing its immutable cache."""

    rng, seed_digest = _derive_seed(master_seed, partition)
    world_id = _world_id(config, partition, seed_digest)
    if cache_dir is not None:
        cached_path = _cache_path(cache_dir, world_id)
        if cached_path.exists():
            cached = load_world(cached_path)
            if cached.manifest.world_id != world_id:
                raise ValueError("cached world identity mismatch")
            return cached

    entities = _generate_entities(config, partition, rng)
    claims = _generate_claims(config, entities, partition, rng)
    sources = _render_sources(claims, entities, partition, config)
    questions = _generate_questions(config, claims, entities, partition)
    artifact_hash = sha256_bytes(
        stable_json(_artifact_payload(entities, claims, sources, questions))
    )
    world = SyntheticWorld(
        manifest=SyntheticManifest(
            schema_version=SYNTHETIC_SCHEMA_VERSION,
            generator_identity="aethersparse.synthetic_world_generator",
            generator_version="1.0.0",
            partition=partition,
            scale_name=config.name,
            seed_digest=seed_digest,
            world_id=world_id,
            entity_count=len(entities),
            packet_count=len(claims),
            question_count=len(questions),
            source_count=len(sources),
            artifact_hash=artifact_hash,
        ),
        entities=entities,
        claims=claims,
        sources=sources,
        questions=questions,
    )
    verify_world(world)
    if cache_dir is not None:
        save_world(world, cache_dir)
    return world


def generate_partition_pair(
    config: ScaleConfig,
    *,
    master_seed: str,
    cache_dir: Path | None = None,
) -> tuple[SyntheticWorld, SyntheticWorld]:
    """Generate domain-separated development and hidden-evaluation worlds."""

    development = generate_world(
        config,
        partition="development",
        master_seed=master_seed,
        cache_dir=cache_dir,
    )
    evaluation = generate_world(
        config,
        partition="evaluation",
        master_seed=master_seed,
        cache_dir=cache_dir,
    )
    if development.manifest.seed_digest == evaluation.manifest.seed_digest:
        raise AssertionError("development and evaluation seed streams are not separated")
    if development.manifest.world_id == evaluation.manifest.world_id:
        raise AssertionError("development and evaluation world identities collided")
    return development, evaluation
