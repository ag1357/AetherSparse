from __future__ import annotations

import bz2
import hashlib
from pathlib import Path

from aethersparse.real_corpus.builder import PackSettings, build_pack
from aethersparse.substrate import (
    SourcePage,
    StructuredSubstrateBuilder,
    SubstrateMetadata,
    write_flat_binary_pack,
)
from aethersparse.v050.gates import HardwareDecision
from aethersparse.v050.profiling import (
    FrozenHardwareCriteria,
    ProfileQuery,
    build_edge_qualification_report,
    nearest_rank_percentile,
    profile_binary_pack,
    profile_sqlite_pack,
)

XML = """<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/">
<page><title>Mercury</title><ns>0</ns><id>31</id>
<revision><id>301</id><timestamp>2026-08-01T00:00:00Z</timestamp><sha1>a</sha1>
<text>Mercury is the closest planet to the Sun.</text></revision></page>
</mediawiki>"""


def _query(**changes: object) -> ProfileQuery:
    values: dict[str, object] = {
        "query_id": "q:mercury",
        "text": "Which planet is closest to the Sun?",
        "title_queries": ("Mercury",),
        "alias_queries": ("Mercury",),
        "document_ids": ("simplewiki:31:301",),
        "retrieval_limit": 8,
        "deterministic_ops": 12_500,
        "neural_macs": 0,
        "model_bytes": 0,
    }
    values.update(changes)
    return ProfileQuery.model_validate(values)


def _sqlite_pack(tmp_path: Path) -> Path:
    dump = tmp_path / "tiny.xml.bz2"
    dump.write_bytes(bz2.compress(XML.encode("utf-8")))
    source = {
        "dump_date": "20260801",
        "filename": dump.name,
        "compressed_bytes": dump.stat().st_size,
        "official_sha1": "official-sha1",
        "official_md5": "official-md5",
        "sha1": "verified-sha1",
        "sha256": hashlib.sha256(dump.read_bytes()).hexdigest(),
        "md5": "verified-md5",
        "url": "https://example.invalid/tiny.xml.bz2",
        "status_url": "https://example.invalid/dumpstatus.json",
        "result": "downloaded_and_verified",
    }
    path = tmp_path / "pack.sqlite"
    build_pack(
        dump,
        path,
        source=source,
        settings=PackSettings(article_limit=1, chunk_chars=480),
    )
    return path


def _binary_pack(tmp_path: Path) -> tuple[Path, str]:
    page = SourcePage(
        page_id="31",
        namespace=0,
        revision_id="301",
        revision_timestamp="2026-08-01T00:00:00Z",
        title="Mercury",
        source_url="https://simple.wikipedia.org/?curid=31",
        license="CC-BY-SA-4.0",
        text="Mercury is the closest planet to the Sun.",
    )
    metadata = SubstrateMetadata(
        series_id="simplewiki_v050_profile_fixture",
        source_dump_id="simplewiki-20260801-pages-articles",
        source_dump_sha256="sha256:" + "1" * 64,
        parser_identity="fixture-parser",
        normalization_identity="fixture-normalizer",
        build_command="fixture",
    )
    pack = StructuredSubstrateBuilder(metadata).build((page,))
    path = tmp_path / "pack.aeth"
    write_flat_binary_pack(pack, path, shard_count=8)
    return path, pack.documents[0].document_id


def test_sqlite_profiler_records_cold_warm_physical_and_page_evidence(
    tmp_path: Path,
) -> None:
    profile = profile_sqlite_pack(
        _sqlite_pack(tmp_path),
        (_query(),),
        profile_id="simplewiki_v050_10k_sqlite",
    )

    assert profile.pack_kind == "real_corpus_sqlite"
    assert profile.pack_sha256.startswith("sha256:")
    assert profile.warm_profile.query_count == 1
    assert profile.cold_advised_profile.query_count == 1
    cold = profile.cold_advised_measurements[0]
    assert cold.cache_state == "cold_cache_advised"
    assert cold.cache_preparation.advisory_only is True
    assert cold.logical_reads.method == "sqlite_bounded_api_page_model"
    assert cold.logical_reads.sqlite_page_bytes is not None
    assert cold.logical_reads.index_blocks >= 1
    assert cold.workload.deterministic_ops == 12_500
    assert cold.peak_rss.scope == "process_high_water_mark_not_query_increment"


def test_binary_profiler_counts_verified_flat_sections_not_cells(tmp_path: Path) -> None:
    path, document_id = _binary_pack(tmp_path)
    profile = profile_binary_pack(
        path,
        (_query(document_ids=(document_id,)),),
        profile_id="simplewiki_v050_10k_binary",
    )

    measurement = profile.cold_advised_measurements[0]
    assert profile.pack_kind == "flat_structured_binary"
    assert measurement.logical_reads.method == "verified_flat_binary_section_reads"
    assert measurement.logical_reads.source_blocks >= 1
    assert measurement.logical_reads.index_blocks >= 1
    assert measurement.workload.total_storage_bytes < profile.pack_bytes
    assert "cell" not in profile.model_dump_json().casefold()


def test_hardware_report_fails_closed_without_frozen_architecture_gate(
    tmp_path: Path,
) -> None:
    path, document_id = _binary_pack(tmp_path)
    profile = profile_binary_pack(
        path,
        (_query(document_ids=(document_id,)),),
        profile_id="decision-50k",
    )
    criteria = FrozenHardwareCriteria(
        criteria_id="V050_HARDWARE_GATE_R1",
        decision_profile_id="decision-50k",
        architecture_qualified=False,
    )
    report = build_edge_qualification_report(
        (profile,), criteria, criteria_sha256="sha256:" + "2" * 64
    )

    assert report.hardware_outcome.decision is HardwareDecision.NO_PURCHASE
    assert "ARCHITECTURE_GATE_NOT_MET" in report.hardware_outcome.reasons
    assert report.board_measurements_present is False
    assert report.topology_excluded is True
    assert all(
        projection.evidence_class
        == "analytical_projection_from_flat_workload_measurements"
        for projection in report.hardware_outcome.projections
    )


def test_nearest_rank_percentile_is_reproducible() -> None:
    assert nearest_rank_percentile((1.0, 7.0, 3.0, 9.0), 0.5) == 3.0
    assert nearest_rank_percentile((1.0, 7.0, 3.0, 9.0), 0.95) == 9.0
