from __future__ import annotations

import bz2
from pathlib import Path

from aethersparse.cells.models import CellKind
from aethersparse.cells.qualification import compare_topologies
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
