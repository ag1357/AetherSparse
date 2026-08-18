#!/usr/bin/env python3
"""Qualify the Mission 7 exact FST/prior channel on lawful available data.

The 397k diagnostic is a targeted post-cap title source, not a complete corpus
address pack. Development rows compile the measured index; tuning rows are used
only for a label-free transfer measurement. Evaluation and final-held rows are
validated and discarded before evidence construction.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, cast

from aethersparse.addressing.exact import (
    AddressChannel,
    AddressEvidence,
    AddressIndexArtifact,
    ExactAddressIndex,
    compile_exact_address_index,
    normalize_surface,
)
from aethersparse.controller.replay import load_replay_bundle

ALLOWED_PARTITIONS = frozenset({"development", "tuning"})
SEALED_PARTITIONS = frozenset({"evaluation", "final_held"})
EXPECTED_REPLAY_BUNDLE = "099cd28b5c2a090d1b08d1e07d39e59ae6583854971c539085741fbb369f0246"
DIAGNOSTIC_SCHEMA = "aethersparse.v10-candidate-diagnostic.v1"
REPORT_SCHEMA = "aethersparse.fst-prior-qualification.v12"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n"


def _load_json(path: Path) -> dict[str, Any]:
    value: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return cast(dict[str, Any], value)


def _load_diagnostic(
    path: Path, manifest_path: Path, replay_bundle: Path
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    manifest = _load_json(manifest_path)
    if manifest.get("schema") != DIAGNOSTIC_SCHEMA:
        raise ValueError("unsupported candidate diagnostic schema")
    output = manifest.get("output")
    if not isinstance(output, dict):
        raise ValueError("candidate diagnostic manifest output is malformed")
    observed_sha = _sha256(path)
    if (
        output.get("sha256") != observed_sha
        or output.get("compressed_bytes") != path.stat().st_size
    ):
        raise ValueError("candidate diagnostic compressed identity mismatch")
    rows: list[dict[str, Any]] = []
    raw_bytes = 0
    with gzip.open(path, "rb") as stream:
        for line in stream:
            raw_bytes += len(line)
            value: Any = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("candidate diagnostic row must be an object")
            rows.append(cast(dict[str, Any], value))
    if raw_bytes != output.get("uncompressed_bytes") or len(rows) != manifest.get("case_count"):
        raise ValueError("candidate diagnostic row count/size mismatch")

    replay_manifest, replay_cases = load_replay_bundle(replay_bundle)
    if replay_manifest.bundle_sha256 != EXPECTED_REPLAY_BUNDLE:
        raise ValueError("unexpected replay bundle identity")
    replay_partitions: dict[str, str] = {}
    for replay in replay_cases:
        prior = replay_partitions.setdefault(replay.case_id, replay.partition)
        if prior != replay.partition:
            raise ValueError("replay case crosses partitions")
    partition_counts: Counter[str] = Counter()
    seen: set[str] = set()
    for row in rows:
        case_id = str(row.get("case_id"))
        partition = str(row.get("partition"))
        if case_id in seen:
            raise ValueError("duplicate candidate diagnostic case")
        seen.add(case_id)
        partition_counts[partition] += 1
        if replay_partitions.get(case_id) != partition:
            raise ValueError("candidate diagnostic/replay partition mismatch")
        runtime = row.get("gold_blind_runtime")
        if not isinstance(runtime, dict) or runtime.get("partition") != partition:
            raise ValueError("candidate diagnostic runtime partition mismatch")
        candidates = row.get("candidates")
        if not isinstance(candidates, list) or any(
            not isinstance(item, dict) for item in candidates
        ):
            raise ValueError("candidate diagnostic candidate list is malformed")
    if set(partition_counts) != ALLOWED_PARTITIONS | SEALED_PARTITIONS:
        raise ValueError("candidate diagnostic partition set is incomplete")
    return rows, {
        "diagnostic_sha256": observed_sha,
        "diagnostic_manifest_sha256": _sha256(manifest_path),
        "diagnostic_case_count": len(rows),
        "diagnostic_partition_counts": dict(sorted(partition_counts.items())),
        "authenticated_replay_bundle_sha256": replay_manifest.bundle_sha256,
        "authenticated_replay_case_count": replay_manifest.case_count,
        "authenticated_replay_decision_count": replay_manifest.decision_count,
        "partition_join_verified": True,
    }


def _diagnostic_evidence(
    rows: list[dict[str, Any]], *, partition: str
) -> tuple[list[AddressEvidence], dict[str, int]]:
    if partition not in ALLOWED_PARTITIONS:
        raise ValueError("only development/tuning may enter diagnostic evidence")
    evidence: list[AddressEvidence] = []
    cases = 0
    for row in rows:
        if row["partition"] != partition:
            continue
        cases += 1
        case_id = str(row["case_id"])
        for raw in row["candidates"]:
            candidate = cast(dict[str, Any], raw)
            title = str(candidate["title"])
            document_id = str(candidate["document_id"])
            chunk_id = str(candidate["chunk_id"])
            evidence.append(
                AddressEvidence(
                    surface=title,
                    entity_id=document_id,
                    canonical_title=title,
                    support_count=1,
                    source_document_ids=(document_id,),
                    channel=AddressChannel.TITLE,
                    provenance_ids=(f"candidate397:{case_id}:{chunk_id}",),
                )
            )
    return evidence, {"cases": cases, "candidate_occurrences": len(evidence)}


def _catalog_evidence(path: Path) -> tuple[list[AddressEvidence], dict[str, int]]:
    document = _load_json(path)
    raw_entities = document.get("entities")
    if not isinstance(raw_entities, list) or any(
        not isinstance(item, dict) for item in raw_entities
    ):
        raise ValueError("entity catalog is malformed")
    evidence: list[AddressEvidence] = []
    for raw in raw_entities:
        entity = cast(dict[str, Any], raw)
        entity_id = str(entity["concept_id"])
        title = str(entity["canonical_name"])
        source = f"catalog:{entity_id}"
        evidence.append(
            AddressEvidence(
                surface=title,
                entity_id=entity_id,
                canonical_title=title,
                support_count=1,
                source_document_ids=(source,),
                channel=AddressChannel.TITLE,
                provenance_ids=(f"{source}:title",),
            )
        )
        aliases = entity.get("aliases")
        if not isinstance(aliases, list) or any(not isinstance(alias, str) for alias in aliases):
            raise ValueError("entity catalog aliases are malformed")
        for alias_index, alias in enumerate(aliases):
            evidence.append(
                AddressEvidence(
                    surface=alias,
                    entity_id=entity_id,
                    canonical_title=title,
                    support_count=1,
                    source_document_ids=(source,),
                    channel=AddressChannel.ALIAS,
                    provenance_ids=(f"{source}:alias:{alias_index}",),
                )
            )
    return evidence, {"entities": len(raw_entities), "declarations": len(evidence)}


def _artifact_dict(artifact: AddressIndexArtifact) -> dict[str, object]:
    return {
        "root_sha256": artifact.root_sha256,
        "file_sha256": artifact.file_sha256,
        "manifest_sha256": artifact.manifest_sha256,
        "total_bytes": artifact.total_bytes,
        "header_bytes": artifact.header_bytes,
        "dictionary_bytes": artifact.dictionary_bytes,
        "posting_bytes": artifact.posting_bytes,
        "provenance_bytes": artifact.provenance_bytes,
        "address_core_bytes_excluding_provenance": (
            artifact.address_core_bytes_excluding_provenance
        ),
        "surface_count": artifact.surface_count,
        "entity_count": artifact.entity_count,
        "posting_count": artifact.posting_count,
        "bytes_per_surface": artifact.total_bytes / artifact.surface_count,
        "prior_encoding": "lossless_integer_support_ratio",
        "prior_quantization_retained": False,
    }


def _pair_metrics(index: ExactAddressIndex, evidence: list[AddressEvidence]) -> dict[str, object]:
    pairs = sorted(
        {
            (normalize_surface(item.surface), item.entity_id)
            for item in evidence
            if item.entity_id is not None
        }
    )
    cutoffs = (1, 4, 8, 16, 32)
    hits = Counter({cutoff: 0 for cutoff in cutoffs})
    surface_hits = 0
    collision_surfaces: set[str] = set()
    maximum_candidates = 0
    for surface, entity_id in pairs:
        result = index.lookup(surface)
        if result is None:
            continue
        surface_hits += 1
        maximum_candidates = max(maximum_candidates, result.total_candidate_count)
        if result.total_candidate_count > 1:
            collision_surfaces.add(surface)
        ranked = [posting.entity_id for posting in result.postings]
        for cutoff in cutoffs:
            hits[cutoff] += entity_id in ranked[:cutoff]
    denominator = len(pairs)
    return {
        "unique_surface_entity_pairs": denominator,
        "surface_found_pairs": surface_hits,
        "surface_coverage": surface_hits / denominator if denominator else 0.0,
        "entity_recall": {
            f"at_{cutoff}": hits[cutoff] / denominator if denominator else 0.0 for cutoff in cutoffs
        },
        "collision_surfaces_seen": len(collision_surfaces),
        "maximum_candidates_per_surface": maximum_candidates,
    }


def qualify(
    *,
    candidate_diagnostic: Path,
    candidate_manifest: Path,
    replay_bundle: Path,
    entity_catalog: Path,
    artifact_dir: Path,
) -> dict[str, object]:
    rows, audit = _load_diagnostic(candidate_diagnostic, candidate_manifest, replay_bundle)
    development, development_scope = _diagnostic_evidence(rows, partition="development")
    tuning, tuning_scope = _diagnostic_evidence(rows, partition="tuning")
    catalog, catalog_scope = _catalog_evidence(entity_catalog)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    development_artifact = compile_exact_address_index(
        development,
        artifact_dir / "targeted-development-title-address.fst",
        source_artifact_sha256=str(audit["diagnostic_sha256"]),
        source_partitions=("development",),
    )
    development_index = ExactAddressIndex(
        Path(development_artifact.path), Path(development_artifact.manifest_path)
    )
    catalog_sha = _sha256(entity_catalog)
    catalog_artifact = compile_exact_address_index(
        catalog,
        artifact_dir / "catalog-title-alias-address.fst",
        source_artifact_sha256=catalog_sha,
    )
    catalog_index = ExactAddressIndex(
        Path(catalog_artifact.path), Path(catalog_artifact.manifest_path)
    )

    development_metrics = _pair_metrics(development_index, development)
    tuning_metrics = _pair_metrics(development_index, tuning)
    catalog_metrics = _pair_metrics(catalog_index, catalog)
    return {
        "schema_version": REPORT_SCHEMA,
        "decision": "EXACT_FST_CHANNEL_IMPLEMENTED_FULL_CORPUS_DATA_REQUIRED",
        "base_commit": "a7dcb187a985164648549eb18f67a7a6a4a964c6",
        "integrity": audit,
        "partition_policy": {
            "construction_partitions": ["development"],
            "measurement_partitions": ["development", "tuning"],
            "sealed_partitions_loaded_into_compiler": [],
            "sealed_partitions_excluded": sorted(SEALED_PARTITIONS),
            "benchmark_labels_or_answers_used": False,
            "tuning_used_for_format_or_feature_selection": False,
        },
        "real_data_scope": {
            "description": "targeted 397k post-cap canonical title rows",
            "development": development_scope,
            "tuning": tuning_scope,
            "known_absences": [
                "full title registry",
                "alias rows",
                "redirect rows",
                "anchor occurrences",
                "pre-cap provenance",
                "retrieval-channel provenance",
            ],
            "global_fst_or_full_corpus_recall_claimed": False,
        },
        "targeted_development_index": {
            "artifact": _artifact_dict(development_artifact),
            "development_self_roundtrip": development_metrics,
            "tuning_title_transfer": tuning_metrics,
            "support_semantics": "post-cap candidate occurrence count",
            "source_diversity_semantics": "distinct canonical source documents",
        },
        "non_benchmark_catalog_fixture": {
            "source_sha256": catalog_sha,
            "scope": catalog_scope,
            "artifact": _artifact_dict(catalog_artifact),
            "roundtrip": catalog_metrics,
        },
        "format": {
            "dictionary": "normalized UTF-8 path-compressed acyclic byte FST",
            "terminal_output": "posting group offset",
            "selection_behavior": "returns distribution; never forces entity",
            "canonical_ids_authoritative": True,
            "unresolved_mass_preserved": True,
            "cap_loss_explicit": True,
            "content_addressed_sections": True,
            "deterministic_serialization": True,
            "prior_quantization": (
                "not applicable: exact integer support/total ratios are smaller and lossless"
            ),
        },
        "limitations": [
            (
                "Targeted diagnostic contains post-cap titles only and cannot qualify "
                "global mention or entity recall."
            ),
            "No alias, redirect, or anchor rows are available in the 397k diagnostic.",
            "Tuning transfer measures title-table coverage, not gold semantic-address correctness.",
            "A full-corpus Factory export is required for production footprint and recall claims.",
        ],
    }


def _markdown(report: dict[str, object]) -> str:
    targeted = cast(dict[str, Any], report["targeted_development_index"])
    artifact = cast(dict[str, Any], targeted["artifact"])
    development = cast(dict[str, Any], targeted["development_self_roundtrip"])
    tuning = cast(dict[str, Any], targeted["tuning_title_transfer"])
    integrity = cast(dict[str, Any], report["integrity"])
    scope = cast(dict[str, Any], report["real_data_scope"])
    development_scope = cast(dict[str, Any], scope["development"])
    tuning_scope = cast(dict[str, Any], scope["tuning"])
    recall = cast(dict[str, float], development["entity_recall"])
    tuning_recall = cast(dict[str, float], tuning["entity_recall"])
    development_extent = (
        f"{development_scope['cases']} / {development_scope['candidate_occurrences']}"
    )
    tuning_extent = f"{tuning_scope['cases']} / {tuning_scope['candidate_occurrences']}"
    address_extent = (
        f"{artifact['surface_count']} / {artifact['entity_count']} / {artifact['posting_count']}"
    )
    section_extent = (
        f"{artifact['header_bytes']} / {artifact['dictionary_bytes']} / "
        f"{artifact['posting_bytes']} / {artifact['provenance_bytes']}"
    )
    address_core_bytes = artifact["address_core_bytes_excluding_provenance"]
    self_recall = " / ".join(f"{recall[f'at_{cutoff}']:.6f}" for cutoff in (1, 4, 8, 16, 32))
    transfer_recall = " / ".join(
        f"{tuning_recall[f'at_{cutoff}']:.6f}" for cutoff in (1, 4, 8, 16, 32)
    )
    return f"""# Mission 7 exact FST/prior channel qualification

