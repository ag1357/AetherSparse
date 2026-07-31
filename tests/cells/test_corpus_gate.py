from __future__ import annotations

import bz2
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from aethersparse.cells.corpus_gate import FrozenCorpusError, verify_frozen_corpus
from aethersparse.traversal.corpus import CorpusStore

XML = """<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/">
<page><title>Moon</title><ns>0</ns><id>1</id><revision><id>11</id>
<text>Moon is Earth's satellite.</text></revision></page>
</mediawiki>"""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    dump = tmp_path / "source.xml.bz2"
    dump.write_bytes(bz2.compress(XML.encode()))
    seed = tmp_path / "seed.sqlite"
    store = CorpusStore(seed)
    store.ingest_mediawiki(dump, chunk_chars=200)
    store.close()
    names = ("simplewiki-1k.sqlite", "simplewiki-10k.sqlite", "simplewiki.sqlite")
    for name in names:
        shutil.copyfile(seed, tmp_path / name)
    questions = {"questions": [{"query": "Moon?", "gold_document_path": ["mw:1"]}]}
    for name in ("questions.json", "scaling_questions.json"):
        (tmp_path / name).write_text(json.dumps(questions), encoding="utf-8")
    packs = {
        label: {
            "articles": 1,
            "chunks": 1,
            "bytes": (tmp_path / filename).stat().st_size,
            "sha256": _sha(tmp_path / filename),
        }
        for label, filename in zip(("1k", "10k", "50k"), names, strict=True)
    }
    manifest = {
        "source": {"sha256": _sha(dump)},
        "packs": packs,
        "questions": {
            "held_out_count": 1,
            "held_out_sha256": _sha(tmp_path / "questions.json"),
            "scaling_count": 1,
            "scaling_sha256": _sha(tmp_path / "scaling_questions.json"),
        },
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, tmp_path


def test_frozen_corpus_verification_checks_bytes_hash_counts_and_integrity(
    tmp_path: Path,
) -> None:
    manifest, root = _fixture(tmp_path)
    report = verify_frozen_corpus(manifest, root)
    assert report["status"] == "FROZEN_CORPUS_VERIFIED"
    assert report["packs"]["50k"]["articles"] == 1


def test_frozen_corpus_verification_fails_closed_on_mutation(tmp_path: Path) -> None:
    manifest, root = _fixture(tmp_path)
    with (root / "simplewiki-10k.sqlite").open("ab") as stream:
        stream.write(b"mutation")
    with pytest.raises(FrozenCorpusError, match="10k byte mismatch"):
        verify_frozen_corpus(manifest, root)


def test_frozen_corpus_verification_fails_before_substituting_missing_pack(
    tmp_path: Path,
) -> None:
    manifest, root = _fixture(tmp_path)
    (root / "simplewiki.sqlite").unlink()
    with pytest.raises(FrozenCorpusError, match="missing pack 50k"):
        verify_frozen_corpus(manifest, root)
