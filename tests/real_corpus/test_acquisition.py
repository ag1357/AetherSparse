from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from aethersparse.real_corpus.acquisition import (
    DumpObject,
    acquire_dump,
    dump_object_from_status,
    hash_file,
)


def test_dump_status_resolves_only_completed_single_stream_archive() -> None:
    status: dict[str, object] = {
        "jobs": {
            "articlesdump": {
                "status": "done",
                "files": {
                    "simplewiki-20260801-pages-articles.xml.bz2": {
                        "size": 123,
                        "url": "/simplewiki/20260801/object.bz2",
                        "sha1": "abc",
                        "md5": "def",
                    }
                },
            }
        }
    }
    resolved = dump_object_from_status(
        status, dump_date="20260801", status_url="https://example.invalid/status"
    )
    assert resolved.compressed_bytes == 123
    assert resolved.url == "https://dumps.wikimedia.org/simplewiki/20260801/object.bz2"


def test_existing_exact_dump_is_verified_without_network(tmp_path: Path) -> None:
    body = b"tiny official object"
    destination = tmp_path / "object.bz2"
    destination.write_bytes(body)
    spec = DumpObject(
        dump_date="20260801",
        filename=destination.name,
        url="https://example.invalid/object.bz2",
        compressed_bytes=len(body),
        official_sha1=hashlib.sha1(body).hexdigest(),
        official_md5=hashlib.md5(body).hexdigest(),
        status_url="https://example.invalid/status",
    )
    result = acquire_dump(spec, destination)
    assert result["result"] == "already_verified"
    assert result["sha256"] == hashlib.sha256(body).hexdigest()
    assert hash_file(destination) == result["sha256"]


def test_oversize_partial_is_rejected_before_network(tmp_path: Path) -> None:
    destination = tmp_path / "object.bz2"
    partial = destination.with_suffix(".bz2.part")
    partial.write_bytes(b"too large")
    spec = DumpObject("x", destination.name, "https://example.invalid", 1, "", "", "")
    with pytest.raises(ValueError, match="larger"):
        acquire_dump(spec, destination)
