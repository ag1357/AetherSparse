from __future__ import annotations

import json
from pathlib import Path

import pytest

from aethersparse.edge_runtime.deployment_v15 import (
    CacheRequest,
    DeploymentContractError,
    DeterministicRegionCache,
    ElasticResidencyController,
    EvidenceDirectory,
    EvidenceEntry,
    EvidenceLayout,
    HardwareCalibration,
    ResidencyDemand,
    projected_pack_v2_controls,
)


def _entries() -> tuple[EvidenceEntry, ...]:
    return (
        EvidenceEntry(0, 0, 11, 1),
        EvidenceEntry(2, 11, 7, 3),
        EvidenceEntry(7, 18, 19, 2),
        EvidenceEntry(260, 37, 4, 5),
    )


def test_all_pack_v2_controls_preserve_exact_logical_output() -> None:
    baseline = EvidenceDirectory(
        _entries(), entity_capacity=300, layout=EvidenceLayout.FLAT_PAGED
    )
    expected = [baseline.lookup(index)[0] for index in range(300)]
    for layout in EvidenceLayout:
        candidate = EvidenceDirectory(_entries(), entity_capacity=300, layout=layout)
        assert [candidate.lookup(index)[0] for index in range(300)] == expected
        image = candidate.encode()
        assert image == candidate.encode()
        assert [candidate.lookup_encoded(image, index) for index in range(300)] == expected
    controls = {item["layout"]: item for item in projected_pack_v2_controls()}
    assert controls[EvidenceLayout.DIRECT_COMPACT_RESIDENT][
        "resident_directory_bytes"
    ] == 3_311_868
    assert controls[EvidenceLayout.DIRECT_COMPACT_RESIDENT][
        "evidence_media_misses_total"
    ] == 0
    assert controls[EvidenceLayout.FLAT_PAGED]["evidence_media_misses_total"] == 23_311


def test_committed_pack_and_cache_reports_match_executable_projection() -> None:
    root = Path(__file__).parents[2]
    pack = json.loads(
        (root / "reports/droid/v15/pack-v2-qualification.json").read_text(encoding="utf-8")
    )
    cache = json.loads(
        (root / "reports/droid/v15/cache-qualification.json").read_text(encoding="utf-8")
    )
    projected = {str(item["layout"]): item for item in projected_pack_v2_controls()}
    selected = projected["direct_compact_resident"]
    assert pack["selection"]["performance"] == "direct_compact_resident"
    assert pack["controls"][2]["resident_directory_bytes"] == selected[
        "resident_directory_bytes"
    ]
    assert pack["controls"][2]["modeled_mean_address_ms"] == pytest.approx(
        selected["modeled_mean_address_ms"]
    )
    assert cache["selection"]["projected_resident_bytes"] == (
        2_061_221 + 1_048_576 + 3_311_868
    )


@pytest.mark.parametrize("cache_kib", [256, 1024, 2048, 4096, 8192])
def test_region_cache_ladder_is_deterministic_and_pinning_is_fail_closed(
    cache_kib: int,
) -> None:
    pages = cache_kib * 1024 // 4096
    first = DeterministicRegionCache({"address": pages // 2, "evidence": pages // 2})
    second = DeterministicRegionCache({"address": pages // 2, "evidence": pages // 2})
    trace = tuple(
        CacheRequest("evidence" if index % 3 == 0 else "address", index % 97, index == 0)
        for index in range(500)
    )
    for request in trace:
        first.access(request)
        second.access(request)
    assert (first.hits, first.misses, first.pressure) == (
        second.hits,
        second.misses,
        second.pressure,
    )
    tiny = DeterministicRegionCache({"evidence": 1})
    assert not tiny.access(CacheRequest("evidence", 1, pinned=True))
    assert not tiny.access(CacheRequest("evidence", 2))
    assert tiny.pressure == 1
    assert tiny.access(CacheRequest("evidence", 1))


def test_hardware_calibration_is_keyed_to_media_bus_and_pack_generation() -> None:
    baseline = HardwareCalibration(
        "Waveshare ESP32-P4-WIFI6 SKU 32020",
        "v1.3",
        32 * 1024 * 1024,
        "USD00",
        "SDMMC-4bit-20MHz",
        "v14",
        1.93 * 1_000_000,
        44_085.9,
        36_859,
        141_480,
    )
    assert baseline.compatible_with(baseline)
    changed = HardwareCalibration(**{**baseline.__dict__, "storage_identity": "Kingston-A2"})
    assert not baseline.compatible_with(changed)


def test_elastic_controller_protects_runtime_and_never_weakens_semantics() -> None:
    controller = ElasticResidencyController(
        psram_bytes=32 * 1024 * 1024, protected_bytes=4 * 1024 * 1024
    )
    allocation = controller.allocate(
        ResidencyDemand(
            knowledge_cache=16 * 1024 * 1024,
            evidence_cache=8 * 1024 * 1024,
            specialist_weights=4 * 1024 * 1024,
            source_cache=8 * 1024 * 1024,
            session_working=2 * 1024 * 1024,
        )
    )
    assert allocation.protected_bytes == 4 * 1024 * 1024
    assigned = sum(
        (
            allocation.knowledge_cache,
            allocation.evidence_cache,
            allocation.specialist_weights,
            allocation.source_cache,
            allocation.session_working,
        )
    )
    assert assigned <= allocation.elastic_bytes
    assert allocation.session_working == 2 * 1024 * 1024
    assert allocation.evidence_cache == 8 * 1024 * 1024
    selected = controller.choose_specialist(
        requested_capability="temporal",
        implementations=(
            {
                "specialist_id": "cold-fast",
                "capability": "temporal",
                "semantic_equivalence": True,
                "load_latency_us": 10,
                "compute_latency_us": 5,
            },
            {
                "specialist_id": "resident",
                "capability": "temporal",
                "semantic_equivalence": True,
                "load_latency_us": 0,
                "compute_latency_us": 20,
            },
            {
                "specialist_id": "weaker",
                "capability": "temporal",
                "semantic_equivalence": False,
                "load_latency_us": 0,
                "compute_latency_us": 1,
            },
        ),
    )
    assert selected == "cold-fast"
    with pytest.raises(DeploymentContractError, match="equivalent"):
        controller.choose_specialist(
            requested_capability="quotation",
            implementations=(
                {
                    "specialist_id": "weaker",
                    "capability": "quotation",
                    "semantic_equivalence": False,
                    "load_latency_us": 0,
                    "compute_latency_us": 1,
                },
            ),
        )