## Decision

`{report["decision"]}`

The immutable exact-address channel is implemented and validated. The available
397k source is a targeted post-cap candidate diagnostic, so this result is a
real-data serialization and title-transfer measurement—not a global address
recall claim. Evaluation and final-held rows were verified against the
authenticated replay and excluded before evidence construction.

## Data and integrity

| Measure | Result |
|---|---:|
| Authenticated replay bundle | `{integrity["authenticated_replay_bundle_sha256"]}` |
| Diagnostic SHA-256 | `{integrity["diagnostic_sha256"]}` |
| Development cases / candidate occurrences | {development_extent} |
| Tuning cases / candidate occurrences | {tuning_extent} |
| Sealed rows entering compiler | 0 |
| Benchmark labels/answers used | no |

## Measured targeted development index

| Measure | Result |
|---|---:|
| Surfaces / entities / postings | {address_extent} |
| Total serialized bytes | {artifact["total_bytes"]} |
| Address core bytes excluding provenance sidecar | {address_core_bytes} |
| Header / dictionary / postings / provenance bytes | {section_extent} |
| Bytes per surface | {artifact["bytes_per_surface"]:.3f} |
| Collision surfaces | {development["collision_surfaces_seen"]} |
| Maximum postings for one surface | {development["maximum_candidates_per_surface"]} |
| Self round-trip recall@1/4/8/16/32 | {self_recall} |
| Tuning title-transfer recall@1/4/8/16/32 | {transfer_recall} |
| Root SHA-256 | `{artifact["root_sha256"]}` |
| File SHA-256 | `{artifact["file_sha256"]}` |

