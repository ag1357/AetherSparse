"""Verified bundle identity, canonical registry, and exact-index adapters."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from aethersparse.addressing.compiler_v2 import (
    AddressArtifactError,
    iter_jsonl_gzip,
    verify_address_export,
)
from aethersparse.addressing.contracts_v2 import (
    ADDRESS_MANIFEST_SCHEMA_VERSION,
    canonical_entity_id,
    normalize_surface,
    validate_record_contract,
)
from aethersparse.addressing.exact import (
    AddressChannel,
    AddressEvidence,
    AddressIndexArtifact,
    compile_exact_address_index,
)

ConsumerPhase = Literal["fit", "selection", "holdout_qualification", "descriptive"]
StatisticsView = Literal["fit", "fit+calibration", "all"]

_VIEW_POLICY: dict[str, tuple[tuple[str, ...], str]] = {
    "fit": (("fit",), "fit_and_selection"),
    "fit+calibration": (
        ("fit", "calibration"),
        "holdout_qualification_only",
    ),
    "all": (("fit", "calibration", "holdout"), "descriptive_only"),
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class AddressBundleStreamIdentity:
    """One named stream identity bound into a verified address bundle."""

    name: str
    file: str
    gzip_sha256: str
    jsonl_sha256: str
    rows: int

    def __post_init__(self) -> None:
        if not self.name or not self.file or self.rows < 0:
            raise ValueError("address stream identity is malformed")
        if not _SHA256.fullmatch(self.gzip_sha256) or not _SHA256.fullmatch(self.jsonl_sha256):
            raise ValueError("address stream identity has an invalid hash")


@dataclass(frozen=True)
class AddressBundleIdentity:
    """Self-addressed identity shared by exact, ANN, and fusion consumers."""

    schema_version: str
    manifest_sha256: str
    source_pack_sha256: str
    corpus_tier: str
    streams: tuple[AddressBundleStreamIdentity, ...]

    def __post_init__(self) -> None:
        if self.schema_version != ADDRESS_MANIFEST_SCHEMA_VERSION:
            raise ValueError("address bundle schema is unsupported")
        if not _SHA256.fullmatch(self.manifest_sha256) or not _SHA256.fullmatch(
            self.source_pack_sha256
        ):
            raise ValueError("address bundle has an invalid content identity")
        names = tuple(stream.name for stream in self.streams)
        if names != tuple(sorted(names)) or len(names) != len(set(names)):
            raise ValueError("address bundle stream identities must be unique and sorted")


@dataclass(frozen=True)
class CanonicalRegistryEntry:
    record_id: str
    entity_id: str
    canonical_title: str
    normalized_title: str
    source_document_id: str


@dataclass(frozen=True)
class CanonicalAddressRegistry:
    """Authoritative entity-ID/title pairs copied from the entity stream."""

    bundle: AddressBundleIdentity
    entries: tuple[CanonicalRegistryEntry, ...]

    def __post_init__(self) -> None:
        entity_ids: set[str] = set()
        normalized_titles: set[str] = set()
        source_documents: set[str] = set()
        for entry in self.entries:
            if entry.entity_id != canonical_entity_id(entry.canonical_title):
                raise ValueError("canonical registry ID/title authority is inconsistent")
            if entry.normalized_title != normalize_surface(entry.canonical_title):
                raise ValueError("canonical registry normalized title is inconsistent")
            if (
                entry.entity_id in entity_ids
                or entry.normalized_title in normalized_titles
                or entry.source_document_id in source_documents
            ):
                raise ValueError("canonical registry authority is not one-to-one")
            entity_ids.add(entry.entity_id)
            normalized_titles.add(entry.normalized_title)
            source_documents.add(entry.source_document_id)

    def as_mapping(self) -> dict[str, str]:
        return {entry.entity_id: entry.canonical_title for entry in self.entries}

    def require_pair(self, entity_id: str, canonical_title: str) -> None:
        expected = self.as_mapping().get(entity_id)
        if expected is None:
            raise AddressArtifactError(f"canonical entity is absent from registry: {entity_id}")
        if expected != canonical_title:
            raise AddressArtifactError(
                f"canonical title mismatch for {entity_id}: {canonical_title!r} != {expected!r}"
            )


def verify_address_bundle(
    directory: Path, *, expected: AddressBundleIdentity | None = None
) -> AddressBundleIdentity:
    """Verify every stream plus the manifest's exact self-addressed identity."""

    manifest = verify_address_export(directory)
    manifest_path = directory / "manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != ADDRESS_MANIFEST_SCHEMA_VERSION:
        raise AddressArtifactError("address bundle manifest schema is unsupported")
    views = raw.get("views")
    observed_views = views.get("surface_statistics") if isinstance(views, dict) else None
    if not isinstance(observed_views, dict) or set(observed_views) != set(_VIEW_POLICY):
        raise AddressArtifactError("surface-statistics view manifest is incomplete")
    for name, (splits, usage) in _VIEW_POLICY.items():
        value = observed_views[name]
        if not isinstance(value, dict) or value.get("included_source_splits") != list(splits):
            raise AddressArtifactError(f"surface-statistics split policy mismatch: {name}")
        if value.get("usage") != usage:
            raise AddressArtifactError(f"surface-statistics usage policy mismatch: {name}")
        rows = value.get("rows")
        if isinstance(rows, bool) or not isinstance(rows, int) or rows < 0:
            raise AddressArtifactError(f"surface-statistics row count is invalid: {name}")
    identity = AddressBundleIdentity(
        schema_version=manifest.schema_version,
        manifest_sha256=_sha256(manifest_path),
        source_pack_sha256=manifest.source_pack_sha256,
        corpus_tier=manifest.corpus_tier,
        streams=tuple(
            AddressBundleStreamIdentity(
                name=name,
                file=stream.file,
                gzip_sha256=stream.gzip_sha256,
                jsonl_sha256=stream.jsonl_sha256,
                rows=stream.rows,
            )
            for name, stream in sorted(manifest.streams.items())
        ),
    )
    if expected is not None and identity != expected:
        raise AddressArtifactError("address bundle identity does not match the required input")
    return identity


