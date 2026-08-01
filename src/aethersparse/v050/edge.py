"""Flat structured-pack workload accounting and edge projections for v0.5.

This module accepts measurements from the winning flat controller only.  It has
no cognitive-cell pack inputs and gives no credit for advertised TOPS.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from pydantic import Field

from aethersparse.v050.gates import FrozenModel, HardwareDecision


class QueryWorkload(FrozenModel):
    source_bytes: int = Field(ge=0)
    index_bytes: int = Field(ge=0)
    source_blocks: int = Field(ge=0)
    index_blocks: int = Field(ge=0)
    deterministic_ops: int = Field(ge=0)
    neural_macs: int = Field(ge=0)
    model_bytes: int = Field(ge=0)
    peak_active_ram_bytes: int = Field(ge=0)
    interface_bytes: int = Field(ge=0)
    measured_host_latency_ms: float = Field(ge=0.0)
    measured_physical_read_bytes: int = Field(ge=0)

    @property
    def total_storage_bytes(self) -> int:
        return self.source_bytes + self.index_bytes

    @property
    def total_storage_reads(self) -> int:
        return self.source_blocks + self.index_blocks


class FlatWorkloadProfile(FrozenModel):
    query_count: int = Field(gt=0)
    p50_storage_bytes: int = Field(ge=0)
    p95_storage_bytes: int = Field(ge=0)
    p50_storage_reads: int = Field(ge=0)
    p95_storage_reads: int = Field(ge=0)
    p50_deterministic_ops: int = Field(ge=0)
    p95_deterministic_ops: int = Field(ge=0)
    p50_neural_macs: int = Field(ge=0)
    p95_neural_macs: int = Field(ge=0)
    model_bytes: int = Field(ge=0)
    peak_active_ram_bytes: int = Field(ge=0)
    p50_host_latency_ms: float = Field(ge=0.0)
    p95_host_latency_ms: float = Field(ge=0.0)
    total_physical_read_bytes: int = Field(ge=0)
    evidence_class: str = "measured_flat_structured_workload"


class BackendSpec(FrozenModel):
    backend_id: str
    usable_ram_bytes: int = Field(gt=0)
    deterministic_ops_per_second: int = Field(gt=0)
    cpu_macs_per_second: int = Field(gt=0)
    accelerated_macs_per_second: int = Field(gt=0)
    mapped_neural_fraction: float = Field(ge=0.0, le=1.0)
    sequential_bytes_per_second: int = Field(gt=0)
    random_read_ms: float = Field(ge=0.0)
    active_power_mw: int = Field(gt=0)
    uncertainty_factor: float = Field(ge=1.0)


class BackendProjection(FrozenModel):
    backend_id: str
    projected_p95_latency_ms: float = Field(ge=0.0)
    projected_p95_energy_mj: float = Field(ge=0.0)
    peak_ram_bytes: int = Field(ge=0)
    ram_headroom_fraction: float = Field(ge=0.0, le=1.0)
    neural_mapping_fraction: float = Field(ge=0.0, le=1.0)
    meets_latency: bool
    meets_ram_reserve: bool
    evidence_class: str = "analytical_projection_from_flat_workload_measurements"


class HardwareOutcome(FrozenModel):
    decision: HardwareDecision
    reasons: tuple[str, ...]
    projections: tuple[BackendProjection, ...]


BACKENDS: tuple[BackendSpec, ...] = (
    BackendSpec(
        backend_id="p4_pico_microsd",
        usable_ram_bytes=24 * 1024 * 1024,
        deterministic_ops_per_second=22_000_000,
        cpu_macs_per_second=12_000_000,
        accelerated_macs_per_second=12_000_000,
        mapped_neural_fraction=0.0,
        sequential_bytes_per_second=4 * 1024 * 1024,
        random_read_ms=0.85,
        active_power_mw=900,
        uncertainty_factor=2.0,
    ),
    BackendSpec(
        backend_id="core1106_emmc_rknn",
        usable_ram_bytes=192 * 1024 * 1024,
        deterministic_ops_per_second=55_000_000,
        cpu_macs_per_second=28_000_000,
        accelerated_macs_per_second=450_000_000,
        mapped_neural_fraction=0.92,
        sequential_bytes_per_second=35 * 1024 * 1024,
        random_read_ms=0.18,
        active_power_mw=2200,
        uncertainty_factor=1.8,
    ),
    BackendSpec(
        backend_id="imx_rt700_neutron",
        usable_ram_bytes=16 * 1024 * 1024,
        deterministic_ops_per_second=32_000_000,
        cpu_macs_per_second=18_000_000,
        accelerated_macs_per_second=230_000_000,
        mapped_neural_fraction=0.85,
        sequential_bytes_per_second=12 * 1024 * 1024,
        random_read_ms=0.50,
        active_power_mw=1200,
        uncertainty_factor=2.0,
    ),
    BackendSpec(
        backend_id="representative_low_power_fpga",
        usable_ram_bytes=64 * 1024 * 1024,
        deterministic_ops_per_second=120_000_000,
        cpu_macs_per_second=30_000_000,
        accelerated_macs_per_second=750_000_000,
        mapped_neural_fraction=0.95,
        sequential_bytes_per_second=25 * 1024 * 1024,
        random_read_ms=0.30,
        active_power_mw=3000,
        uncertainty_factor=2.2,
    ),
)


def _percentile(values: Sequence[int | float], fraction: float) -> int | float:
    if not values:
        raise ValueError("at least one workload observation is required")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1)]


def build_flat_workload_profile(samples: Sequence[QueryWorkload]) -> FlatWorkloadProfile:
    if not samples:
        raise ValueError("at least one workload observation is required")
    return FlatWorkloadProfile(
        query_count=len(samples),
        p50_storage_bytes=int(_percentile([item.total_storage_bytes for item in samples], 0.50)),
        p95_storage_bytes=int(_percentile([item.total_storage_bytes for item in samples], 0.95)),
        p50_storage_reads=int(_percentile([item.total_storage_reads for item in samples], 0.50)),
        p95_storage_reads=int(_percentile([item.total_storage_reads for item in samples], 0.95)),
        p50_deterministic_ops=int(
            _percentile([item.deterministic_ops for item in samples], 0.50)
        ),
        p95_deterministic_ops=int(
            _percentile([item.deterministic_ops for item in samples], 0.95)
        ),
        p50_neural_macs=int(_percentile([item.neural_macs for item in samples], 0.50)),
        p95_neural_macs=int(_percentile([item.neural_macs for item in samples], 0.95)),
        model_bytes=max(item.model_bytes for item in samples),
        peak_active_ram_bytes=max(item.peak_active_ram_bytes for item in samples),
        p50_host_latency_ms=float(
            _percentile([item.measured_host_latency_ms for item in samples], 0.50)
        ),
        p95_host_latency_ms=float(
            _percentile([item.measured_host_latency_ms for item in samples], 0.95)
        ),
        total_physical_read_bytes=sum(item.measured_physical_read_bytes for item in samples),
    )


def project_flat_workload(
    workload: FlatWorkloadProfile,
    *,
    latency_target_ms: float = 1000.0,
) -> tuple[BackendProjection, ...]:
    peak_ram = workload.peak_active_ram_bytes + workload.model_bytes
    projections: list[BackendProjection] = []
    for backend in BACKENDS:
        mapped_macs = workload.p95_neural_macs * backend.mapped_neural_fraction
        fallback_macs = workload.p95_neural_macs - mapped_macs
        compute_ms = (
            workload.p95_deterministic_ops / backend.deterministic_ops_per_second * 1000
            + mapped_macs / backend.accelerated_macs_per_second * 1000
            + fallback_macs / backend.cpu_macs_per_second * 1000
        )
        io_ms = (
            workload.p95_storage_bytes / backend.sequential_bytes_per_second * 1000
            + workload.p95_storage_reads * backend.random_read_ms
        )
        latency = (compute_ms + io_ms) * backend.uncertainty_factor
        headroom = (backend.usable_ram_bytes - peak_ram) / backend.usable_ram_bytes
        projections.append(
            BackendProjection(
                backend_id=backend.backend_id,
                projected_p95_latency_ms=round(latency, 3),
                projected_p95_energy_mj=round(latency / 1000 * backend.active_power_mw, 3),
                peak_ram_bytes=peak_ram,
                ram_headroom_fraction=max(0.0, min(1.0, round(headroom, 6))),
                neural_mapping_fraction=(
                    backend.mapped_neural_fraction if workload.p95_neural_macs else 0.0
                ),
                meets_latency=latency <= latency_target_ms,
                meets_ram_reserve=peak_ram <= backend.usable_ram_bytes * 0.8,
            )
        )
    return tuple(projections)


def select_hardware(
    workload: FlatWorkloadProfile,
    *,
    architecture_qualified: bool,
    bounded_reads_measured: bool,
    architecture_frozen: bool,
    neural_mapping_measured: bool,
    p4_board_measured: bool = False,
    latency_target_ms: float = 1000.0,
) -> HardwareOutcome:
    projections = project_flat_workload(workload, latency_target_ms=latency_target_ms)
    by_id = {item.backend_id: item for item in projections}
    if not architecture_qualified or not bounded_reads_measured:
        return HardwareOutcome(
            decision=HardwareDecision.NO_PURCHASE,
            reasons=tuple(
                reason
                for passed, reason in (
                    (architecture_qualified, "ARCHITECTURE_GATE_NOT_MET"),
                    (bounded_reads_measured, "FLAT_BOUNDED_READS_NOT_MEASURED"),
                )
                if not passed
            ),
            projections=projections,
        )
    p4 = by_id["p4_pico_microsd"]
    core = by_id["core1106_emmc_rknn"]
    rt700 = by_id["imx_rt700_neutron"]
    fpga = by_id["representative_low_power_fpga"]
    neural_major = workload.p95_neural_macs > workload.p95_deterministic_ops
    if neural_major and neural_mapping_measured and core.meets_latency and core.meets_ram_reserve:
        return HardwareOutcome(
            decision=HardwareDecision.CORE1106,
            reasons=("MEASURED_NEURAL_WORK_DOMINATES", "CORE1106_MEETS_P95_AND_RAM"),
            projections=projections,
        )
    if p4.meets_latency and p4.meets_ram_reserve and not neural_major:
        return HardwareOutcome(
            decision=(
                HardwareDecision.P4_FINAL if p4_board_measured else HardwareDecision.P4_REFERENCE
            ),
            reasons=("DETERMINISTIC_FLAT_WORKLOAD_DOMINATES", "P4_MEETS_P95_AND_RAM"),
            projections=projections,
        )
    if architecture_frozen and fpga.meets_latency and fpga.meets_ram_reserve:
        return HardwareOutcome(
            decision=HardwareDecision.FPGA,
            reasons=("FROZEN_DETERMINISTIC_KERNEL_SET", "FPGA_MEETS_P95_AND_RAM"),
            projections=projections,
        )
    if rt700.meets_latency and rt700.meets_ram_reserve:
        return HardwareOutcome(
            decision=HardwareDecision.RT700,
            reasons=("MIXED_FLAT_CONTROLLER_WORKLOAD", "RT700_MEETS_P95_AND_RAM"),
            projections=projections,
        )
    return HardwareOutcome(
        decision=HardwareDecision.NO_PURCHASE,
        reasons=("NO_BACKEND_MEETS_PURCHASE_CONDITIONS",),
        projections=projections,
    )
