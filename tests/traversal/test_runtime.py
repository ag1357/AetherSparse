from __future__ import annotations

import bz2
from pathlib import Path

from aethersparse.traversal.corpus import CorpusStore
from aethersparse.traversal.models import TraversalBudget
from aethersparse.traversal.runtime import TraversalRuntime

from .test_corpus import XML


def test_runtime_is_bounded_and_source_bound(tmp_path: Path) -> None:
    dump = tmp_path / "tiny.xml.bz2"
    dump.write_bytes(bz2.compress(XML.encode()))
    corpus = tmp_path / "corpus.sqlite"
    CorpusStore(corpus).ingest_mediawiki(dump, chunk_chars=200)
    result = TraversalRuntime(corpus).query(
        "How are the Moon and tides related?",
        budget=TraversalBudget(max_steps=6, max_articles=4, max_chunks=8, max_bytes=4096),
    )
    assert result.bytes_read <= 4096
    assert result.unique_articles_visited <= 4
    assert result.citations
    assert result.answer in result.citations[0].normalized_text
    assert all(citation.raw_text for citation in result.citations)


def test_unknown_entity_fails_closed(tmp_path: Path) -> None:
    dump = tmp_path / "tiny.xml.bz2"
    dump.write_bytes(bz2.compress(XML.encode()))
    corpus = tmp_path / "corpus.sqlite"
    CorpusStore(corpus).ingest_mediawiki(dump, chunk_chars=200)
    result = TraversalRuntime(corpus).query("Who founded Qzzyxx-999?")
    assert result.disposition == "ABSTAIN"
    assert result.answer is None
