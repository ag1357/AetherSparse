"""V15 deployment-pack, cache, and elastic-residency reference machinery.

This module does not replace the frozen V14 physical binary.  It provides the
host-qualified Pack-v2 layouts selected for the next Factory build and keeps
logical evidence results invariant while changing only physical placement.
"""

from __future__ import annotations

import math
import struct
from collections import OrderedDict
from dataclasses import dataclass
from enum import StrEnum


class DeploymentContractError(ValueError):
    pass


class EvidenceLayout(StrEnum):
    FLAT_PAGED = "flat_paged"
    FLAT_RESIDENT = "flat_resident"
    DIRECT_COMPACT_RESIDENT = "direct_compact_resident"
    TWO_LEVEL_PAGED = "two_level_paged"


class StorageProfile(StrEnum):
    COMPACT = "COMPACT"
    PERFORMANCE = "PERFORMANCE"


@dataclass(frozen=True)
class EvidenceEntry:
    entity_index: int
    blob_offset: int
    blob_length: int
    occurrence_count: int

    def __post_init__(self) -> None:
        if not 0 <= self.entity_index <= 0xFFFFFFFF:
            raise DeploymentContractError("entity index does not fit uint32")
        if any(
            not 0 <= value <= 0xFFFFFFFF
            for value in (self.blob_offset, self.blob_length, self.occurrence_count)
        ):
            raise DeploymentContractError("evidence value does not fit uint32")


@dataclass(frozen=True)
class LookupAudit:
    probes: int
    media_pages: int
    media_bytes: int


