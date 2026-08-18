"""Deterministic semantic-address compression and ANN reference primitives.

The encoder in this module is an untrained static reference.  It is useful for
measuring index/compression mechanics without pretending that a text-similarity
hash is a trained semantic address model.  The training-readiness gate requires
occurrence-level hyperlink labels and source-document identities before a learned
encoder or rotation can be fitted.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from aethersparse.addressing.bundle_v2 import load_canonical_registry, verify_address_bundle
from aethersparse.addressing.compiler_v2 import (
    ADDRESS_EXPORT_SCHEMA_VERSION,
    ADDRESS_MANIFEST_SCHEMA_VERSION,
    AddressArtifactError,
    AddressExportManifest,
    canonical_entity_id,
    iter_jsonl_gzip,
    normalize_surface,
    verify_address_export,
)
from aethersparse.addressing.contracts_v2 import validate_record_contract

PROTECTED_PARTITIONS = frozenset({"evaluation", "final_held"})
BENCHMARK_TRAINING_PARTITIONS = frozenset({"development", "tuning"})
SEMANTIC_SUPERVISION_MANIFEST_SCHEMA_VERSION = "aethersparse.semantic-ann-supervision-manifest.v2"
SEMANTIC_INDEX_MANIFEST_SCHEMA_VERSION = "aethersparse.semantic-ann-index-manifest.v2"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CorpusSourceSplit(StrEnum):
    """Source-document split owned by the v2 corpus compiler."""

    FIT = "fit"
    CALIBRATION = "calibration"
    HOLDOUT = "holdout"


_EXPECTED_SPLIT_POLICY: Mapping[str, object] = {
    "unit": "source_document_id",
    "hash": "sha256-first-32-bits-mod-100",
    "fit_buckets": "0-79",
    "calibration_buckets": "80-89",
    "holdout_buckets": "90-99",
    "benchmark_partitions_used": False,
}
_SOURCE_SPLIT_ROLES: Mapping[str, str] = {
    CorpusSourceSplit.FIT.value: "encoder/rotation/PQ fit only",
    CorpusSourceSplit.CALIBRATION.value: "successive-halving and model selection only",
    CorpusSourceSplit.HOLDOUT.value: "corpus-only qualification; never fit or selection",
}


def _normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _power_of_two(value: int) -> bool:
    return value > 0 and value & (value - 1) == 0


@dataclass(frozen=True)
class HyperlinkSupervision:
    """One source-bound v2 hyperlink occurrence, resolved or quarantined.

    ``source_split`` is compiler-owned corpus provenance. ``benchmark_partition``
    is optional benchmark provenance and never determines training use.  A
    non-canonical occurrence remains represented with null canonical fields so
    that unresolved mass cannot disappear from audit statistics.
    """

    occurrence_record_id: str
    compiler_bundle_id: str
    corpus_tier: str
    anchor_id: str
    source_document_id: str
    source_text_sha256: str
    source_span_sha256: str
    source_split: CorpusSourceSplit
    mention: str
    normalized_mention: str
    mention_start: int
    mention_end: int
    link_start: int
    link_end: int
    context: str
    context_start: int
    context_end: int
    raw_target_title: str
    target_entity_id: str | None
    canonical_title: str | None
    resolution_state: str
    redirect_path: tuple[str, ...]
    provenance_ids: tuple[str, ...]
    benchmark_partition: str | None = None
    offset_unit: str = "unicode_codepoint"

    def __post_init__(self) -> None:
        required = (
            self.occurrence_record_id,
            self.compiler_bundle_id,
            self.corpus_tier,
            self.anchor_id,
            self.source_document_id,
            self.mention,
            self.normalized_mention,
            self.raw_target_title,
        )
        if any(not value.strip() for value in required):
            raise ValueError("hyperlink supervision fields must be non-empty")
        if not _SHA256.fullmatch(self.source_text_sha256):
            raise ValueError("source_text_sha256 must be a lowercase SHA-256 value")
        if not _SHA256.fullmatch(self.source_span_sha256):
            raise ValueError("source_span_sha256 must be a lowercase SHA-256 value")
        if self.offset_unit != "unicode_codepoint":
            raise ValueError("unsupported hyperlink offset unit")
        if not (
            0 <= self.link_start <= self.mention_start < self.mention_end <= self.link_end
            and 0 <= self.context_start <= self.mention_start
            and self.mention_end <= self.context_end
        ):
            raise ValueError("hyperlink offsets are malformed")
        relative_start = self.mention_start - self.context_start
        relative_end = self.mention_end - self.context_start
        if self.context[relative_start:relative_end] != self.mention:
            raise ValueError("mention offsets do not copy context")
        if normalize_surface(self.mention) != self.normalized_mention:
            raise ValueError("normalized mention does not match exact mention")
        try:
            split = CorpusSourceSplit(self.source_split)
        except ValueError as error:
            raise ValueError(f"unsupported corpus source split: {self.source_split}") from error
        object.__setattr__(self, "source_split", split)
        if self.benchmark_partition in PROTECTED_PARTITIONS:
            raise ValueError("protected-partition hyperlink labels cannot enter training")
        if (
            self.benchmark_partition is not None
            and self.benchmark_partition not in BENCHMARK_TRAINING_PARTITIONS
        ):
            raise ValueError(f"unsupported benchmark partition: {self.benchmark_partition}")
        if not self.provenance_ids or any(not value for value in self.provenance_ids):
            raise ValueError("occurrence provenance IDs must be non-empty")
        if len(set(self.provenance_ids)) != len(self.provenance_ids):
            raise ValueError("occurrence provenance IDs contain duplicates")
        if self.resolution_state == "canonical":
            if not self.target_entity_id or not self.canonical_title:
                raise ValueError("canonical occurrence lacks canonical identity")
            if canonical_entity_id(self.canonical_title) != self.target_entity_id:
                raise ValueError("canonical occurrence ID/title mismatch")
        elif self.resolution_state in {"missing", "ambiguous", "redirect_cycle"}:
            if self.target_entity_id is not None or self.canonical_title is not None:
                raise ValueError("unresolved occurrence carries canonical identity")
        else:
            raise ValueError(f"unsupported resolution state: {self.resolution_state}")

    @property
    def resolved(self) -> bool:
        """Whether this occurrence is lawful target supervision."""

        return self.resolution_state == "canonical"


@dataclass(frozen=True)
class SemanticSupervisionBundle:
    """Verified compiler bundle plus lossless occurrence supervision views."""

    compiler_manifest: AddressExportManifest
    compiler_manifest_sha256: str
    compiler_bundle_id: str
    canonical_registry_sha256: str
    canonical_registry: tuple[tuple[str, str], ...]
    occurrences: tuple[HyperlinkSupervision, ...]
    compiler_quarantine_record_sha256: tuple[str, ...]

    @property
    def resolved_occurrences(self) -> tuple[HyperlinkSupervision, ...]:
        return tuple(row for row in self.occurrences if row.resolved)

    @property
    def quarantined_occurrences(self) -> tuple[HyperlinkSupervision, ...]:
        return tuple(row for row in self.occurrences if not row.resolved)


@dataclass(frozen=True)
class TrainingReadiness:
    """Measured prerequisites for a fair learned semantic-address experiment."""

    occurrence_count: int
    resolved_occurrences: int
    unresolved_occurrences: int
    fit_occurrences: int
    calibration_occurrences: int
    holdout_occurrences: int
    source_document_count: int
    mention_surface_count: int
    target_entity_count: int
    source_document_overlap: int
    has_occurrence_labels: bool
    has_source_document_holdout: bool
    has_unseen_surface_calibration: bool
    has_unseen_surface_holdout: bool
    has_head_tail_support: bool
    learned_training_authorized: bool
    blockers: tuple[str, ...]


def training_readiness(rows: Sequence[HyperlinkSupervision]) -> TrainingReadiness:
    """Audit lawful corpus-split sufficiency without using benchmark labels."""

    resolved = [row for row in rows if row.resolved]
    unresolved = [row for row in rows if not row.resolved]
    fit = [row for row in resolved if row.source_split is CorpusSourceSplit.FIT]
    calibration = [row for row in resolved if row.source_split is CorpusSourceSplit.CALIBRATION]
    holdout = [row for row in resolved if row.source_split is CorpusSourceSplit.HOLDOUT]
    documents_by_split = {
        split: {row.source_document_id for row in rows if row.source_split is split}
        for split in CorpusSourceSplit
    }
    fit_documents = documents_by_split[CorpusSourceSplit.FIT]
    calibration_documents = documents_by_split[CorpusSourceSplit.CALIBRATION]
    holdout_documents = documents_by_split[CorpusSourceSplit.HOLDOUT]
    overlapping_documents = (
        (fit_documents & calibration_documents)
        | (fit_documents & holdout_documents)
        | (calibration_documents & holdout_documents)
    )
    fit_surfaces = {_normalized(row.mention) for row in fit}
    calibration_surfaces = {_normalized(row.mention) for row in calibration}
    holdout_surfaces = {_normalized(row.mention) for row in holdout}
    support = Counter(_normalized(row.mention) for row in fit)
    unseen_calibration = calibration_surfaces - fit_surfaces
    unseen_holdout = holdout_surfaces - fit_surfaces - calibration_surfaces
    blockers: list[str] = []
    if not resolved:
        blockers.append("resolved occurrence-level hyperlink labels are absent")
    if not fit:
        blockers.append("fit hyperlink occurrences are absent")
    if not calibration:
        blockers.append("calibration hyperlink occurrences are absent")
    if not holdout:
        blockers.append("holdout hyperlink occurrences are absent")
    if overlapping_documents:
        blockers.append("source documents cross corpus source splits")
    if calibration and not unseen_calibration:
        blockers.append("no unseen-surface calibration view is available")
    if holdout and not unseen_holdout:
        blockers.append("no unseen-surface holdout qualification view is available")
    has_head_tail = bool(support) and min(support.values()) == 1 and max(support.values()) >= 4
    if fit and not has_head_tail:
        blockers.append("fit lacks both singleton-tail and supported-head surfaces")
    return TrainingReadiness(
        occurrence_count=len(rows),
        resolved_occurrences=len(resolved),
        unresolved_occurrences=len(unresolved),
        fit_occurrences=len(fit),
        calibration_occurrences=len(calibration),
        holdout_occurrences=len(holdout),
        source_document_count=len({row.source_document_id for row in rows}),
        mention_surface_count=len({_normalized(row.mention) for row in rows}),
        target_entity_count=len(
            {row.target_entity_id for row in resolved if row.target_entity_id is not None}
        ),
        source_document_overlap=len(overlapping_documents),
        has_occurrence_labels=bool(resolved),
        has_source_document_holdout=bool(fit and calibration and holdout)
        and not overlapping_documents,
        has_unseen_surface_calibration=bool(unseen_calibration),
        has_unseen_surface_holdout=bool(unseen_holdout),
        has_head_tail_support=has_head_tail,
        learned_training_authorized=not blockers,
        blockers=tuple(blockers),
    )


def fit_supervision(
    rows: Sequence[HyperlinkSupervision],
) -> tuple[HyperlinkSupervision, ...]:
    """Return only resolved fit rows; no other source split can train parameters."""

    return tuple(row for row in rows if row.resolved and row.source_split is CorpusSourceSplit.FIT)


def calibration_supervision(
    rows: Sequence[HyperlinkSupervision],
) -> tuple[HyperlinkSupervision, ...]:
    """Return resolved rows lawful for successive halving/model selection."""

    return tuple(
        row for row in rows if row.resolved and row.source_split is CorpusSourceSplit.CALIBRATION
    )


def holdout_qualification(
    rows: Sequence[HyperlinkSupervision],
) -> tuple[HyperlinkSupervision, ...]:
    """Return corpus-only qualification rows, never fit or model selection rows."""

    return tuple(
        row for row in rows if row.resolved and row.source_split is CorpusSourceSplit.HOLDOUT
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _required_string(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value:
        raise AddressArtifactError(f"occurrence {field} must be a non-empty string")
    return value


def _required_integer(row: Mapping[str, Any], field: str) -> int:
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise AddressArtifactError(f"occurrence {field} must be an integer")
    return value


def _optional_string(row: Mapping[str, Any], field: str) -> str | None:
    value = row.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise AddressArtifactError(f"occurrence {field} must be null or non-empty")
    return value


def _string_tuple(row: Mapping[str, Any], field: str) -> tuple[str, ...]:
    value = row.get(field)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise AddressArtifactError(f"occurrence {field} must be a string list")
    return tuple(value)


def _validate_hash(value: str, field: str) -> None:
    if not _SHA256.fullmatch(value):
        raise AddressArtifactError(f"occurrence {field} is not a lowercase SHA-256 value")


def _load_canonical_registry(
    directory: Path,
) -> tuple[tuple[tuple[str, str], ...], str]:
    shared = load_canonical_registry(directory)
    ordered = tuple((entry.entity_id, entry.canonical_title) for entry in shared.entries)
    if not ordered:
        raise AddressArtifactError("canonical entity registry is empty")
    ordered = tuple(sorted(ordered))
    return ordered, _json_sha256(ordered)


def _compiler_quarantine_identities(directory: Path) -> tuple[str, ...]:
    identities: list[str] = []
    for row in iter_jsonl_gzip(directory / "quarantine.jsonl.gz"):
        try:
            validate_record_contract(row)
        except ValueError as error:
            raise AddressArtifactError(f"invalid compiler quarantine record: {error}") from error
        identities.append(_required_string(row, "record_id"))
    return tuple(identities)


def load_compiler_supervision(
    directory: Path,
    *,
    benchmark_partition_by_occurrence: Mapping[str, str] | None = None,
) -> SemanticSupervisionBundle:
    """Load only a fully verified Semantic Address v2 compiler bundle.

    The compiler manifest and every stream identity are recomputed first.
    Resolved targets must exist in the canonical entity stream with an exact
    ID/title match. Unresolved occurrences remain in ``occurrences`` and the
    bundle's explicit ``quarantined_occurrences`` view.
    """

    bundle_identity = verify_address_bundle(directory)
    manifest = verify_address_export(directory)
    if manifest.schema_version != ADDRESS_MANIFEST_SCHEMA_VERSION:
        raise AddressArtifactError("unsupported compiler manifest schema")
    if dict(manifest.split_policy) != dict(_EXPECTED_SPLIT_POLICY):
        raise AddressArtifactError("compiler source split policy mismatch")
    manifest_hash = bundle_identity.manifest_sha256
    bundle_id = f"as:v2:compiler-bundle:{manifest_hash}"
    canonical_registry, registry_hash = _load_canonical_registry(directory)
    registry = dict(canonical_registry)
    occurrences: list[HyperlinkSupervision] = []
    occurrence_ids: set[str] = set()
    anchor_ids: set[tuple[str, str]] = set()
    document_splits: dict[tuple[str, str], CorpusSourceSplit] = {}
    used_benchmark_ids: set[str] = set()
    resolution_counts: Counter[str] = Counter()
    source_split_counts: Counter[str] = Counter()
    for row in iter_jsonl_gzip(directory / "occurrences.jsonl.gz"):
        try:
            validate_record_contract(row)
        except ValueError as error:
            raise AddressArtifactError(f"invalid compiler occurrence record: {error}") from error
        if row.get("record_type") != "hyperlink_occurrence":
            raise AddressArtifactError("occurrences stream contains a non-occurrence row")
        corpus_tier = _required_string(row, "corpus_tier")
        if corpus_tier != manifest.corpus_tier:
            raise AddressArtifactError("occurrence corpus tier differs from manifest")
        anchor_id = _required_string(row, "anchor_id")
        anchor_key = (corpus_tier, anchor_id)
        if anchor_key in anchor_ids:
            raise AddressArtifactError(f"duplicate compiler anchor ID: {anchor_id}")
        anchor_ids.add(anchor_key)
        source_document_id = _required_string(row, "source_document_id")
        source_text_sha256 = _required_string(row, "source_text_sha256")
        source_span_sha256 = _required_string(row, "source_span_sha256")
        _validate_hash(source_text_sha256, "source_text_sha256")
        _validate_hash(source_span_sha256, "source_span_sha256")
        try:
            source_split = CorpusSourceSplit(_required_string(row, "source_split"))
        except ValueError as error:
            raise AddressArtifactError("occurrence has unsupported source split") from error
        document_key = (corpus_tier, source_document_id)
        previous_split = document_splits.get(document_key)
        if previous_split is not None and previous_split is not source_split:
            raise AddressArtifactError(
                f"source document crosses corpus splits: {source_document_id}"
            )
        document_splits[document_key] = source_split
        source_split_counts[source_split.value] += 1
        mention_start = _required_integer(row, "mention_start")
        mention_end = _required_integer(row, "mention_end")
        link_start = _required_integer(row, "link_start")
        link_end = _required_integer(row, "link_end")
        record_id = _required_string(row, "record_id")
        if record_id in occurrence_ids:
            raise AddressArtifactError(f"duplicate occurrence record ID: {record_id}")
        occurrence_ids.add(record_id)
        benchmark_partition = None
        if benchmark_partition_by_occurrence is not None:
            benchmark_partition = benchmark_partition_by_occurrence.get(record_id)
            if benchmark_partition is not None:
                used_benchmark_ids.add(record_id)
        target_entity_id = _optional_string(row, "canonical_entity_id")
        canonical_title = _optional_string(row, "canonical_title")
        resolution_state = _required_string(row, "resolution_state")
        resolution_counts[resolution_state] += 1
        if resolution_state == "canonical":
            if target_entity_id is None or canonical_title is None:
                raise AddressArtifactError("canonical occurrence lacks target identity")
            registry_title = registry.get(target_entity_id)
            if registry_title is None:
                raise AddressArtifactError(
                    f"occurrence target is absent from canonical registry: {target_entity_id}"
                )
            if registry_title != canonical_title:
                raise AddressArtifactError(
                    f"occurrence canonical title differs from registry: {target_entity_id}"
                )
        elif target_entity_id is not None or canonical_title is not None:
            raise AddressArtifactError("unresolved occurrence carries canonical target")
        supplied_provenance = row.get("provenance_ids", [])
        if not isinstance(supplied_provenance, list) or any(
            not isinstance(value, str) or not value for value in supplied_provenance
        ):
            raise AddressArtifactError("occurrence provenance_ids must be a string list")
        provenance_ids = (
            f"compiler-manifest-sha256:{manifest_hash}",
            bundle_id,
            f"compiler-record:{record_id}",
            f"source-document:{source_document_id}:{source_text_sha256}",
            f"source-span:{anchor_id}:{source_span_sha256}",
            *(str(value) for value in supplied_provenance),
        )
        if len(set(provenance_ids)) != len(provenance_ids):
            raise AddressArtifactError("occurrence provenance IDs contain duplicates")
        try:
            occurrence = HyperlinkSupervision(
                occurrence_record_id=record_id,
                compiler_bundle_id=bundle_id,
                corpus_tier=corpus_tier,
                anchor_id=anchor_id,
                source_document_id=source_document_id,
                source_text_sha256=source_text_sha256,
                source_span_sha256=source_span_sha256,
                source_split=source_split,
                mention=_required_string(row, "mention"),
                normalized_mention=_required_string(row, "normalized_mention"),
                mention_start=mention_start,
                mention_end=mention_end,
                link_start=link_start,
                link_end=link_end,
                context=_required_string(row, "context"),
                context_start=_required_integer(row, "context_start"),
                context_end=_required_integer(row, "context_end"),
                raw_target_title=_required_string(row, "raw_target_title"),
                target_entity_id=target_entity_id,
                canonical_title=canonical_title,
                resolution_state=resolution_state,
                redirect_path=_string_tuple(row, "redirect_path"),
                provenance_ids=provenance_ids,
                benchmark_partition=benchmark_partition,
                offset_unit=_required_string(row, "offset_unit"),
            )
        except ValueError as error:
            raise AddressArtifactError(
                f"invalid hyperlink occurrence {anchor_id}: {error}"
            ) from error
        occurrences.append(occurrence)
    if benchmark_partition_by_occurrence is not None:
        unused = set(benchmark_partition_by_occurrence) - used_benchmark_ids
        if unused:
            raise AddressArtifactError(
                f"benchmark partition map has unknown occurrence IDs: {sorted(unused)[:3]}"
            )
    if manifest.counts.get("occurrences") != len(occurrences):
        raise AddressArtifactError("compiler occurrence count differs from loaded rows")
    for state in ("canonical", "missing", "ambiguous", "redirect_cycle"):
        expected = manifest.counts.get(f"occurrence_resolution_{state}", 0)
        if expected != resolution_counts[state]:
            raise AddressArtifactError(f"compiler occurrence count mismatch: {state}")
    manifest_split_counts = manifest.views.get("source_split_occurrences")
    if not isinstance(manifest_split_counts, dict) or manifest_split_counts != dict(
        sorted(source_split_counts.items())
    ):
        raise AddressArtifactError("compiler source split summary mismatch")
    unresolved_statistical_count = 0
    for row in iter_jsonl_gzip(directory / "surface_statistics.jsonl.gz"):
        if row.get("statistics_view") != "all":
            continue
        if row.get("included_source_splits") != ["fit", "calibration", "holdout"]:
            raise AddressArtifactError("all surface-statistics view has an invalid split scope")
        if row.get("usage") != "descriptive_only":
            raise AddressArtifactError("all surface-statistics view has an invalid usage policy")
        candidates = row.get("candidates")
        if not isinstance(candidates, list):
            raise AddressArtifactError("surface statistics candidates are malformed")
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise AddressArtifactError("surface statistics candidate is malformed")
            if candidate.get("canonical_entity_id") is None:
                candidate_count = candidate.get("occurrence_count")
                if (
                    isinstance(candidate_count, bool)
                    or not isinstance(candidate_count, int)
                    or candidate_count < 1
                ):
                    raise AddressArtifactError("unresolved surface support is malformed")
                unresolved_statistical_count += candidate_count
    unresolved_occurrence_count = sum(
        resolution_counts[state] for state in ("missing", "ambiguous", "redirect_cycle")
    )
    if unresolved_statistical_count != unresolved_occurrence_count:
        raise AddressArtifactError("unresolved occurrence/statistical support counts differ")
    return SemanticSupervisionBundle(
        compiler_manifest=manifest,
        compiler_manifest_sha256=manifest_hash,
        compiler_bundle_id=bundle_id,
        canonical_registry_sha256=registry_hash,
        canonical_registry=canonical_registry,
        occurrences=tuple(occurrences),
        compiler_quarantine_record_sha256=_compiler_quarantine_identities(directory),
    )


def semantic_manifest_contract() -> dict[str, object]:
    """Return the machine-readable supervision/index interoperation contract."""

    return {
        "compiler_export_schema": ADDRESS_EXPORT_SCHEMA_VERSION,
        "compiler_manifest_schema": ADDRESS_MANIFEST_SCHEMA_VERSION,
        "supervision_manifest_schema": SEMANTIC_SUPERVISION_MANIFEST_SCHEMA_VERSION,
        "index_manifest_schema": SEMANTIC_INDEX_MANIFEST_SCHEMA_VERSION,
        "source_split_policy": dict(_EXPECTED_SPLIT_POLICY),
        "source_split_roles": dict(_SOURCE_SPLIT_ROLES),
        "benchmark_partition_controls_source_split": False,
        "canonical_registry_validation_required": True,
        "unresolved_occurrence_policy": (
            "retain as explicit quarantine rows and reconcile against surface statistics"
        ),
        "required_occurrence_identity_fields": [
            "record_id",
            "corpus_tier",
            "anchor_id",
            "source_document_id",
            "source_text_sha256",
            "source_span_sha256",
            "mention_start",
            "mention_end",
            "link_start",
            "link_end",
        ],
        "index_authority": (
            "canonical entity IDs/titles come only from the verified compiler registry; "
            "ANN codes are non-authoritative proposals"
        ),
    }


def _training_readiness_document(readiness: TrainingReadiness) -> dict[str, object]:
    document: dict[str, object] = asdict(readiness)
    document["blockers"] = list(readiness.blockers)
    return document


def semantic_supervision_manifest_document(
    bundle: SemanticSupervisionBundle,
) -> dict[str, object]:
    """Build the deterministic serialized supervision manifest document."""

    readiness = training_readiness(bundle.occurrences)
    record_ids = [row.occurrence_record_id for row in bundle.occurrences]
    resolution_counts = Counter(row.resolution_state for row in bundle.occurrences)
    split_counts = Counter(row.source_split.value for row in bundle.occurrences)
    return {
        "schema_version": SEMANTIC_SUPERVISION_MANIFEST_SCHEMA_VERSION,
        "compiler": {
            "manifest_schema": bundle.compiler_manifest.schema_version,
            "manifest_sha256": bundle.compiler_manifest_sha256,
            "bundle_id": bundle.compiler_bundle_id,
            "source_pack_sha256": bundle.compiler_manifest.source_pack_sha256,
            "corpus_tier": bundle.compiler_manifest.corpus_tier,
        },
        "canonical_registry": {
            "sha256": bundle.canonical_registry_sha256,
            "entity_count": len(bundle.canonical_registry),
            "validation": "entity ID/title exact match against verified entities stream",
        },
        "occurrences": {
            "count": len(bundle.occurrences),
            "resolved_count": len(bundle.resolved_occurrences),
            "quarantined_unresolved_count": len(bundle.quarantined_occurrences),
            "record_ids_sha256": _json_sha256(record_ids),
            "source_split_counts": dict(sorted(split_counts.items())),
            "resolution_counts": dict(sorted(resolution_counts.items())),
            "provenance_required": True,
        },
        "compiler_quarantine": {
            "record_count": len(bundle.compiler_quarantine_record_sha256),
            "record_ids_sha256": _json_sha256(bundle.compiler_quarantine_record_sha256),
        },
        "split_roles": dict(_SOURCE_SPLIT_ROLES),
        "benchmark_partition_controls_source_split": False,
        "training_readiness": _training_readiness_document(readiness),
    }


def write_semantic_supervision_manifest(bundle: SemanticSupervisionBundle, path: Path) -> str:
    """Serialize a deterministic supervision manifest and return its SHA-256."""

    document = semantic_supervision_manifest_document(bundle)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return _sha256_file(path)


def verify_semantic_supervision_manifest(
    bundle: SemanticSupervisionBundle, path: Path
) -> dict[str, object]:
    """Reject a supervision manifest that is not the exact bundle projection."""

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AddressArtifactError("semantic supervision manifest is missing or invalid") from error
    expected = semantic_supervision_manifest_document(bundle)
    if document != expected:
        raise AddressArtifactError("semantic supervision manifest identity mismatch")
    return expected


def semantic_index_manifest_document(
    bundle: SemanticSupervisionBundle,
    *,
    supervision_manifest_sha256: str,
    encoder_artifact_sha256: str | None = None,
    index_artifact_sha256: str | None = None,
) -> dict[str, object]:
    """Build the serialized ANN index contract, including a blocked state."""

    _validate_hash(supervision_manifest_sha256, "supervision_manifest_sha256")
    if (encoder_artifact_sha256 is None) != (index_artifact_sha256 is None):
        raise ValueError("encoder and index artifact identities must be supplied together")
    if encoder_artifact_sha256 is not None:
        _validate_hash(encoder_artifact_sha256, "encoder_artifact_sha256")
        if index_artifact_sha256 is None:  # pragma: no cover - narrowed above
            raise AssertionError("index identity missing")
        _validate_hash(index_artifact_sha256, "index_artifact_sha256")
    readiness = training_readiness(bundle.occurrences)
    built = encoder_artifact_sha256 is not None
    if built and not readiness.learned_training_authorized:
        raise AddressArtifactError("cannot declare a learned index from blocked supervision")
    status = (
        "BUILT"
        if built
        else (
            "NOT_BUILT_NO_ARTIFACT"
            if readiness.learned_training_authorized
            else "NOT_BUILT_TRAINING_READINESS_GATE"
        )
    )
    return {
        "schema_version": SEMANTIC_INDEX_MANIFEST_SCHEMA_VERSION,
        "status": status,
        "supervision_manifest_sha256": supervision_manifest_sha256,
        "compiler_bundle_id": bundle.compiler_bundle_id,
        "canonical_registry_sha256": bundle.canonical_registry_sha256,
        "entity_count": len(bundle.canonical_registry),
        "encoder_artifact_sha256": encoder_artifact_sha256,
        "index_artifact_sha256": index_artifact_sha256,
        "representation_dimensions": [64, 128, 256],
        "source_split_roles": dict(_SOURCE_SPLIT_ROLES),
        "benchmark_partition_controls_training": False,
        "canonical_registry_authoritative": True,
        "ann_codes_authoritative": False,
        "training_readiness": _training_readiness_document(readiness),
    }


def write_semantic_index_manifest(
    bundle: SemanticSupervisionBundle,
    path: Path,
    *,
    supervision_manifest_sha256: str,
    encoder_artifact_sha256: str | None = None,
    index_artifact_sha256: str | None = None,
) -> str:
    """Serialize the ANN index identity contract and return its SHA-256."""

    document = semantic_index_manifest_document(
        bundle,
        supervision_manifest_sha256=supervision_manifest_sha256,
        encoder_artifact_sha256=encoder_artifact_sha256,
        index_artifact_sha256=index_artifact_sha256,
    )
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return _sha256_file(path)


@dataclass(frozen=True)
class StaticSubwordEncoder:
    """Parameter-free signed feature hashing over word and character n-grams."""

    dimension: int = 256
    minimum_n: int = 2
    maximum_n: int = 5

    def __post_init__(self) -> None:
        if not _power_of_two(self.dimension):
            raise ValueError("encoder dimension must be a positive power of two")
        if self.minimum_n < 1 or self.maximum_n < self.minimum_n:
            raise ValueError("invalid character n-gram bounds")

    def encode(self, text: str) -> tuple[float, ...]:
        normalized = _normalized(text)
        features = [f"w:{token}" for token in normalized.split()]
        padded = f"^{normalized}$"
        for width in range(self.minimum_n, self.maximum_n + 1):
            features.extend(
                f"c:{padded[start : start + width]}"
                for start in range(max(0, len(padded) - width + 1))
            )
        if not features:
            return (0.0,) * self.dimension
        values = [0.0] * self.dimension
        for feature in features:
            digest = hashlib.blake2b(feature.encode(), digest_size=16).digest()
            index = int.from_bytes(digest[:8], "little") & (self.dimension - 1)
            sign = 1.0 if digest[8] & 1 else -1.0
            values[index] += sign
        norm = math.sqrt(sum(value * value for value in values))
        if norm == 0.0:
            return tuple(values)
        return tuple(value / norm for value in values)


def dot(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("vector dimensions differ")
    return sum(a * b for a, b in zip(left, right, strict=True))


def squared_distance(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("vector dimensions differ")
    return sum((a - b) ** 2 for a, b in zip(left, right, strict=True))


class BinaryVariant(StrEnum):
    RAW = "raw_sign_bq"
    GLOBAL_FWHT = "global_fwht_sign"
    PREFIX_BLOCK_FWHT = "prefix_block_fwht_sign"


def _fwht(values: Sequence[float]) -> tuple[float, ...]:
    if not _power_of_two(len(values)):
        raise ValueError("FWHT dimension must be a power of two")
    result = list(values)
    width = 1
    while width < len(result):
        for start in range(0, len(result), width * 2):
            for offset in range(width):
                left = result[start + offset]
                right = result[start + offset + width]
                result[start + offset] = left + right
                result[start + offset + width] = left - right
        width *= 2
    scale = 1.0 / math.sqrt(len(result))
    return tuple(value * scale for value in result)


def _random_signs(dimension: int, seed: int) -> tuple[float, ...]:
    return tuple(
        1.0 if hashlib.sha256(f"{seed}:{index}".encode()).digest()[0] & 1 else -1.0
        for index in range(dimension)
    )


def _pack_signs(values: Sequence[float], bits: int) -> bytes:
    if bits < 1 or bits > len(values) or bits % 8:
        raise ValueError("binary prefix must be byte-aligned and inside the vector")
    output = bytearray(bits // 8)
    for index, value in enumerate(values[:bits]):
        if value >= 0.0:
            output[index // 8] |= 1 << (index % 8)
    return bytes(output)


def binary_code(
    vector: Sequence[float],
    *,
    variant: BinaryVariant | str = BinaryVariant.RAW,
    bits: int | None = None,
    seed: int = 0xA37E12,
    block_size: int = 64,
) -> bytes:
    """Compress a vector while keeping global and prefix-compatible FWHT distinct."""

    selected = BinaryVariant(variant)
    bit_count = len(vector) if bits is None else bits
    if selected is BinaryVariant.RAW:
        transformed = tuple(vector)
    elif selected is BinaryVariant.GLOBAL_FWHT:
        signs = _random_signs(len(vector), seed)
        transformed = _fwht(tuple(a * b for a, b in zip(vector, signs, strict=True)))
    else:
        if not _power_of_two(block_size) or len(vector) % block_size:
            raise ValueError("prefix FWHT requires power-of-two blocks dividing the vector")
        signs = _random_signs(len(vector), seed)
        signed = tuple(a * b for a, b in zip(vector, signs, strict=True))
        blocks = [
            _fwht(signed[start : start + block_size]) for start in range(0, len(signed), block_size)
        ]
        transformed = tuple(value for block in blocks for value in block)
    return _pack_signs(transformed, bit_count)


def hamming_distance(left: bytes, right: bytes) -> int:
    if len(left) != len(right):
        raise ValueError("binary code lengths differ")
    return sum((a ^ b).bit_count() for a, b in zip(left, right, strict=True))


@dataclass(frozen=True)
class Int8Vector:
    values: bytes
    scale: float

    @classmethod
    def encode(cls, vector: Sequence[float]) -> Int8Vector:
        maximum = max((abs(value) for value in vector), default=0.0)
        scale = maximum / 127.0 if maximum else 1.0
        packed = bytes(max(0, min(255, round(value / scale) + 128)) for value in vector)
        return cls(packed, scale)

    def approximate_dot(self, query: Sequence[float]) -> float:
        if len(query) != len(self.values):
            raise ValueError("query and int8 vector dimensions differ")
        return sum(
            value * ((quantized - 128) * self.scale)
            for value, quantized in zip(query, self.values, strict=True)
        )


@dataclass(frozen=True)
class ProductQuantizer:
    dimension: int
    code_bytes: int
    centroid_count: int
    centroids: tuple[tuple[tuple[float, ...], ...], ...]

    @property
    def subvector_dimension(self) -> int:
        return self.dimension // self.code_bytes

    @property
    def codebook_bytes_float32(self) -> int:
        return self.code_bytes * self.centroid_count * self.subvector_dimension * 4

    def encode(self, vector: Sequence[float]) -> bytes:
        if len(vector) != self.dimension:
            raise ValueError("PQ vector dimension differs")
        width = self.subvector_dimension
        code = bytearray()
        for index, codebook in enumerate(self.centroids):
            part = vector[index * width : (index + 1) * width]
            best = min(
                range(len(codebook)),
                key=lambda candidate: (squared_distance(part, codebook[candidate]), candidate),
            )
            code.append(best)
        return bytes(code)

    def adc_distance(self, query: Sequence[float], code: bytes) -> float:
        if len(query) != self.dimension or len(code) != self.code_bytes:
            raise ValueError("PQ query or code dimension differs")
        width = self.subvector_dimension
        return sum(
            squared_distance(
                query[index * width : (index + 1) * width], self.centroids[index][code[index]]
            )
            for index in range(self.code_bytes)
        )


def _mean(vectors: Sequence[Sequence[float]], dimension: int) -> tuple[float, ...]:
    if not vectors:
        raise ValueError("cannot average no vectors")
    return tuple(
        sum(vector[index] for vector in vectors) / len(vectors) for index in range(dimension)
    )


def fit_product_quantizer(
    vectors: Sequence[Sequence[float]],
    *,
    code_bytes: int,
    centroid_count: int = 16,
    iterations: int = 3,
) -> ProductQuantizer:
    """Fit deterministic development-only subvector codebooks.

    Each subquantizer index occupies one byte.  ``centroid_count`` may be below
    256 for a small-data screen, but never exceeds the byte-addressable limit.
    """

    if not vectors:
        raise ValueError("PQ fitting requires development vectors")
    dimension = len(vectors[0])
    if any(len(vector) != dimension for vector in vectors):
        raise ValueError("PQ vectors must share a dimension")
    if code_bytes not in {8, 16} or dimension % code_bytes:
        raise ValueError("PQ requires an 8- or 16-byte code dividing the dimension")
    if centroid_count < 2 or centroid_count > 256:
        raise ValueError("PQ centroid count must be in [2, 256]")
    if iterations < 1:
        raise ValueError("PQ iterations must be positive")
    k = min(centroid_count, len(vectors))
    width = dimension // code_bytes
    all_centroids: list[tuple[tuple[float, ...], ...]] = []
    for part_index in range(code_bytes):
        parts = [tuple(vector[part_index * width : (part_index + 1) * width]) for vector in vectors]
        seeds = [parts[(index * len(parts)) // k] for index in range(k)]
        centroids = list(seeds)
        for _ in range(iterations):
            groups: list[list[tuple[float, ...]]] = [[] for _ in range(k)]
            for part in parts:
                best = min(
                    range(k),
                    key=lambda candidate: (
                        squared_distance(part, centroids[candidate]),
                        candidate,
                    ),
                )
                groups[best].append(part)
            centroids = [
                _mean(group, width) if group else centroids[index]
                for index, group in enumerate(groups)
            ]
        all_centroids.append(tuple(centroids))
    return ProductQuantizer(dimension, code_bytes, k, tuple(all_centroids))


@dataclass(frozen=True)
class BinaryIVFIndex:
    """Hamming-native IVF whose coarse buckets are observed code prefixes."""

    nlist: int
    code_bytes: int
    identifiers: tuple[str, ...]
    codes: tuple[bytes, ...]
    lists: Mapping[int, tuple[int, ...]]

    @property
    def coarse_bits(self) -> int:
        return self.nlist.bit_length() - 1


def _prefix_bucket(code: bytes, bits: int) -> int:
    value = int.from_bytes(code, "little")
    return value & ((1 << bits) - 1)


def build_binary_ivf(
    identifiers: Sequence[str], codes: Sequence[bytes], *, nlist: int
) -> BinaryIVFIndex:
    if len(identifiers) != len(codes) or not identifiers:
        raise ValueError("IVF identifiers/codes must be non-empty and aligned")
    if nlist not in {256, 512, 1024}:
        raise ValueError("Mission 7 IVF sweep is restricted to 256/512/1024 lists")
    code_bytes = len(codes[0])
    if code_bytes < 2 or any(len(code) != code_bytes for code in codes):
        raise ValueError("IVF codes must share a usable width")
    buckets: dict[int, list[int]] = {}
    bits = nlist.bit_length() - 1
    for index, code in enumerate(codes):
        buckets.setdefault(_prefix_bucket(code, bits), []).append(index)
    return BinaryIVFIndex(
        nlist=nlist,
        code_bytes=code_bytes,
        identifiers=tuple(identifiers),
        codes=tuple(codes),
        lists={key: tuple(values) for key, values in sorted(buckets.items())},
    )


@dataclass(frozen=True)
class ProgressiveSearchResult:
    identifiers: tuple[str, ...]
    probed_candidates: int
    coarse_bytes_read: int
    extension_bytes_read: int
    total_bytes_read: int
    pages_4k: int
    xor_popcount_operations: int


def progressive_ivf_search(
    index: BinaryIVFIndex,
    query_code: bytes,
    *,
    nprobe: int = 8,
    top_k: int = 16,
    retain_128: int = 64,
    retain_256: int = 32,
) -> ProgressiveSearchResult:
    """Read 64 coarse bits before optional 128/256-bit refinement."""

    if len(query_code) != index.code_bytes or index.code_bytes < 32:
        raise ValueError("progressive search requires aligned 256-bit codes")
    if min(nprobe, top_k, retain_128, retain_256) < 1:
        raise ValueError("search bounds must be positive")
    query_bucket = _prefix_bucket(query_code, index.coarse_bits)
    bucket_order = sorted(
        range(index.nlist),
        key=lambda bucket: ((bucket ^ query_bucket).bit_count(), bucket),
    )[:nprobe]
    candidates = [item for bucket in bucket_order for item in index.lists.get(bucket, ())]

    def ranked(rows: Sequence[int], width: int) -> list[int]:
        return sorted(
            rows,
            key=lambda item: (
                hamming_distance(query_code[:width], index.codes[item][:width]),
                index.identifiers[item],
            ),
        )

    stage64 = ranked(candidates, 8)
    stage128 = ranked(stage64[:retain_128], 16)
    stage256 = ranked(stage128[:retain_256], 32)
    coarse_bytes = len(candidates) * 8
    extension_bytes = min(len(stage64), retain_128) * 8 + min(len(stage128), retain_256) * 16
    total = coarse_bytes + extension_bytes
    operations = len(candidates) + min(len(stage64), retain_128) + min(len(stage128), retain_256)
    return ProgressiveSearchResult(
        identifiers=tuple(index.identifiers[item] for item in stage256[:top_k]),
        probed_candidates=len(candidates),
        coarse_bytes_read=coarse_bytes,
        extension_bytes_read=extension_bytes,
        total_bytes_read=total,
        pages_4k=(total + 4095) // 4096,
        xor_popcount_operations=operations,
    )
