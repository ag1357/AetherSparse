"""Production-facing V15 compile, pack, and qualification operations."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from aethersparse.edge_runtime.deployment_v15 import (
    EvidenceDirectory,
    EvidenceEntry,
    EvidenceLayout,
    projected_pack_v2_controls,
)

EVIDENCE_V14_MAGIC = b"ACP1EVD1"
PACK_V2_SCHEMA = "aethersparse.deployment-pack-v2.v1"


class ProductionOperationError(RuntimeError):
    pass


def _aethersparse_version() -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("aethersparse")
    except PackageNotFoundError:
        return "0+unknown"


def compile_native_runtime(output: Path, *, compiler: str | None = None) -> dict[str, object]:
    """Build the portable allocation-free C++17 runtime and return its identity."""

    repository = Path(__file__).resolve().parents[3]
    selected_compiler = compiler or shutil.which("g++")
    if selected_compiler is None:
        raise ProductionOperationError("g++ is unavailable")
    source = repository / "native/aethercore_runtime/src/aethercore_runtime.cpp"
    include = repository / "native/aethercore_runtime/include"
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        selected_compiler,
        "-I",
        str(include),
        "-std=c++17",
        "-O2",
        "-fno-exceptions",
        "-fno-rtti",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-fPIC",
        "-shared",
        str(source),
        "-o",
        str(output),
    ]
    subprocess.run(command, check=True)
    payload = output.read_bytes()
    return {
        "schema_version": "aethersparse.v15.native-compile.v1",
        "output": str(output),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "cxx_standard": "C++17",
        "exceptions": False,
        "rtti": False,
        "command": command,
    }


def compile_pack_v2_evidence(
    source: Path,
    output: Path,
    *,
    entity_capacity: int,
    layout: EvidenceLayout,
) -> dict[str, object]:
    """Convert the V14 evidence directory into a prepacked V15 lookup image."""

    with source.open("rb") as stream:
        header = stream.read(4096)
        if len(header) != 4096 or header[:8] != EVIDENCE_V14_MAGIC:
            raise ProductionOperationError("source is not an ACP1EVD1 evidence region")
        values = struct.unpack_from("<8sI Q I QQQQQQ", header, 0)
        directory_offset = values[4]
        directory_length = values[5]
        if directory_length % 16:
            raise ProductionOperationError("V14 evidence directory is not 16-byte aligned")
        stream.seek(directory_offset)
        flat = stream.read(directory_length)
    if len(flat) != directory_length:
        raise ProductionOperationError("V14 evidence directory is truncated")
    entries = tuple(
        EvidenceEntry(*struct.unpack_from("<4I", flat, offset))
        for offset in range(0, len(flat), 16)
    )
    directory = EvidenceDirectory(entries, entity_capacity=entity_capacity, layout=layout)
    image = directory.encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor_path = output.with_suffix(output.suffix + ".json")
    # Physical-identity binding: the derived image must never become factual
    # authority separate from the pack it was compiled from.  Hash the whole
    # evidence region (chunked; it can exceed 1 GiB) and bind the source pack
    # when the conventional <pack>/regions/evidence.bin layout is present.
    region_hasher = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            region_hasher.update(chunk)
    manifest_path = source.parent.parent / "manifest.json"
    source_pack_id: str | None = None
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ProductionOperationError(
                f"pack manifest unreadable: {manifest_path}: {error}"
            ) from error
        pack_id = manifest.get("pack_id")
        if not isinstance(pack_id, str):
            raise ProductionOperationError("pack manifest has no pack_id")
        source_pack_id = pack_id
    descriptor: dict[str, Any] = {
        "schema_version": PACK_V2_SCHEMA,
        "layout": str(layout),
        "entity_capacity": entity_capacity,
        "evidence_entries": len(entries),
        "record_bytes": (
            16
            if layout in (EvidenceLayout.FLAT_PAGED, EvidenceLayout.FLAT_RESIDENT)
            else 12
        ),
        "page_bytes": 4096,
        "image_bytes": len(image),
        "image_sha256": hashlib.sha256(image).hexdigest(),
        "source_directory_sha256": hashlib.sha256(flat).hexdigest(),
        "source_evidence_region_sha256": region_hasher.hexdigest(),
        "source_pack_id": source_pack_id,
        "compiler_identity": f"aethersparse {_aethersparse_version()} aethercore pack",
        "resident_bytes": directory.resident_bytes,
        "cold_bytes": directory.cold_bytes,
        "device_time_repack_required": False,
    }
    temporary_descriptor: str | None = None
    temporary_image: str | None = None
    try:
        image_fd, temporary_image = tempfile.mkstemp(prefix=output.name, dir=output.parent)
        with os.fdopen(image_fd, "wb") as destination:
            destination.write(image)
            destination.flush()
            os.fsync(destination.fileno())
        descriptor_fd, temporary_descriptor = tempfile.mkstemp(
            prefix=descriptor_path.name, dir=descriptor_path.parent
        )
        with os.fdopen(descriptor_fd, "w", encoding="utf-8") as destination:
            json.dump(descriptor, destination, indent=2, sort_keys=True)
            destination.write("\n")
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary_image, output)
        temporary_image = None
        os.replace(temporary_descriptor, descriptor_path)
        temporary_descriptor = None
    finally:
        for path in (temporary_image, temporary_descriptor):
            if path is not None and os.path.exists(path):
                os.unlink(path)
    return {**descriptor, "output": str(output), "descriptor": str(descriptor_path)}


def qualify_production_candidate(report_root: Path) -> dict[str, object]:
    """Fail closed unless committed native/pack/cache gates agree with executable controls."""

    paths = {
        "native": report_root / "native-hardening-qualification.json",
        "pack": report_root / "pack-v2-qualification.json",
        "cache": report_root / "cache-qualification.json",
    }
    try:
        reports = {
            name: json.loads(path.read_text(encoding="utf-8")) for name, path in paths.items()
        }
    except (OSError, json.JSONDecodeError) as error:
        raise ProductionOperationError(f"qualification report unavailable: {error}") from error
    if reports["native"].get("status") != "PASS":
        raise ProductionOperationError("native hardening gate is not PASS")
    controls = {str(item["layout"]): item for item in projected_pack_v2_controls()}
    selected = reports["pack"].get("selection", {}).get("performance")
    if selected != "direct_compact_resident" or selected not in controls:
        raise ProductionOperationError("Pack-v2 performance selection is inconsistent")
    selected_control = controls[selected]
    if selected_control["evidence_media_misses_total"] != 0:
        raise ProductionOperationError("selected evidence directory retains media misses")
    return {
        "schema_version": "aethersparse.v15.production-qualification.v1",
        "status": "PASS",
        "gates": {name: str(path) for name, path in paths.items()},
        "pack_layout": selected,
        "cache_bytes": reports["cache"]["selection"]["cache_bytes"],
        "physical_deployment_pending": True,
    }
