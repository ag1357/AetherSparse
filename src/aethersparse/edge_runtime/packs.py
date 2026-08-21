"""Immutable multi-pack deployment and provenance contracts.

Factory intermediates are deliberately outside this format. A deployed pack is
an immutable manifest plus page-addressable runtime regions. The registry is a
small atomic activation record, allowing media to be added, removed, or updated
without mutating an active pack.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Self, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

PACK_SCHEMA = "aethersparse.knowledge-pack.v13"
REGISTRY_SCHEMA = "aethersparse.active-pack-registry.v13"


class PackContractError(ValueError):
    """A pack or active registry violates the immutable deployment contract."""


class SourceType(StrEnum):
    ENCYCLOPEDIA = "encyclopedia"
    SOFTWARE_DOCUMENTATION = "software_documentation"
    SOURCE_CODE = "source_code"
    MANUAL_SPECIFICATION = "manual/specification"


class RegionRole(StrEnum):
    ADDRESSING_INDEX = "addressing_index"
    CANONICAL_OBJECT_TABLE = "canonical_object_table"
    EVIDENCE_STORE = "evidence_store"
    CONTENT_STORE = "content_store"
    POLICY_MODEL = "policy_model"
    OPTIONAL_CACHE_SEED = "optional_cache_seed"


class PackRegion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: RegionRole
    path: str
    offset: int = Field(ge=0)
    length: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_bytes: int = Field(default=4096, ge=512, le=65536)

    @model_validator(mode="after")
    def safe_page_aligned_region(self) -> Self:
        relative = PurePosixPath(self.path)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError("pack region path must be a safe relative POSIX path")
        if self.page_bytes & (self.page_bytes - 1):
            raise ValueError("page_bytes must be a power of two")
        if self.offset % self.page_bytes:
            raise ValueError("region offset must be page aligned")
        return self


class KnowledgePackManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = PACK_SCHEMA
    pack_id: str = Field(pattern=r"^acpack:[0-9a-f]{64}$")
    source_namespace: str = Field(min_length=1)
    source_type: SourceType
    source_version: str = Field(min_length=1)
    source_license_provenance: tuple[str, ...] = Field(min_length=1)
    canonical_object_id_scheme: str = Field(min_length=1)
    compiler_identity: str = Field(min_length=1)
    update_lineage: tuple[str, ...] = ()
    regions: tuple[PackRegion, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def identity_and_regions_are_valid(self) -> Self:
        if self.pack_id != _manifest_identity(self.model_dump(mode="json")):
            raise ValueError("pack_id does not match canonical immutable manifest")
        occupied: dict[str, list[tuple[int, int]]] = {}
        roles: set[RegionRole] = set()
        for region in self.regions:
            if region.role in roles:
                raise ValueError(f"duplicate region role: {region.role}")
            roles.add(region.role)
            ranges = occupied.setdefault(region.path, [])
            current = (region.offset, region.offset + region.length)
            if any(current[0] < end and start < current[1] for start, end in ranges):
                raise ValueError(f"overlapping regions in {region.path}")
            ranges.append(current)
        required = {
            RegionRole.ADDRESSING_INDEX,
            RegionRole.CANONICAL_OBJECT_TABLE,
            RegionRole.EVIDENCE_STORE,
        }
        if not required.issubset(roles):
            raise ValueError("pack lacks an addressing, canonical-object, or evidence region")
        return self

    @classmethod
    def create(
        cls,
        *,
        source_namespace: str,
        source_type: SourceType,
        source_version: str,
        source_license_provenance: tuple[str, ...],
        canonical_object_id_scheme: str,
        compiler_identity: str,
        regions: tuple[PackRegion, ...],
        update_lineage: tuple[str, ...] = (),
    ) -> KnowledgePackManifest:
        values: dict[str, Any] = {
            "schema_version": PACK_SCHEMA,
            "pack_id": "",
            "source_namespace": source_namespace,
            "source_type": source_type,
            "source_version": source_version,
            "source_license_provenance": source_license_provenance,
            "canonical_object_id_scheme": canonical_object_id_scheme,
            "compiler_identity": compiler_identity,
            "update_lineage": update_lineage,
            "regions": regions,
        }
        serialized = {
            key: _json_value(value)
            for key, value in values.items()
        }
        values["pack_id"] = _manifest_identity(serialized)
        return cls.model_validate(values)


def _json_value(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, StrEnum):
        return str(value)
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


def _manifest_identity(values: dict[str, Any]) -> str:
    identity = dict(values)
    identity.pop("pack_id", None)
    encoded = json.dumps(
        identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return f"acpack:{hashlib.sha256(encoded).hexdigest()}"


def validate_pack_directory(pack_directory: Path) -> KnowledgePackManifest:
    manifest_path = pack_directory / "manifest.json"
    try:
        manifest = KnowledgePackManifest.model_validate_json(manifest_path.read_bytes())
    except (OSError, ValueError) as error:
        raise PackContractError(f"invalid pack manifest: {error}") from error
    for region in manifest.regions:
        payload_path = pack_directory.joinpath(*PurePosixPath(region.path).parts)
        try:
            with payload_path.open("rb") as payload:
                payload.seek(region.offset)
                content = payload.read(region.length)
        except OSError as error:
            raise PackContractError(f"cannot read region {region.role}: {error}") from error
        if len(content) != region.length:
            raise PackContractError(f"region {region.role} is truncated")
        if hashlib.sha256(content).hexdigest() != region.sha256:
            raise PackContractError(f"region {region.role} digest mismatch")
    return manifest


class PackRegistry:
    """Atomic active-set registry for immutable on-media knowledge packs."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.state_path = root / "active-packs.json"

    def read(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"schema_version": REGISTRY_SCHEMA, "generation": 0, "packs": []}
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise PackContractError(f"invalid active registry: {error}") from error
        if value.get("schema_version") != REGISTRY_SCHEMA or not isinstance(
            value.get("generation"), int
        ) or not isinstance(value.get("packs"), list):
            raise PackContractError("active registry schema mismatch")
        return cast(dict[str, Any], value)

    def activate(self, pack_directory: Path) -> dict[str, Any]:
        manifest = validate_pack_directory(pack_directory)
        state = self.read()
        packs = [
            item
            for item in state["packs"]
            if item["source_namespace"] != manifest.source_namespace
        ]
        packs.append(
            {
                "pack_id": manifest.pack_id,
                "path": str(pack_directory.resolve()),
                "source_namespace": manifest.source_namespace,
                "source_version": manifest.source_version,
            }
        )
        packs.sort(key=lambda item: (item["source_namespace"], item["pack_id"]))
        return self._replace({**state, "generation": state["generation"] + 1, "packs": packs})

    def deactivate(self, pack_id: str) -> dict[str, Any]:
        state = self.read()
        packs = [item for item in state["packs"] if item["pack_id"] != pack_id]
        if len(packs) == len(state["packs"]):
            raise PackContractError("pack is not active")
        return self._replace({**state, "generation": state["generation"] + 1, "packs": packs})

    def _replace(self, state: dict[str, Any]) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        encoded = (
            json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
        ).encode("utf-8")
        descriptor, temporary = tempfile.mkstemp(prefix="active-packs.", dir=self.root)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.state_path)
            directory_fd = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return state
