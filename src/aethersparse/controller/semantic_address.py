"""Occurrence-backed semantic entity addresses with explicit uncertainty.

The Semantic Address Plane does not resolve a question by itself.  It exposes
the corpus-measured distribution :math:`P(entity|mention)` and the support used
to estimate it.  Consumers may combine those features with contextual evidence,
but must not turn an unresolved target title into a canonical entity ID or infer
whether a candidate missing from a retained set was never generated or was
removed by a cap.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import unicodedata
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from aethersparse.controller.models import EntityMention

STATISTICS_SCHEMA_VERSION = "aethersparse.entity-anchor-statistics.v11"
STATISTICS_MANIFEST_SCHEMA_VERSION = "aethersparse.entity-anchor-statistics-manifest.v11"
CORPUS_ENTITY_ID_PREFIX = "as:v050:entity:"


class SemanticAddressDataError(ValueError):
    """Raised when occurrence statistics or their identity are inconsistent."""


class RetainedAddressState(StrEnum):
    """Gold-aware qualification states supported by retained replay evidence.

    The two absent states deliberately say only what the retained candidate set
    proves.  They do not distinguish generator misses from pre-retention caps.
    """

    MENTION_SET_EMPTY = "mention_set_empty"
    REQUIRED_ABSENT_FROM_RETAINED_SET = "required_absent_from_retained_set"
    REQUIRED_SET_INCOMPLETE_IN_RETAINED_SET = "required_set_incomplete_in_retained_set"
    REQUIRED_TOP_RANKED_NOT_SELECTED = "required_top_ranked_not_selected"
    REQUIRED_PRESENT_SELECTION_INCOMPLETE = "required_present_selection_incomplete"
    REQUIRED_SELECTED = "required_selected"


@dataclass(frozen=True)
class StatisticsArtifactIdentity:
    """Verified identity of a targeted occurrence-statistics artifact."""

    gzip_sha256: str
    json_sha256: str
    manifest_sha256: str
    source_pack_sha256: str
    hard_negatives_sha256: str
    requested_mention_count: int
    covered_mention_count: int
    statistic_count: int


@dataclass(frozen=True)
class SemanticAddressHypothesis:
    """One canonical address supported by a mention occurrence distribution."""

    entity_id: str
    target_title: str
    mention_probability: float
    occurrence_count: int
    source_document_count: int
    source_diversity: float
    ambiguity_entropy_nats: float
    title_indicator: bool
    title_prior: float
    redirect_indicator: bool
    redirect_support_count: int
    redirect_prior: float
    alias_types: tuple[str, ...]
    retained_candidate_rank: int | None = None
    retained_candidate_confidence: float | None = None


@dataclass(frozen=True)
class SemanticAddressDistribution:
    """A calibrated subdistribution over authoritative canonical addresses.

    ``unresolved_probability_mass`` is probability assigned to anchor targets
    that could not be mapped unambiguously to a canonical entity in the source
    pack.  It is retained rather than renormalized away.  Retained linker
    candidates without occurrence support are reported separately and do not
    receive invented probability mass.
    """

    mention: str
    normalized_mention: str
    hypotheses: tuple[SemanticAddressHypothesis, ...]
    unresolved_probability_mass: float
    unsupported_retained_entity_ids: tuple[str, ...]
    total_mention_occurrences: int
    ambiguity_count: int
    ambiguity_entropy_nats: float
    smoothing_alpha: float

    @property
    def resolved_probability_mass(self) -> float:
        return sum(item.mention_probability for item in self.hypotheses)

    @property
    def probability_mass(self) -> float:
        return self.resolved_probability_mass + self.unresolved_probability_mass


@dataclass(frozen=True)
class _OccurrenceRow:
    mention: str
    target_title: str
    target_entity_id: str | None
    occurrence_count: int
    total_mention_occurrences: int
    probability: float
    ambiguity_count: int
    entropy_nats: float
    source_document_count: int
    title_indicator: bool
    title_prior: float
    redirect_indicator: bool
    redirect_support_count: int
    redirect_prior: float
    alias_types: tuple[str, ...]


def normalize_mention(value: str) -> str:
    """Return the canonical v0.5 lookup form for a copied mention surface."""

    replaced = value.replace("_", " ")
    return " ".join(unicodedata.normalize("NFKC", replaced).casefold().split())


def canonical_entity_id(title: str) -> str:
    """Mint the deterministic corpus-band ID for an authoritative title."""

    digest = hashlib.sha256(normalize_mention(title).encode("utf-8")).hexdigest()[:24]
    return f"{CORPUS_ENTITY_ID_PREFIX}{digest}"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _integer(value: object, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SemanticAddressDataError(f"{field} must be an integer >= {minimum}")
    return value


def _number(value: object, field: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SemanticAddressDataError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise SemanticAddressDataError(f"{field} must be finite and >= {minimum}")
    return result


def _boolean(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise SemanticAddressDataError(f"{field} must be boolean")
    return value


def _sha256_string(value: object, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise SemanticAddressDataError(f"{field} must be a SHA-256 hex string")
    try:
        int(value, 16)
    except ValueError as error:
        raise SemanticAddressDataError(f"{field} must be a SHA-256 hex string") from error
    return value


def _parse_row(raw: Mapping[str, object]) -> _OccurrenceRow:
    required = {
        "mention",
        "target_title",
        "target_entity_id",
        "occurrence_count",
        "total_mention_occurrences",
        "probability",
        "ambiguity_count",
        "entropy_nats",
        "source_document_count",
        "title_indicator",
        "title_prior",
        "redirect_indicator",
        "redirect_support_count",
        "redirect_prior",
        "alias_types",
    }
    missing = required - raw.keys()
    if missing:
        raise SemanticAddressDataError(f"occurrence statistic lacks fields: {sorted(missing)}")
    mention = str(raw["mention"])
    target_title = str(raw["target_title"])
    if mention != normalize_mention(mention) or target_title != normalize_mention(target_title):
        raise SemanticAddressDataError("mention and target title must be normalized")
    entity_value = raw["target_entity_id"]
    entity_id = None if entity_value is None else str(entity_value)
    if entity_id is not None:
        if not entity_id.startswith(CORPUS_ENTITY_ID_PREFIX):
            raise SemanticAddressDataError("target entity ID is outside the corpus band")
        if entity_id != canonical_entity_id(target_title):
            raise SemanticAddressDataError("target entity ID does not match its canonical title")
    occurrence_count = _integer(raw["occurrence_count"], "occurrence_count", minimum=1)
    source_document_count = _integer(
        raw["source_document_count"], "source_document_count", minimum=1
    )
    if source_document_count > occurrence_count:
        raise SemanticAddressDataError("source document support exceeds occurrence support")
    aliases = raw["alias_types"]
    if not isinstance(aliases, list) or not aliases or any(not isinstance(x, str) for x in aliases):
        raise SemanticAddressDataError("alias_types must be a non-empty string list")
    if len(set(aliases)) != len(aliases) or not set(aliases) <= {"anchor", "title", "redirect"}:
        raise SemanticAddressDataError("alias_types contains duplicate or unknown channels")
    title_indicator = _boolean(raw["title_indicator"], "title_indicator")
    redirect_indicator = _boolean(raw["redirect_indicator"], "redirect_indicator")
    if title_indicator != ("title" in aliases) or redirect_indicator != ("redirect" in aliases):
        raise SemanticAddressDataError("alias_types disagrees with title/redirect indicators")
    probability = _number(raw["probability"], "probability")
    title_prior = _number(raw["title_prior"], "title_prior")
    redirect_prior = _number(raw["redirect_prior"], "redirect_prior")
    if probability > 1.0 or title_prior > 1.0 or redirect_prior > 1.0:
        raise SemanticAddressDataError("probability and channel priors must not exceed one")
    return _OccurrenceRow(
        mention=mention,
        target_title=target_title,
        target_entity_id=entity_id,
        occurrence_count=occurrence_count,
        total_mention_occurrences=_integer(
            raw["total_mention_occurrences"], "total_mention_occurrences", minimum=1
        ),
        probability=probability,
        ambiguity_count=_integer(raw["ambiguity_count"], "ambiguity_count", minimum=1),
        entropy_nats=_number(raw["entropy_nats"], "entropy_nats"),
        source_document_count=source_document_count,
        title_indicator=title_indicator,
        title_prior=title_prior,
        redirect_indicator=redirect_indicator,
        redirect_support_count=_integer(raw["redirect_support_count"], "redirect_support_count"),
        redirect_prior=redirect_prior,
        alias_types=tuple(str(x) for x in aliases),
    )


class SemanticAddressPlane:
    """Immutable occurrence-backed lookup plane for semantic entity addresses."""

    def __init__(
        self,
        rows: Sequence[_OccurrenceRow],
        *,
        alpha: float,
        requested_mention_count: int,
        source_pack_sha256: str,
        identity: StatisticsArtifactIdentity | None = None,
    ) -> None:
        if alpha <= 0.0 or not math.isfinite(alpha):
            raise SemanticAddressDataError("alpha must be finite and positive")
        groups: dict[str, list[_OccurrenceRow]] = defaultdict(list)
        for row in rows:
            groups[row.mention].append(row)
        if requested_mention_count < len(groups):
            raise SemanticAddressDataError("covered mentions exceed requested mentions")
        self.alpha = alpha
        self.requested_mention_count = requested_mention_count
        self.source_pack_sha256 = source_pack_sha256
        self.identity = identity
        validated: dict[str, tuple[_OccurrenceRow, ...]] = {}
        for mention, group in groups.items():
            ordered = tuple(
                sorted(
                    group,
                    key=lambda item: (item.target_title, item.target_entity_id or ""),
                )
            )
            if len({item.target_title for item in ordered}) != len(ordered):
                raise SemanticAddressDataError(f"duplicate target title for mention {mention!r}")
            total = sum(item.occurrence_count for item in ordered)
            ambiguity = len(ordered)
            denominator = total + alpha * ambiguity
            probabilities = tuple((item.occurrence_count + alpha) / denominator for item in ordered)
            entropy = -sum(value * math.log(value) for value in probabilities)
            for row, expected in zip(ordered, probabilities, strict=True):
                if row.total_mention_occurrences != total or row.ambiguity_count != ambiguity:
                    raise SemanticAddressDataError(
                        f"support totals disagree within mention {mention!r}"
                    )
                if not math.isclose(row.probability, expected, abs_tol=1e-12):
                    raise SemanticAddressDataError(
                        f"smoothed probability disagrees for mention {mention!r}"
                    )
                if not math.isclose(row.entropy_nats, entropy, abs_tol=1e-12):
                    raise SemanticAddressDataError(f"entropy disagrees for mention {mention!r}")
                if row.redirect_indicator != (row.redirect_support_count > 0):
                    raise SemanticAddressDataError(
                        f"redirect indicator disagrees for mention {mention!r}"
                    )
            validated[mention] = ordered
        self._rows = validated

    @classmethod
    def from_document(
        cls,
        document: Mapping[str, Any],
        *,
        identity: StatisticsArtifactIdentity | None = None,
    ) -> SemanticAddressPlane:
        """Validate and load an already-decoded statistics document."""

        if not isinstance(document, Mapping):
            raise SemanticAddressDataError("occurrence-statistics document must be an object")
        if document.get("schema_version") != STATISTICS_SCHEMA_VERSION:
            raise SemanticAddressDataError("unsupported occurrence-statistics schema")
        raw_statistics = document.get("statistics")
        if not isinstance(raw_statistics, list):
            raise SemanticAddressDataError("statistics must be a list")
        rows = tuple(_parse_row(item) for item in raw_statistics if isinstance(item, Mapping))
        if len(rows) != len(raw_statistics):
            raise SemanticAddressDataError("every statistic must be an object")
        plane = cls(
            rows,
            alpha=_number(document.get("alpha"), "alpha", minimum=0.0),
            requested_mention_count=_integer(
                document.get("requested_mention_count"), "requested_mention_count"
            ),
            source_pack_sha256=_sha256_string(
                document.get("source_pack_sha256"), "source_pack_sha256"
            ),
            identity=identity,
        )
        covered = _integer(document.get("covered_mention_count"), "covered_mention_count")
        if covered != plane.covered_mention_count:
            raise SemanticAddressDataError("covered mention count disagrees with statistics")
        return plane

    @classmethod
    def from_gzip(
        cls,
        statistics_path: Path,
        manifest_path: Path,
        *,
        expected_hard_negatives_sha256: str | None = None,
    ) -> SemanticAddressPlane:
        """Verify an external gzip + manifest and load it without copying payloads."""

        compressed = statistics_path.read_bytes()
        try:
            raw = gzip.decompress(compressed)
            document = json.loads(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SemanticAddressDataError("invalid occurrence-statistics gzip") from error
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SemanticAddressDataError("invalid occurrence-statistics manifest") from error
        if not isinstance(document, Mapping) or not isinstance(manifest, Mapping):
            raise SemanticAddressDataError("statistics payload and manifest must be objects")
        if manifest.get("schema_version") != STATISTICS_MANIFEST_SCHEMA_VERSION:
            raise SemanticAddressDataError("unsupported occurrence-statistics manifest schema")
        gzip_sha256 = _sha256_bytes(compressed)
        json_sha256 = _sha256_bytes(raw)
        checks = {
            "output_gzip_sha256": gzip_sha256,
            "output_json_sha256": json_sha256,
            "source_pack_sha256": document.get("source_pack_sha256"),
            "requested_mention_count": document.get("requested_mention_count"),
            "covered_mention_count": document.get("covered_mention_count"),
            "statistic_count": len(document.get("statistics", ())),
            "alpha": document.get("alpha"),
        }
        for field, observed in checks.items():
            if manifest.get(field) != observed:
                raise SemanticAddressDataError(f"manifest mismatch for {field}")
        hard_negatives_sha256 = _sha256_string(
            manifest.get("hard_negatives_sha256"), "hard_negatives_sha256"
        )
        if (
            expected_hard_negatives_sha256 is not None
            and hard_negatives_sha256 != expected_hard_negatives_sha256
        ):
            raise SemanticAddressDataError("hard-negative identity mismatch")
        identity = StatisticsArtifactIdentity(
            gzip_sha256=gzip_sha256,
            json_sha256=json_sha256,
            manifest_sha256=_sha256_file(manifest_path),
            source_pack_sha256=_sha256_string(manifest["source_pack_sha256"], "source_pack_sha256"),
            hard_negatives_sha256=hard_negatives_sha256,
            requested_mention_count=int(manifest["requested_mention_count"]),
            covered_mention_count=int(manifest["covered_mention_count"]),
            statistic_count=int(manifest["statistic_count"]),
        )
        return cls.from_document(document, identity=identity)

    @property
    def covered_mention_count(self) -> int:
        return len(self._rows)

    @property
    def statistic_count(self) -> int:
        return sum(len(value) for value in self._rows.values())

    def mentions(self) -> tuple[str, ...]:
        return tuple(sorted(self._rows))

    def distribution(
        self,
        mention: str,
        *,
        retained_candidates: Sequence[tuple[str, float]] = (),
    ) -> SemanticAddressDistribution:
        """Return P(entity|mention) plus linker-state annotations.

        ``retained_candidates`` contains ``(entity_id, confidence)`` in retained
        rank order.  It annotates empirical hypotheses but never changes their
        occurrence-derived probability.
        """

        normalized = normalize_mention(mention)
        rows = self._rows.get(normalized, ())
        retained: dict[str, tuple[int, float]] = {}
        for rank, (entity_id, confidence) in enumerate(retained_candidates, start=1):
            if entity_id in retained:
                continue
            if not entity_id.startswith(CORPUS_ENTITY_ID_PREFIX):
                raise SemanticAddressDataError("retained candidate is outside the corpus band")
            probability = _number(confidence, "retained candidate confidence")
            if probability > 1.0:
                raise SemanticAddressDataError("retained candidate confidence exceeds 1")
            retained[entity_id] = (rank, probability)
        combined: dict[str, SemanticAddressHypothesis] = {}
        unresolved = 0.0
        for row in rows:
            if row.target_entity_id is None:
                unresolved += row.probability
                continue
            rank_confidence = retained.get(row.target_entity_id)
            existing = combined.get(row.target_entity_id)
            if existing is None:
                combined[row.target_entity_id] = SemanticAddressHypothesis(
                    entity_id=row.target_entity_id,
                    target_title=row.target_title,
                    mention_probability=row.probability,
                    occurrence_count=row.occurrence_count,
                    source_document_count=row.source_document_count,
                    source_diversity=row.source_document_count / row.occurrence_count,
                    ambiguity_entropy_nats=row.entropy_nats,
                    title_indicator=row.title_indicator,
                    title_prior=row.title_prior,
                    redirect_indicator=row.redirect_indicator,
                    redirect_support_count=row.redirect_support_count,
                    redirect_prior=row.redirect_prior,
                    alias_types=row.alias_types,
                    retained_candidate_rank=(rank_confidence[0] if rank_confidence else None),
                    retained_candidate_confidence=(rank_confidence[1] if rank_confidence else None),
                )
            else:
                combined[row.target_entity_id] = SemanticAddressHypothesis(
                    entity_id=existing.entity_id,
                    target_title=existing.target_title,
                    mention_probability=existing.mention_probability + row.probability,
                    occurrence_count=existing.occurrence_count + row.occurrence_count,
                    source_document_count=max(
                        existing.source_document_count, row.source_document_count
                    ),
                    source_diversity=max(
                        existing.source_diversity,
                        row.source_document_count / row.occurrence_count,
                    ),
                    ambiguity_entropy_nats=existing.ambiguity_entropy_nats,
                    title_indicator=existing.title_indicator or row.title_indicator,
                    title_prior=max(existing.title_prior, row.title_prior),
                    redirect_indicator=existing.redirect_indicator or row.redirect_indicator,
                    redirect_support_count=(
                        existing.redirect_support_count + row.redirect_support_count
                    ),
                    redirect_prior=max(existing.redirect_prior, row.redirect_prior),
                    alias_types=tuple(dict.fromkeys((*existing.alias_types, *row.alias_types))),
                    retained_candidate_rank=existing.retained_candidate_rank,
                    retained_candidate_confidence=existing.retained_candidate_confidence,
                )
        hypotheses = tuple(
            sorted(
                combined.values(),
                key=lambda item: (-item.mention_probability, item.entity_id),
            )
        )
        supported_ids = set(combined)
        unsupported = tuple(entity_id for entity_id in retained if entity_id not in supported_ids)
        total = rows[0].total_mention_occurrences if rows else 0
        ambiguity = rows[0].ambiguity_count if rows else 0
        entropy = rows[0].entropy_nats if rows else 0.0
        result = SemanticAddressDistribution(
            mention=mention,
            normalized_mention=normalized,
            hypotheses=hypotheses,
            unresolved_probability_mass=unresolved,
            unsupported_retained_entity_ids=unsupported,
            total_mention_occurrences=total,
            ambiguity_count=ambiguity,
            ambiguity_entropy_nats=entropy,
            smoothing_alpha=self.alpha,
        )
        if rows and not math.isclose(result.probability_mass, 1.0, abs_tol=1e-12):
            raise SemanticAddressDataError("address probability mass does not sum to one")
        return result


def classify_retained_address_state(
    required_entity_ids: Sequence[str], mentions: Sequence[EntityMention]
) -> RetainedAddressState:
    """Classify only what a retained, case-level labeled replay proves."""

    required = set(required_entity_ids)
    if not required:
        raise ValueError("at least one required entity ID is needed for qualification")
    if not mentions:
        return RetainedAddressState.MENTION_SET_EMPTY
    retained = {candidate.entity_id for mention in mentions for candidate in mention.candidates}
    if not required.intersection(retained):
        return RetainedAddressState.REQUIRED_ABSENT_FROM_RETAINED_SET
    if not required.issubset(retained):
        return RetainedAddressState.REQUIRED_SET_INCOMPLETE_IN_RETAINED_SET
    selected = {
        mention.selected_entity_id for mention in mentions if mention.selected_entity_id is not None
    }
    if required.issubset(selected):
        return RetainedAddressState.REQUIRED_SELECTED
    top_ranked = {mention.candidates[0].entity_id for mention in mentions if mention.candidates}
    if required.issubset(top_ranked):
        return RetainedAddressState.REQUIRED_TOP_RANKED_NOT_SELECTED
    return RetainedAddressState.REQUIRED_PRESENT_SELECTION_INCOMPLETE
