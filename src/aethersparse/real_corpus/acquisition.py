"""Resumable, checksum-verified Wikimedia dump acquisition."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO, cast

WIKIMEDIA_DUMP_ROOT = "https://dumps.wikimedia.org"


@dataclass(frozen=True)
class DumpObject:
    """Identity resolved from an official Wikimedia dump-status manifest."""

    dump_date: str
    filename: str
    url: str
    compressed_bytes: int
    official_sha1: str
    official_md5: str
    status_url: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def hash_file(path: Path, algorithm: str = "sha256", block_size: int = 1024 * 1024) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(block_size), b""):
            digest.update(block)
    return digest.hexdigest()


def dump_object_from_status(
    status: dict[str, object], *, dump_date: str, status_url: str
) -> DumpObject:
    """Resolve the single-stream namespace/article archive from dumpstatus.json."""

    jobs = cast(dict[str, object], status.get("jobs", {}))
    article_job = cast(dict[str, object], jobs.get("articlesdump", {}))
    if article_job.get("status") != "done":
        raise ValueError("official articlesdump job is not complete")
    files = cast(dict[str, object], article_job.get("files", {}))
    matches: list[tuple[str, dict[str, object]]] = []
    for filename, value in files.items():
        if filename.endswith("-pages-articles.xml.bz2"):
            matches.append((filename, cast(dict[str, object], value)))
    if len(matches) != 1:
        raise ValueError(f"expected one pages-articles archive, found {len(matches)}")
    filename, item = matches[0]
    relative_url = str(item["url"])
    return DumpObject(
        dump_date=dump_date,
        filename=filename,
        url=f"{WIKIMEDIA_DUMP_ROOT}{relative_url}",
        compressed_bytes=int(str(item["size"])),
        official_sha1=str(item["sha1"]),
        official_md5=str(item["md5"]),
        status_url=status_url,
    )


def load_status(location: str, timeout_seconds: float = 30.0) -> dict[str, object]:
    if location.startswith(("https://", "http://")):
        request = urllib.request.Request(
            location, headers={"User-Agent": "AetherSparse-v0.5-corpus-builder/1"}
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return cast(dict[str, object], json.load(response))
    with Path(location).open(encoding="utf-8") as stream:
        return cast(dict[str, object], json.load(stream))


def _blocks(stream: BinaryIO, block_size: int) -> Iterator[bytes]:
    while block := stream.read(block_size):
        yield block


def _append_progress(log_path: Path | None, payload: dict[str, object]) -> None:
    if log_path is None:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")


def _verify(path: Path, spec: DumpObject) -> dict[str, object]:
    size = path.stat().st_size
    sha1 = hash_file(path, "sha1")
    sha256 = hash_file(path, "sha256")
    md5 = hash_file(path, "md5")
    if size != spec.compressed_bytes:
        raise ValueError(f"byte-count mismatch: expected {spec.compressed_bytes}, received {size}")
    if sha1 != spec.official_sha1:
        raise ValueError(f"SHA-1 mismatch: expected {spec.official_sha1}, received {sha1}")
    if md5 != spec.official_md5:
        raise ValueError(f"MD5 mismatch: expected {spec.official_md5}, received {md5}")
    return {"compressed_bytes": size, "sha1": sha1, "sha256": sha256, "md5": md5}


def acquire_dump(
    spec: DumpObject,
    destination: Path,
    *,
    retries: int = 8,
    timeout_seconds: float = 60.0,
    block_size: int = 1024 * 1024,
    progress_log: Path | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Resume into ``.part``, verify, and atomically activate an official archive."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        verified = _verify(destination, spec)
        result = {**spec.to_dict(), **verified, "result": "already_verified"}
        _append_progress(progress_log, {"event": "already_verified", **result})
        return result
    partial = destination.with_suffix(destination.suffix + ".part")
    if partial.exists() and partial.stat().st_size > spec.compressed_bytes:
        raise ValueError("partial file is larger than the official object")
    if partial.exists() and partial.stat().st_size == spec.compressed_bytes:
        verified = _verify(partial, spec)
        os.replace(partial, destination)
        result = {**spec.to_dict(), **verified, "result": "resumed_and_verified"}
        _append_progress(progress_log, {"event": "verified", **result})
        return result
    for attempt in range(retries + 1):
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {"User-Agent": "AetherSparse-v0.5-corpus-builder/1"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(spec.url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                status = getattr(response, "status", response.getcode())
                if offset and status != 206:
                    raise ValueError("server ignored Range request for an existing partial file")
                mode = "ab" if offset else "wb"
                with partial.open(mode) as output:
                    for block in _blocks(response, block_size):
                        output.write(block)
            received = partial.stat().st_size
            _append_progress(
                progress_log,
                {"attempt": attempt, "event": "transfer_complete", "received": received},
            )
            verified = _verify(partial, spec)
            os.replace(partial, destination)
            result = {**spec.to_dict(), **verified, "result": "downloaded_and_verified"}
            _append_progress(progress_log, {"event": "verified", **result})
            return result
        except (
            OSError,
            TimeoutError,
            http.client.IncompleteRead,
            urllib.error.URLError,
            ValueError,
        ) as error:
            _append_progress(
                progress_log,
                {
                    "attempt": attempt,
                    "event": "retry" if attempt < retries else "failed",
                    "error": type(error).__name__,
                    "received": partial.stat().st_size if partial.exists() else 0,
                },
            )
            if attempt >= retries:
                raise
            sleep(min(60.0, float(2**attempt)))
    raise AssertionError("unreachable")
