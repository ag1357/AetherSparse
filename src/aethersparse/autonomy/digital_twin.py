"""Transparent, conservative workload digital twin.

These projections are analytical estimates, not hardware measurements.  Every
coefficient is explicit and can be replaced with board measurements without
changing the workload accounting or recommendation policy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from aethersparse.autonomy.systems import SystemResult


class BackendId(StrEnum):
    ESP32_P4_PICO = "esp32_p4_pico_microsd"
    CORE1106 = "luckfox_core1106_256mb_emmc_rknn"
    IMX_RT700 = "imx_rt700_neutron_external_storage"
    LOW_POWER_FPGA = "representative_low_power_fpga"


class RecommendationDecision(StrEnum):
    ESP32_P4_PICO = "RECOMMEND_ESP32_P4_PICO"
    CORE1106 = "RECOMMEND_CORE1106"
    IMX_RT700 = "RECOMMEND_IMX_RT700"
    LOW_POWER_FPGA = "RECOMMEND_LOW_POWER_FPGA"
    NO_PURCHASE = "NO_PURCHASE"
    ARCHITECTURE_FAILED = "ARCHITECTURE_FAILED"


@dataclass(frozen=True, slots=True)
class WorkloadSample:
    symbolic_ops: int
    neural_macs: int
    model_bytes: int
    index_bytes: int
    peak_live_ram_bytes: int
    storage_bytes: int
    storage_reads: int
    sequential_reads: int
    random_reads: int
    scheduler_cycles: int
    realization_ops: int
    interface_bytes: int
    deterministic_ops: int
    total_ops: int


@dataclass(frozen=True, slots=True)
class WorkloadProfile:
    samples: tuple[WorkloadSample, ...]
    corpus_bytes: int
    architecture_frozen: bool
    p50_storage_bytes: int
    p95_storage_bytes: int
    p50_storage_reads: int
    p95_storage_reads: int
    peak_live_ram_bytes: int
    model_bytes: int
    index_bytes: int
    symbolic_ops: int
    neural_macs: int
    scheduler_cycles: int
    realization_ops: int
    interface_bytes: int
    deterministic_share: float
    symbolic_control_share: float


@dataclass(frozen=True, slots=True)
class BackendProfile:
    backend_id: BackendId
    usable_ram_bytes: int
    cold_start_ms: float
    symbol_ns_per_op: float
    cpu_mac_ns: float
    accelerated_mac_ns: float
    mapped_neural_fraction: float
    random_read_us: float
    sequential_mb_s: float
    interface_mb_s: float
    idle_operation_mw: float
    accelerator_mw: float
    storage_mw: float
    safety_latency_factor: float
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BackendProjection:
    backend_id: BackendId
    evidence_class: Literal["analytical_estimate_not_measured"]
    p50_latency_ms: float
    p95_latency_ms: float
    p50_energy_mj: float
    p95_energy_mj: float
    peak_live_ram_bytes: int
    ram_headroom_fraction: float
    mapped_neural_fraction: float
    cold_start_ms: float
    model_bytes: int
    index_bytes: int
    p95_storage_bytes: int
    p95_storage_reads: int
    meets_latency_target: bool
    meets_ram_limit: bool
    assumptions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HardwareRecommendation:
    decision: RecommendationDecision
    reason_codes: tuple[str, ...]
    winner: BackendProjection | None
    projections: tuple[BackendProjection, ...]


CONSERVATIVE_BACKENDS: tuple[BackendProfile, ...] = (
    BackendProfile(
        backend_id=BackendId.ESP32_P4_PICO,
        usable_ram_bytes=24 * 1024 * 1024,
        cold_start_ms=900.0,
        symbol_ns_per_op=45.0,
        cpu_mac_ns=24.0,
        accelerated_mac_ns=24.0,
        mapped_neural_fraction=0.0,
        random_read_us=850.0,
        sequential_mb_s=4.0,
        interface_mb_s=4.0,
        idle_operation_mw=650.0,
        accelerator_mw=0.0,
        storage_mw=250.0,
        safety_latency_factor=2.0,
        notes=(
            "P4/C6 display board remains terminal-only.",
            "Profile represents a dedicated P4-class accessory with microSD.",
            "No neural accelerator credit is assumed.",
        ),
    ),
    BackendProfile(
        backend_id=BackendId.CORE1106,
        usable_ram_bytes=192 * 1024 * 1024,
        cold_start_ms=1250.0,
        symbol_ns_per_op=18.0,
        cpu_mac_ns=12.0,
        accelerated_mac_ns=1.8,
        mapped_neural_fraction=0.92,
        random_read_us=180.0,
        sequential_mb_s=35.0,
        interface_mb_s=20.0,
        idle_operation_mw=1300.0,
        accelerator_mw=900.0,
        storage_mw=450.0,
        safety_latency_factor=1.8,
        notes=(
            "RKNN mapping fraction is an explicit scenario assumption.",
            "256 MB physical RAM is discounted to 192 MB usable.",
        ),
    ),
    BackendProfile(
        backend_id=BackendId.IMX_RT700,
        usable_ram_bytes=16 * 1024 * 1024,
        cold_start_ms=500.0,
        symbol_ns_per_op=32.0,
        cpu_mac_ns=18.0,
        accelerated_mac_ns=3.5,
        mapped_neural_fraction=0.85,
        random_read_us=500.0,
        sequential_mb_s=12.0,
        interface_mb_s=8.0,
        idle_operation_mw=750.0,
        accelerator_mw=450.0,
        storage_mw=300.0,
        safety_latency_factor=2.0,
        notes=(
            "External-storage latency is charged.",
            "Neutron mapping is not assumed complete.",
        ),
    ),
    BackendProfile(
        backend_id=BackendId.LOW_POWER_FPGA,
        usable_ram_bytes=64 * 1024 * 1024,
        cold_start_ms=300.0,
        symbol_ns_per_op=10.0,
        cpu_mac_ns=10.0,
        accelerated_mac_ns=1.2,
        mapped_neural_fraction=0.95,
        random_read_us=300.0,
        sequential_mb_s=25.0,
        interface_mb_s=20.0,
        idle_operation_mw=1800.0,
        accelerator_mw=1000.0,
        storage_mw=400.0,
        safety_latency_factor=2.2,
        notes=(
            "Representative architecture only; no specific FPGA is implied.",
            "Projection has no purchase validity until kernels and interfaces freeze.",
        ),
    ),
)


def sample_from_result(result: SystemResult) -> WorkloadSample:
    operations = result.trace.operations
    symbolic = sum(
        operation.integer_ops
        for operation in operations
        if operation.category
        in {"parse", "index", "reason", "schedule", "realize", "verify"}
    )
    deterministic_ops = sum(
        operation.integer_ops + operation.neural_macs
        for operation in operations
        if operation.deterministic
    )
    total_ops = sum(
        operation.integer_ops + operation.neural_macs for operation in operations
    )
    return WorkloadSample(
        symbolic_ops=symbolic,
        neural_macs=sum(operation.neural_macs for operation in operations),
        model_bytes=result.model_bytes,
        index_bytes=result.index_bytes,
        peak_live_ram_bytes=result.peak_working_ram_bytes,
        storage_bytes=sum(operation.bytes_read for operation in operations),
        storage_reads=sum(operation.storage_reads for operation in operations),
        sequential_reads=sum(
            operation.storage_reads
            for operation in operations
            if operation.read_pattern == "sequential"
        ),
        random_reads=sum(
            operation.storage_reads
            for operation in operations
            if operation.read_pattern == "random"
        ),
        scheduler_cycles=sum(operation.scheduler_cycles for operation in operations),
        realization_ops=sum(operation.realization_ops for operation in operations),
        interface_bytes=sum(operation.interface_bytes for operation in operations),
        deterministic_ops=deterministic_ops,
        total_ops=total_ops,
    )


def build_workload_profile(
    results: tuple[SystemResult, ...],
    *,
    corpus_bytes: int,
    architecture_frozen: bool,
) -> WorkloadProfile:
    if not results:
        raise ValueError("at least one measured emulator result is required")
    samples = tuple(sample_from_result(result) for result in results)
    total_ops = sum(sample.total_ops for sample in samples)
    deterministic_ops = sum(sample.deterministic_ops for sample in samples)
    symbolic = sum(sample.symbolic_ops for sample in samples)
    neural = sum(sample.neural_macs for sample in samples)
    return WorkloadProfile(
        samples=samples,
        corpus_bytes=corpus_bytes,
        architecture_frozen=architecture_frozen,
        p50_storage_bytes=_percentile(
            tuple(sample.storage_bytes for sample in samples), 0.50
        ),
        p95_storage_bytes=_percentile(
            tuple(sample.storage_bytes for sample in samples), 0.95
        ),
        p50_storage_reads=_percentile(
            tuple(sample.storage_reads for sample in samples), 0.50
        ),
        p95_storage_reads=_percentile(
            tuple(sample.storage_reads for sample in samples), 0.95
        ),
        peak_live_ram_bytes=max(sample.peak_live_ram_bytes for sample in samples),
        model_bytes=max(sample.model_bytes for sample in samples),
        index_bytes=max(sample.index_bytes for sample in samples),
        symbolic_ops=symbolic,
        neural_macs=neural,
        scheduler_cycles=sum(sample.scheduler_cycles for sample in samples),
        realization_ops=sum(sample.realization_ops for sample in samples),
        interface_bytes=sum(sample.interface_bytes for sample in samples),
        deterministic_share=deterministic_ops / max(1, total_ops),
        symbolic_control_share=symbolic / max(1, symbolic + neural),
    )


def project_backend(
    workload: WorkloadProfile,
    backend: BackendProfile,
    *,
    latency_target_ms: float,
) -> BackendProjection:
    """Project latency and energy from p50/p95 workload samples.

    Time coefficients are intentionally direct per-operation assumptions rather
    than transformations of advertised TOPS.
    """

    sample_latencies = tuple(
        _sample_latency_ms(sample, backend) for sample in workload.samples
    )
    sample_energies = tuple(
        _sample_energy_mj(sample, backend) for sample in workload.samples
    )
    p50_latency = _float_percentile(sample_latencies, 0.50)
    p95_latency = (
        _float_percentile(sample_latencies, 0.95) * backend.safety_latency_factor
    )
    p50_energy = _float_percentile(sample_energies, 0.50)
    p95_energy = (
        _float_percentile(sample_energies, 0.95) * backend.safety_latency_factor
    )
    peak_ram = (
        workload.peak_live_ram_bytes + workload.model_bytes + workload.index_bytes
    )
    headroom = max(0.0, (backend.usable_ram_bytes - peak_ram) / backend.usable_ram_bytes)
    return BackendProjection(
        backend_id=backend.backend_id,
        evidence_class="analytical_estimate_not_measured",
        p50_latency_ms=p50_latency,
        p95_latency_ms=p95_latency,
        p50_energy_mj=p50_energy,
        p95_energy_mj=p95_energy,
        peak_live_ram_bytes=peak_ram,
        ram_headroom_fraction=headroom,
        mapped_neural_fraction=(
            backend.mapped_neural_fraction if workload.neural_macs else 0.0
        ),
        cold_start_ms=backend.cold_start_ms,
        model_bytes=workload.model_bytes,
        index_bytes=workload.index_bytes,
        p95_storage_bytes=workload.p95_storage_bytes,
        p95_storage_reads=workload.p95_storage_reads,
        meets_latency_target=p95_latency <= latency_target_ms,
        meets_ram_limit=peak_ram <= int(backend.usable_ram_bytes * 0.8),
        assumptions=(
            *backend.notes,
            f"p95 includes {backend.safety_latency_factor:g}x uncertainty factor.",
            "Energy is workload-time integration, not a board measurement.",
        ),
    )


def project_all_backends(
    workload: WorkloadProfile,
    *,
    latency_target_ms: float,
) -> tuple[BackendProjection, ...]:
    return tuple(
        project_backend(workload, backend, latency_target_ms=latency_target_ms)
        for backend in CONSERVATIVE_BACKENDS
    )


def recommend_backend(
    workload: WorkloadProfile,
    *,
    latency_target_ms: float,
    accuracy_targets_met: bool,
    bounded_reads_demonstrated: bool,
    neural_mapping_validated: bool = False,
) -> HardwareRecommendation:
    """Apply the user's purchase conditions without substituting TOPS claims."""

    projections = project_all_backends(
        workload,
        latency_target_ms=latency_target_ms,
    )
    by_id = {projection.backend_id: projection for projection in projections}
    if not accuracy_targets_met or not bounded_reads_demonstrated:
        return HardwareRecommendation(
            decision=RecommendationDecision.ARCHITECTURE_FAILED,
            reason_codes=tuple(
                reason
                for condition, reason in (
                    (accuracy_targets_met, "ACCURACY_TARGETS_NOT_MET"),
                    (bounded_reads_demonstrated, "BOUNDED_READS_NOT_DEMONSTRATED"),
                )
                if not condition
            ),
            winner=None,
            projections=projections,
        )

    p4 = by_id[BackendId.ESP32_P4_PICO]
    core = by_id[BackendId.CORE1106]
    fpga = by_id[BackendId.LOW_POWER_FPGA]
    rt700 = by_id[BackendId.IMX_RT700]

    neural_major = workload.neural_macs > workload.symbolic_ops
    neural_share = workload.neural_macs / max(
        1, workload.neural_macs + workload.symbolic_ops
    )
    core_latency_gain = p4.p95_latency_ms / max(core.p95_latency_ms, 1e-9)
    core_energy_gain = 1.0 - core.p95_energy_mj / max(p4.p95_energy_mj, 1e-9)
    core_condition = (
        neural_major
        and neural_mapping_validated
        and core.mapped_neural_fraction > 0.90
        and core_latency_gain >= 1.5
        and core_energy_gain >= 0.20
        and core.meets_ram_limit
        and core.meets_latency_target
    )
    if core_condition:
        return HardwareRecommendation(
            decision=RecommendationDecision.CORE1106,
            reason_codes=(
                "NEURAL_KERNELS_MAJOR_RUNTIME_SHARE",
                "RKNN_MAPPING_OVER_90_PERCENT_VALIDATED",
                "PROJECTED_LATENCY_GAIN_AT_LEAST_1_5X",
                "PROJECTED_ENERGY_GAIN_AT_LEAST_20_PERCENT",
            ),
            winner=core,
            projections=projections,
        )

    neural_material_gain = (
        neural_mapping_validated
        and neural_share >= 0.10
        and (core_latency_gain >= 1.2 or core_energy_gain >= 0.10)
    )
    p4_condition = (
        workload.symbolic_control_share > 0.5
        and p4.meets_ram_limit
        and p4.meets_latency_target
        and not neural_material_gain
    )
    if p4_condition:
        return HardwareRecommendation(
            decision=RecommendationDecision.ESP32_P4_PICO,
            reason_codes=(
                "SYMBOLIC_CONTROL_WORKLOAD_DOMINATES",
                "MEMORY_FITS_WITH_20_PERCENT_RESERVE",
                "PROJECTED_P95_MEETS_TARGET",
                "NPU_DOES_NOT_MATERIALLY_IMPROVE_END_TO_END",
            ),
            winner=p4,
            projections=projections,
        )

    stable_deterministic_kernels = workload.deterministic_share >= 0.90
    fpga_condition = (
        stable_deterministic_kernels
        and workload.architecture_frozen
        and fpga.meets_ram_limit
        and fpga.meets_latency_target
    )
    if fpga_condition:
        return HardwareRecommendation(
            decision=RecommendationDecision.LOW_POWER_FPGA,
            reason_codes=(
                "STABLE_REPEATABLE_DETERMINISTIC_KERNELS_DOMINATE",
                "ARCHITECTURE_AND_OPERATION_SET_FROZEN",
            ),
            winner=fpga,
            projections=projections,
        )

    rt700_condition = (
        rt700.meets_ram_limit
        and rt700.meets_latency_target
        and workload.neural_macs > 0
        and 0.10 <= neural_share <= 0.50
    )
    if rt700_condition:
        return HardwareRecommendation(
            decision=RecommendationDecision.IMX_RT700,
            reason_codes=(
                "MIXED_SYMBOLIC_AND_COMPACT_NEURAL_WORKLOAD",
                "PROJECTED_P95_AND_MEMORY_MEET_TARGET",
                "CORE1106_PURCHASE_CONDITIONS_NOT_MET",
            ),
            winner=rt700,
            projections=projections,
        )

    return HardwareRecommendation(
        decision=RecommendationDecision.NO_PURCHASE,
        reason_codes=(
            "NO_BACKEND_SATISFIES_ITS_PURCHASE_CONDITIONS",
            "REQUIRE_BOARD_MEASUREMENTS_OR_ARCHITECTURE_FREEZE",
        ),
        winner=None,
        projections=projections,
    )


