"""Shared wire contracts for Semantic Address Plane v2 artifacts."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

ADDRESS_EXPORT_SCHEMA_VERSION = "aethersparse.semantic-address-export.v2"
ADDRESS_MANIFEST_SCHEMA_VERSION = "aethersparse.semantic-address-manifest.v2"
CANONICAL_ENTITY_PREFIX = "as:v050:entity:"
RECORD_ID_PREFIX = "as:v2:record:"

_CANONICAL_ENTITY = re.compile(r"^as:v050:entity:[0-9a-f]{24}$")
_RECORD_ID = re.compile(r"^as:v2:record:[0-9a-f]{64}$")


class AddressChannelV2(StrEnum):
    """Canonical export channel names shared by compiler consumers."""

    TITLE = "title"
    ALIAS = "alias"
    REDIRECT = "redirect"
    ANCHOR = "anchor"
    FUZZY = "fuzzy"
    SEMANTIC = "semantic"


_FUSION_CHANNEL = {
    AddressChannelV2.TITLE: "exact_title",
    AddressChannelV2.ALIAS: "alias",
    AddressChannelV2.REDIRECT: "redirect",
    AddressChannelV2.ANCHOR: "anchor_prior",
    AddressChannelV2.FUZZY: "fuzzy",
    AddressChannelV2.SEMANTIC: "semantic",
}


def fusion_channel_name(channel: AddressChannelV2 | str) -> str:
    """Map an export channel to the fusion wire name without guessing."""

    return _FUSION_CHANNEL[AddressChannelV2(channel)]


def normalize_surface(value: str) -> str:
    """Return the one canonical v0.5 lookup form."""

    return " ".join(unicodedata.normalize("NFKC", value.replace("_", " ")).casefold().split())


def canonical_entity_id(title: str) -> str:
    """Mint a corpus-band ID from an authoritative canonical title."""

    digest = hashlib.sha256(normalize_surface(title).encode()).hexdigest()[:24]
    return f"{CANONICAL_ENTITY_PREFIX}{digest}"


def is_canonical_entity_id(value: str) -> bool:
    return bool(_CANONICAL_ENTITY.fullmatch(value))


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def stable_record_id(row: Mapping[str, object]) -> str:
    """Content-address a record, excluding its own identity field."""

    payload = {key: value for key, value in row.items() if key != "record_id"}
    return f"{RECORD_ID_PREFIX}{hashlib.sha256(_canonical_json(payload)).hexdigest()}"


def with_stable_record_id(row: Mapping[str, object]) -> dict[str, object]:
    """Copy a v2 record and attach its deterministic content identity."""

    output = dict(row)
    output["record_id"] = stable_record_id(output)
    return output


_FIELDS = {
    "entity": frozenset(
        {"entity_id", "title", "normalized_title", "document_id", "source_text_sha256"}
    ),
    "alias": frozenset(
        {
            "surface",
            "kind",
            "source_document_id",
            "canonical_entity_id",
            "canonical_title",
            "resolution_state",
            "redirect_path",
        }
    ),
    "redirect": frozenset(
        {
            "source_document_id",
            "source_title",
            "target_title",
            "canonical_entity_id",
            "canonical_title",
            "resolution_state",
            "redirect_path",
            "source_text_sha256",
        }
    ),
    "hyperlink_occurrence": frozenset(
        {
            "corpus_tier",
            "anchor_id",
            "source_document_id",
            "source_text_sha256",
            "source_split",
            "mention",
            "normalized_mention",
            "mention_start",
            "mention_end",
            "link_start",
            "link_end",
            "offset_unit",
            "context",
            "context_start",
            "context_end",
            "raw_target_title",
            "canonical_entity_id",
            "canonical_title",
            "resolution_state",
            "redirect_path",
            "source_span_sha256",
        }
    ),
    "surface_statistics": frozenset(
        {
            "statistics_view",
            "included_source_splits",
            "usage",
            "surface",
            "occurrence_count",
            "ambiguity_count",
            "entropy_nats",
            "unresolved_probability_mass",
            "support_bin",
            "source_splits_present",
            "unseen_surface_in_holdout",
            "candidates",
        }
    ),
    "duplicate_title": frozenset({"normalized_title", "source_document_ids"}),
    "unresolved_redirect": frozenset(
        {
            "source_document_id",
            "source_title",
            "target_title",
            "resolution_state",
            "redirect_path",
        }
    ),
    "benchmark_mention_runtime": frozenset(
        {
            "case_id",
            "partition",
            "corpus_tier",
            "query_sha256",
            "mention_id",
            "surface",
            "char_start",
            "char_end",
            "mention_detected",
            "candidate_count_generated",
            "pre_cap_candidates",
            "retained_entity_ids",
            "selected_entity_ids",
            "confidence_rejected_entity_ids",
            "retained_cap",
        }
    ),
    "benchmark_mention_label": frozenset(
        {
            "case_id",
            "partition",
            "corpus_tier",
            "mention_id",
            "correct_entity_ids",
            "alignment_basis",
            "alignment_evidence_sha256",
            "alignment_exact",
            "quarantine_reason",
            "failure_state",
        }
    ),
    "alignment_quarantine": frozenset(
        {
            "case_id",
            "partition",
            "corpus_tier",
            "mention_id",
            "correct_entity_ids",
            "alignment_basis",
            "alignment_evidence_sha256",
            "alignment_exact",
            "quarantine_reason",
        }
    ),
}
_OPTIONAL: dict[str, frozenset[str]] = {}
_COMMON = frozenset({"schema_version", "record_type", "record_id"})
_CANDIDATE_FIELDS = frozenset(
    {
        "entity_id",
        "canonical_title",
        "channel",
        "channel_rank",
        "global_pre_cap_rank",
        "raw_score",
        "channel_score",
        "provenance_ids",
    }
)
_SURFACE_CANDIDATE_FIELDS = frozenset(
    {
        "canonical_entity_id",
        "canonical_title",
        "resolution_state",
        "occurrence_count",
        "source_document_count",
        "probability",
        "source_diversity",
    }
)
_STATISTICS_VIEW_POLICY = {
    "fit": (["fit"], "fit_and_selection"),
    "fit+calibration": (["fit", "calibration"], "holdout_qualification_only"),
    "all": (["fit", "calibration", "holdout"], "descriptive_only"),
}


def validate_record_contract(row: Mapping[str, Any]) -> None:
    """Enforce the closed record contract used by JSON Schema and writers."""

    if row.get("schema_version") != ADDRESS_EXPORT_SCHEMA_VERSION:
        raise ValueError("unsupported Semantic Address v2 record schema")
    record_type = row.get("record_type")
    if not isinstance(record_type, str) or record_type not in _FIELDS:
        raise ValueError("unknown Semantic Address v2 record type")
    expected = _COMMON | _FIELDS[record_type]
    if set(row) - expected:
        unknown = sorted(set(row) - expected)
        raise ValueError(f"closed {record_type} record has unknown fields: {unknown}")
    required = expected - _OPTIONAL.get(record_type, frozenset())
    if missing := required - set(row):
        raise ValueError(f"{record_type} record is missing fields: {sorted(missing)}")
    record_id = row.get("record_id")
    if not isinstance(record_id, str) or not _RECORD_ID.fullmatch(record_id):
        raise ValueError("record_id is not a Semantic Address v2 content identity")
    if record_id != stable_record_id(row):
        raise ValueError("record_id does not match record content")
    for field in ("entity_id", "canonical_entity_id"):
        value = row.get(field)
        if value is not None and (not isinstance(value, str) or not is_canonical_entity_id(value)):
            raise ValueError(f"{field} is outside the canonical corpus ID band")
    candidates = row.get("pre_cap_candidates")
    if candidates is not None:
        if not isinstance(candidates, list):
            raise ValueError("pre_cap_candidates must be a list")
        for candidate in candidates:
            if not isinstance(candidate, dict) or set(candidate) != _CANDIDATE_FIELDS:
                raise ValueError("candidate provenance does not match its closed contract")
            if not is_canonical_entity_id(str(candidate["entity_id"])):
                raise ValueError("candidate entity ID is outside the canonical corpus band")
            AddressChannelV2(str(candidate["channel"]))
            score = candidate["channel_score"]
            if not isinstance(score, int | float) or isinstance(score, bool):
                raise ValueError("channel_score must be numeric")
            numeric_score = float(score)
            if not math.isfinite(numeric_score) or not 0.0 <= numeric_score <= 1.0:
                raise ValueError("channel_score must be finite and in [0,1]")
            raw_score = candidate["raw_score"]
            if raw_score is not None and (
                not isinstance(raw_score, int | float)
                or isinstance(raw_score, bool)
                or not math.isfinite(float(raw_score))
            ):
                raise ValueError("raw_score must be null or finite numeric evidence")
            provenance = candidate["provenance_ids"]
            if (
                not isinstance(provenance, list)
                or not provenance
                or any(not isinstance(item, str) or not item for item in provenance)
                or len(provenance) != len(set(provenance))
            ):
                raise ValueError("candidate provenance_ids must be non-empty strings")
    if record_type == "surface_statistics":
        view = row["statistics_view"]
        if not isinstance(view, str) or view not in _STATISTICS_VIEW_POLICY:
            raise ValueError("surface-statistics view is unknown")
        included, usage = _STATISTICS_VIEW_POLICY[view]
        if row["included_source_splits"] != included or row["usage"] != usage:
            raise ValueError("surface-statistics row violates its split/usage policy")
        present = row["source_splits_present"]
        if not isinstance(present, list) or not set(present).issubset(included):
            raise ValueError("surface-statistics row exposes a split outside its view")
        if row["unseen_surface_in_holdout"] is True and view != "all":
            raise ValueError("unseen holdout identity may appear only in the all-data view")
        row_occurrence_count = row["occurrence_count"]
        row_unresolved_mass = row["unresolved_probability_mass"]
        if (
            isinstance(row_occurrence_count, bool)
            or not isinstance(row_occurrence_count, int)
            or row_occurrence_count < 1
            or isinstance(row_unresolved_mass, bool)
            or not isinstance(row_unresolved_mass, int | float)
        ):
            raise ValueError("surface-statistics aggregate fields are invalid")
        alternatives = row["candidates"]
        if not isinstance(alternatives, list) or not alternatives:
            raise ValueError("surface-statistics candidates must be a non-empty list")
        occurrence_total = 0
        probability_total = 0.0
        unresolved_total = 0.0
        for alternative in alternatives:
            if not isinstance(alternative, dict) or set(alternative) != _SURFACE_CANDIDATE_FIELDS:
                raise ValueError("surface-statistics candidate violates its closed contract")
            occurrence_count = alternative["occurrence_count"]
            document_count = alternative["source_document_count"]
            probability = alternative["probability"]
            diversity = alternative["source_diversity"]
            if (
                isinstance(occurrence_count, bool)
                or not isinstance(occurrence_count, int)
                or occurrence_count < 1
                or isinstance(document_count, bool)
                or not isinstance(document_count, int)
                or not 1 <= document_count <= occurrence_count
            ):
                raise ValueError("surface-statistics support/diversity counts are invalid")
            if not isinstance(probability, int | float) or isinstance(probability, bool):
                raise ValueError("surface-statistics probability must be numeric")
            if not isinstance(diversity, int | float) or isinstance(diversity, bool):
                raise ValueError("surface-statistics diversity must be numeric")
            if not math.isclose(float(diversity), document_count / occurrence_count):
                raise ValueError("surface-statistics diversity disagrees with exact counts")
            occurrence_total += occurrence_count
            probability_total += float(probability)
            if alternative["canonical_entity_id"] is None:
                unresolved_total += float(probability)
        if occurrence_total != row_occurrence_count or not math.isclose(
            probability_total, 1.0, abs_tol=1e-12
        ):
            raise ValueError("surface-statistics probability/support mass is inconsistent")
        if not math.isclose(unresolved_total, float(row_unresolved_mass), abs_tol=1e-12):
            raise ValueError("surface-statistics unresolved mass is inconsistent")
