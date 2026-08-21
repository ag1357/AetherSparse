"""Generic provenance and deployable knowledge-pack contracts."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable, Iterator
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SourceType(StrEnum):
    ENCYCLOPEDIA = "encyclopedia"
    SOFTWARE_DOCUMENTATION = "software_documentation"
    SOURCE_CODE = "source_code"
    MANUAL_SPECIFICATION = "manual/specification"


class PackRegionKind(StrEnum):
    ADDRESSING_INDEX = "addressing_index"
    EVIDENCE_CONTENT = "evidence/content"
    OPTIONAL_CACHE = "optional_cache"


class SourceProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    license_id: str
    origin: str
    revision: str
    content_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class PackRegion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: PackRegionKind
    relative_path: str
    logical_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def relative_and_safe(self) -> PackRegion:
        path = Path(self.relative_path)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("pack region path must remain relative")
        return self


class KnowledgePackManifest(BaseModel):
    """Immutable identity for a runtime pack; build databases stay off-device."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: str = "aethercore.knowledge-pack.v1"
    pack_id: str
    pack_version: str
    source_namespace: str
    source_type: SourceType
    source_version: str
    source_license_provenance: tuple[SourceProvenance, ...] = Field(min_length=1)
    canonical_object_id_scheme: str
    canonical_object_count: int = Field(ge=0)
    addressing_index: PackRegion
    content_evidence_store: PackRegion
    optional_cache: PackRegion | None = None
    compiler_identity: str
    update_lineage: tuple[str, ...] = ()
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def regions_have_required_kinds(self) -> KnowledgePackManifest:
        if self.addressing_index.kind is not PackRegionKind.ADDRESSING_INDEX:
            raise ValueError("addressing_index region has wrong kind")
        if self.content_evidence_store.kind is not PackRegionKind.EVIDENCE_CONTENT:
            raise ValueError("content_evidence_store region has wrong kind")
        if (
            self.optional_cache is not None
            and self.optional_cache.kind is not PackRegionKind.OPTIONAL_CACHE
        ):
            raise ValueError("optional_cache region has wrong kind")
        canonical = json.dumps(
            self.model_dump(mode="json", exclude={"manifest_sha256"}),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        if hashlib.sha256(canonical).hexdigest() != self.manifest_sha256:
            raise ValueError("manifest_sha256 does not bind the canonical manifest")
        return self

    def identity(self) -> str:
        return f"{self.pack_id}@{self.pack_version}:{self.manifest_sha256}"

    @staticmethod
    def digest_fields(fields: dict[str, object]) -> str:
        """Compute the value to place in ``manifest_sha256`` before validation."""

        canonical = json.dumps(
            {key: value for key, value in fields.items() if key != "manifest_sha256"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(canonical).hexdigest()


class CanonicalSourceObject(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    canonical_object_id: str
    source_namespace: str
    source_type: SourceType
    source_version: str
    title: str
    body: str
    provenance: SourceProvenance


class KnowledgeSourceAdapter(Protocol):
    source_type: SourceType

    def iter_objects(self) -> Iterable[CanonicalSourceObject]: ...


class JsonLinesSourceAdapter:
    """Streaming reference adapter shared by all required source classes."""

    def __init__(self, path: Path, source_type: SourceType) -> None:
        self.path = path
        self.source_type = source_type

    def iter_objects(self) -> Iterator[CanonicalSourceObject]:
        with self.path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                record = CanonicalSourceObject.model_validate_json(line)
                if record.source_type is not self.source_type:
                    raise ValueError(f"source type mismatch on line {line_number}")
                yield record


class DeploymentPackRegistry:
    """Atomically activates mounted immutable packs; it never stores build intermediates."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "active-packs.json"

    @staticmethod
    def _digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1 << 20):
                digest.update(chunk)
        return digest.hexdigest()

    def _active(self) -> dict[str, dict[str, str]]:
        if not self.index_path.exists():
            return {}
        value = json.loads(self.index_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("pack registry is corrupt")
        return {
            str(key): {str(field): str(item) for field, item in entry.items()}
            for key, entry in value.items()
            if isinstance(entry, dict)
        }

    def _commit(self, active: dict[str, dict[str, str]]) -> None:
        temporary = self.index_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(active, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, self.index_path)

    def validate(self, manifest: KnowledgePackManifest, pack_root: Path) -> None:
        resolved_root = pack_root.resolve()
        for region in (
            manifest.addressing_index,
            manifest.content_evidence_store,
            manifest.optional_cache,
        ):
            if region is None:
                continue
            path = (resolved_root / region.relative_path).resolve()
            try:
                path.relative_to(resolved_root)
            except ValueError as error:
                raise ValueError("pack region escapes pack root") from error
            if not path.is_file() or path.stat().st_size != region.logical_bytes:
                raise ValueError(f"pack region size mismatch: {region.relative_path}")
            if self._digest(path) != region.sha256:
                raise ValueError(f"pack region digest mismatch: {region.relative_path}")

    def add(self, manifest: KnowledgePackManifest, pack_root: Path) -> None:
        self.validate(manifest, pack_root)
        active = self._active()
        if manifest.pack_id in active:
            raise ValueError("pack is already active; use update")
        active[manifest.pack_id] = {
            "identity": manifest.identity(),
            "root": str(pack_root.resolve()),
        }
        self._commit(active)

    def update(self, manifest: KnowledgePackManifest, pack_root: Path) -> None:
        self.validate(manifest, pack_root)
        active = self._active()
        previous = active.get(manifest.pack_id)
        if previous is None:
            raise ValueError("cannot update an inactive pack")
        previous_identity = previous["identity"]
        if previous_identity not in manifest.update_lineage:
            raise ValueError("update lineage does not name the active immutable identity")
        active[manifest.pack_id] = {
            "identity": manifest.identity(),
            "root": str(pack_root.resolve()),
        }
        self._commit(active)

    def remove(self, pack_id: str) -> None:
        active = self._active()
        if pack_id not in active:
            raise ValueError("pack is not active")
        del active[pack_id]
        self._commit(active)

    def active(self) -> dict[str, dict[str, str]]:
        return self._active()
