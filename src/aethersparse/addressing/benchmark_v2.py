"""Leakage-safe benchmark mention/provenance capture for Semantic Address v2."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from aethersparse.addressing.compiler_v2 import AddressArtifactError
from aethersparse.addressing.contracts_v2 import (
    ADDRESS_EXPORT_SCHEMA_VERSION,
    CANONICAL_ENTITY_PREFIX,
    AddressChannelV2,
    validate_record_contract,
    with_stable_record_id,
)

BENCHMARK_CAPTURE_SCHEMA_VERSION = "aethersparse.semantic-address-benchmark-capture.v2"
BENCHMARK_CAPTURE_MANIFEST_VERSION = "aethersparse.semantic-address-benchmark-manifest.v2"
_ALLOWED_PARTITIONS = frozenset({"development", "tuning"})
_EXACT_ALIGNMENT_BASES = frozenset(
    {
        "author_exact_mention",
        "direct_hyperlink",
        "single_mention_single_required_case_label",
        "source_bound_explicit_entity",
    }
)
_CHANNELS = frozenset(channel.value for channel in AddressChannelV2)


@dataclass(frozen=True)
class BenchmarkCaptureManifest:
    """Identity and split counts for a separated benchmark address capture."""

    schema_version: str
    source_sha256: str
    source_bytes: int
    counts: Mapping[str, int]
    outputs: Mapping[str, Mapping[str, object]]
    sealed_partitions_excluded: tuple[str, str] = ("evaluation", "final_held")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_gzip(path: Path, rows: Iterator[Mapping[str, object]]) -> dict[str, object]:
    raw_hash = hashlib.sha256()
    raw_bytes = 0
    count = 0
    with (
        path.open("wb") as raw_stream,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw_stream, mtime=0) as stream,
    ):
        for row in rows:
            row = with_stable_record_id(row)
            validate_record_contract(row)
            line = (
                json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
            ).encode()
            stream.write(line)
            raw_hash.update(line)
            raw_bytes += len(line)
            count += 1
    return {
        "file": path.name,
        "rows": count,
        "compressed_bytes": path.stat().st_size,
        "gzip_sha256": _sha256(path),
        "jsonl_bytes": raw_bytes,
        "jsonl_sha256": raw_hash.hexdigest(),
    }


def _objects(path: Path) -> Iterator[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise AddressArtifactError(f"invalid capture JSON on line {line_number}") from error
            if not isinstance(value, dict):
                raise AddressArtifactError(f"non-object capture row on line {line_number}")
            yield value


def _integer(value: object, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise AddressArtifactError(f"{name} must be an integer >= {minimum}")
    return value


def _strings(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise AddressArtifactError(f"{name} must be a string list")
    return tuple(value)


def _validated(row: Mapping[str, Any]) -> tuple[dict[str, object], dict[str, object] | None]:
    if row.get("schema_version") != BENCHMARK_CAPTURE_SCHEMA_VERSION:
        raise AddressArtifactError("unsupported benchmark capture row schema")
    partition = str(row.get("partition", ""))
    if partition not in _ALLOWED_PARTITIONS:
        raise AddressArtifactError(f"sealed or unknown partition in capture: {partition}")
    query = str(row.get("query", ""))
    surface = str(row.get("surface", ""))
    start = _integer(row.get("char_start"), "char_start")
    end = _integer(row.get("char_end"), "char_end", minimum=start)
    mention_detected = row.get("mention_detected")
    if not isinstance(mention_detected, bool):
        raise AddressArtifactError("mention_detected must be boolean")
    if query[start:end] != surface:
        raise AddressArtifactError("mention offsets do not copy query text")
    candidates = row.get("pre_cap_candidates")
    if not isinstance(candidates, list):
        raise AddressArtifactError("pre_cap_candidates must be a list")
    clean_candidates: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for raw in candidates:
        if not isinstance(raw, dict):
            raise AddressArtifactError("candidate rows must be objects")
        entity_id = str(raw.get("entity_id", ""))
        channel = str(raw.get("channel", ""))
        if not entity_id.startswith(CANONICAL_ENTITY_PREFIX):
            raise AddressArtifactError("candidate is outside the canonical corpus ID band")
        if channel not in _CHANNELS:
            raise AddressArtifactError(f"unknown candidate channel: {channel}")
        identity = (entity_id, channel)
        if identity in seen:
            raise AddressArtifactError("duplicate entity/channel candidate provenance")
        seen.add(identity)
        raw_score_value = raw.get("raw_score")
        if raw_score_value is not None and (
            isinstance(raw_score_value, bool) or not isinstance(raw_score_value, int | float)
        ):
            raise AddressArtifactError("candidate raw_score must be numeric or null")
        channel_score_value = raw.get("channel_score")
        if isinstance(channel_score_value, bool) or not isinstance(
            channel_score_value, int | float
        ):
            raise AddressArtifactError("candidate channel_score must be numeric")
        clean_raw_score = float(raw_score_value) if raw_score_value is not None else None
        clean_channel_score = float(channel_score_value)
        if clean_raw_score is not None and not math.isfinite(clean_raw_score):
            raise AddressArtifactError("candidate raw_score must be finite or null")
        if not math.isfinite(clean_channel_score) or not 0.0 <= clean_channel_score <= 1.0:
            raise AddressArtifactError("candidate channel_score must be finite and in [0,1]")
        canonical_title = str(raw.get("canonical_title", ""))
        if not canonical_title.strip():
            raise AddressArtifactError("candidate canonical title must be non-empty")
        clean_candidates.append(
            {
                "entity_id": entity_id,
                "canonical_title": canonical_title,
                "channel": channel,
                "channel_rank": _integer(raw.get("channel_rank"), "channel_rank", minimum=1),
                "global_pre_cap_rank": _integer(
                    raw.get("global_pre_cap_rank"), "global_pre_cap_rank", minimum=1
                ),
                "raw_score": clean_raw_score,
                "channel_score": clean_channel_score,
                "provenance_ids": list(_strings(raw.get("provenance_ids"), "provenance_ids")),
            }
        )
    generated_count = _integer(row.get("candidate_count_generated"), "candidate_count_generated")
    if generated_count != len({item["entity_id"] for item in clean_candidates}):
        raise AddressArtifactError("candidate_count_generated does not match canonical union")
    retained = _strings(row.get("retained_entity_ids"), "retained_entity_ids")
    selected = _strings(row.get("selected_entity_ids"), "selected_entity_ids")
    rejected = _strings(row.get("confidence_rejected_entity_ids"), "confidence_rejected_entity_ids")
    if any(
        not entity_id.startswith(CANONICAL_ENTITY_PREFIX)
        for entity_id in (*retained, *selected, *rejected)
    ):
        raise AddressArtifactError("retained/selected/rejected ID is outside the corpus band")
    runtime = {
        "schema_version": ADDRESS_EXPORT_SCHEMA_VERSION,
        "record_type": "benchmark_mention_runtime",
        "case_id": str(row.get("case_id", "")),
        "partition": partition,
        "corpus_tier": str(row.get("corpus_tier", "")),
        "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
        "mention_id": str(row.get("mention_id", "")),
        "surface": surface,
        "char_start": start,
        "char_end": end,
        "mention_detected": mention_detected,
        "candidate_count_generated": generated_count,
        "pre_cap_candidates": sorted(
            clean_candidates,
            key=lambda item: (
                _integer(item["global_pre_cap_rank"], "global_pre_cap_rank", minimum=1),
                str(item["entity_id"]),
            ),
        ),
        "retained_entity_ids": list(retained),
        "selected_entity_ids": list(selected),
        "confidence_rejected_entity_ids": list(rejected),
        "retained_cap": _integer(row.get("retained_cap"), "retained_cap", minimum=1),
    }
    correct = _strings(row.get("correct_entity_ids"), "correct_entity_ids")
    if any(not entity_id.startswith(CANONICAL_ENTITY_PREFIX) for entity_id in correct):
        raise AddressArtifactError("correct entity ID is outside the canonical corpus band")
    if not correct:
        return runtime, None
    basis = str(row.get("alignment_basis", ""))
    evidence = str(row.get("alignment_evidence_sha256", ""))
    evidence_is_sha256 = len(evidence) == 64 and evidence == evidence.casefold()
    try:
        int(evidence, 16)
    except ValueError:
        evidence_is_sha256 = False
    exact = len(correct) == 1 and basis in _EXACT_ALIGNMENT_BASES and evidence_is_sha256
    label = {
        "schema_version": ADDRESS_EXPORT_SCHEMA_VERSION,
        "record_type": "benchmark_mention_label" if exact else "alignment_quarantine",
        "case_id": runtime["case_id"],
        "partition": partition,
        "corpus_tier": runtime["corpus_tier"],
        "mention_id": runtime["mention_id"],
        "correct_entity_ids": list(correct),
        "alignment_basis": basis,
        "alignment_evidence_sha256": evidence,
        "alignment_exact": exact,
        "quarantine_reason": (
            None if exact else "alignment is ambiguous or lacks exact source-bound evidence"
        ),
    }
    return runtime, label


def _failure_state(runtime: Mapping[str, object], label: Mapping[str, object]) -> str:
    correct = set(_strings(label["correct_entity_ids"], "correct_entity_ids"))
    if not bool(runtime["mention_detected"]):
        return "mention_missing"
    raw_candidates = runtime["pre_cap_candidates"]
    if not isinstance(raw_candidates, list) or any(
        not isinstance(item, dict) for item in raw_candidates
    ):
        raise AddressArtifactError("validated runtime candidate list became malformed")
    pre_cap = {str(item["entity_id"]) for item in raw_candidates}
    if not correct.issubset(pre_cap):
        return "correct_candidate_absent"
    retained = set(_strings(runtime["retained_entity_ids"], "retained_entity_ids"))
    if not correct.issubset(retained):
        return "candidate_outside_cap"
    selected = set(_strings(runtime["selected_entity_ids"], "selected_entity_ids"))
    if correct.issubset(selected):
        return "correct_selected"
    rejected = set(
        _strings(runtime["confidence_rejected_entity_ids"], "confidence_rejected_entity_ids")
    )
    if correct & rejected:
        return "candidate_rejected_by_confidence"
    return "candidate_misranked"


def compile_benchmark_capture(source: Path, output_directory: Path) -> BenchmarkCaptureManifest:
    """Validate and split a Factory capture so tuning labels cannot enter fitting."""

    output_directory.mkdir(parents=True, exist_ok=True)
    names = ("runtime", "development_labels", "tuning_labels", "quarantine")
    paths = {name: output_directory / f"{name}.jsonl.gz" for name in names}
    manifest_path = output_directory / "benchmark-manifest.json"
    existing = [path for path in (*paths.values(), manifest_path) if path.exists()]
    if existing:
        raise FileExistsError(f"refusing to overwrite benchmark capture: {existing}")
    buckets: dict[str, list[dict[str, object]]] = {name: [] for name in names}
    counts: Counter[str] = Counter()
    seen: set[tuple[str, str, str, str]] = set()
    for raw in _objects(source):
        runtime, label = _validated(raw)
        identity = (
            str(runtime["partition"]),
            str(runtime["case_id"]),
            str(runtime["corpus_tier"]),
            str(runtime["mention_id"]),
        )
        if identity in seen:
            raise AddressArtifactError(f"duplicate benchmark mention capture: {identity}")
        seen.add(identity)
        buckets["runtime"].append(runtime)
        partition = str(runtime["partition"])
        counts[f"runtime_{partition}"] += 1
        if label is None:
            counts["unaligned"] += 1
            continue
        if not bool(label["alignment_exact"]):
            buckets["quarantine"].append(label)
            counts["alignment_quarantine"] += 1
            continue
        destination = "development_labels" if partition == "development" else "tuning_labels"
        label = dict(label)
        label["failure_state"] = _failure_state(runtime, label)
        buckets[destination].append(label)
        counts[f"exact_alignment_{partition}"] += 1
        counts[f"failure_{label['failure_state']}"] += 1

    def key(row: Mapping[str, object]) -> tuple[str, str, str, str]:
        return (
            str(row.get("partition", "")),
            str(row.get("case_id", "")),
            str(row.get("corpus_tier", "")),
            str(row.get("mention_id", "")),
        )

    outputs = {
        name: _write_gzip(paths[name], iter(sorted(rows, key=key)))
        for name, rows in buckets.items()
    }
    manifest = BenchmarkCaptureManifest(
        schema_version=BENCHMARK_CAPTURE_MANIFEST_VERSION,
        source_sha256=_sha256(source),
        source_bytes=source.stat().st_size,
        counts=dict(sorted(counts.items())),
        outputs=outputs,
    )
    manifest_path.write_text(
        json.dumps(asdict(manifest), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest
