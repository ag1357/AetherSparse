from __future__ import annotations

import bz2
from pathlib import Path

from aethersparse.selection.models import FEATURE_NAMES
from aethersparse.selection.selector import EvidenceSelector
from aethersparse.traversal.corpus import CorpusStore

XML = """<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/">
<page><title>Moon</title><ns>0</ns><id>1</id><revision><id>11</id>
<text>'''Moon''' is Earth's satellite. It affects [[Tide|tides]].
== Orbit ==\nThe Moon orbits Earth in about 27 days.</text></revision></page>
<page><title>Tide</title><ns>0</ns><id>2</id><revision><id>12</id>
<text>A '''tide''' is the rise and fall of sea level caused mainly by the Moon.</text>
</revision></page></mediawiki>"""


def _corpus(tmp_path: Path) -> Path:
    dump = tmp_path / "tiny.xml.bz2"
    dump.write_bytes(bz2.compress(XML.encode()))
    corpus = tmp_path / "corpus.sqlite"
    CorpusStore(corpus).ingest_mediawiki(dump, chunk_chars=200)
    return corpus


def test_fixed_shape_scores_are_bounded_and_inspectable(tmp_path: Path) -> None:
    selector = EvidenceSelector(_corpus(tmp_path), candidate_limit=8)
    trace = selector.select("How are the Moon and tides related?")
    assert trace.initial_candidates
    assert len(trace.initial_candidates[0].features) == len(FEATURE_NAMES)
    assert trace.model_macs == len(trace.initial_candidates) * len(FEATURE_NAMES)
    assert trace.source_bytes <= sum(
        len(item.raw_text.encode()) for item in trace.reranked_candidates
    )


def test_no_match_fails_closed(tmp_path: Path) -> None:
    selector = EvidenceSelector(_corpus(tmp_path), candidate_limit=8)
    trace = selector.select("Who invented Qzzyxx-999?")
    assert trace.stop_reason == "EVIDENCE_GAP"
    assert not trace.selected_evidence


def test_targeted_traversal_needs_explicit_gap(tmp_path: Path) -> None:
    selector = EvidenceSelector(_corpus(tmp_path), candidate_limit=8)
    trace = selector.select(
        "How are the Moon and tides related?", permit_targeted_traversal=True
    )
    assert not trace.traversal_activated
    assert trace.traversal_depth == 0
