"""Measured host profiling for the winning flat v0.5 structured workload.

The profiler deliberately understands only the flat SQLite corpus pack and the
flat structured binary pack.  It contains no cognitive-cell address, overlap,
or topology accounting.  Host physical-read counters and cache-drop advice are
recorded as measurement evidence; neither is represented as a board result.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import resource
import sys
import time
from collections.abc import Callable, Sequence
from functools import partial
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from aethersparse.real_corpus.pack import RealCorpusPack
from aethersparse.substrate.binary_pack import FlatBinaryPackReader
from aethersparse.v050.edge import (
    FlatWorkloadProfile,
    HardwareOutcome,
    QueryWorkload,
    build_flat_workload_profile,
    select_hardware,
)
from aethersparse.v050.gates import FrozenModel

PackKind = Literal["real_corpus_sqlite", "flat_structured_binary"]
CacheState = Literal["cold_cache_advised", "warm"]


class ProfileQuery(FrozenModel):
    """Frozen query addresses plus counters emitted by the controller runtime."""

    query_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    title_queries: tuple[str, ...] = ()
    alias_queries: tuple[str, ...] = ()
    anchor_queries: tuple[str, ...] = ()
    relation_families: tuple[str, ...] = ()
    entity_ids: tuple[str, ...] = ()
    document_ids: tuple[str, ...] = Field(default=(), max_length=32)
    claim_ids: tuple[str, ...] = ()
    source_binding_chunk_ids: tuple[str, ...] = ()
    retrieval_limit: int = Field(default=8, ge=1, le=64)
    max_binary_sections: int = Field(default=32, ge=1, le=128)
    deterministic_ops: int = Field(ge=0)
    neural_macs: int = Field(ge=0)
    model_bytes: int = Field(ge=0)
    interface_bytes: int | None = Field(default=None, ge=0)

    @property
    def measured_interface_bytes(self) -> int:
        if self.interface_bytes is not None:
            return self.interface_bytes
        return len(
            json.dumps(
                self.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )


class CachePreparation(FrozenModel):
    requested: bool
    method: str
    advisory_only: bool
    succeeded: bool
    detail: str


class PhysicalReadEvidence(FrozenModel):
    available: bool
    counter: str
    bytes_read: int = Field(ge=0)
    detail: str


class PeakRssEvidence(FrozenModel):
    bytes: int = Field(ge=0)
    method: str
    scope: str = "process_high_water_mark_not_query_increment"


class LogicalReadEvidence(FrozenModel):
    method: str
    bounded: bool
    source_bytes: int = Field(ge=0)
    index_bytes: int = Field(ge=0)
    source_blocks: int = Field(ge=0)
    index_blocks: int = Field(ge=0)
    payload_bytes: int = Field(ge=0)
    sqlite_page_bytes: int | None = Field(default=None, gt=0)
    operations: int = Field(ge=0)


class QueryMeasurement(FrozenModel):
    query_id: str
    repetition: int = Field(ge=1)
    cache_state: CacheState
    workload: QueryWorkload
    logical_reads: LogicalReadEvidence
    physical_reads: PhysicalReadEvidence
    peak_rss: PeakRssEvidence
    cache_preparation: CachePreparation


class PackProfile(FrozenModel):
    profile_id: str
    pack_kind: PackKind
    pack_path: str
    pack_bytes: int = Field(gt=0)
    pack_sha256: str
    warm_measurements: tuple[QueryMeasurement, ...] = Field(min_length=1)
    cold_advised_measurements: tuple[QueryMeasurement, ...] = Field(min_length=1)
    warm_profile: FlatWorkloadProfile
    cold_advised_profile: FlatWorkloadProfile
    evidence_class: str = "host_measurement_not_edge_board_measurement"

    @model_validator(mode="after")
    def query_counts_match(self) -> PackProfile:
        if self.warm_profile.query_count != len(self.warm_measurements):
            raise ValueError("warm workload profile count differs from measurements")
        if self.cold_advised_profile.query_count != len(self.cold_advised_measurements):
            raise ValueError("cold workload profile count differs from measurements")
        return self

    @property
    def bounded_cold_reads_measured(self) -> bool:
        return all(
            measurement.logical_reads.bounded
            and measurement.physical_reads.available
            and measurement.cache_preparation.succeeded
            for measurement in self.cold_advised_measurements
        )


class FrozenHardwareCriteria(FrozenModel):
    """Qualification state frozen independently from the workload profiler."""

    criteria_id: str = Field(min_length=1)
    decision_profile_id: str = Field(min_length=1)
    architecture_qualified: bool = False
    architecture_frozen: bool = False
    neural_mapping_measured: bool = False
    p4_board_measured: bool = False
    latency_target_ms: float = Field(default=1000.0, gt=0.0)


class EdgeQualificationReport(FrozenModel):
    format_id: Literal["AETHERSPARSE_V050_FLAT_EDGE_PROFILE_R1"] = (
        "AETHERSPARSE_V050_FLAT_EDGE_PROFILE_R1"
    )
    profiles: tuple[PackProfile, ...] = Field(min_length=1)
    criteria: FrozenHardwareCriteria
    criteria_sha256: str
    hardware_outcome: HardwareOutcome
    board_measurements_present: bool
    topology_excluded: Literal[True] = True


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def _physical_read_bytes() -> int | None:
    """Return Linux block-I/O bytes, excluding page-cache hits, when exposed."""

    try:
        lines = Path("/proc/self/io").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        name, separator, value = line.partition(":")
        if separator and name.strip() == "read_bytes":
            try:
                return int(value.strip())
            except ValueError:
                return None
    return None


def _peak_rss() -> PeakRssEvidence:
    try:
        lines = Path("/proc/self/status").read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        if line.startswith("VmHWM:"):
            fields = line.split()
            if len(fields) >= 2:
                return PeakRssEvidence(
                    bytes=int(fields[1]) * 1024,
                    method="linux_proc_status_vmhwm",
                )
    maximum = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    if sys.platform != "darwin":
        maximum *= 1024
    return PeakRssEvidence(bytes=max(0, maximum), method="getrusage_ru_maxrss")


def advise_cold_cache(path: Path) -> CachePreparation:
    """Ask the kernel to discard this file's cached pages.

    POSIX_FADV_DONTNEED is advisory: success means the request was accepted, not
    that every page was evicted.  The report preserves that limitation.
    """

    advice = getattr(os, "POSIX_FADV_DONTNEED", None)
    function = getattr(os, "posix_fadvise", None)
    if advice is None or function is None:
        return CachePreparation(
            requested=True,
            method="posix_fadvise_dontneed",
            advisory_only=True,
            succeeded=False,
            detail="POSIX_FADV_DONTNEED is unavailable on this host",
        )
    descriptor = os.open(path, os.O_RDONLY)
    try:
        function(descriptor, 0, 0, advice)
    except OSError as error:
        return CachePreparation(
            requested=True,
            method="posix_fadvise_dontneed",
            advisory_only=True,
            succeeded=False,
            detail=f"kernel rejected advisory: {error}",
        )
    finally:
        os.close(descriptor)
    return CachePreparation(
        requested=True,
        method="posix_fadvise_dontneed",
        advisory_only=True,
        succeeded=True,
        detail="kernel accepted advisory; eviction is not guaranteed",
    )


def _warm_cache_state() -> CachePreparation:
    return CachePreparation(
        requested=False,
        method="reuse_after_unmeasured_warmup",
        advisory_only=False,
        succeeded=True,
        detail="reader and file pages were reused after an unmeasured warmup pass",
    )


def _sqlite_accounting(pack: RealCorpusPack, query: ProfileQuery) -> LogicalReadEvidence:
    pack.workload_trace(clear=True)
    pack.search_chunks(query.text, query.retrieval_limit)
    for title in query.title_queries:
        pack.title_lookup(title, query.retrieval_limit)
    for alias in query.alias_queries:
        pack.alias_lookup(alias, query.retrieval_limit)
    for anchor in query.anchor_queries:
        pack.anchor_lookup(anchor, query.retrieval_limit)
    if query.document_ids:
        pack.chunks_for_documents(list(query.document_ids), query.retrieval_limit)
    for chunk_id in query.source_binding_chunk_ids:
        pack.source_binding(chunk_id)
    traces = pack.workload_trace(clear=True)
    source_blocks = sum(int(trace["estimated_payload_blocks"]) for trace in traces)
    index_blocks = sum(int(trace["index_probes"]) for trace in traces)
    payload_bytes = sum(int(trace["payload_bytes"]) for trace in traces)
    page_bytes = max((int(trace["sqlite_page_bytes"]) for trace in traces), default=4096)
    return LogicalReadEvidence(
        method="sqlite_bounded_api_page_model",
        bounded=True,
        source_bytes=source_blocks * page_bytes,
        index_bytes=index_blocks * page_bytes,
        source_blocks=source_blocks,
        index_blocks=index_blocks,
        payload_bytes=payload_bytes,
        sqlite_page_bytes=page_bytes,
        operations=len(traces),
    )


def _binary_accounting(
    reader: FlatBinaryPackReader, query: ProfileQuery
) -> LogicalReadEvidence:
    result = reader.query_sections(
        text=query.text,
        relation_families=query.relation_families,
        entity_ids=query.entity_ids,
        document_ids=query.document_ids,
        claim_ids=query.claim_ids,
        max_sections=query.max_binary_sections,
    )
    source_sections = tuple(
        payload
        for name, payload in result.sections
        if name.startswith(("documents/", "bindings/", "chunks/", "claims/"))
    )
    index_sections = tuple(
        payload
        for name, payload in result.sections
        if not name.startswith(("documents/", "bindings/", "chunks/", "claims/"))
    )
    payload_bytes = sum(len(payload) for _, payload in result.sections)
    manifest_bytes = max(0, result.trace.bytes_read - payload_bytes)
    return LogicalReadEvidence(
        method="verified_flat_binary_section_reads",
        bounded=True,
        source_bytes=sum(len(payload) for payload in source_sections),
        index_bytes=sum(len(payload) for payload in index_sections) + manifest_bytes,
        source_blocks=len(source_sections),
        index_blocks=len(index_sections) + int(manifest_bytes > 0),
        payload_bytes=payload_bytes,
        sqlite_page_bytes=None,
        operations=1,
    )


def _measure(
    query: ProfileQuery,
    *,
    repetition: int,
    cache_state: CacheState,
    cache_preparation: CachePreparation,
    operation: Callable[[], LogicalReadEvidence],
) -> QueryMeasurement:
    before_reads = _physical_read_bytes()
    started_ns = time.perf_counter_ns()
    logical = operation()
    elapsed_ms = (time.perf_counter_ns() - started_ns) / 1_000_000
    after_reads = _physical_read_bytes()
    available = before_reads is not None and after_reads is not None
    physical_bytes = max(0, (after_reads or 0) - (before_reads or 0)) if available else 0
    physical = PhysicalReadEvidence(
        available=available,
        counter="linux_proc_self_io_read_bytes",
        bytes_read=physical_bytes,
        detail=(
            "delta of kernel storage-layer read_bytes; page-cache hits are excluded"
            if available
            else "host does not expose /proc/self/io read_bytes"
        ),
    )
    peak = _peak_rss()
    workload = QueryWorkload(
        source_bytes=logical.source_bytes,
        index_bytes=logical.index_bytes,
        source_blocks=logical.source_blocks,
        index_blocks=logical.index_blocks,
        deterministic_ops=query.deterministic_ops,
        neural_macs=query.neural_macs,
        model_bytes=query.model_bytes,
        peak_active_ram_bytes=peak.bytes,
        interface_bytes=query.measured_interface_bytes,
        measured_host_latency_ms=elapsed_ms,
        measured_physical_read_bytes=physical.bytes_read,
    )
    return QueryMeasurement(
        query_id=query.query_id,
        repetition=repetition,
        cache_state=cache_state,
        workload=workload,
        logical_reads=logical,
        physical_reads=physical,
        peak_rss=peak,
        cache_preparation=cache_preparation,
    )


def _pack_profile(
    *,
    profile_id: str,
    pack_kind: PackKind,
    path: Path,
    warm: Sequence[QueryMeasurement],
    cold: Sequence[QueryMeasurement],
) -> PackProfile:
    return PackProfile(
        profile_id=profile_id,
        pack_kind=pack_kind,
        pack_path=str(path.resolve()),
        pack_bytes=path.stat().st_size,
        pack_sha256=_sha256_file(path),
        warm_measurements=tuple(warm),
        cold_advised_measurements=tuple(cold),
        warm_profile=build_flat_workload_profile(tuple(item.workload for item in warm)),
        cold_advised_profile=build_flat_workload_profile(
            tuple(item.workload for item in cold)
        ),
    )


def profile_sqlite_pack(
    path: Path,
    queries: Sequence[ProfileQuery],
    *,
    profile_id: str,
    repetitions: int = 1,
) -> PackProfile:
    """Measure bounded SQLite retrieval without materializing the corpus."""

    if not queries:
        raise ValueError("at least one profile query is required")
    if repetitions < 1:
        raise ValueError("repetitions must be positive")
    if not path.is_file():
        raise FileNotFoundError(path)
    cold: list[QueryMeasurement] = []
    for repetition in range(1, repetitions + 1):
        for query in queries:
            preparation = advise_cold_cache(path)

            def cold_operation(query: ProfileQuery = query) -> LogicalReadEvidence:
                with RealCorpusPack(path, maximum_limit=64) as pack:
                    return _sqlite_accounting(pack, query)

            cold.append(
                _measure(
                    query,
                    repetition=repetition,
                    cache_state="cold_cache_advised",
                    cache_preparation=preparation,
                    operation=cold_operation,
                )
            )
    warm: list[QueryMeasurement] = []
    with RealCorpusPack(path, maximum_limit=64) as pack:
        for query in queries:
            _sqlite_accounting(pack, query)
        for repetition in range(1, repetitions + 1):
            for query in queries:
                warm.append(
                    _measure(
                        query,
                        repetition=repetition,
                        cache_state="warm",
                        cache_preparation=_warm_cache_state(),
                        operation=partial(_sqlite_accounting, pack, query),
                    )
                )
    return _pack_profile(
        profile_id=profile_id,
        pack_kind="real_corpus_sqlite",
        path=path,
        warm=warm,
        cold=cold,
    )


def profile_binary_pack(
    path: Path,
    queries: Sequence[ProfileQuery],
    *,
    profile_id: str,
    repetitions: int = 1,
) -> PackProfile:
    """Measure checksum-verified flat section reads without loading the pack."""

    if not queries:
        raise ValueError("at least one profile query is required")
    if repetitions < 1:
        raise ValueError("repetitions must be positive")
    if not path.is_file():
        raise FileNotFoundError(path)
    cold: list[QueryMeasurement] = []
    for repetition in range(1, repetitions + 1):
        for query in queries:
            preparation = advise_cold_cache(path)

            def cold_operation(query: ProfileQuery = query) -> LogicalReadEvidence:
                return _binary_accounting(FlatBinaryPackReader(path), query)

            cold.append(
                _measure(
                    query,
                    repetition=repetition,
                    cache_state="cold_cache_advised",
                    cache_preparation=preparation,
                    operation=cold_operation,
                )
            )
    warm: list[QueryMeasurement] = []
    reader = FlatBinaryPackReader(path)
    for query in queries:
        _binary_accounting(reader, query)
    for repetition in range(1, repetitions + 1):
        for query in queries:
            warm.append(
                _measure(
                    query,
                    repetition=repetition,
                    cache_state="warm",
                    cache_preparation=_warm_cache_state(),
                    operation=partial(_binary_accounting, reader, query),
                )
            )
    return _pack_profile(
        profile_id=profile_id,
        pack_kind="flat_structured_binary",
        path=path,
        warm=warm,
        cold=cold,
    )


def build_edge_qualification_report(
    profiles: Sequence[PackProfile],
    criteria: FrozenHardwareCriteria,
    *,
    criteria_sha256: str,
) -> EdgeQualificationReport:
    if not profiles:
        raise ValueError("at least one pack profile is required")
    matches = [item for item in profiles if item.profile_id == criteria.decision_profile_id]
    if len(matches) != 1:
        raise ValueError("criteria decision_profile_id must identify exactly one profile")
    selected = matches[0]
    outcome = select_hardware(
        selected.cold_advised_profile,
        architecture_qualified=criteria.architecture_qualified,
        bounded_reads_measured=selected.bounded_cold_reads_measured,
        architecture_frozen=criteria.architecture_frozen,
        neural_mapping_measured=criteria.neural_mapping_measured,
        p4_board_measured=criteria.p4_board_measured,
        latency_target_ms=criteria.latency_target_ms,
    )
    return EdgeQualificationReport(
        profiles=tuple(profiles),
        criteria=criteria,
        criteria_sha256=criteria_sha256,
        hardware_outcome=outcome,
        board_measurements_present=criteria.p4_board_measured,
    )


def percentile_latency_summary(profile: PackProfile) -> dict[str, float | int]:
    """Compact cold/warm latency and read summary used by the report document."""

    cold = profile.cold_advised_profile
    warm = profile.warm_profile
    return {
        "warm_p50_latency_ms": round(warm.p50_host_latency_ms, 6),
        "warm_p95_latency_ms": round(warm.p95_host_latency_ms, 6),
        "cold_advised_p50_latency_ms": round(cold.p50_host_latency_ms, 6),
        "cold_advised_p95_latency_ms": round(cold.p95_host_latency_ms, 6),
        "cold_advised_p95_logical_bytes": cold.p95_storage_bytes,
        "cold_advised_p95_logical_blocks": cold.p95_storage_reads,
        "cold_physical_read_bytes": cold.total_physical_read_bytes,
        "peak_rss_bytes": max(cold.peak_active_ram_bytes, warm.peak_active_ram_bytes),
        "model_bytes": cold.model_bytes,
        "p95_neural_macs": cold.p95_neural_macs,
        "p95_deterministic_ops": cold.p95_deterministic_ops,
    }


def nearest_rank_percentile(values: Sequence[float], fraction: float) -> float:
    """Exposed deterministic percentile helper for independent report audits."""

    if not values:
        raise ValueError("at least one value is required")
    if not 0.0 < fraction <= 1.0:
        raise ValueError("fraction must be in (0,1]")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1)]
