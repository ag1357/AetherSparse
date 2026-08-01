from __future__ import annotations

from aethersparse.v050.edge import QueryWorkload, build_flat_workload_profile, select_hardware
from aethersparse.v050.gates import HardwareDecision


def _sample(storage_bytes: int = 64_000, macs: int = 0) -> QueryWorkload:
    return QueryWorkload(
        source_bytes=storage_bytes,
        index_bytes=16_000,
        source_blocks=4,
        index_blocks=2,
        deterministic_ops=80_000,
        neural_macs=macs,
        model_bytes=1024 if macs else 0,
        peak_active_ram_bytes=900_000,
        interface_bytes=2048,
        measured_host_latency_ms=12.5,
        measured_physical_read_bytes=storage_bytes,
    )


def test_flat_profile_reports_measured_percentiles() -> None:
    profile = build_flat_workload_profile((_sample(32_000), _sample(64_000), _sample(96_000)))
    assert profile.query_count == 3
    assert profile.p50_storage_bytes == 80_000
    assert profile.p95_storage_bytes == 112_000
    assert profile.total_physical_read_bytes == 192_000
    assert profile.evidence_class == "measured_flat_structured_workload"


def test_purchase_fails_closed_before_architecture_gate() -> None:
    profile = build_flat_workload_profile((_sample(),))
    outcome = select_hardware(
        profile,
        architecture_qualified=False,
        bounded_reads_measured=True,
        architecture_frozen=False,
        neural_mapping_measured=False,
    )
    assert outcome.decision is HardwareDecision.NO_PURCHASE
    assert "ARCHITECTURE_GATE_NOT_MET" in outcome.reasons


def test_p4_is_reference_not_final_without_board_measurement() -> None:
    profile = build_flat_workload_profile((_sample(),))
    outcome = select_hardware(
        profile,
        architecture_qualified=True,
        bounded_reads_measured=True,
        architecture_frozen=False,
        neural_mapping_measured=False,
    )
    assert outcome.decision is HardwareDecision.P4_REFERENCE