def _verified_rows(directory: Path, stream: str) -> Iterator[dict[str, object]]:
    for row in iter_jsonl_gzip(directory / f"{stream}.jsonl.gz"):
        try:
            validate_record_contract(row)
        except ValueError as error:
            raise AddressArtifactError(f"invalid {stream} record: {error}") from error
        yield row


def load_canonical_registry(
    directory: Path, *, expected_bundle: AddressBundleIdentity | None = None
) -> CanonicalAddressRegistry:
    """Load and strictly verify the sole canonical ID/title authority."""

    bundle = verify_address_bundle(directory, expected=expected_bundle)
    entries: list[CanonicalRegistryEntry] = []
    entity_ids: set[str] = set()
    normalized_titles: set[str] = set()
    source_documents: set[str] = set()
    for row in _verified_rows(directory, "entities"):
        entity_id = str(row["entity_id"])
        title = str(row["title"])
        normalized_title = str(row["normalized_title"])
        source_document_id = str(row["document_id"])
        if entity_id != canonical_entity_id(title):
            raise AddressArtifactError(f"canonical entity ID/title mismatch: {entity_id}")
        if normalized_title != normalize_surface(title):
            raise AddressArtifactError(f"canonical normalized title mismatch: {entity_id}")
        if entity_id in entity_ids or normalized_title in normalized_titles:
            raise AddressArtifactError("canonical registry contains a duplicate ID or title")
        if source_document_id in source_documents:
            raise AddressArtifactError("canonical registry contains a duplicate source document")
        entity_ids.add(entity_id)
        normalized_titles.add(normalized_title)
        source_documents.add(source_document_id)
        entries.append(
            CanonicalRegistryEntry(
                record_id=str(row["record_id"]),
                entity_id=entity_id,
                canonical_title=title,
                normalized_title=normalized_title,
                source_document_id=source_document_id,
            )
        )
    return CanonicalAddressRegistry(bundle=bundle, entries=tuple(entries))


def validate_statistics_view(view: StatisticsView, *, consumer_phase: ConsumerPhase) -> None:
    """Reject priors that contain labels unavailable to the requested phase."""

    if consumer_phase in {"fit", "selection"} and view != "fit":
        raise AddressArtifactError(f"{consumer_phase} consumers may use only the fit prior view")
    if consumer_phase == "holdout_qualification" and view == "all":
        raise AddressArtifactError("holdout qualification may not consume the all-data prior view")


def iter_surface_statistics_view(
    directory: Path,
    *,
    view: StatisticsView,
    consumer_phase: ConsumerPhase,
    expected_bundle: AddressBundleIdentity | None = None,
) -> Iterator[dict[str, object]]:
    """Yield exactly one verified, phase-lawful statistics view."""

    validate_statistics_view(view, consumer_phase=consumer_phase)
    bundle = verify_address_bundle(directory, expected=expected_bundle)
    del bundle
    expected_splits, usage = _VIEW_POLICY[view]
    for row in _verified_rows(directory, "surface_statistics"):
        if row["statistics_view"] != view:
            continue
        if row["included_source_splits"] != list(expected_splits) or row["usage"] != usage:
            raise AddressArtifactError(f"surface-statistics row policy mismatch: {view}")
        yield row


def _unresolved_key(row: Mapping[str, object]) -> str:
    path = row.get("redirect_path")
    if not isinstance(path, list):
        raise AddressArtifactError("unresolved address record lacks redirect path")
    return f"{row.get('resolution_state')}:{'|'.join(str(item) for item in path)}"