class EvidenceDirectory:
    """Exact fixture codec for each controlled V15 evidence-directory layout."""

    _MISSING = 0xFFFFFFFF
    _LEAF_ENTITIES = 256
    _PAGE_BYTES = 4096

    def __init__(
        self,
        entries: tuple[EvidenceEntry, ...],
        *,
        entity_capacity: int,
        layout: EvidenceLayout,
    ) -> None:
        if entity_capacity <= 0:
            raise DeploymentContractError("entity capacity must be positive")
        if tuple(sorted(entries, key=lambda item: item.entity_index)) != entries:
            raise DeploymentContractError("evidence entries must be sorted")
        if len({entry.entity_index for entry in entries}) != len(entries):
            raise DeploymentContractError("duplicate evidence entity")
        if entries and entries[-1].entity_index >= entity_capacity:
            raise DeploymentContractError("evidence entity exceeds capacity")
        self.entries = entries
        self.entity_capacity = entity_capacity
        self.layout = layout
        self._by_id = {entry.entity_index: entry for entry in entries}

    @property
    def resident_bytes(self) -> int:
        if self.layout is EvidenceLayout.FLAT_RESIDENT:
            return len(self.entries) * 16
        if self.layout is EvidenceLayout.DIRECT_COMPACT_RESIDENT:
            return self.entity_capacity * 12
        if self.layout is EvidenceLayout.TWO_LEVEL_PAGED:
            return math.ceil(self.entity_capacity / self._LEAF_ENTITIES) * 8
        return 0

    @property
    def cold_bytes(self) -> int:
        if self.layout is EvidenceLayout.FLAT_PAGED:
            return len(self.entries) * 16
        if self.layout is EvidenceLayout.TWO_LEVEL_PAGED:
            leaves = math.ceil(self.entity_capacity / self._LEAF_ENTITIES)
            return leaves * self._PAGE_BYTES
        return 0

    def lookup(self, entity_index: int) -> tuple[EvidenceEntry | None, LookupAudit]:
        if not 0 <= entity_index < self.entity_capacity:
            return None, LookupAudit(1, 0, 0)
        if self.layout in (
            EvidenceLayout.DIRECT_COMPACT_RESIDENT,
            EvidenceLayout.TWO_LEVEL_PAGED,
        ):
            direct_pages = int(self.layout is EvidenceLayout.TWO_LEVEL_PAGED)
            return self._by_id.get(entity_index), LookupAudit(
                1, direct_pages, direct_pages * 4096
            )
        low, high = 0, len(self.entries) - 1
        probes = 0
        page_set: set[int] = set()
        while low <= high:
            middle = (low + high) // 2
            probes += 1
            if self.layout is EvidenceLayout.FLAT_PAGED:
                page_set.add((middle * 16) // self._PAGE_BYTES)
            candidate = self.entries[middle]
            if candidate.entity_index == entity_index:
                return candidate, LookupAudit(
                    probes, len(page_set), len(page_set) * 4096
                )
            if candidate.entity_index < entity_index:
                low = middle + 1
            else:
                high = middle - 1
        return None, LookupAudit(probes, len(page_set), len(page_set) * 4096)

    def encode(self) -> bytes:
        """Emit a deterministic device-consumable directory image."""

        if self.layout in (EvidenceLayout.FLAT_PAGED, EvidenceLayout.FLAT_RESIDENT):
            return b"".join(
                struct.pack(
                    "<4I",
                    item.entity_index,
                    item.blob_offset,
                    item.blob_length,
                    item.occurrence_count,
                )
                for item in self.entries
            )
        direct = bytearray()
        for entity_index in range(self.entity_capacity):
            item = self._by_id.get(entity_index)
            if item is None:
                direct.extend(struct.pack("<3I", self._MISSING, 0, 0))
            else:
                direct.extend(
                    struct.pack(
                        "<3I", item.blob_offset, item.blob_length, item.occurrence_count
                    )
                )
        if self.layout is EvidenceLayout.DIRECT_COMPACT_RESIDENT:
            return bytes(direct)
        leaves = bytearray()
        for start in range(0, len(direct), self._LEAF_ENTITIES * 12):
            leaf = direct[start : start + self._LEAF_ENTITIES * 12]
            leaf.extend(bytes(self._PAGE_BYTES - len(leaf)))
            leaves.extend(leaf)
        return bytes(leaves)

    def lookup_encoded(self, image: bytes, entity_index: int) -> EvidenceEntry | None:
        """Read an entry directly from the final prepacked LE image."""

        if not 0 <= entity_index < self.entity_capacity:
            return None
        if self.layout in (EvidenceLayout.FLAT_PAGED, EvidenceLayout.FLAT_RESIDENT):
            if len(image) != len(self.entries) * 16:
                raise DeploymentContractError("flat evidence image size mismatch")
            low, high = 0, len(self.entries) - 1
            while low <= high:
                middle = (low + high) // 2
                values = struct.unpack_from("<4I", image, middle * 16)
                if values[0] == entity_index:
                    return EvidenceEntry(*values)
                if values[0] < entity_index:
                    low = middle + 1
                else:
                    high = middle - 1
            return None
        if self.layout is EvidenceLayout.DIRECT_COMPACT_RESIDENT:
            offset = entity_index * 12
        else:
            leaf = entity_index // self._LEAF_ENTITIES
            slot = entity_index % self._LEAF_ENTITIES
            offset = leaf * self._PAGE_BYTES + slot * 12
        if offset + 12 > len(image):
            raise DeploymentContractError("direct evidence image is truncated")
        blob_offset, blob_length, count = struct.unpack_from("<3I", image, offset)
        if blob_offset == self._MISSING:
            return None
        return EvidenceEntry(entity_index, blob_offset, blob_length, count)


@dataclass(frozen=True)
class HardwareCalibration:
    board: str
    chip_revision: str
    psram_bytes: int
    storage_identity: str
    storage_bus: str
    pack_generation: str
    sequential_bytes_per_second: float
    random_4k_mean_us: float
    random_4k_p50_us: float
    random_4k_p95_us: float
    internal_memory_bytes_per_second: float | None = None
    psram_bytes_per_second: float | None = None
    psram_copy_latency_us: float | None = None
    xai_int8_ops_per_second: float | None = None
    dma_staging_bytes_per_second: float | None = None
    transport_bytes_per_second: float | None = None
    transport_latency_us: float | None = None

    @property
    def identity(self) -> tuple[str, str, int, str, str, str]:
        return (
            self.board,
            self.chip_revision,
            self.psram_bytes,
            self.storage_identity,
            self.storage_bus,
            self.pack_generation,
        )

    def compatible_with(self, other: HardwareCalibration) -> bool:
        return self.identity == other.identity


@dataclass(frozen=True)
class CacheRequest:
    region: str
    page: int
    pinned: bool = False


class DeterministicRegionCache:
    """O(1) page-to-slot lookup with deterministic region-aware LRU eviction."""

    def __init__(self, capacities: dict[str, int]) -> None:
        if any(value < 0 for value in capacities.values()):
            raise DeploymentContractError("cache capacities cannot be negative")
        self.capacities = dict(sorted(capacities.items()))
        self._regions: dict[str, OrderedDict[int, bool]] = {
            region: OrderedDict() for region in self.capacities
        }
        self.hits = 0
        self.misses = 0
        self.pressure = 0

    def access(self, request: CacheRequest) -> bool:
        if request.region not in self._regions:
            raise DeploymentContractError("request uses unbudgeted region")
        region = self._regions[request.region]
        if request.page in region:
            self.hits += 1
            region[request.page] = region[request.page] or request.pinned
            region.move_to_end(request.page)
            return True
        self.misses += 1
        capacity = self.capacities[request.region]
        if capacity == 0:
            return False
        if len(region) >= capacity:
            victim = next((page for page, pinned in region.items() if not pinned), None)
            if victim is None:
                self.pressure += 1
                return False
            del region[victim]
        region[request.page] = request.pinned
        return False

    def unpin(self, region_name: str, page: int) -> None:
        if page in self._regions.get(region_name, {}):
            self._regions[region_name][page] = False


@dataclass(frozen=True)
class ResidencyDemand:
    knowledge_cache: int
    evidence_cache: int
    specialist_weights: int
    source_cache: int
    session_working: int


@dataclass(frozen=True)
class ResidencyAllocation:
    protected_bytes: int
    elastic_bytes: int
    knowledge_cache: int
    evidence_cache: int
    specialist_weights: int
    source_cache: int
    session_working: int


class ElasticResidencyController:
    """Placement-only controller; it never changes the requested semantic operation."""

    def __init__(self, *, psram_bytes: int, protected_bytes: int) -> None:
        if not 0 < protected_bytes < psram_bytes:
            raise DeploymentContractError("invalid protected PSRAM boundary")
        self.psram_bytes = psram_bytes
        self.protected_bytes = protected_bytes

    def allocate(self, demand: ResidencyDemand) -> ResidencyAllocation:
        requested = (
            demand.knowledge_cache
            + demand.evidence_cache
            + demand.specialist_weights
            + demand.source_cache
            + demand.session_working
        )
        elastic = self.psram_bytes - self.protected_bytes
        if requested <= elastic:
            values = demand
        else:
            # Stable priority: active session, selected evidence, specialists,
            # knowledge/address cache, optional source cache.
            remaining = elastic
            assigned: dict[str, int] = {}
            for name in (
                "session_working",
                "evidence_cache",
                "specialist_weights",
                "knowledge_cache",
                "source_cache",
            ):
                value = min(getattr(demand, name), remaining)
                assigned[name] = value
                remaining -= value
            values = ResidencyDemand(**assigned)
        return ResidencyAllocation(
            protected_bytes=self.protected_bytes,
            elastic_bytes=elastic,
            **values.__dict__,
        )

    @staticmethod
    def choose_specialist(
        *,
        requested_capability: str,
        implementations: tuple[dict[str, object], ...],
    ) -> str:
        equivalent = [
            item
            for item in implementations
            if item.get("capability") == requested_capability
            and item.get("semantic_equivalence") is True
        ]
        if not equivalent:
            raise DeploymentContractError("no semantically equivalent specialist")

        def cost(item: dict[str, object]) -> tuple[int, str]:
            load = item.get("load_latency_us")
            compute = item.get("compute_latency_us")
            if not isinstance(load, int) or not isinstance(compute, int):
                raise DeploymentContractError("specialist latency must be an integer")
            return load + compute, str(item["specialist_id"])

        selected = min(
            equivalent,
            key=cost,
        )
        return str(selected["specialist_id"])


def projected_pack_v2_controls() -> tuple[dict[str, object], ...]:
    """Project mandatory controls from the authenticated V14 physical counters."""

    queries = 107
    entity_capacity = 275_989
    evidence_entries = 239_630
    flat_bytes = evidence_entries * 16
    direct_bytes = entity_capacity * 12
    baseline_evidence_misses = 23_311  # physical 1 MiB cold pass
    total_misses = 37_636
    other_misses = total_misses - baseline_evidence_misses
    estimated_lookups = math.ceil(93_806 / math.ceil(math.log2(evidence_entries)))
    controls = (
        {
            "layout": EvidenceLayout.FLAT_PAGED,
            "resident_directory_bytes": 0,
            "cold_directory_bytes": flat_bytes,
            "directory_probes_total": 93_806,
            "evidence_media_misses_total": baseline_evidence_misses,
        },
        {
            "layout": EvidenceLayout.FLAT_RESIDENT,
            "resident_directory_bytes": flat_bytes,
            "cold_directory_bytes": 0,
            "directory_probes_total": 93_806,
            "evidence_media_misses_total": 0,
        },
        {
            "layout": EvidenceLayout.DIRECT_COMPACT_RESIDENT,
            "resident_directory_bytes": direct_bytes,
            "cold_directory_bytes": 0,
            "directory_probes_total": estimated_lookups,
            "evidence_media_misses_total": 0,
        },
        {
            "layout": EvidenceLayout.TWO_LEVEL_PAGED,
            "resident_directory_bytes": math.ceil(entity_capacity / 256) * 8,
            "cold_directory_bytes": math.ceil(entity_capacity / 256) * 4096,
            "directory_probes_total": estimated_lookups,
            "evidence_media_misses_total": estimated_lookups,
        },
    )
    output: list[dict[str, object]] = []
    for item in controls:
        evidence_value = item["evidence_media_misses_total"]
        if not isinstance(evidence_value, int):
            raise AssertionError("evidence miss projection must be integer")
        evidence_misses = evidence_value
        misses = other_misses + evidence_misses
        # Bind to physical V14 wall time by observed miss share.  This is a
        # projection for unchanged media, not a hardware-performance claim.
        ratio = misses / total_misses
        output.append(
            {
                **item,
                "queries": queries,
                "media_misses_per_query": misses / queries,
                "evidence_misses_per_query": evidence_misses / queries,
                "modeled_mean_address_ms": 1_217.2502 * ratio,
                "candidate_completeness": 1.0,
                "address_logical_parity": 1.0,
            }
        )
    return tuple(output)
