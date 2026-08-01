from __future__ import annotations

import bz2
import hashlib
import sqlite3
from pathlib import Path

from aethersparse.real_corpus.builder import PackSettings, build_pack, inspect_pack
from aethersparse.real_corpus.pack import RealCorpusPack

XML = """<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/">
<page><title>First alias</title><ns>0</ns><id>31</id><redirect title="Target"/>
<revision><id>301</id><timestamp>2026-08-01T00:00:00Z</timestamp><sha1>a</sha1>
<text>#REDIRECT [[Target]]</text></revision></page>
<page><title>Second alias</title><ns>0</ns><id>32</id><redirect title="Target"/>
<revision><id>302</id><timestamp>2026-08-01T00:00:00Z</timestamp><sha1>b</sha1>
<text>#REDIRECT [[Target]]</text></revision></page>
</mediawiki>"""


def test_builder_preserves_distinct_source_identity_and_exact_bindings(tmp_path: Path) -> None:
    dump = tmp_path / "tiny.xml.bz2"
    dump.write_bytes(bz2.compress(XML.encode()))
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
    pack = tmp_path / "pack.sqlite"
    manifest = build_pack(
        dump,
        pack,
        source=source,
        settings=PackSettings(article_limit=2, chunk_chars=480),
    )
    assert manifest["documents"] == 2
    assert manifest["duplicate_source_hash_groups_preserved"] == 1
    assert manifest["documents_in_duplicate_source_hash_groups"] == 2
    assert manifest["integrity"] == {
        "sqlite_integrity": "ok",
        "foreign_key_violations": 0,
        "source_binding_failures": 0,
    }
    db = sqlite3.connect(pack)
    rows = db.execute(
        "SELECT document_id,source_text_sha256 FROM documents ORDER BY wiki_page_id"
    ).fetchall()
    assert rows[0][0] != rows[1][0]
    assert rows[0][1] == rows[1][1]
    assert inspect_pack(pack)["documents"] == 2

    db = sqlite3.connect(pack)
    stored_source = db.execute("SELECT value FROM corpus_meta WHERE key='source'").fetchone()[0]
    assert "downloaded_and_verified" not in stored_source

    with RealCorpusPack(pack, maximum_limit=16) as reader:
        assert reader.title_lookup("First alias", 4)[0]["document_id"] == "simplewiki:31:301"
        assert len(reader.anchor_lookup("Target", 4)) == 2
        hits = reader.search_chunks("Target", 4)
        assert len(hits) == 2
        binding = reader.source_binding(str(hits[0]["chunk_id"]))
        assert binding is not None
        assert binding["slice_matches"] is True
        assert binding["span_hash_matches"] is True
        assert binding["document_hash_matches"] is True
        assert reader.last_trace is not None
        assert reader.last_trace.estimated_payload_blocks >= 1
        assert reader.workload_trace(clear=True)
        assert reader.workload_trace() == []
