"""Content-addressed architecture-registry persistence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from aethersparse.observer.models import ArchitectureRegistry


def registry_sha256(registry: ArchitectureRegistry) -> str:
    payload = registry.model_dump(mode="json", exclude={"registry_sha256"})
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def seal_registry(registry: ArchitectureRegistry) -> ArchitectureRegistry:
    return registry.model_copy(update={"registry_sha256": registry_sha256(registry)})


def write_registry(path: Path, registry: ArchitectureRegistry) -> ArchitectureRegistry:
    sealed = seal_registry(registry)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(sealed.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return sealed


def load_registry(path: Path) -> ArchitectureRegistry:
    registry = ArchitectureRegistry.model_validate_json(Path(path).read_text(encoding="utf-8"))
    if registry.registry_sha256 is None:
        raise ValueError("architecture registry is not sealed")
    if registry.registry_sha256 != registry_sha256(registry):
        raise ValueError("architecture registry content hash mismatch")
    return registry
