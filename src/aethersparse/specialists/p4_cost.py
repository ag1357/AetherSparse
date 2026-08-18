"""Accelerated analytical ESP32-P4 cost model; it never sleeps or throttles."""

from __future__ import annotations

from pydantic import Field

from aethersparse.controller.models import FrozenModel

V11_P4_CALIBRATION_ID = "aethercore.v11-p4-scalar-reference.v1"


class P4Assumptions(FrozenModel):
    clock_mhz: int = Field(ge=200, le=400)
    integer_ops_per_cycle: float = Field(gt=0.0)
    macs_per_cycle: float = Field(gt=0.0)
    psram_bandwidth_mb_s: float = Field(gt=0.0)
    flash_bandwidth_mb_s: float = Field(gt=0.0)
    psram_random_access_us: float = Field(ge=0.0)
    flash_random_access_us: float = Field(ge=0.0)


class P4OperationCost(FrozenModel):
    operation_id: str
    integer_operations: int = Field(ge=0)
    macs: int = Field(ge=0)
    memory_bytes: int = Field(ge=0)
    psram_bytes: int = Field(ge=0)
    flash_bytes: int = Field(ge=0)
    psram_accesses: int = Field(ge=0)
    flash_accesses: int = Field(ge=0)
    random_psram_reads: int = Field(ge=0)
    random_flash_reads: int = Field(ge=0)
    sequential_reads: int = Field(ge=0)
    scratch_ram_bytes: int = Field(ge=0)
    model_bytes: int = Field(ge=0)


class P4Projection(FrozenModel):
    evidence_class: str = "analytical_projection_not_hardware_measurement"
    clock_mhz: int
    compute_ms: float
    psram_transfer_ms: float
    flash_transfer_ms: float
    random_access_ms: float
    virtual_latency_ms: float
    peak_workspace_ram_bytes: int
    model_bytes: int


def project_p4(costs: tuple[P4OperationCost, ...], assumptions: P4Assumptions) -> P4Projection:
    integer_operations = sum(item.integer_operations for item in costs)
    macs = sum(item.macs for item in costs)
    cycles = (
        integer_operations / assumptions.integer_ops_per_cycle + macs / assumptions.macs_per_cycle
    )
    compute_ms = cycles / (assumptions.clock_mhz * 1_000.0)
    psram_bytes = sum(item.psram_bytes for item in costs)
    flash_bytes = sum(item.flash_bytes for item in costs)
    psram_ms = psram_bytes / (assumptions.psram_bandwidth_mb_s * 1_000_000.0) * 1_000.0
    flash_ms = flash_bytes / (assumptions.flash_bandwidth_mb_s * 1_000_000.0) * 1_000.0
    random_ms = sum(item.random_psram_reads for item in costs) * (
        assumptions.psram_random_access_us / 1_000.0
    ) + sum(item.random_flash_reads for item in costs) * (
        assumptions.flash_random_access_us / 1_000.0
    )
    return P4Projection(
        clock_mhz=assumptions.clock_mhz,
        compute_ms=compute_ms,
        psram_transfer_ms=psram_ms,
        flash_transfer_ms=flash_ms,
        random_access_ms=random_ms,
        virtual_latency_ms=compute_ms + psram_ms + flash_ms + random_ms,
        peak_workspace_ram_bytes=max((item.scratch_ram_bytes for item in costs), default=0),
        model_bytes=sum(item.model_bytes for item in costs),
    )


def clock_sensitivity(
    costs: tuple[P4OperationCost, ...],
    *,
    integer_ops_per_cycle: float,
    macs_per_cycle: float,
    psram_bandwidth_mb_s: float,
    flash_bandwidth_mb_s: float,
    psram_random_access_us: float,
    flash_random_access_us: float,
) -> tuple[P4Projection, ...]:
    return tuple(
        project_p4(
            costs,
            P4Assumptions(
                clock_mhz=clock,
                integer_ops_per_cycle=integer_ops_per_cycle,
                macs_per_cycle=macs_per_cycle,
                psram_bandwidth_mb_s=psram_bandwidth_mb_s,
                flash_bandwidth_mb_s=flash_bandwidth_mb_s,
                psram_random_access_us=psram_random_access_us,
                flash_random_access_us=flash_random_access_us,
            ),
        )
        for clock in (200, 300, 400)
    )


def v11_reference_assumptions() -> dict[str, P4Assumptions]:
    """Return the unchanged v11 200/300/400 MHz analytical scenarios.

    ``flash_*`` remains the historical digital-twin name for parameterized
    external storage.  These values are not an eMMC specification or hardware
    measurement.  Callers must report them as the v11 reference assumptions.
    """

    return {
        "conservative_200mhz": P4Assumptions(
            clock_mhz=200,
            integer_ops_per_cycle=1.0,
            macs_per_cycle=1.0,
            psram_bandwidth_mb_s=20.0,
            flash_bandwidth_mb_s=5.0,
            psram_random_access_us=2.0,
            flash_random_access_us=100.0,
        ),
        "nominal_300mhz": P4Assumptions(
            clock_mhz=300,
            integer_ops_per_cycle=1.0,
            macs_per_cycle=1.0,
            psram_bandwidth_mb_s=40.0,
            flash_bandwidth_mb_s=10.0,
            psram_random_access_us=1.0,
            flash_random_access_us=60.0,
        ),
        "optimistic_plausible_400mhz": P4Assumptions(
            clock_mhz=400,
            integer_ops_per_cycle=1.0,
            macs_per_cycle=1.0,
            psram_bandwidth_mb_s=80.0,
            flash_bandwidth_mb_s=20.0,
            psram_random_access_us=0.5,
            flash_random_access_us=30.0,
        ),
    }
