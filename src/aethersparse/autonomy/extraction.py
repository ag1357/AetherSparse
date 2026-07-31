"""Independent extraction, validation, and adjudication for synthetic sources.

The extractor receives prose plus an entity lexicon, never structured claims.
The validator receives only source bytes plus the extractor output and uses an
independently implemented set of checks. Structured truth is exposed only to the
adjudicator for synthetic grading and mutation falsification.
"""

from __future__ import annotations

import hashlib
import html
import re
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field

from aethersparse.autonomy.synthetic import (
    SyntheticClaim,
    SyntheticEntity,
    SyntheticSourceDocument,
    SyntheticWorld,
    sha256_bytes,
    sha256_text,
    stable_json,
)
from aethersparse.models import StrictModel

EXTRACTOR_IDENTITY = "aethersparse.synthetic_regex_extractor"
EXTRACTOR_VERSION = "1.0.0"
VALIDATOR_IDENTITY = "aethersparse.synthetic_independent_validator"
VALIDATOR_VERSION = "1.0.1"
ADJUDICATOR_IDENTITY = "aethersparse.synthetic_gold_adjudicator"
ADJUDICATOR_VERSION = "1.0.1"

CandidatePacketKind = Literal["PROPOSITION", "EVENT", "QUOTATION", "PERSPECTIVE"]


class CandidatePacket(StrictModel):
    candidate_id: str
    source_doc_id: str
    source_revision: str
    source_content_hash: str
    raw_char_start: int = Field(ge=0)
    raw_char_end: int = Field(gt=0)
    raw_byte_start: int = Field(ge=0)
    raw_byte_end: int = Field(gt=0)
    evidence_surface: str
    evidence_hash: str
    packet_type: CandidatePacketKind
    subject_id: str | None
    subject_surface: str
    relation: str
    object_value: str
    object_is_entity: bool
    date_value: str | None = None
    quantity_value: float | None = None
    quantity_unit: str | None = None
    quantity_owner_id: str | None = None
    polarity: Literal["positive", "negative"]
    attribution_id: str | None = None
    attribution_surface: str | None = None
    extractor_identity: str
    extractor_version: str
    extractor_confidence: float = Field(ge=0.0, le=1.0)
    extraction_issues: tuple[str, ...] = ()


class ExtractionArtifact(StrictModel):
    artifact_id: str
    world_id: str
    extractor_identity: str
    extractor_version: str
    candidate_count: int = Field(ge=0)
    candidates: tuple[CandidatePacket, ...]
    artifact_hash: str


class CheckStatus(StrEnum):
    PASS = "PASS"
    REVIEW = "REVIEW"
    FAIL = "FAIL"


class ValidationDecision(StrEnum):
    PASS = "PASS"
    REVIEW = "REVIEW"
    FAIL = "FAIL"


class ValidationCheck(StrictModel):
    name: str
    status: CheckStatus
    detail: str


class ValidationResult(StrictModel):
    candidate_id: str
    extractor_identity: str
    validator_identity: str
    validator_version: str
    independent_from_extractor: bool
    decision: ValidationDecision
    checks: tuple[ValidationCheck, ...]
    mutation_rejection_count: int = Field(ge=0)
    result_hash: str


class ValidationArtifact(StrictModel):
    artifact_id: str
    extraction_artifact_id: str
    validator_identity: str
    validator_version: str
    result_count: int = Field(ge=0)
    results: tuple[ValidationResult, ...]
    artifact_hash: str


class AdjudicationDecision(StrEnum):
    CANONICAL = "CANONICAL"
    QUARANTINE = "QUARANTINE"
    REJECT = "REJECT"


class AdjudicationResult(StrictModel):
    candidate_id: str
    adjudicator_identity: str
    adjudicator_version: str
    decision: AdjudicationDecision
    synthetic_truth_match: bool | None
    matched_claim_id: str | None = None
    mutation_rejection_count: int = Field(ge=0)
    reasons: tuple[str, ...]
    result_hash: str


class AdjudicationArtifact(StrictModel):
    artifact_id: str
    validation_artifact_id: str
    adjudicator_identity: str
    adjudicator_version: str
    result_count: int = Field(ge=0)
    results: tuple[AdjudicationResult, ...]
    artifact_hash: str


def _artifact_hash(records: object) -> str:
    return sha256_bytes(stable_json(records))


def _save_artifact(artifact: StrictModel, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = stable_json(artifact.model_dump(mode="json"))
    if path.exists():
        if path.read_bytes() != encoded:
            raise ValueError(f"immutable artifact collision: {path}")
        return path
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(encoded)
    temporary.replace(path)
    return path


class IndependentExtractor:
    """Deterministic prose parser with no access to synthetic claim records."""

    identity = EXTRACTOR_IDENTITY
    version = EXTRACTOR_VERSION

    def __init__(self, entities: tuple[SyntheticEntity, ...]) -> None:
        alias_map: dict[str, set[str]] = {}
        for entity in entities:
            for surface in (entity.canonical_name, *entity.aliases):
                key = self._normalize_name(surface)
                alias_map.setdefault(key, set()).add(entity.entity_id)
        self._alias_map = {key: tuple(sorted(values)) for key, values in alias_map.items()}

    @staticmethod
    def _normalize_name(surface: str) -> str:
        return " ".join(html.unescape(surface).replace("\u00a0", " ").casefold().split())

    @staticmethod
    def _normalize_sentence(surface: str) -> str:
        replacements = str.maketrans({"“": '"', "”": '"', "\u00a0": " "})
        normalized = html.unescape(surface).translate(replacements)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized.replace(" ,", ",")

    def _resolve(self, surface: str) -> tuple[str | None, bool]:
        identities = self._alias_map.get(self._normalize_name(surface), ())
        if len(identities) == 1:
            return identities[0], False
        return None, len(identities) > 1

    @staticmethod
    def _candidate_identity(
        document: SyntheticSourceDocument,
        raw_start: int,
        raw_end: int,
        surface: str,
    ) -> str:
        digest = hashlib.sha256(
            stable_json(
                (
                    EXTRACTOR_IDENTITY,
                    EXTRACTOR_VERSION,
                    document.source_doc_id,
                    document.revision_id,
                    raw_start,
                    raw_end,
                    surface,
                )
            )
        ).hexdigest()
        return f"candidate_{digest[:24]}"

    def _build_candidate(
        self,
        *,
        document: SyntheticSourceDocument,
        raw_start: int,
        raw_end: int,
        evidence: str,
        packet_type: CandidatePacketKind,
        subject_surface: str,
        relation: str,
        object_surface: str,
        polarity: Literal["positive", "negative"],
        date_value: str | None = None,
        quantity_value: float | None = None,
        quantity_unit: str | None = None,
        attribution_surface: str | None = None,
    ) -> CandidatePacket:
        subject_id, subject_ambiguous = self._resolve(subject_surface)
        object_id, object_ambiguous = self._resolve(object_surface)
        attribution_id: str | None = None
        attribution_ambiguous = False
        if attribution_surface is not None:
            attribution_id, attribution_ambiguous = self._resolve(attribution_surface)
        issues: list[str] = []
        if subject_id is None:
            issues.append("ambiguous_subject" if subject_ambiguous else "unknown_subject")
        if object_ambiguous:
            issues.append("ambiguous_object")
        if attribution_surface is not None and attribution_id is None:
            issues.append(
                "ambiguous_attribution" if attribution_ambiguous else "unknown_attribution"
            )
        raw_byte_start = len(document.raw_text[:raw_start].encode("utf-8"))
        raw_byte_end = len(document.raw_text[:raw_end].encode("utf-8"))
        return CandidatePacket(
            candidate_id=self._candidate_identity(
                document,
                raw_start,
                raw_end,
                evidence,
            ),
            source_doc_id=document.source_doc_id,
            source_revision=document.revision_id,
            source_content_hash=document.raw_content_hash,
            raw_char_start=raw_start,
            raw_char_end=raw_end,
            raw_byte_start=raw_byte_start,
            raw_byte_end=raw_byte_end,
            evidence_surface=evidence,
            evidence_hash=sha256_text(evidence),
            packet_type=packet_type,
            subject_id=subject_id,
            subject_surface=subject_surface,
            relation=relation,
            object_value=object_id or object_surface,
            object_is_entity=object_id is not None,
            date_value=date_value,
            quantity_value=quantity_value,
            quantity_unit=quantity_unit,
            quantity_owner_id=subject_id if quantity_value is not None else None,
            polarity=polarity,
            attribution_id=attribution_id,
            attribution_surface=attribution_surface,
            extractor_identity=self.identity,
            extractor_version=self.version,
            extractor_confidence=max(0.2, 0.98 - 0.18 * len(issues)),
            extraction_issues=tuple(issues),
        )

    def _parse_line(
        self,
        document: SyntheticSourceDocument,
        raw_start: int,
        raw_end: int,
        evidence: str,
    ) -> CandidatePacket | None:
        text = self._normalize_sentence(evidence)
        match = re.fullmatch(r'(.+?) said, "(.+)"\.', text)
        if match:
            return self._build_candidate(
                document=document,
                raw_start=raw_start,
                raw_end=raw_end,
                evidence=evidence,
                packet_type="QUOTATION",
                subject_surface=match.group(1),
                relation="said",
                object_surface=match.group(2),
                polarity="positive",
                attribution_surface=match.group(1),
            )
        match = re.fullmatch(r"According to (.+?), (.+?) is (not )?(.+)\.", text)
        if match:
            return self._build_candidate(
                document=document,
                raw_start=raw_start,
                raw_end=raw_end,
                evidence=evidence,
                packet_type="PERSPECTIVE",
                attribution_surface=match.group(1),
                subject_surface=match.group(2),
                relation="reports_status",
                object_surface=match.group(4),
                polarity="negative" if match.group(3) else "positive",
            )
        match = re.fullmatch(
            r"On (\d{4}-\d{2}-\d{2}), (.+?) (does not )?activated (.+)\.",
            text,
        )
        if match:
            return self._build_candidate(
                document=document,
                raw_start=raw_start,
                raw_end=raw_end,
                evidence=evidence,
                packet_type="EVENT",
                date_value=match.group(1),
                subject_surface=match.group(2),
                relation="activated",
                object_surface=match.group(4),
                polarity="negative" if match.group(3) else "positive",
            )
        match = re.fullmatch(
            r"(.+?) (does not )?activated (.+) on (\d{4}-\d{2}-\d{2})\.",
            text,
        )
        if match:
            return self._build_candidate(
                document=document,
                raw_start=raw_start,
                raw_end=raw_end,
                evidence=evidence,
                packet_type="EVENT",
                date_value=match.group(4),
                subject_surface=match.group(1),
                relation="activated",
                object_surface=match.group(3),
                polarity="negative" if match.group(2) else "positive",
            )
        match = re.fullmatch(r"(.+?) has (not )?a mass of ([0-9.]+) ([A-Za-z]+)\.", text)
        if match:
            return self._build_candidate(
                document=document,
                raw_start=raw_start,
                raw_end=raw_end,
                evidence=evidence,
                packet_type="PROPOSITION",
                subject_surface=match.group(1),
                relation="has_mass",
                object_surface=f"{match.group(3)} {match.group(4)}",
                quantity_value=float(match.group(3)),
                quantity_unit=match.group(4),
                polarity="negative" if match.group(2) else "positive",
            )
        match = re.fullmatch(r"Within (.+?), (.+?) is (not )?located\.", text)
        if match:
            return self._build_candidate(
                document=document,
                raw_start=raw_start,
                raw_end=raw_end,
                evidence=evidence,
                packet_type="PROPOSITION",
                subject_surface=match.group(2),
                relation="located_in",
                object_surface=match.group(1),
                polarity="negative" if match.group(3) else "positive",
            )
        match = re.fullmatch(r"(.+?) is (not )?located in (.+)\.", text)
        if match:
            return self._build_candidate(
                document=document,
                raw_start=raw_start,
                raw_end=raw_end,
                evidence=evidence,
                packet_type="PROPOSITION",
                subject_surface=match.group(1),
                relation="located_in",
                object_surface=match.group(3),
                polarity="negative" if match.group(2) else "positive",
            )
        match = re.fullmatch(
            r"(.+?) (does not )?(orbits|causes|uses) (.+)\.",
            text,
        )
        if match:
            return self._build_candidate(
                document=document,
                raw_start=raw_start,
                raw_end=raw_end,
                evidence=evidence,
                packet_type="PROPOSITION",
                subject_surface=match.group(1),
                relation=match.group(3),
                object_surface=match.group(4),
                polarity="negative" if match.group(2) else "positive",
            )
        match = re.fullmatch(
            r"(.+?) (does not )?precedes (.+) in the recorded sequence\.",
            text,
        )
        if match:
            return self._build_candidate(
                document=document,
                raw_start=raw_start,
                raw_end=raw_end,
                evidence=evidence,
                packet_type="PROPOSITION",
                subject_surface=match.group(1),
                relation="precedes",
                object_surface=match.group(3),
                polarity="negative" if match.group(2) else "positive",
            )
        match = re.fullmatch(r"(.+?) is (not )?(.+)\.", text)
        if match:
            return self._build_candidate(
                document=document,
                raw_start=raw_start,
                raw_end=raw_end,
                evidence=evidence,
                packet_type="PROPOSITION",
                subject_surface=match.group(1),
                relation="has_status",
                object_surface=match.group(3),
                polarity="negative" if match.group(2) else "positive",
            )
        return None

    def extract_document(self, document: SyntheticSourceDocument) -> tuple[CandidatePacket, ...]:
        if sha256_text(document.raw_text) != document.raw_content_hash:
            raise ValueError("extractor refused a source with a changed content hash")
        candidates: list[CandidatePacket] = []
        for line_match in re.finditer(r"[^\r\n]+", document.raw_text):
            raw_line = line_match.group(0)
            leading = len(raw_line) - len(raw_line.lstrip())
            trailing = len(raw_line.rstrip())
            if trailing <= leading:
                continue
            raw_start = line_match.start() + leading
            raw_end = line_match.start() + trailing
            evidence = document.raw_text[raw_start:raw_end]
            candidate = self._parse_line(document, raw_start, raw_end, evidence)
            if candidate is not None:
                candidates.append(candidate)
        return tuple(candidates)

    def extract_world(
        self,
        world: SyntheticWorld,
        *,
        cache_dir: Path | None = None,
    ) -> ExtractionArtifact:
        candidates = tuple(
            candidate
            for source in world.sources
            for candidate in self.extract_document(source)
        )
        records = [candidate.model_dump(mode="json") for candidate in candidates]
        artifact_hash = _artifact_hash(records)
        artifact_identity = hashlib.sha256(
            stable_json(
                (
                    world.manifest.world_id,
                    self.identity,
                    self.version,
                    artifact_hash,
                )
            )
        ).hexdigest()
        artifact = ExtractionArtifact(
            artifact_id=f"extraction_{artifact_identity[:24]}",
            world_id=world.manifest.world_id,
            extractor_identity=self.identity,
            extractor_version=self.version,
            candidate_count=len(candidates),
            candidates=candidates,
            artifact_hash=artifact_hash,
        )
        if cache_dir is not None:
            _save_artifact(
                artifact,
                cache_dir / "extraction" / f"{artifact.artifact_id}.json",
            )
        return artifact


class IndependentValidator:
    """Independent evidence checker; it does not call extractor parsing code."""

    identity = VALIDATOR_IDENTITY
    version = VALIDATOR_VERSION

    def __init__(self, entities: tuple[SyntheticEntity, ...]) -> None:
        self._entity_surfaces: dict[str, tuple[str, ...]] = {
            entity.entity_id: tuple(
                self._validator_normalize(surface)
                for surface in (entity.canonical_name, *entity.aliases)
            )
            for entity in entities
        }

    @staticmethod
    def _validator_normalize(surface: str) -> str:
        decoded = html.unescape(surface)
        decoded = decoded.replace("\u00a0", " ").replace("“", '"').replace("”", '"')
        decoded = re.sub(r"\s+", " ", decoded)
        return decoded.strip().casefold().replace(" ,", ",")

    @staticmethod
    def _check(name: str, passed: bool, detail: str, *, review: bool = False) -> ValidationCheck:
        if passed:
            status = CheckStatus.PASS
        elif review:
            status = CheckStatus.REVIEW
        else:
            status = CheckStatus.FAIL
        return ValidationCheck(name=name, status=status, detail=detail)

    def _entity_mentioned(self, entity_id: str | None, evidence: str) -> bool:
        if entity_id is None:
            return False
        normalized = self._validator_normalize(evidence)
        return any(surface in normalized for surface in self._entity_surfaces.get(entity_id, ()))

    @staticmethod
    def _relation_visible(relation: str, normalized_evidence: str) -> bool:
        lexemes = {
            "said": " said, ",
            "reports_status": "according to ",
            "activated": "activated",
            "has_mass": "mass of",
            "located_in": "located",
            "orbits": "orbits",
            "precedes": "precedes",
            "causes": "causes",
            "uses": "uses",
            "has_status": " is ",
        }
        expected = lexemes.get(relation)
        return expected is not None and expected in normalized_evidence

    def validate(
        self,
        candidate: CandidatePacket,
        document: SyntheticSourceDocument,
    ) -> ValidationResult:
        checks: list[ValidationCheck] = []
        source_ok = (
            candidate.source_doc_id == document.source_doc_id
            and candidate.source_revision == document.revision_id
            and candidate.source_content_hash == document.raw_content_hash
            and sha256_text(document.raw_text) == document.raw_content_hash
        )
        checks.append(self._check("source_integrity", source_ok, "immutable source identity"))
        range_ok = 0 <= candidate.raw_char_start < candidate.raw_char_end <= len(document.raw_text)
        aligned_surface = (
            document.raw_text[candidate.raw_char_start : candidate.raw_char_end]
            if range_ok
            else ""
        )
        alignment_ok = (
            range_ok
            and aligned_surface == candidate.evidence_surface
            and sha256_text(aligned_surface) == candidate.evidence_hash
            and len(document.raw_text[: candidate.raw_char_start].encode("utf-8"))
            == candidate.raw_byte_start
            and len(document.raw_text[: candidate.raw_char_end].encode("utf-8"))
            == candidate.raw_byte_end
        )
        checks.append(self._check("atomic_alignment", alignment_ok, "exact raw and byte offsets"))
        normalized = self._validator_normalize(candidate.evidence_surface)
        subject_ok = self._entity_mentioned(candidate.subject_id, candidate.evidence_surface)
        checks.append(
            self._check(
                "entity_identity",
                subject_ok,
                "subject has an unambiguous evidence surface",
                review=candidate.subject_id is None,
            )
        )
        relation_ok = self._relation_visible(candidate.relation, normalized)
        checks.append(self._check("relation_direction", relation_ok, "relation lexeme is present"))
        observed_negative = " not " in f" {normalized} " or "does not" in normalized
        polarity_ok = observed_negative == (candidate.polarity == "negative")
        checks.append(self._check("negation", polarity_ok, "polarity agrees with evidence"))
        dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", normalized)
        temporal_ok = (
            (candidate.date_value is None and not dates)
            or (candidate.date_value is not None and dates == [candidate.date_value])
        )
        checks.append(self._check("temporal_value", temporal_ok, "date value is evidence-bound"))
        # Scope quantity recovery to the relation phrase. Entity aliases contain
        # digits (for example ``E-00054``), so taking the first number in the
        # sentence would silently assign the alias suffix as the measurement.
        quantity_match = re.search(
            r"\bmass of\s+([0-9]+(?:\.[0-9]+)?)\s*([a-z]+)\b",
            normalized,
        )
        if candidate.quantity_value is None:
            quantity_ok = candidate.relation != "has_mass" or quantity_match is None
        else:
            quantity_ok = (
                quantity_match is not None
                and float(quantity_match.group(1)) == candidate.quantity_value
                and quantity_match.group(2) == (candidate.quantity_unit or "").casefold()
                and candidate.quantity_owner_id == candidate.subject_id
            )
        checks.append(
            self._check("quantity_unit_owner", quantity_ok, "quantity and ownership agree")
        )
        if candidate.attribution_surface is None:
            attribution_ok = candidate.packet_type not in {"QUOTATION", "PERSPECTIVE"}
        else:
            attribution_ok = (
                candidate.attribution_id is not None
                and self._entity_mentioned(candidate.attribution_id, candidate.evidence_surface)
                and (
                    "said," in normalized
                    if candidate.packet_type == "QUOTATION"
                    else normalized.startswith("according to ")
                )
            )
        checks.append(
            self._check(
                "attribution",
                attribution_ok,
                "speaker or perspective owner is explicit",
                review=candidate.attribution_surface is not None
                and candidate.attribution_id is None,
            )
        )
        type_ok = (
            (candidate.packet_type == "EVENT") == (candidate.relation == "activated")
            and (candidate.packet_type == "QUOTATION") == (candidate.relation == "said")
            and (candidate.packet_type == "PERSPECTIVE")
            == (candidate.relation == "reports_status")
        )
        checks.append(self._check("type_compatibility", type_ok, "packet type matches relation"))

        failure_names = {
            check.name for check in checks if check.status is CheckStatus.FAIL
        }
        review_names = {
            check.name for check in checks if check.status is CheckStatus.REVIEW
        }
        if {"source_integrity", "atomic_alignment"} & failure_names:
            decision = ValidationDecision.FAIL
        elif failure_names or review_names or candidate.extraction_issues:
            decision = ValidationDecision.REVIEW
        else:
            decision = ValidationDecision.PASS
        mutation_rejection_count = sum(
            (
                not self._entity_mentioned("ent_nonexistent", candidate.evidence_surface),
                not self._relation_visible("relation_nonexistent", normalized),
                observed_negative != (candidate.polarity != "negative"),
                "2099-12-31" not in normalized,
                "mutation-attributor" not in normalized,
            )
        )
        unsigned = {
            "candidate_id": candidate.candidate_id,
            "extractor_identity": candidate.extractor_identity,
            "validator_identity": self.identity,
            "validator_version": self.version,
            "independent_from_extractor": candidate.extractor_identity != self.identity,
            "decision": decision,
            "checks": [check.model_dump(mode="json") for check in checks],
            "mutation_rejection_count": mutation_rejection_count,
        }
        return ValidationResult(
            candidate_id=candidate.candidate_id,
            extractor_identity=candidate.extractor_identity,
            validator_identity=self.identity,
            validator_version=self.version,
            independent_from_extractor=candidate.extractor_identity != self.identity,
            decision=decision,
            checks=tuple(checks),
            mutation_rejection_count=mutation_rejection_count,
            result_hash=_artifact_hash(unsigned),
        )

    def validate_world(
        self,
        world: SyntheticWorld,
        extraction: ExtractionArtifact,
        *,
        cache_dir: Path | None = None,
    ) -> ValidationArtifact:
        if extraction.world_id != world.manifest.world_id:
            raise ValueError("validation artifact and world identities differ")
        source_index = {source.source_doc_id: source for source in world.sources}
        results = tuple(
            self.validate(candidate, source_index[candidate.source_doc_id])
            for candidate in extraction.candidates
        )
        records = [result.model_dump(mode="json") for result in results]
        artifact_hash = _artifact_hash(records)
        artifact_id = (
            "validation_"
            + hashlib.sha256(
                stable_json((extraction.artifact_id, self.identity, self.version, artifact_hash))
            ).hexdigest()[:24]
        )
        artifact = ValidationArtifact(
            artifact_id=artifact_id,
            extraction_artifact_id=extraction.artifact_id,
            validator_identity=self.identity,
            validator_version=self.version,
            result_count=len(results),
            results=results,
            artifact_hash=artifact_hash,
        )
        if cache_dir is not None:
            _save_artifact(
                artifact,
                cache_dir / "validation" / f"{artifact.artifact_id}.json",
            )
        return artifact


class IndependentAdjudicator:
    """Resolve role disagreement and use gold only for synthetic grading."""

    identity = ADJUDICATOR_IDENTITY
    version = ADJUDICATOR_VERSION

    @staticmethod
    def _matches_gold(candidate: CandidatePacket, claim: SyntheticClaim) -> bool:
        quantity_matches = (
            candidate.quantity_value == claim.quantity_value
            and candidate.quantity_unit == claim.quantity_unit
            and candidate.quantity_owner_id == claim.quantity_owner_id
        )
        return (
            candidate.packet_type == claim.packet_type
            and candidate.subject_id == claim.subject_id
            and candidate.relation == claim.relation
            and candidate.object_value == claim.object_value
            and candidate.object_is_entity == claim.object_is_entity
            and candidate.date_value == claim.date_value
            and quantity_matches
            and candidate.polarity == claim.polarity
            and candidate.attribution_id == claim.attribution_id
        )

    @staticmethod
    def _gold_for_candidate(
        candidate: CandidatePacket,
        world: SyntheticWorld,
    ) -> SyntheticClaim | None:
        claim_index = {claim.claim_id: claim for claim in world.claims}
        source = next(
            (
                item
                for item in world.sources
                if item.source_doc_id == candidate.source_doc_id
            ),
            None,
        )
        if source is None:
            return None
        span = next(
            (
                item
                for item in source.spans
                if item.raw_char_start == candidate.raw_char_start
                and item.raw_char_end == candidate.raw_char_end
                and item.raw_text_hash == candidate.evidence_hash
            ),
            None,
        )
        return claim_index.get(span.claim_id) if span is not None else None

    def adjudicate(
        self,
        candidate: CandidatePacket,
        validation: ValidationResult,
        *,
        synthetic_world: SyntheticWorld | None = None,
    ) -> AdjudicationResult:
        gold_claim = (
            self._gold_for_candidate(candidate, synthetic_world)
            if synthetic_world is not None
            else None
        )
        return self._adjudicate_with_gold(
            candidate,
            validation,
            gold_claim=gold_claim,
            use_gold=synthetic_world is not None,
        )

    def _adjudicate_with_gold(
        self,
        candidate: CandidatePacket,
        validation: ValidationResult,
        *,
        gold_claim: SyntheticClaim | None,
        use_gold: bool,
    ) -> AdjudicationResult:
        reasons: list[str] = []
        truth_match: bool | None = None
        mutation_rejections = 0
        if validation.candidate_id != candidate.candidate_id:
            decision = AdjudicationDecision.REJECT
            reasons.append("candidate_validation_identity_mismatch")
        elif (
            validation.validator_identity == candidate.extractor_identity
            or not validation.independent_from_extractor
        ):
            decision = AdjudicationDecision.REJECT
            reasons.append("self_approval_forbidden")
        elif validation.decision is ValidationDecision.FAIL:
            decision = AdjudicationDecision.REJECT
            reasons.append("validator_failed_integrity")
        elif validation.decision is ValidationDecision.REVIEW:
            decision = AdjudicationDecision.QUARANTINE
            reasons.append("extractor_validator_disagreement")
        else:
            decision = AdjudicationDecision.CANONICAL

        if use_gold:
            truth_match = gold_claim is not None and self._matches_gold(candidate, gold_claim)
            if gold_claim is None:
                decision = AdjudicationDecision.REJECT
                reasons.append("no_structured_gold_binding")
            elif not truth_match:
                decision = AdjudicationDecision.REJECT
                reasons.append("structured_gold_mismatch")
            else:
                mutations = (
                    candidate.model_copy(update={"subject_id": "ent_mutated"}),
                    candidate.model_copy(update={"relation": "relation_mutated"}),
                    candidate.model_copy(
                        update={
                            "polarity": (
                                "negative"
                                if candidate.polarity == "positive"
                                else "positive"
                            )
                        }
                    ),
                    candidate.model_copy(update={"date_value": "2099-12-31"}),
                    candidate.model_copy(update={"attribution_id": "ent_mutated"}),
                )
                mutation_rejections = sum(
                    not self._matches_gold(mutation, gold_claim) for mutation in mutations
                )
                if mutation_rejections != len(mutations):
                    decision = AdjudicationDecision.QUARANTINE
                    reasons.append("mutation_falsification_failed")

        unsigned = {
            "candidate_id": candidate.candidate_id,
            "adjudicator_identity": self.identity,
            "adjudicator_version": self.version,
            "decision": decision,
            "synthetic_truth_match": truth_match,
            "matched_claim_id": gold_claim.claim_id if gold_claim is not None else None,
            "mutation_rejection_count": mutation_rejections,
            "reasons": tuple(reasons),
        }
        return AdjudicationResult(
            candidate_id=candidate.candidate_id,
            adjudicator_identity=self.identity,
            adjudicator_version=self.version,
            decision=decision,
            synthetic_truth_match=truth_match,
            matched_claim_id=gold_claim.claim_id if gold_claim is not None else None,
            mutation_rejection_count=mutation_rejections,
            reasons=tuple(reasons),
            result_hash=_artifact_hash(unsigned),
        )

    def adjudicate_world(
        self,
        world: SyntheticWorld,
        extraction: ExtractionArtifact,
        validation: ValidationArtifact,
        *,
        cache_dir: Path | None = None,
    ) -> AdjudicationArtifact:
        if validation.extraction_artifact_id != extraction.artifact_id:
            raise ValueError("adjudication inputs do not identify the same extraction")
        validation_index = {result.candidate_id: result for result in validation.results}
        claim_index = {claim.claim_id: claim for claim in world.claims}
        span_claim_index = {
            (
                source.source_doc_id,
                span.raw_char_start,
                span.raw_char_end,
                span.raw_text_hash,
            ): claim_index[span.claim_id]
            for source in world.sources
            for span in source.spans
        }
        results = tuple(
            self._adjudicate_with_gold(
                candidate,
                validation_index[candidate.candidate_id],
                gold_claim=span_claim_index.get(
                    (
                        candidate.source_doc_id,
                        candidate.raw_char_start,
                        candidate.raw_char_end,
                        candidate.evidence_hash,
                    )
                ),
                use_gold=True,
            )
            for candidate in extraction.candidates
        )
        records = [result.model_dump(mode="json") for result in results]
        artifact_hash = _artifact_hash(records)
        artifact_id = (
            "adjudication_"
            + hashlib.sha256(
                stable_json((validation.artifact_id, self.identity, self.version, artifact_hash))
            ).hexdigest()[:24]
        )
        artifact = AdjudicationArtifact(
            artifact_id=artifact_id,
            validation_artifact_id=validation.artifact_id,
            adjudicator_identity=self.identity,
            adjudicator_version=self.version,
            result_count=len(results),
            results=results,
            artifact_hash=artifact_hash,
        )
        if cache_dir is not None:
            _save_artifact(
                artifact,
                cache_dir / "adjudication" / f"{artifact.artifact_id}.json",
            )
        return artifact
