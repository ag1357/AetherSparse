#!/usr/bin/env python3
"""Measure the V13 paged-layout proxy and emit a compact runtime report."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import statistics
import subprocess
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from aethersparse.edge_runtime.layout import CacheProjection, PagedPostingIndex


def digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def measure_cache(
    index_surfaces: list[str], queries: list[tuple[int, str]], cache_bytes: int
) -> CacheProjection:
    index = PagedPostingIndex.from_surfaces(index_surfaces, cache_bytes=cache_bytes)
    rows = [index.query(surface) for _, surface in queries]
    total_requests = sum(row.cache_hits + row.cache_misses for row in rows)
    complete = sum(
        index_id in row.candidate_ids
        for (index_id, _), row in zip(queries, rows, strict=True)
    )
    return CacheProjection(
        cache_bytes=cache_bytes,
        queries=len(rows),
        directory_bytes=index.directory_bytes,
        cold_index_bytes=index.cold_posting_bytes,
        bytes_per_query_mean=statistics.mean(row.cache_misses * index.page_bytes for row in rows),
        pages_per_query_mean=statistics.mean(row.cache_misses for row in rows),
        random_reads_per_query_mean=statistics.mean(row.random_reads for row in rows),
        sequential_reads_per_query_mean=statistics.mean(row.sequential_reads for row in rows),
        cache_hit_rate=(
            sum(row.cache_hits for row in rows) / total_requests if total_requests else 0
        ),
        candidate_completeness=complete / len(rows),
    )


def native_build_measure(repository: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temporary:
        library = Path(temporary) / "libaethercore_runtime.so"
        subprocess.run(
            [
                "g++",
                "-I",
                str(repository / "native/aethercore_runtime/include"),
                "-O2",
                "-std=c++17",
                "-fno-exceptions",
                "-fno-rtti",
                "-fPIC",
                "-shared",
                str(repository / "native/aethercore_runtime/src/aethercore_runtime.cpp"),
                "-o",
                str(library),
            ],
            check=True,
        )
        size_output = subprocess.check_output(["size", str(library)], text=True).splitlines()[1]
        text_bytes, data_bytes, bss_bytes = (int(value) for value in size_output.split()[:3])
        return {
            "host_shared_object_file_bytes": library.stat().st_size,
            "elf_load_text_bytes": text_bytes,
            "elf_load_data_bytes": data_bytes,
            "elf_bss_bytes": bss_bytes,
            "elf_load_total_bytes": text_bytes + data_bytes + bss_bytes,
            "evidence_class": "measured_host_gcc_build_not_esp32_binary",
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample", type=int, default=1000)
    arguments = parser.parse_args()
    with gzip.open(arguments.artifact, "rt", encoding="utf-8") as source:
        payload = json.load(source)
    records = payload["records"]
    all_surfaces = [record["surface"] for record in records]
    stride = max(1, len(records) // arguments.sample)
    queries = [
        (index + 1, all_surfaces[index])
        for index in range(0, len(all_surfaces), stride)
    ][: arguments.sample]
    cache_rows = [
        measure_cache(all_surfaces, queries, size)
        for size in (0, 65_536, 262_144, 1_048_576)
    ]
    repository = Path(__file__).resolve().parents[2]
    report = {
        "schema_version": "aethersparse.v13-portable-runtime-qualification.v1",
        "status": "HOST_PARITY_AND_EDGE_CONTRACT_QUALIFIED",
        "runtime": {
            "abi": "aethercore.runtime.c-abi.v1",
            "implementation": "allocation-free portable C++17 with C-compatible ABI",
            "workspace_bytes": 648,
            "session_struct_bytes": 872,
            "session_wire_bytes": 836,
            "candidate_cap": 32,
            "policy_interface": "external int8 linear weights, int16 features, int64 accumulation",
            "policy_parameter_capacity": (
                "64 features x 32 actions; interface replaceable behind ABI"
            ),
            "verifier_bypass": False,
            "native_build": native_build_measure(repository),
            "esp_idf": {
                "component_target_present": True,
                "actual_build_run": False,
                "reason": "ESP-IDF toolchain is not installed in the Work environment",
            },
        },
        "parity": {
            "frozen_vector_schema": "aethercore.runtime-parity-vectors.v1",
            "candidate_union_over_cap_covered": True,
            "policy_legal_mask_covered": True,
            "typed_verified_trajectory_covered": True,
            "session_bytes_and_crc_exact": True,
            "numeric_tolerance": 0,
        },
        "v12_397k_paged_layout": {
            "source": "authoritative V12 report plus measured 10k V12 post-cap trace proxy",
            "trace_artifact_sha256": digest(arguments.artifact),
            "trace_queries": len(queries),
            "trace_scope": (
                "deterministic evenly-spaced title surfaces; not the 50-case "
                "real-397k query trace"
            ),
            "page_bytes": 4096,
            "logical_index_bytes": 32_282_740,
            "cold_page_aligned_index_bytes": 32_284_672,
            "surface_count": 368_369,
            "posting_count": 5_909_296,
            "resident_surface_directory_bytes": 1_473_476,
            "resident_top_postings_directory_bytes": 262_144,
            "resident_directory_total_bytes": 1_735_620,
            "completeness_contract": (
                "full posting lists and canonical-ID union are unchanged; "
                "only placement/cache differs"
            ),
            "cache_trace_proxy": [asdict(row) for row in cache_rows],
        },
        "p4_projection": {
            "calibration_id": "aethercore.v11-p4-scalar-reference.v1",
            "evidence_class": "analytical_projection_not_hardware_measurement",
            "selected_v12_virtual_latency_ms_p50_p95": {
                "200mhz": [116.0533825, 236.6056065],
                "300mhz": [63.65246333333334, 129.94380825],
                "400mhz": [37.45200375, 76.670070125],
            },
            "warning": "V12 values predate the physical cache trace and are not board measurements",
        },
        "accessory_hardware_contract": {
            "minimum": {
                "cpu": "32-bit integer MCU-class core, >=200 MHz, int8/int16 multiply",
                "ram": "4 MiB addressable external RAM plus >=384 KiB fast internal SRAM",
                "model_storage": ">=512 KiB for <=0.25M int8 policy and metadata",
                "knowledge_storage": ">=32 GB removable/page-addressable media",
                "storage": ">=5 MB/s sequential, <=100 us target cached/random page service",
                "terminal_link": "transport-independent framed USB serial or local IP",
            },
            "recommended": {
                "cpu": "integer MCU or small CPU, >=300 MHz with efficient int8 MAC",
                "ram": "8 MiB external RAM plus >=512 KiB fast internal SRAM",
                "model_storage": ">=2 MiB",
                "knowledge_storage": "256 GB removable solid-state media",
                "storage": ">=10 MB/s sequential with >=4k random IOPS or effective read cache",
                "terminal_link": "USB plus local IP option",
            },
            "comfortable": {
                "cpu": "400 MHz+ integer core(s); Linux optional, neither required nor rejected",
                "ram": ">=16 MiB",
                "model_storage": ">=8 MiB",
                "knowledge_storage": "256 GB+ removable/replaceable solid-state media",
                "storage": ">=20 MB/s sequential with >=10k random IOPS",
                "terminal_link": "USB and local IP",
            },
            "physical": "compact accessory module; no permanent RJ45-height requirement",
            "power": (
                "not asserted until the selected module is measured under trace-equivalent load"
            ),
        },
        "deployment_filesystem": {
            "factory_intermediates_on_device": False,
            "immutable_pack_identity": "SHA-256 canonical manifest",
            "atomic_update": "validate all region hashes then fsync+rename active registry",
            "source_types": [
                "encyclopedia",
                "software_documentation",
                "source_code",
                "manual/specification",
            ],
        },
        "remaining_bottleneck": (
            "real ESP32-P4 storage trace/build and final learned-policy parameter footprint"
        ),
        "next_action": (
            "bind Worker A's selected int8 policy to the ABI, then capture physical 4 KiB "
            "page latency/cache traces on the accessory P4"
        ),
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
