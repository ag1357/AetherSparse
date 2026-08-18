"""Formula-derived storage/compute proxies for Semantic Address Plane queries."""

from __future__ import annotations

from pydantic import Field, model_validator

from aethersparse.controller.models import FrozenModel
from aethersparse.specialists.p4_cost import P4Assumptions, P4OperationCost, project_p4


class AddressQueryCost(FrozenModel):
    """Analytical query inputs under one explicit page-oriented layout proxy.

    Posting regions are already resident in PSRAM. Exact source regions are on
    parameterized external storage. Transfers charge page-aligned physical
    bytes, never logical payload bytes, and each channel is charged exactly once.
    """

    operation_id: str
    evidence_class: str = "formula_derived_analytical_proxy_not_runtime_counters"
    physical_layout: str = "psram_postings_external_source_regions_v1"
    transfer_accounting: str = "page_aligned_physical_bytes"
    page_bytes: int = Field(default=4096, gt=0)
    internal_sram_dma_peak_bytes: int = Field(ge=0)
    psram_resident_posting_bytes: int = Field(ge=0)
    psram_peak_known_allocation_bytes: int = Field(ge=0)
    fst_payload_bytes_read: int = Field(ge=0)
    posting_payload_bytes_read: int = Field(ge=0)
    query_key_bytes_processed: int = Field(ge=0)
    bq_payload_bytes_read: int = Field(ge=0)
    pq_payload_bytes_read: int = Field(ge=0)
    int8_payload_bytes_read: int = Field(ge=0)
    source_region_payload_bytes_read: int = Field(ge=0)
    psram_page_aligned_transfer_bytes: int = Field(ge=0)
    external_page_aligned_transfer_bytes: int = Field(ge=0)
    psram_random_page_reads: int = Field(ge=0)
    psram_sequential_page_reads: int = Field(ge=0)
    external_random_page_reads: int = Field(ge=0)
    external_sequential_page_reads: int = Field(ge=0)
    formula_derived_integer_operations: int = Field(ge=0)
    xor_popcount_operations: int = Field(ge=0)
    simd_operations: int = Field(ge=0)
    neural_macs: int = Field(ge=0)
    candidates_before_address: int = Field(ge=0)
    candidates_after_address: int = Field(ge=0)
    candidates_after_cap: int = Field(ge=0)
    active_parameters: int = Field(ge=0)
    model_bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def candidate_funnel_is_monotone(self) -> AddressQueryCost:
        if self.candidates_after_address > self.candidates_before_address:
            raise ValueError("address stage may not add candidates")
        if self.candidates_after_cap > self.candidates_after_address:
            raise ValueError("candidate cap may not add candidates")
        absent_channel_payloads = (
            self.fst_payload_bytes_read,
            self.bq_payload_bytes_read,
            self.pq_payload_bytes_read,
            self.int8_payload_bytes_read,
        )
        if any(absent_channel_payloads):
            raise ValueError("direct-claim layout may not charge absent retrieval channels")
        if self.psram_peak_known_allocation_bytes < self.psram_resident_posting_bytes:
            raise ValueError("PSRAM known peak may not be smaller than its resident postings")
        if self.psram_page_aligned_transfer_bytes % self.page_bytes:
            raise ValueError("PSRAM physical transfer bytes must be page aligned")
        if self.external_page_aligned_transfer_bytes % self.page_bytes:
            raise ValueError("external physical transfer bytes must be page aligned")
        if self.psram_page_reads * self.page_bytes != self.psram_page_aligned_transfer_bytes:
            raise ValueError("PSRAM page count and physical transfer bytes disagree")
        if self.external_page_reads * self.page_bytes != self.external_page_aligned_transfer_bytes:
            raise ValueError("external page count and physical transfer bytes disagree")
        if self.posting_payload_bytes_read > self.psram_page_aligned_transfer_bytes:
            raise ValueError("posting payload may not exceed its PSRAM physical transfer")
        if self.source_region_payload_bytes_read > self.external_page_aligned_transfer_bytes:
            raise ValueError("source payload may not exceed its external physical transfer")
        return self

    @property
    def psram_page_reads(self) -> int:
        return self.psram_random_page_reads + self.psram_sequential_page_reads

    @property
    def external_page_reads(self) -> int:
        return self.external_random_page_reads + self.external_sequential_page_reads

    @property
    def page_aligned_transfer_bytes(self) -> int:
        return self.psram_page_aligned_transfer_bytes + self.external_page_aligned_transfer_bytes

    @property
    def random_page_reads(self) -> int:
        return self.psram_random_page_reads + self.external_random_page_reads

    @property
    def sequential_page_reads(self) -> int:
        return self.psram_sequential_page_reads + self.external_sequential_page_reads


