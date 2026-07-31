from __future__ import annotations

import bz2
from pathlib import Path

from aethersparse.cells.models import CellKind
from aethersparse.cells.qualification import compare_topologies
from aethersparse.cells.retrieval import TwoLevelCellRetriever
from aethersparse.cells.router import CognitiveCellRouter
from aethersparse.cells.topology import CognitiveCellBuilder
from aethersparse.traversal.corpus import CorpusStore

XML = """<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/">
<page><title>Moon</title><ns>0</ns><id>1</id><revision><id>11</id>
<text>'''Moon''' is Earth's satellite. It affects [[Tide]].
[[Category:Astronomy]]</text></revision></page>
<page><title>Tide</title><ns>0</ns><id>2</id><revision><id>12</id>
<text>A tide is the rise and fall of sea level caused mainly by the [[Moon]].
[[Category:Oceanography]]</text></revision></page>
</mediawiki>"""


def _store(tmp_path: Path) -> CorpusStore:
    dump = tmp_path / "tiny.xml.bz2"
    dump.write_bytes(bz2.compress(XML.encode()))
    store = CorpusStore(tmp_path / "corpus.sqlite")
    store.ingest_mediawiki(dump, chunk_chars=200)
    return store


def test_topologies_are_bounded_and_generated_ids_fail_closed(tmp_path: Path) -> None:
    builder = CognitiveCellBuilder(_store(tmp_path), max_documents=8)
    cells = builder.build(CellKind.ENTITY_COMMUNITY)
    assert cells and all(len(cell.document_ids) <= 8 for cell in cells)
    router = CognitiveCellRouter(cells)
    assert router.validate_predictions((cells[0].cell_id, "cell:invalid")) == (cells[0].cell_id,)
    routes = router.route(
        "How are the Moon and tides related?", predicted_cell_ids=("cell:invalid",)
    )
    assert routes and all(route.cell_id != "cell:invalid" for route in routes)
    assert any("Moon" in cell.entity_aliases for cell in cells)


def test_hybrid_identity_and_alias_matching_are_topology_safe(tmp_path: Path) -> None:
    builder = CognitiveCellBuilder(_store(tmp_path), max_documents=8)
    hybrids = builder.build(CellKind.HYBRID)
    assert hybrids
    assert all(cell.kind is CellKind.HYBRID for cell in hybrids)
    assert all(cell.cell_id.startswith("cell:hybrid:") for cell in hybrids)

    moon_cell = builder._cell(
        CellKind.CATEGORY, "astronomy", {builder.store.title_search("Moon")[0]["document_id"]}
    )
    moon_cell = moon_cell.model_copy(update={"entity_aliases": ("moon",)})
    router = CognitiveCellRouter([moon_cell])
    assert router.route("A moon is visible")[0].exact_alias == 1.0
    assert router.route("A moonstone is visible")[0].exact_alias == 0.0
    assert router.route("", use_vsa=True)[0].vsa_similarity == 0.0


def test_semantic_bucket_honors_non_nibble_prefix_width(tmp_path: Path) -> None:
    builder = CognitiveCellBuilder(_store(tmp_path), max_documents=8)
    cells = builder.semantic_bucket_cells(prefix_bits=10)
    assert cells and all(cell.label.startswith("10:") for cell in cells)
    router = CognitiveCellRouter(cells)
    assert len(router.candidate_ids("Moon tides")) <= router.candidate_limit


def test_valid_generated_hint_enters_bounded_candidate_set(tmp_path: Path) -> None:
    builder = CognitiveCellBuilder(_store(tmp_path), max_documents=8)
    cells = builder.build(CellKind.CATEGORY)
    router = CognitiveCellRouter(cells, candidate_limit=1)
    hinted = cells[-1].cell_id
    assert router.route("unmatched", predicted_cell_ids=(hinted,))[0].cell_id == hinted


def test_comparative_gate_reports_all_four_topologies(tmp_path: Path) -> None:
    builder = CognitiveCellBuilder(_store(tmp_path), max_documents=8)
    moon = builder.store.title_search("Moon")[0]["document_id"]
    report = compare_topologies(
        builder,
        [
            {
                "query": "How are the Moon and tides related?",
                "gold_document_path": [moon],
            }
        ],
    )
    assert set(report["topologies"]) == {kind.value for kind in CellKind}
    assert report["decision"] == "NOT_QUALIFIED_WITHOUT_FROZEN_REAL_CORPUS_RUN"


def test_two_level_retrieval_is_bounded_and_vsa_is_ablatable(tmp_path: Path) -> None:
    builder = CognitiveCellBuilder(_store(tmp_path), max_documents=8)
    retriever = TwoLevelCellRetriever(
        builder.store,
        builder.build(CellKind.ENTITY_COMMUNITY),
        cell_limit=2,
        document_limit=4,
        chunk_limit=8,
        evidence_limit=2,
    )
    with_vsa = retriever.retrieve("How are the Moon and tides related?")
    without_vsa = retriever.retrieve("How are the Moon and tides related?", use_vsa=False)
    assert with_vsa.candidate_documents <= 4
    assert with_vsa.routed_cell_candidates <= retriever.router.candidate_limit
    assert with_vsa.candidate_chunks <= 8
    assert len(with_vsa.selected_evidence) <= 2
    assert with_vsa.broad_frontier_expansion is False
    assert without_vsa.vsa_enabled is False
    assert all(route.vsa_similarity == 0.0 for route in without_vsa.cell_routes)
