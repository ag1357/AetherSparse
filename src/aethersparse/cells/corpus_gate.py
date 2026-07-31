"""Fail-closed verification and orchestration for frozen corpus qualification."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aethersparse.cells.qualification import compare_topologies
from aethersparse.cells.topology import CognitiveCellBuilder
from aethersparse.traversal.corpus import CorpusStore

PACK_FILENAMES = {
    "1k": "simplewiki-1k.sqlite",
    "10k": "simplewiki-10k.sqlite",
    "50k": "simplewiki.sqlite",
}
QUESTION_FILENAMES = {
    "held_out": "questions.json",
    "scaling": "scaling_questions.json",
}


class FrozenCorpusError(RuntimeError):
    """Raised when an input differs from the frozen corpus identity."""


def sha256_file(path: Path, *, block_bytes: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(block_bytes):
            digest.update(block)
    return digest.hexdigest()


def _question_count(path: Path) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    questions = payload.get("questions") if isinstance(payload, dict) else None
    if not isinstance(questions, list):
        raise FrozenCorpusError(f"invalid question payload: {path}")
    return len(questions)


def _sqlite_counts(path: Path) -> tuple[int, int]:
    uri = f"file:{path.resolve()}?mode=ro&immutable=1"
    try:
        with sqlite3.connect(uri, uri=True) as database:
            integrity = database.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise FrozenCorpusError(f"SQLite integrity check failed: {path}")
            documents = int(database.execute("SELECT count(*) FROM documents").fetchone()[0])
            chunks = int(database.execute("SELECT count(*) FROM chunks").fetchone()[0])
    except sqlite3.Error as error:
        raise FrozenCorpusError(f"invalid frozen SQLite pack {path}: {error}") from error
    return documents, chunks


def verify_frozen_corpus(
    manifest_path: Path,
    corpus_root: Path,
    *,
    include_source_dump: bool = False,
) -> dict[str, Any]:
    """Verify every frozen byte identity before allowing a comparative run."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    verified: dict[str, Any] = {"packs": {}, "questions": {}}

    for label, filename in PACK_FILENAMES.items():
        expected = manifest["packs"][label]
        path = corpus_root / filename
        if not path.is_file():
            errors.append(f"missing pack {label}: {path}")
            continue
        actual_bytes = path.stat().st_size
        actual_hash = sha256_file(path)
        if actual_bytes != int(expected["bytes"]):
            errors.append(
                f"pack {label} byte mismatch: expected {expected['bytes']}, got {actual_bytes}"
            )
        if actual_hash != expected["sha256"]:
            errors.append(
                f"pack {label} hash mismatch: expected {expected['sha256']}, got {actual_hash}"
            )
        try:
            documents, chunks = _sqlite_counts(path)
        except FrozenCorpusError as error:
            errors.append(str(error))
            continue
        if documents != int(expected["articles"]):
            errors.append(
                f"pack {label} article mismatch: expected {expected['articles']}, got {documents}"
            )
        if chunks != int(expected["chunks"]):
            errors.append(
                f"pack {label} chunk mismatch: expected {expected['chunks']}, got {chunks}"
            )
        verified["packs"][label] = {
            "path": str(path),
            "bytes": actual_bytes,
            "sha256": actual_hash,
            "articles": documents,
            "chunks": chunks,
        }

    question_specs = {
        "held_out": (
            manifest["questions"]["held_out_count"],
            manifest["questions"]["held_out_sha256"],
        ),
        "scaling": (
            manifest["questions"]["scaling_count"],
            manifest["questions"]["scaling_sha256"],
        ),
    }
    for label, filename in QUESTION_FILENAMES.items():
        expected_count, expected_hash = question_specs[label]
        path = corpus_root / filename
        if not path.is_file():
            errors.append(f"missing questions {label}: {path}")
            continue
        actual_hash = sha256_file(path)
        try:
            actual_count = _question_count(path)
        except (FrozenCorpusError, json.JSONDecodeError) as error:
            errors.append(str(error))
            continue
        if actual_hash != expected_hash:
            errors.append(
                f"questions {label} hash mismatch: expected {expected_hash}, got {actual_hash}"
            )
        if actual_count != int(expected_count):
            errors.append(
                f"questions {label} count mismatch: expected {expected_count}, got {actual_count}"
            )
        verified["questions"][label] = {
            "path": str(path),
            "sha256": actual_hash,
            "count": actual_count,
        }

    if include_source_dump:
        source_path = corpus_root / "simplewiki-latest-pages-articles.xml.bz2"
        if not source_path.is_file():
            errors.append(f"missing source dump: {source_path}")
        else:
            actual_hash = sha256_file(source_path)
            expected_hash = manifest["source"]["sha256"]
            if actual_hash != expected_hash:
                errors.append(
                    f"source dump hash mismatch: expected {expected_hash}, got {actual_hash}"
                )
            verified["source"] = {"path": str(source_path), "sha256": actual_hash}

    if errors:
        joined = "\n- ".join(errors)
        raise FrozenCorpusError(f"frozen corpus preflight failed:\n- {joined}")
    verified["manifest_path"] = str(manifest_path)
    verified["manifest_sha256"] = sha256_file(manifest_path)
    verified["status"] = "FROZEN_CORPUS_VERIFIED"
    return verified


def qualify_frozen_scales(
    manifest_path: Path,
    corpus_root: Path,
    *,
    max_documents: int = 256,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run the same topology comparison at all scales after complete preflight."""
    verification = verify_frozen_corpus(manifest_path, corpus_root)
    questions_payload = json.loads(
        (corpus_root / QUESTION_FILENAMES["scaling"]).read_text(encoding="utf-8")
    )
    results: dict[str, Any] = {}
    for label, filename in PACK_FILENAMES.items():
        if progress is not None:
            progress(label)
        store = CorpusStore(corpus_root / filename, read_only=True)
        try:
            results[label] = compare_topologies(
                CognitiveCellBuilder(store, max_documents=max_documents),
                questions_payload["questions"],
            )
        finally:
            store.close()
    return {
        "classification": "FROZEN_COGNITIVE_CELL_SCALING_QUALIFICATION",
        "verification": verification,
        "max_documents_per_cell": max_documents,
        "scales": results,
        "decision": "MEASURED_COMPARATIVE_GATE_COMPLETE",
    }