Priors are not quantized: each posting stores integer support and each group
stores total support, so `P(entity|surface)` is reconstructed losslessly. This
preserves recall and every support-based ranking while using fewer bytes than a
stored floating-point prior.

## Runtime contract

- Normalized UTF-8 bytes traverse an immutable path-compressed acyclic byte FST.
- A terminal state returns a posting byte offset and full address distribution.
- Canonical entity IDs remain authoritative; title collisions are retained.
- Title/redirect/alias/anchor support, source diversity, ambiguity entropy,
  unresolved mass, and provenance references are represented explicitly.
- A caller cap reports omitted candidate count and probability mass; it cannot
  silently convert truncation into confidence.
- Every section and the complete file are content-addressed and verified.

## Limitation and next dependency

The diagnostic has no full title registry, aliases, redirects, anchors, pre-cap
pool, or channel provenance. It therefore cannot qualify global FST size,
mention recall, or semantic-address recall. Lane A's full-corpus exporter can
feed the same `AddressEvidence` compiler without changing the runtime format.
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-diagnostic", type=Path, required=True)
    parser.add_argument("--candidate-manifest", type=Path, required=True)
    parser.add_argument("--replay-bundle", type=Path, required=True)
    parser.add_argument("--entity-catalog", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    args = parser.parse_args()
    report = qualify(
        candidate_diagnostic=args.candidate_diagnostic,
        candidate_manifest=args.candidate_manifest,
        replay_bundle=args.replay_bundle,
        entity_catalog=args.entity_catalog,
        artifact_dir=args.artifact_dir,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(_canonical_json(report))
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(_markdown(report), encoding="utf-8")


if __name__ == "__main__":
    main()
