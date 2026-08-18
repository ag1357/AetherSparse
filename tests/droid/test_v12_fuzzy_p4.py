from __future__ import annotations

from aethersparse.controller.fuzzy_address import AddressSurfaceRecord, FuzzyAddressIndex
from aethersparse.controller.semantic_address import canonical_entity_id
from scripts.droid.v12_fuzzy_address_qualify import _Case, _selected_exact_char_p4


def _index() -> FuzzyAddressIndex:
    return FuzzyAddressIndex(
        (
            AddressSurfaceRecord(
                surface="Alpha Centauri",
                entity_id=canonical_entity_id("Alpha Centauri"),
                canonical_title="Alpha Centauri",
                support_count=2,
                source_document_count=2,
                source_document_ids=("doc:1", "doc:2"),
                support_provenance_ids=("obs:1", "obs:2"),
                source_provenance=("test-pack",),
            ),
            AddressSurfaceRecord(
                surface="Bette Davis",
                entity_id=canonical_entity_id("Bette Davis"),
                canonical_title="Bette Davis",
                source_document_ids=("doc:3",),
                support_provenance_ids=("obs:3",),
                source_provenance=("test-pack",),
            ),
        )
    )


def test_selected_fuzzy_exact_char_p4_is_deterministic_and_explicit() -> None:
    cases = (
        _Case("case:1", "development", "Where is Alpha Centauri?", ()),
        _Case("case:2", "tuning", "Who was Bette Davs?", ()),
    )
    first = _selected_exact_char_p4(
        _index(), cases, char_threshold=0.52, resident_index_bytes=12_345
    )
    second = _selected_exact_char_p4(
        _index(), cases, char_threshold=0.52, resident_index_bytes=12_345
    )

    assert first == second
    assert first["case_count"] == 2
    assert first["selected_channels"] == ["fuzzy_normalized_exact", "char_ngram"]
    assert "simhash_lsh_rejected" in first["inactive_channels"]
    assert first["evidence_class"] == "analytical_projection_not_hardware_measurement"
    distributions = first["distributions"]
    assert distributions["estimated_bytes_touched"]["p95"] > 0
    assert distributions["posting_entries_read"]["p95"] >= 0
    assert distributions["ideal_packed_4kb_pages"]["p95"] >= 1
    assert distributions["random_logical_index_reads"]["p95"] > 0
    assert distributions["xor_popcount_operations"]["p50"] == 0
    assert distributions["xor_popcount_operations"]["p95"] == 0
    assert first["memory"]["resident_index_bytes"] == 12_345
    assert first["memory"]["resident_plus_peak_working_bytes"] > 12_345
    assert first["cap_saturation"]["global_address_cap"] == {"count": 0, "rate": 0.0}

    scenarios = first["p4_scenarios"]
    latency_200 = scenarios["conservative_200mhz"]["latency_ms"]["virtual_latency_ms"]["p95"]
    latency_300 = scenarios["nominal_300mhz"]["latency_ms"]["virtual_latency_ms"]["p95"]
    latency_400 = scenarios["optimistic_plausible_400mhz"]["latency_ms"]["virtual_latency_ms"][
        "p95"
    ]
    assert latency_400 < latency_300 < latency_200
    assert scenarios["nominal_300mhz"]["nominal"] is True
    assert first["storage"]["external_storage_projection"].startswith("not_projected")
    assert first["storage"]["external_bandwidth_mb_s"] is None
    assert first["storage"]["external_random_access_us"] is None
    assert first["logical_io"]["physical_random_pages"] is None
    assert first["logical_io"]["physical_sequential_pages"] is None
