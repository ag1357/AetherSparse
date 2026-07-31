"""Immutable source snapshots and normalization-aware raw alignments."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from aethersparse.gate0.models import (
    AlignmentMethod,
    FrozenSourceSnapshot,
    SourceAlignment,
    utc_now,
)

NORMALIZATION_VERSION = "gate0-normalization-v1"


class SourceIntegrityError(ValueError):
    """Raised when a frozen source or alignment fails closed."""


@dataclass(frozen=True)
class MappedCharacter:
    value: str
    raw_start: int
    raw_end: int


@dataclass(frozen=True)
class NormalizedView:
    text: str
    characters: tuple[MappedCharacter, ...]

    def raw_range(self, normalized_start: int, normalized_end: int) -> tuple[int, int]:
        if normalized_start < 0 or normalized_end > len(self.characters):
            raise SourceIntegrityError("normalized range is outside the source")
        if normalized_end <= normalized_start:
            raise SourceIntegrityError("normalized range must be non-empty")
        selected = self.characters[normalized_start:normalized_end]
        return min(char.raw_start for char in selected), max(char.raw_end for char in selected)


def sha256_text(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def stable_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _decode_html_entities(raw_text: str) -> list[MappedCharacter]:
    result: list[MappedCharacter] = []
    entity_pattern = re.compile(r"&(?:#\d+|#x[0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]+);")
    cursor = 0
    for match in entity_pattern.finditer(raw_text):
        for index in range(cursor, match.start()):
            result.append(MappedCharacter(raw_text[index], index, index + 1))
        decoded = html.unescape(match.group(0))
        for char in decoded:
            result.append(MappedCharacter(char, match.start(), match.end()))
        cursor = match.end()
    for index in range(cursor, len(raw_text)):
        result.append(MappedCharacter(raw_text[index], index, index + 1))
    return result


def _unicode_nfc(units: list[MappedCharacter]) -> list[MappedCharacter]:
    result: list[MappedCharacter] = []
    index = 0
    while index < len(units):
        cluster = [units[index]]
        index += 1
        while index < len(units) and unicodedata.combining(units[index].value):
            cluster.append(units[index])
            index += 1
        normalized = unicodedata.normalize("NFC", "".join(unit.value for unit in cluster))
        raw_start = min(unit.raw_start for unit in cluster)
        raw_end = max(unit.raw_end for unit in cluster)
        for char in normalized:
            result.append(MappedCharacter(char, raw_start, raw_end))
    return result


def _canonicalize_punctuation(units: list[MappedCharacter]) -> list[MappedCharacter]:
    replacements = {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u2026": "...",
        "\u00a0": " ",
    }
    result: list[MappedCharacter] = []
    for unit in units:
        replacement = replacements.get(unit.value, unit.value)
        for char in replacement:
            result.append(MappedCharacter(char, unit.raw_start, unit.raw_end))
    return result


def _remove_pdf_line_hyphenation(units: list[MappedCharacter]) -> list[MappedCharacter]:
    result: list[MappedCharacter] = []
    index = 0
    while index < len(units):
        unit = units[index]
        if unit.value == "-" and result and result[-1].value.isalpha() and index + 1 < len(units):
            probe = index + 1
            saw_newline = False
            while probe < len(units) and units[probe].value.isspace():
                saw_newline = saw_newline or units[probe].value in {"\n", "\r"}
                probe += 1
            if saw_newline and probe < len(units) and units[probe].value.isalpha():
                index = probe
                continue
        result.append(unit)
        index += 1
    return result


def _collapse_whitespace(units: list[MappedCharacter]) -> list[MappedCharacter]:
    result: list[MappedCharacter] = []
    index = 0
    while index < len(units):
        if not units[index].value.isspace():
            result.append(units[index])
            index += 1
            continue
        start = units[index].raw_start
        end = units[index].raw_end
        while index < len(units) and units[index].value.isspace():
            end = max(end, units[index].raw_end)
            index += 1
        if result and index < len(units):
            result.append(MappedCharacter(" ", start, end))
    return result


def normalize_with_map(raw_text: str) -> NormalizedView:
    units = _decode_html_entities(raw_text)
    units = _unicode_nfc(units)
    units = _canonicalize_punctuation(units)
    units = _remove_pdf_line_hyphenation(units)
    units = _collapse_whitespace(units)
    return NormalizedView(
        text="".join(unit.value for unit in units),
        characters=tuple(units),
    )


def normalize_text(raw_text: str) -> str:
    return normalize_with_map(raw_text).text


def freeze_source(
    *,
    source_doc_id: str,
    title: str,
    source_url: str,
    source_revision: str,
    license: Literal["public_domain", "compatible_open_license"],
    source_group: str,
    raw_text: str,
    retrieved_at: datetime | None = None,
) -> FrozenSourceSnapshot:
    normalized = normalize_text(raw_text)
    return FrozenSourceSnapshot(
        source_doc_id=source_doc_id,
        title=title,
        source_url=source_url,
        source_revision=source_revision,
        retrieved_at=retrieved_at or utc_now(),
        license=license,
        source_group=source_group,
        raw_text=raw_text,
        raw_content_hash=sha256_text(raw_text),
        raw_byte_length=len(raw_text.encode("utf-8")),
        raw_char_length=len(raw_text),
        normalization_version=NORMALIZATION_VERSION,
        normalized_text=normalized,
        normalized_content_hash=sha256_text(normalized),
    )


def verify_snapshot(snapshot: FrozenSourceSnapshot) -> None:
    if snapshot.raw_content_hash != sha256_text(snapshot.raw_text):
        raise SourceIntegrityError(f"raw content hash mismatch: {snapshot.source_doc_id}")
    if snapshot.normalization_version != NORMALIZATION_VERSION:
        raise SourceIntegrityError(
            f"unsupported normalization version: {snapshot.normalization_version}"
        )
    normalized = normalize_text(snapshot.raw_text)
    if snapshot.normalized_text != normalized:
        raise SourceIntegrityError(f"normalized text mismatch: {snapshot.source_doc_id}")
    if snapshot.normalized_content_hash != sha256_text(normalized):
        raise SourceIntegrityError(f"normalized content hash mismatch: {snapshot.source_doc_id}")


def _unique_find(haystack: str, needle: str) -> int:
    start = haystack.find(needle)
    if start < 0:
        raise SourceIntegrityError(f"evidence not found: {needle!r}")
    if haystack.find(needle, start + 1) >= 0:
        raise SourceIntegrityError(f"evidence alignment is ambiguous: {needle!r}")
    return start


def align_evidence(
    snapshot: FrozenSourceSnapshot,
    evidence_surface: str,
    *,
    direct_quotation: bool = False,
) -> SourceAlignment:
    verify_snapshot(snapshot)
    raw_start = snapshot.raw_text.find(evidence_surface)
    method = AlignmentMethod.EXACT_RAW
    if raw_start >= 0 and snapshot.raw_text.find(evidence_surface, raw_start + 1) < 0:
        raw_end = raw_start + len(evidence_surface)
        normalized_view = normalize_with_map(snapshot.raw_text)
        normalized_surface = normalize_text(evidence_surface)
        normalized_start = _unique_find(normalized_view.text, normalized_surface)
        normalized_end = normalized_start + len(normalized_surface)
    else:
        if direct_quotation:
            raise SourceIntegrityError("direct quotations require exact raw substring alignment")
        normalized_view = normalize_with_map(snapshot.raw_text)
        normalized_surface = normalize_text(evidence_surface)
        normalized_start = _unique_find(normalized_view.text, normalized_surface)
        normalized_end = normalized_start + len(normalized_surface)
        raw_start, raw_end = normalized_view.raw_range(normalized_start, normalized_end)
        method = AlignmentMethod.NORMALIZED_EQUIVALENT

    raw_text = snapshot.raw_text[raw_start:raw_end]
    raw_byte_start = len(snapshot.raw_text[:raw_start].encode("utf-8"))
    raw_byte_end = len(snapshot.raw_text[:raw_end].encode("utf-8"))
    return SourceAlignment(
        source_doc_id=snapshot.source_doc_id,
        source_revision=snapshot.source_revision,
        source_content_hash=snapshot.raw_content_hash,
        raw_char_start=raw_start,
        raw_char_end=raw_end,
        raw_byte_start=raw_byte_start,
        raw_byte_end=raw_byte_end,
        raw_text=raw_text,
        raw_text_hash=sha256_text(raw_text),
        normalized_char_start=normalized_start,
        normalized_char_end=normalized_end,
        normalized_text=normalized_surface,
        normalized_text_hash=sha256_text(normalized_surface),
        alignment_method=method,
        direct_quotation=direct_quotation,
    )


def align_normalized_range(
    snapshot: FrozenSourceSnapshot,
    normalized_start: int,
    normalized_end: int,
) -> SourceAlignment:
    """Bind a known normalized range back to immutable raw char and byte offsets."""

    verify_snapshot(snapshot)
    view = normalize_with_map(snapshot.raw_text)
    raw_start, raw_end = view.raw_range(normalized_start, normalized_end)
    normalized_surface = view.text[normalized_start:normalized_end]
    raw_text = snapshot.raw_text[raw_start:raw_end]
    return SourceAlignment(
        source_doc_id=snapshot.source_doc_id,
        source_revision=snapshot.source_revision,
        source_content_hash=snapshot.raw_content_hash,
        raw_char_start=raw_start,
        raw_char_end=raw_end,
        raw_byte_start=len(snapshot.raw_text[:raw_start].encode("utf-8")),
        raw_byte_end=len(snapshot.raw_text[:raw_end].encode("utf-8")),
        raw_text=raw_text,
        raw_text_hash=sha256_text(raw_text),
        normalized_char_start=normalized_start,
        normalized_char_end=normalized_end,
        normalized_text=normalized_surface,
        normalized_text_hash=sha256_text(normalized_surface),
        alignment_method=(
            AlignmentMethod.EXACT_RAW
            if raw_text == normalized_surface
            else AlignmentMethod.NORMALIZED_EQUIVALENT
        ),
    )


class SourceRepository:
    """Filesystem store that refuses mutation of a frozen source identity."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, source_doc_id: str) -> Path:
        safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", source_doc_id)
        return self.root / f"{safe_id}.json"

    def add(self, snapshot: FrozenSourceSnapshot) -> Path:
        verify_snapshot(snapshot)
        path = self.path_for(snapshot.source_doc_id)
        rendered = (
            json.dumps(
                snapshot.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        if path.exists():
            existing = FrozenSourceSnapshot.model_validate_json(path.read_text("utf-8"))
            if existing.model_dump(mode="json") != snapshot.model_dump(mode="json"):
                raise SourceIntegrityError(
                    f"source identity is already frozen with different bytes: "
                    f"{snapshot.source_doc_id}"
                )
            return path
        temporary = path.with_suffix(".tmp")
        temporary.write_text(rendered, encoding="utf-8")
        os.replace(temporary, path)
        return path

    def get(self, source_doc_id: str) -> FrozenSourceSnapshot:
        path = self.path_for(source_doc_id)
        snapshot = FrozenSourceSnapshot.model_validate_json(path.read_text("utf-8"))
        verify_snapshot(snapshot)
        return snapshot

    def list(self) -> tuple[FrozenSourceSnapshot, ...]:
        snapshots = [
            FrozenSourceSnapshot.model_validate_json(path.read_text("utf-8"))
            for path in sorted(self.root.glob("*.json"))
        ]
        for snapshot in snapshots:
            verify_snapshot(snapshot)
        return tuple(snapshots)

    def manifest_hash(self) -> str:
        manifest = [
            {
                "source_doc_id": snapshot.source_doc_id,
                "source_revision": snapshot.source_revision,
                "raw_content_hash": snapshot.raw_content_hash,
                "normalized_content_hash": snapshot.normalized_content_hash,
                "license": snapshot.license,
            }
            for snapshot in self.list()
        ]
        return f"sha256:{hashlib.sha256(stable_json(manifest)).hexdigest()}"
