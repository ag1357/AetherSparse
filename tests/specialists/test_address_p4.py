from __future__ import annotations

import pytest
from pydantic import ValidationError

from aethersparse.specialists.address_p4 import AddressQueryCost, project_address_cost
from aethersparse.specialists.p4_cost import (
    V11_P4_CALIBRATION_ID,
    v11_reference_assumptions,
)


def _cost() -> AddressQueryCost:
    return AddressQueryCost(
        operation_id="claim-address.direct.v1",
        internal_sram_dma_peak_bytes=4096,
        psram_resident_posting_bytes=8192,
        psram_peak_known_allocation_bytes=8192,
        fst_payload_bytes_read=0,
        posting_payload_bytes_read=1024,
        query_key_bytes_processed=24,
        bq_payload_bytes_read=0,
        pq_payload_bytes_read=0,
        int8_payload_bytes_read=0,
        source_region_payload_bytes_read=512,
        psram_page_aligned_transfer_bytes=4096,
        external_page_aligned_transfer_bytes=4096,
        psram_random_page_reads=1,
        psram_sequential_page_reads=0,
        external_random_page_reads=1,
        external_sequential_page_reads=0,
        formula_derived_integer_operations=500,
        xor_popcount_operations=0,
        simd_operations=0,
        neural_macs=0,
        candidates_before_address=32,
        candidates_after_address=4,
        candidates_after_cap=4,
        active_parameters=0,
        model_bytes=0,
    )


def test_v11_reference_calibration_is_named_and_unchanged() -> None:
    assumptions = v11_reference_assumptions()

    assert V11_P4_CALIBRATION_ID == "aethercore.v11-p4-scalar-reference.v1"
    assert tuple(item.clock_mhz for item in assumptions.values()) == (200, 300, 400)
    assert assumptions["nominal_300mhz"].flash_bandwidth_mb_s == 10.0
    assert assumptions["nominal_300mhz"].flash_random_access_us == 60.0


def test_address_projection_separates_counted_storage_and_p4_projection() -> None:
    cost = _cost()
    projected = project_address_cost(cost, v11_reference_assumptions()["nominal_300mhz"])

    assert cost.psram_page_aligned_transfer_bytes == 4096
    assert cost.external_page_aligned_transfer_bytes == 4096
    assert projected.clock_mhz == 300
    assert projected.psram_page_aligned_transfer_bytes == 4096
    assert projected.external_page_aligned_transfer_bytes == 4096
    assert projected.random_page_reads == 2
    assert projected.virtual_latency_ms > projected.compute_ms
    assert projected.evidence_class == "analytical_projection_not_hardware_measurement"
    assert "not_emmc" in projected.storage_scope


def test_candidate_funnel_may_not_add_candidates_at_cap() -> None:
    with pytest.raises(ValidationError, match="may not add"):
        _cost().model_copy(
            update={"candidates_after_address": 3, "candidates_after_cap": 4}
        ).model_validate(
            {
                **_cost().model_dump(),
                "candidates_after_address": 3,
                "candidates_after_cap": 4,
            }
        )


def test_physical_page_transfer_must_match_per_channel_page_counts() -> None:
    payload = _cost().model_dump()
    payload["psram_page_aligned_transfer_bytes"] = 8192

    with pytest.raises(ValidationError, match="page count"):
        AddressQueryCost.model_validate(payload)


def test_direct_layout_rejects_unaccounted_retrieval_channel_payload() -> None:
    payload = _cost().model_dump()
    payload["bq_payload_bytes_read"] = 16

    with pytest.raises(ValidationError, match="absent retrieval channels"):
        AddressQueryCost.model_validate(payload)