def _sample_latency_ms(sample: WorkloadSample, backend: BackendProfile) -> float:
    mapped = sample.neural_macs * backend.mapped_neural_fraction
    fallback = sample.neural_macs - mapped
    compute_ns = (
        sample.symbolic_ops * backend.symbol_ns_per_op
        + mapped * backend.accelerated_mac_ns
        + fallback * backend.cpu_mac_ns
    )
    storage_ms = (
        sample.random_reads * backend.random_read_us / 1000.0
        + sample.storage_bytes / max(1.0, backend.sequential_mb_s * 1_000_000.0)
        * 1000.0
    )
    interface_ms = (
        sample.interface_bytes
        / max(1.0, backend.interface_mb_s * 1_000_000.0)
        * 1000.0
    )
    return compute_ns / 1_000_000.0 + storage_ms + interface_ms


def _sample_energy_mj(sample: WorkloadSample, backend: BackendProfile) -> float:
    latency_ms = _sample_latency_ms(sample, backend)
    neural_share = sample.neural_macs / max(
        1, sample.neural_macs + sample.symbolic_ops
    )
    storage_share = min(1.0, sample.storage_reads / 8.0)
    power_mw = (
        backend.idle_operation_mw
        + backend.accelerator_mw * neural_share
        + backend.storage_mw * storage_share
    )
    return power_mw * latency_ms / 1000.0


def _percentile(values: tuple[int, ...], quantile: float) -> int:
    return math.ceil(_float_percentile(tuple(float(value) for value in values), quantile))


def _float_percentile(values: tuple[float, ...], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight
