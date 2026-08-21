"""Targeted Factory exporter for v11 entity residual mention requests."""

from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from aethersparse.addressing.benchmark_v2 import BENCHMARK_CAPTURE_SCHEMA_VERSION
from aethersparse.addressing.compiler_v2 import (
    AddressArtifactError,
    _documents,
    _open_source,
    _resolution_index,
    _resolve_target,
    pack_lookup_normalize,
)

_TRAINING = frozenset({"development", "tuning"})
_CHANNEL_ORDER = {"title": 0, "redirect": 1, "alias": 2, "anchor": 3}


def _provenance_id(kind: str, *parts: str) -> str:
    payload = json.dumps((kind, *parts), separators=(",", ":"), ensure_ascii=False).encode()
    return f"factory:{kind}:{hashlib.sha256(payload).hexdigest()}"


def _candidate_support(item: dict[str, object]) -> int:
    value = item["support"]
    if isinstance(value, bool) or not isinstance(value, int):
        raise AddressArtifactError("candidate support is not an integer")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict) or not isinstance(value.get("cases"), list):
        raise AddressArtifactError("v11 hard-negative capture is malformed")
    partition_counts = value.get("partition_counts")
    if not isinstance(partition_counts, dict) or set(partition_counts) != _TRAINING:
        raise AddressArtifactError("v11 hard negatives do not prove training-only scope")
    if set(value.get("sealed_partitions_excluded", ())) != {"evaluation", "final_held"}:
        raise AddressArtifactError("v11 hard negatives do not prove sealed split exclusion")
    return value


def _write(path: Path, rows: list[dict[str, object]]) -> dict[str, object]:
    raw = b"".join(
        (json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
        for row in rows
    )
    with (
        path.open("wb") as raw_stream,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw_stream, mtime=0) as stream,
    ):
        stream.write(raw)
    return {
        "file": path.name,
        "rows": len(rows),
        "compressed_bytes": path.stat().st_size,
        "gzip_sha256": _sha256(path),
        "jsonl_bytes": len(raw),
        "jsonl_sha256": hashlib.sha256(raw).hexdigest(),
    }


def _proposals(
    connection: sqlite3.Connection,
    surface: str,
    by_title: dict[str, tuple[Any, ...]],
    normalize_lookup: Any,
) -> list[dict[str, object]]:
    # The pack's alias/anchor_text columns are stored under the pack's declared
    # normalization_id, so the query side must use that same declared
    # normalization rather than the generic surface contract.
    normalized = normalize_lookup(surface)
    proposals: dict[tuple[str, str], dict[str, object]] = {}
    aliases = connection.execute(
        """SELECT a.kind,d.document_id,d.normalized_title,d.redirect_target
             FROM aliases AS a JOIN documents AS d USING(document_id)
            WHERE a.alias=? ORDER BY a.kind,d.document_id""",
        (normalized,),
    )
    for row in aliases:
        resolved = _resolve_target(str(row["normalized_title"]), by_title, normalize_lookup)
        if resolved.entity_id is None:
            continue
        if row["redirect_target"]:
            channel = "redirect"
        else:
            channel = "title" if str(row["kind"]) == "title" else "alias"
        key = (resolved.entity_id, channel)
        candidate = proposals.setdefault(
            key,
            {
                "entity_id": resolved.entity_id,
                "canonical_title": resolved.canonical_title or "",
                "channel": channel,
                "support": 0,
                "provenance_ids": [],
            },
        )
        candidate["support"] = _candidate_support(candidate) + 1
        provenance = candidate["provenance_ids"]
        if not isinstance(provenance, list):
            raise AddressArtifactError("candidate provenance list is malformed")
        provenance.append(
            _provenance_id("alias", normalized, str(row["kind"]), str(row["document_id"]))
        )
    anchors = connection.execute(
        """SELECT anchor_id,target_title FROM anchors
            WHERE anchor_text=? ORDER BY target_title,anchor_id""",
        (normalized,),
    ).fetchall()
    anchor_total = len(anchors)
    for row in anchors:
        resolved = _resolve_target(str(row["target_title"]), by_title, normalize_lookup)
        if resolved.entity_id is None:
            continue
        key = (resolved.entity_id, "anchor")
        candidate = proposals.setdefault(
            key,
            {
                "entity_id": resolved.entity_id,
                "canonical_title": resolved.canonical_title or "",
                "channel": "anchor",
                "support": 0,
                "provenance_ids": [],
            },
        )
        candidate["support"] = _candidate_support(candidate) + 1
        provenance = candidate["provenance_ids"]
        if not isinstance(provenance, list):
            raise AddressArtifactError("candidate provenance list is malformed")
        provenance.append(str(row["anchor_id"]))
    ordered = sorted(
        proposals.values(),
        key=lambda item: (
            _CHANNEL_ORDER[str(item["channel"])],
            -_candidate_support(item),
            str(item["entity_id"]),
        ),
    )
    entity_rank: dict[str, int] = {}
    channel_rank: Counter[str] = Counter()
    for item in ordered:
        entity = str(item["entity_id"])
        if entity not in entity_rank:
            entity_rank[entity] = len(entity_rank) + 1
        channel = str(item["channel"])
        channel_rank[channel] += 1
        item["global_pre_cap_rank"] = entity_rank[entity]
        item["channel_rank"] = channel_rank[channel]
        item["raw_score"] = float(_candidate_support(item))
        item["channel_score"] = (
            _candidate_support(item) / anchor_total if channel == "anchor" and anchor_total else 1.0
        )
        provenance = item["provenance_ids"]
        if not isinstance(provenance, list) or any(
            not isinstance(value, str) for value in provenance
        ):
            raise AddressArtifactError("candidate provenance list is malformed")
        item["provenance_ids"] = sorted(set(provenance))
        del item["support"]
    return ordered


def export_v11_benchmark_capture(
    *,
    pack: Path,
    hard_negatives: Path,
    corpus_tier: str,
    output: Path,
) -> dict[str, object]:
    """Export exact/prior channel provenance for one tier's v11 residual mentions.

    A per-mention gold label is emitted only for a single detected mention with
    one case-level required entity.  Every other case-level association is
    marked ambiguous and will be quarantined by ``compile_benchmark_capture``.
    """

    if output.exists():
        raise FileExistsError(output)
    corpus = _load(hard_negatives)
    connection = _open_source(pack)
    try:
        normalize_lookup = pack_lookup_normalize(connection)
        by_title_raw, _by_id = _resolution_index(_documents(connection))
        by_title = dict(by_title_raw)
        rows: list[dict[str, object]] = []
        counts: Counter[str] = Counter()
        for case in corpus["cases"]:
            if not isinstance(case, dict):
                raise AddressArtifactError("hard-negative case is malformed")
            partition = str(case.get("partition", ""))
            if partition not in _TRAINING:
                raise AddressArtifactError(f"sealed partition in hard negatives: {partition}")
            query = str(case.get("query", ""))
            correct = [str(item) for item in case.get("correct_entity_ids", ())]
            # Select the unique replica for the requested tier inside the
            # exporter.  A cross-tier freeze is lawful input: cases with no
            # replica at this tier, or with duplicates, are excluded and
            # counted rather than aborting the export or guessing an
            # alignment from another tier's replica.
            replicas = [
                item
                for item in case.get("replicas", ())
                if isinstance(item, dict) and item.get("corpus_tier") == corpus_tier
            ]
            if not replicas:
                counts["cases_excluded_absent_requested_tier_replica"] += 1
                continue
            if len(replicas) > 1:
                counts["cases_excluded_duplicate_requested_tier_replica"] += 1
                continue
            mentions = replicas[0].get("mentions", ())
            if not isinstance(mentions, list):
                raise AddressArtifactError("replica mention list is malformed")
            exact_alignment = len(mentions) == 1 and len(correct) == 1
            counts["cases"] += 1
            counts["mentions"] += len(mentions)
            counts["exact_single_mention_alignments"] += exact_alignment
            counts["cases_without_detected_mentions"] += not mentions
            for index, mention in enumerate(mentions):
                if not isinstance(mention, dict):
                    raise AddressArtifactError("mention row is malformed")
                surface = str(mention.get("surface", ""))
                start = int(mention.get("char_start", -1))
                end = int(mention.get("char_end", -1))
                if start < 0 or end < start or query[start:end] != surface:
                    raise AddressArtifactError("mention offsets do not copy query text")
                proposals = _proposals(connection, surface, by_title, normalize_lookup)
                retained_candidates = mention.get("candidates", ())
                if not isinstance(retained_candidates, list):
                    raise AddressArtifactError("retained candidate list is malformed")
                retained = [
                    str(item["entity_id"])
                    for item in retained_candidates
                    if isinstance(item, dict) and item.get("entity_id")
                ]
                selected = str(mention.get("selected_entity_id") or "")
                alignment_basis = (
                    "single_mention_single_required_case_label"
                    if exact_alignment
                    else "case_level_ambiguous"
                )
                evidence_identity = {
                    "case_id": case.get("case_id"),
                    "partition": partition,
                    "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
                    "surface": surface,
                    "char_start": start,
                    "char_end": end,
                    "correct_entity_ids": correct,
                    "basis": alignment_basis,
                }
                evidence_sha256 = hashlib.sha256(
                    json.dumps(evidence_identity, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                rows.append(
                    {
                        "schema_version": BENCHMARK_CAPTURE_SCHEMA_VERSION,
                        "case_id": str(case.get("case_id", "")),
                        "partition": partition,
                        "corpus_tier": corpus_tier,
                        "query": query,
                        "mention_id": (
                            f"mention:{case.get('case_id')}:{corpus_tier}:{start}:{end}:{index}"
                        ),
                        "surface": surface,
                        "char_start": start,
                        "char_end": end,
                        "mention_detected": True,
                        "pre_cap_candidates": proposals,
                        "candidate_count_generated": len(
                            {str(item["entity_id"]) for item in proposals}
                        ),
                        "retained_entity_ids": retained,
                        "selected_entity_ids": [selected] if selected else [],
                        "confidence_rejected_entity_ids": [],
                        "retained_cap": 8,
                        "correct_entity_ids": correct,
                        "alignment_basis": alignment_basis,
                        "alignment_evidence_sha256": evidence_sha256,
                    }
                )
        rows.sort(
            key=lambda item: (
                str(item["partition"]),
                str(item["case_id"]),
                str(item["mention_id"]),
            )
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        identity = _write(output, rows)
        return {
            "schema_version": "aethersparse.semantic-address-factory-export.v2",
            "source_pack_sha256": _sha256(pack),
            "hard_negatives_sha256": _sha256(hard_negatives),
            "corpus_tier": corpus_tier,
            "partitions": sorted(_TRAINING),
            "sealed_partitions_excluded": ["evaluation", "final_held"],
            "channels": sorted(_CHANNEL_ORDER),
            "counts": dict(sorted(counts.items())),
            "output": identity,
        }
    finally:
        connection.close()