def _channel_provenance(channel: AddressChannel, record_id: str) -> str:
    """Keep the exact generator channel attached to an immutable source record."""

    return f"{channel.value}:{record_id}"


def _require_registry_pair(
    registry: CanonicalAddressRegistry, row: Mapping[str, object]
) -> tuple[str | None, str | None]:
    entity = row.get("canonical_entity_id")
    title = row.get("canonical_title")
    if entity is None or title is None:
        if entity is not None or title is not None:
            raise AddressArtifactError("canonical entity ID/title nullability differs")
        return None, None
    entity_id = str(entity)
    canonical_title = str(title)
    registry.require_pair(entity_id, canonical_title)
    return entity_id, canonical_title


def iter_exact_address_evidence(
    directory: Path,
    *,
    included_source_splits: Sequence[str] = ("fit",),
    consumer_phase: ConsumerPhase = "fit",
    expected_bundle: AddressBundleIdentity | None = None,
) -> Iterator[AddressEvidence]:
    """Adapt verified v2 streams to exact evidence without dropping provenance."""

    splits = tuple(sorted(set(included_source_splits)))
    if not splits or not set(splits).issubset({"fit", "calibration", "holdout"}):
        raise AddressArtifactError("exact adapter source splits are invalid")
    if consumer_phase in {"fit", "selection"} and splits != ("fit",):
        raise AddressArtifactError(f"{consumer_phase} exact evidence may use only fit occurrences")
    if consumer_phase == "holdout_qualification" and "holdout" in splits:
        raise AddressArtifactError("holdout exact evidence may not consume holdout occurrences")
    registry = load_canonical_registry(directory, expected_bundle=expected_bundle)

    for entry in registry.entries:
        yield AddressEvidence(
            surface=entry.canonical_title,
            entity_id=entry.entity_id,
            canonical_title=entry.canonical_title,
            support_count=1,
            source_document_ids=(entry.source_document_id,),
            channel=AddressChannel.TITLE,
            provenance_ids=(_channel_provenance(AddressChannel.TITLE, entry.record_id),),
        )
    for row in _verified_rows(directory, "aliases"):
        entity_id, title = _require_registry_pair(registry, row)
        if str(row["kind"]) == "title":
            continue
        yield AddressEvidence(
            surface=str(row["surface"]),
            entity_id=entity_id,
            canonical_title=title,
            support_count=1,
            source_document_ids=(str(row["source_document_id"]),),
            channel=AddressChannel.ALIAS,
            provenance_ids=(_channel_provenance(AddressChannel.ALIAS, str(row["record_id"])),),
            unresolved_key=None if entity_id is not None else _unresolved_key(row),
        )
    for row in _verified_rows(directory, "redirects"):
        entity_id, title = _require_registry_pair(registry, row)
        yield AddressEvidence(
            surface=str(row["source_title"]),
            entity_id=entity_id,
            canonical_title=title,
            support_count=1,
            source_document_ids=(str(row["source_document_id"]),),
            channel=AddressChannel.REDIRECT,
            provenance_ids=(_channel_provenance(AddressChannel.REDIRECT, str(row["record_id"])),),
            unresolved_key=None if entity_id is not None else _unresolved_key(row),
        )
    for row in _verified_rows(directory, "occurrences"):
        if str(row["source_split"]) not in splits:
            continue
        entity_id, title = _require_registry_pair(registry, row)
        yield AddressEvidence(
            surface=str(row["mention"]),
            entity_id=entity_id,
            canonical_title=title,
            support_count=1,
            source_document_ids=(str(row["source_document_id"]),),
            channel=AddressChannel.ANCHOR,
            provenance_ids=(_channel_provenance(AddressChannel.ANCHOR, str(row["record_id"])),),
            unresolved_key=None if entity_id is not None else _unresolved_key(row),
        )


def compile_verified_exact_address_index(
    directory: Path,
    output_path: Path,
    *,
    included_source_splits: Sequence[str] = ("fit",),
    consumer_phase: ConsumerPhase = "fit",
    expected_bundle: AddressBundleIdentity | None = None,
) -> AddressIndexArtifact:
    """Compile an exact index whose source identity is the verified v2 manifest."""

    bundle = verify_address_bundle(directory, expected=expected_bundle)
    splits = tuple(sorted(set(included_source_splits)))
    evidence = iter_exact_address_evidence(
        directory,
        included_source_splits=splits,
        consumer_phase=consumer_phase,
        expected_bundle=bundle,
    )
    return compile_exact_address_index(
        evidence,
        output_path,
        source_artifact_sha256=bundle.manifest_sha256,
        source_partitions=splits,
    )
