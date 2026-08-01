from __future__ import annotations

import bz2
from pathlib import Path

from aethersparse.traversal.corpus import CorpusStore, normalize_text

XML = """<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/">
<page><title>Moon</title><ns>0</ns><id>1</id><revision><id>11</id>
<text>'''Moon''' is Earth's satellite. It affects [[Tide|tides]].
== Orbit ==\nThe Moon orbits Earth in about 27 days.</text></revision></page>
<page><title>Tide</title><ns>0</ns><id>2</id><revision><id>12</id>
<text>A '''tide''' is the rise and fall of sea level caused mainly by the Moon.</text>
</revision></page></mediawiki>"""

IDENTICAL_REDIRECTS_XML = """<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/">
<page><title>First alias</title><ns>0</ns><id>31</id><redirect title="Target"/>
<revision><id>301</id><text>#REDIRECT [[Target]]</text></revision></page>
<page><title>Second alias</title><ns>0</ns><id>32</id><redirect title="Target"/>
<revision><id>302</id><text>#REDIRECT [[Target]]</text></revision></page>
</mediawiki>"""


def test_mediawiki_ingestion_preserves_offsets_and_builds_generic_indexes(tmp_path: Path) -> None:
    dump = tmp_path / "tiny.xml.bz2"
    dump.write_bytes(bz2.compress(XML.encode()))
    store = CorpusStore(tmp_path / "corpus.sqlite")
    manifest = store.ingest_mediawiki(dump, chunk_chars=200)
    assert manifest["articles"] == 2
    assert store.stats()["chunks"] >= 2
    assert store.title_search("Moon")[0]["title"] == "Moon"
    row = store.search("sea level caused Moon")[0]
    assert row["raw_text"]
    assert row["raw_end"] > row["raw_start"]


def test_normalization_is_deterministic_but_raw_is_unchanged() -> None:
    raw = "A\u00a0“quoted”\n line &amp; value"
    assert normalize_text(raw) == 'A "quoted" line & value'
    assert "\u00a0" in raw


def test_distinct_pages_with_identical_source_text_are_not_collapsed(tmp_path: Path) -> None:
    dump = tmp_path / "redirects.xml.bz2"
    dump.write_bytes(bz2.compress(IDENTICAL_REDIRECTS_XML.encode()))
    store = CorpusStore(tmp_path / "corpus.sqlite")

    manifest = store.ingest_mediawiki(dump)

    assert manifest["articles"] == 2
    rows = store.db.execute(
        "SELECT document_id, title, content_hash FROM documents ORDER BY title"
    ).fetchall()
    assert [row["title"] for row in rows] == ["First alias", "Second alias"]
    assert rows[0]["document_id"] != rows[1]["document_id"]
    assert rows[0]["content_hash"] == rows[1]["content_hash"]
