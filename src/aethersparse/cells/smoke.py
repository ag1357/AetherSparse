"""Deterministic, tiny-corpus smoke artifact for cognitive-cell CI.

This is a contract/reproducibility fixture.  It is deliberately too small to be
used as evidence for the real-corpus topology qualification gate.
"""

from __future__ import annotations

import bz2
import json
import tempfile
from pathlib import Path

from aethersparse.cells.pack import CognitiveCellPack
from aethersparse.cells.qualification import compare_topologies
from aethersparse.cells.topology import CognitiveCellBuilder
from aethersparse.traversal.corpus import CorpusStore

_XML = """<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/">
<page><title>Moon</title><ns>0</ns><id>1</id><revision><id>11</id>
<text>'''Moon''' is Earth's satellite. It affects [[Tide]].
[[Category:Astronomy]]</text></revision></page>
<page><title>Tide</title><ns>0</ns><id>2</id><revision><id>12</id>
<text>A tide is the rise and fall of sea level caused mainly by the [[Moon]].
[[Category:Oceanography]]</text></revision></page>
</mediawiki>"""


def cognitive_cell_smoke_report() -> dict[str, object]:
    """Build the canonical tiny fixture and return its stable contract report."""
    with tempfile.TemporaryDirectory(prefix="aethersparse-cell-smoke-") as temporary:
        root = Path(temporary)
        dump = root / "tiny.xml.bz2"
        dump.write_bytes(bz2.compress(_XML.encode("utf-8")))
        store = CorpusStore(root / "corpus.sqlite")
        try:
            store.ingest_mediawiki(dump, chunk_chars=200)
            builder = CognitiveCellBuilder(store, max_documents=8)
            moon = str(store.title_search("Moon")[0]["document_id"])
            tide = str(store.title_search("Tide")[0]["document_id"])
            report = compare_topologies(
                builder,
                (
                    {
                        "query": "How are the Moon and tides related?",
                        "gold_document_path": [moon],
                    },
                    {
                        "query": "What mainly causes a tide?",
                        "gold_document_path": [tide],
                    },
                ),
            )
            hybrid = builder.hybrid_cells()
            pack = CognitiveCellPack.compile(
                hybrid,
                topology="hybrid",
                source_manifest_hash="sha256:contract-smoke-fixture-v1",
            )
            if not pack.verify():
                raise RuntimeError("deterministic smoke pack failed integrity verification")
            report.update(
                {
                    "scope": "CONTRACT_SMOKE_ONLY_NOT_REAL_CORPUS_EVIDENCE",
                    "terminal_role": "EXTERNAL_API_CLIENT_ONLY",
                    "fixture": "tiny_mediawiki_v1",
                    "hybrid_pack_root": pack.manifest.root_hash,
                    "hybrid_pack_blocks": len(pack.blocks),
                }
            )
            return report
        finally:
            store.db.close()


def canonical_smoke_bytes() -> bytes:
    """Serialize the smoke artifact canonically for byte-level comparisons."""
    return (json.dumps(cognitive_cell_smoke_report(), indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