class AddressP4Projection(FrozenModel):
    evidence_class: str = "analytical_projection_not_hardware_measurement"
    calibration_scope: str = "unchanged_v11_reference_assumptions"
    storage_scope: str = "parameterized_external_storage_not_emmc_measurement"
    operation_id: str
    clock_mhz: int
    virtual_latency_ms: float
    compute_ms: float
    psram_transfer_ms: float
    external_storage_transfer_ms: float
    random_access_ms: float
    internal_sram_dma_peak_bytes: int
    psram_resident_posting_bytes: int
    psram_peak_known_allocation_bytes: int
    psram_page_aligned_transfer_bytes: int
    external_page_aligned_transfer_bytes: int
    psram_random_page_reads: int
    psram_sequential_page_reads: int
    external_random_page_reads: int
    external_sequential_page_reads: int
    random_page_reads: int
    sequential_page_reads: int
    active_parameters: int
    model_bytes: int


def project_address_cost(
    cost: AddressQueryCost,
    assumptions: P4Assumptions,
) -> AddressP4Projection:
    """Project formula-derived work with v11's scalar digital twin."""

    scalar_integer_ops = (
        cost.formula_derived_integer_operations
        + cost.xor_popcount_operations
        + cost.simd_operations
    )
    operation = P4OperationCost(
        operation_id=cost.operation_id,
        integer_operations=scalar_integer_ops,
        macs=cost.neural_macs,
        memory_bytes=(
            cost.psram_page_aligned_transfer_bytes + cost.external_page_aligned_transfer_bytes
        ),
        psram_bytes=cost.psram_page_aligned_transfer_bytes,
        flash_bytes=cost.external_page_aligned_transfer_bytes,
        psram_accesses=cost.psram_page_reads,
        flash_accesses=cost.external_page_reads,
        random_psram_reads=cost.psram_random_page_reads,
        random_flash_reads=cost.external_random_page_reads,
        sequential_reads=cost.sequential_page_reads,
        scratch_ram_bytes=cost.internal_sram_dma_peak_bytes,
        model_bytes=cost.model_bytes,
    )
    projection = project_p4((operation,), assumptions)
    return AddressP4Projection(
        operation_id=cost.operation_id,
        clock_mhz=assumptions.clock_mhz,
        virtual_latency_ms=projection.virtual_latency_ms,
        compute_ms=projection.compute_ms,
        psram_transfer_ms=projection.psram_transfer_ms,
        external_storage_transfer_ms=projection.flash_transfer_ms,
        random_access_ms=projection.random_access_ms,
        internal_sram_dma_peak_bytes=cost.internal_sram_dma_peak_bytes,
        psram_resident_posting_bytes=cost.psram_resident_posting_bytes,
        psram_peak_known_allocation_bytes=cost.psram_peak_known_allocation_bytes,
        psram_page_aligned_transfer_bytes=cost.psram_page_aligned_transfer_bytes,
        external_page_aligned_transfer_bytes=cost.external_page_aligned_transfer_bytes,
        psram_random_page_reads=cost.psram_random_page_reads,
        psram_sequential_page_reads=cost.psram_sequential_page_reads,
        external_random_page_reads=cost.external_random_page_reads,
        external_sequential_page_reads=cost.external_sequential_page_reads,
        random_page_reads=cost.random_page_reads,
        sequential_page_reads=cost.sequential_page_reads,
        active_parameters=cost.active_parameters,
        model_bytes=cost.model_bytes,
    )
