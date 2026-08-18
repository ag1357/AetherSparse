from __future__ import annotations

import gzip
import hashlib
import json
import math
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from aethersparse.addressing.benchmark_v2 import (
    BENCHMARK_CAPTURE_SCHEMA_VERSION,
    compile_benchmark_capture,
)
from aethersparse.addressing.bundle_v2 import (
    AddressBundleIdentity,
    CanonicalAddressRegistry,
    CanonicalRegistryEntry,
    compile_verified_exact_address_index,
    iter_surface_statistics_view,
    load_canonical_registry,
    verify_address_bundle,
)
from aethersparse.addressing.compiler_v2 import (
    AddressArtifactError,
    canonical_entity_id,
    compile_address_pack,
    iter_jsonl_gzip,
    verify_address_export,
)
from aethersparse.addressing.contracts_v2 import (
    AddressChannelV2,
    fusion_channel_name,
    validate_record_contract,
    with_stable_record_id,
)
from aethersparse.addressing.exact import AddressChannel, ExactAddressIndex
from aethersparse.addressing.factory_export_v2 import export_v11_benchmark_capture
from aethersparse.addressing.semantic_ann import (
    CorpusSourceSplit,
    load_compiler_supervision,
    semantic_index_manifest_document,
    semantic_supervision_manifest_document,
    verify_semantic_supervision_manifest,
    write_semantic_supervision_manifest,
)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rewrite_compiled_stream(
    output: Path,
    name: str,
    mutate: Callable[[list[dict[str, object]]], None],
) -> None:
    rows = list(iter_jsonl_gzip(output / f"{name}.jsonl.gz"))
    mutate(rows)
    rows = [with_stable_record_id(row) for row in rows]
    raw = b"".join(
        (json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
        for row in rows
    )
    path = output / f"{name}.jsonl.gz"
    with (
        path.open("wb") as raw_stream,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw_stream, mtime=0) as stream,
    ):
        stream.write(raw)
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["streams"][name] = {
        "file": path.name,
        "compressed_bytes": path.stat().st_size,
        "gzip_sha256": _hash(path),
        "jsonl_bytes": len(raw),
        "jsonl_sha256": hashlib.sha256(raw).hexdigest(),
        "rows": len(rows),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _pack(path: Path) -> None:
    db = sqlite3.connect(path)
    db.executescript(
        """
        PRAGMA user_version=500;
        CREATE TABLE documents(
          document_id TEXT PRIMARY KEY,title TEXT,normalized_title TEXT,
          redirect_target TEXT,source_text_sha256 TEXT,raw_wikitext TEXT
        );
        CREATE TABLE aliases(alias TEXT,document_id TEXT,kind TEXT);
        CREATE TABLE redirects(
          source_document_id TEXT,target_title TEXT,source_text_sha256 TEXT
        );
        CREATE TABLE anchors(
          anchor_id TEXT,source_document_id TEXT,target_title TEXT,anchor_text TEXT,
          raw_start INTEGER,raw_end INTEGER,raw_text TEXT,source_span_sha256 TEXT
        );
        """
    )
    alpha = "Alpha links to [[Beta|B.]] and [[Missing]]."
    beta = "Beta."
    rows = [
        ("doc:a", "Alpha", "alpha", None, "a" * 64, alpha),
        ("doc:b", "Beta", "beta", None, "b" * 64, beta),
        ("doc:r", "A", "a", "Alpha", "c" * 64, "#REDIRECT [[Alpha]]"),
        ("doc:x", "Loop X", "loop x", "Loop Y", "d" * 64, "#REDIRECT [[Loop Y]]"),
        ("doc:y", "Loop Y", "loop y", "Loop X", "e" * 64, "#REDIRECT [[Loop X]]"),
        ("doc:d1", "Duplicate", "duplicate", None, "f" * 64, "One"),
        ("doc:d2", "DUPLICATE", "duplicate", None, "0" * 64, "Two"),
    ]
    db.executemany("INSERT INTO documents VALUES(?,?,?,?,?,?)", rows)
    db.executemany(
        "INSERT INTO aliases VALUES(?,?,?)",
        [
            ("alpha", "doc:a", "title"),
            ("beta", "doc:b", "title"),
            ("a", "doc:r", "title"),
            ("loop x", "doc:x", "title"),
            ("aleph", "doc:a", "nickname"),
            ("alpha", "doc:a", "nickname"),
        ],
    )
    db.executemany(
        "INSERT INTO redirects VALUES(?,?,?)",
        [("doc:r", "Alpha", "c" * 64), ("doc:x", "Loop Y", "d" * 64)],
    )
    for anchor_id, raw_link, target, anchor in (
        ("anchor:1", "[[Beta|B.]]", "Beta", "b."),
        ("anchor:2", "[[Missing]]", "Missing", "missing"),
    ):
        start = alpha.index(raw_link)
        db.execute(
            "INSERT INTO anchors VALUES(?,?,?,?,?,?,?,?)",
            (
                anchor_id,
                "doc:a",
                target,
                anchor,
                start,
                start + len(raw_link),
                raw_link,
                hashlib.sha256(raw_link.encode()).hexdigest(),
            ),
        )
    db.commit()
    db.close()


def test_compiler_preserves_exact_offsets_uncertainty_and_source_immutability(
    tmp_path: Path,
) -> None:
    pack = tmp_path / "pack.sqlite"
    _pack(pack)
    before = _hash(pack)

    manifest = compile_address_pack(pack, tmp_path / "first", corpus_tier="fixture")
    second = compile_address_pack(pack, tmp_path / "second", corpus_tier="fixture")

    assert _hash(pack) == before
    assert manifest.counts["occurrences"] == 2
    assert manifest.counts["occurrence_resolution_canonical"] == 1
    assert manifest.counts["occurrence_resolution_missing"] == 1
    assert manifest.streams["occurrences"].gzip_sha256 == second.streams["occurrences"].gzip_sha256
    occurrences = list(iter_jsonl_gzip(tmp_path / "first" / "occurrences.jsonl.gz"))
    alpha = "Alpha links to [[Beta|B.]] and [[Missing]]."
    assert [alpha[item["mention_start"] : item["mention_end"]] for item in occurrences] == [
        "B.",
        "Missing",
    ]
    assert occurrences[0]["canonical_entity_id"] == canonical_entity_id("Beta")
    assert occurrences[1]["canonical_entity_id"] is None
    statistics = list(iter_jsonl_gzip(tmp_path / "first" / "surface_statistics.jsonl.gz"))
    surfaces = [item for item in statistics if item["statistics_view"] == "fit"]
    assert {item["surface"] for item in surfaces} == {"b.", "missing"}
    missing = next(item for item in surfaces if item["surface"] == "missing")
    assert missing["unresolved_probability_mass"] == 1.0
    assert missing["candidates"][0]["resolution_state"] == "missing"
    quarantine = list(iter_jsonl_gzip(tmp_path / "first" / "quarantine.jsonl.gz"))
    assert {item["record_type"] for item in quarantine} == {
        "duplicate_title",
        "unresolved_redirect",
    }
    assert all(
        str(item["record_id"]).startswith("as:v2:record:")
        for name in (
            "entities",
            "aliases",
            "redirects",
            "occurrences",
            "surface_statistics",
            "quarantine",
        )
        for item in iter_jsonl_gzip(tmp_path / "first" / f"{name}.jsonl.gz")
    )
    assert [item["record_id"] for item in occurrences] == [
        item["record_id"] for item in iter_jsonl_gzip(tmp_path / "second" / "occurrences.jsonl.gz")
    ]
    assert verify_address_export(tmp_path / "first") == manifest


def test_verifier_rejects_corrupt_stream(tmp_path: Path) -> None:
    pack = tmp_path / "pack.sqlite"
    _pack(pack)
    output = tmp_path / "compiled"
    compile_address_pack(pack, output, corpus_tier="fixture")
    with (output / "entities.jsonl.gz").open("ab") as stream:
        stream.write(b"corruption")

    with pytest.raises(AddressArtifactError, match=r"corrupt gzip|stream identity mismatch"):
        verify_address_export(output)


def test_verifier_rejects_manifest_row_count_drift(tmp_path: Path) -> None:
    pack = tmp_path / "pack.sqlite"
    _pack(pack)
    output = tmp_path / "compiled"
    compile_address_pack(pack, output, corpus_tier="fixture")
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["counts"]["entities_rows"] += 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(AddressArtifactError, match="manifest row count mismatch"):
        verify_address_export(output)


def test_semantic_loader_verifies_registry_and_preserves_unresolved_occurrences(
    tmp_path: Path,
) -> None:
    pack = tmp_path / "pack.sqlite"
    _pack(pack)
    first = tmp_path / "first"
    second = tmp_path / "second"
    compile_address_pack(pack, first, corpus_tier="fixture")
    compile_address_pack(pack, second, corpus_tier="fixture")

    bundle = load_compiler_supervision(first)
    repeated = load_compiler_supervision(second)

    assert len(bundle.occurrences) == 2
    assert len(bundle.resolved_occurrences) == 1
    assert len(bundle.quarantined_occurrences) == 1
    assert bundle.quarantined_occurrences[0].resolution_state == "missing"
    assert bundle.resolved_occurrences[0].canonical_title == "Beta"
    assert bundle.resolved_occurrences[0].corpus_tier == "fixture"
    assert bundle.resolved_occurrences[0].source_text_sha256 == "a" * 64
    assert bundle.resolved_occurrences[0].source_span_sha256
    assert bundle.resolved_occurrences[0].provenance_ids
    assert [row.occurrence_record_id for row in bundle.occurrences] == [
        row.occurrence_record_id for row in repeated.occurrences
    ]
    assert all(row.source_split in set(CorpusSourceSplit) for row in bundle.occurrences)


def test_semantic_supervision_and_blocked_index_manifests_are_identity_bound(
    tmp_path: Path,
) -> None:
    pack = tmp_path / "pack.sqlite"
    output = tmp_path / "compiled"
    _pack(pack)
    compile_address_pack(pack, output, corpus_tier="fixture")
    bundle = load_compiler_supervision(output)
    supervision_path = tmp_path / "supervision-manifest.json"

    manifest_hash = write_semantic_supervision_manifest(bundle, supervision_path)
    assert verify_semantic_supervision_manifest(bundle, supervision_path) == (
        semantic_supervision_manifest_document(bundle)
    )
    index = semantic_index_manifest_document(bundle, supervision_manifest_sha256=manifest_hash)
    assert index["status"] == "NOT_BUILT_TRAINING_READINESS_GATE"
    assert index["canonical_registry_authoritative"] is True
    assert index["ann_codes_authoritative"] is False
    split_roles = index["source_split_roles"]
    assert isinstance(split_roles, dict)
    assert str(split_roles["fit"]).endswith("fit only")

    document = json.loads(supervision_path.read_text(encoding="utf-8"))
    document["occurrences"]["count"] += 1
    supervision_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(AddressArtifactError, match="identity mismatch"):
        verify_semantic_supervision_manifest(bundle, supervision_path)


def test_semantic_loader_rejects_validly_rehashed_canonical_registry_drift(
    tmp_path: Path,
) -> None:
    pack = tmp_path / "pack.sqlite"
    output = tmp_path / "compiled"
    _pack(pack)
    compile_address_pack(pack, output, corpus_tier="fixture")

    def alter(rows: list[dict[str, object]]) -> None:
        beta = next(row for row in rows if row["title"] == "Beta")
        beta["title"] = "Not Beta"

    _rewrite_compiled_stream(output, "entities", alter)
    assert verify_address_export(output)
    with pytest.raises(AddressArtifactError, match=r"normalization mismatch|ID/title mismatch"):
        load_compiler_supervision(output)


def test_semantic_loader_rejects_occurrence_target_not_in_canonical_registry(
    tmp_path: Path,
) -> None:
    pack = tmp_path / "pack.sqlite"
    output = tmp_path / "compiled"
    _pack(pack)
    compile_address_pack(pack, output, corpus_tier="fixture")

    def alter(rows: list[dict[str, object]]) -> None:
        canonical = next(row for row in rows if row["resolution_state"] == "canonical")
        canonical["canonical_entity_id"] = canonical_entity_id("Unknown target")
        canonical["canonical_title"] = "Unknown target"

    _rewrite_compiled_stream(output, "occurrences", alter)
    assert verify_address_export(output)
    with pytest.raises(AddressArtifactError, match="absent from canonical registry"):
        load_compiler_supervision(output)


def test_semantic_loader_rejects_cross_split_source_document(tmp_path: Path) -> None:
    pack = tmp_path / "pack.sqlite"
    output = tmp_path / "compiled"
    _pack(pack)
    compile_address_pack(pack, output, corpus_tier="fixture")

    def alter(rows: list[dict[str, object]]) -> None:
        assert len(rows) == 2
        rows[1]["source_split"] = "holdout" if rows[0]["source_split"] != "holdout" else "fit"

    _rewrite_compiled_stream(output, "occurrences", alter)
    assert verify_address_export(output)
    with pytest.raises(AddressArtifactError, match="crosses corpus splits"):
        load_compiler_supervision(output)


def test_semantic_loader_rejects_invalid_source_provenance_hash(tmp_path: Path) -> None:
    pack = tmp_path / "pack.sqlite"
    output = tmp_path / "compiled"
    _pack(pack)
    compile_address_pack(pack, output, corpus_tier="fixture")

    def alter(rows: list[dict[str, object]]) -> None:
        rows[0]["source_span_sha256"] = "not-a-hash"

    _rewrite_compiled_stream(output, "occurrences", alter)
    assert verify_address_export(output)
    with pytest.raises(AddressArtifactError, match="source_span_sha256"):
        load_compiler_supervision(output)


def test_semantic_loader_rejects_compiler_manifest_identity_drift(tmp_path: Path) -> None:
    pack = tmp_path / "pack.sqlite"
    output = tmp_path / "compiled"
    _pack(pack)
    compile_address_pack(pack, output, corpus_tier="fixture")
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["streams"]["occurrences"]["jsonl_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(AddressArtifactError, match="stream identity mismatch"):
        load_compiler_supervision(output)


def _capture_row(partition: str, *, correct: list[str]) -> dict[str, object]:
    alpha = canonical_entity_id("Alpha")
    beta = canonical_entity_id("Beta")
    return {
        "schema_version": BENCHMARK_CAPTURE_SCHEMA_VERSION,
        "case_id": f"case:{partition}",
        "partition": partition,
        "corpus_tier": "10k",
        "query": "Who is Alpha?",
        "mention_id": f"mention:{partition}",
        "surface": "Alpha",
        "char_start": 7,
        "char_end": 12,
        "mention_detected": True,
        "pre_cap_candidates": [
            {
                "entity_id": beta,
                "canonical_title": "Beta",
                "channel": "alias",
                "channel_rank": 1,
                "global_pre_cap_rank": 1,
                "raw_score": 9.0,
                "channel_score": 0.9,
                "provenance_ids": ["alias:beta"],
            },
            {
                "entity_id": alpha,
                "canonical_title": "Alpha",
                "channel": "anchor",
                "channel_rank": 1,
                "global_pre_cap_rank": 2,
                "raw_score": 8.0,
                "channel_score": 0.8,
                "provenance_ids": ["anchor:alpha"],
            },
        ],
        "candidate_count_generated": 2,
        "retained_entity_ids": [beta],
        "selected_entity_ids": [beta],
        "confidence_rejected_entity_ids": [],
        "retained_cap": 1,
        "correct_entity_ids": correct,
        "alignment_basis": "author_exact_mention",
        "alignment_evidence_sha256": "1" * 64,
    }


def test_benchmark_capture_separates_runtime_development_tuning_and_quarantine(
    tmp_path: Path,
) -> None:
    capture = tmp_path / "capture.jsonl"
    rows = [
        _capture_row("development", correct=[canonical_entity_id("Alpha")]),
        _capture_row("tuning", correct=[canonical_entity_id("Alpha")]),
        _capture_row(
            "development",
            correct=[canonical_entity_id("Alpha"), canonical_entity_id("Beta")],
        ),
    ]
    rows[2]["case_id"] = "case:ambiguous"
    rows[2]["mention_id"] = "mention:ambiguous"
    capture.write_text("".join(json.dumps(item) + "\n" for item in rows), encoding="utf-8")

    manifest = compile_benchmark_capture(capture, tmp_path / "output")

    assert manifest.counts["runtime_development"] == 2
    assert manifest.counts["runtime_tuning"] == 1
    assert manifest.counts["failure_candidate_outside_cap"] == 2
    assert manifest.counts["alignment_quarantine"] == 1
    runtime = list(iter_jsonl_gzip(tmp_path / "output" / "runtime.jsonl.gz"))
    assert all("correct_entity_ids" not in item for item in runtime)
    assert len(list(iter_jsonl_gzip(tmp_path / "output" / "development_labels.jsonl.gz"))) == 1
    assert len(list(iter_jsonl_gzip(tmp_path / "output" / "tuning_labels.jsonl.gz"))) == 1


def test_benchmark_capture_rejects_sealed_partition(tmp_path: Path) -> None:
    capture = tmp_path / "capture.jsonl"
    capture.write_text(json.dumps(_capture_row("evaluation", correct=[])) + "\n", encoding="utf-8")

    with pytest.raises(AddressArtifactError, match="sealed"):
        compile_benchmark_capture(capture, tmp_path / "output")


def test_benchmark_capture_rejects_unbounded_channel_score(tmp_path: Path) -> None:
    capture = tmp_path / "capture.jsonl"
    row = _capture_row("development", correct=[])
    candidates = row["pre_cap_candidates"]
    assert isinstance(candidates, list) and isinstance(candidates[0], dict)
    candidates[0]["channel_score"] = 1.01
    capture.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(AddressArtifactError, match=r"channel_score.*\[0,1\]"):
        compile_benchmark_capture(capture, tmp_path / "output")


def test_factory_export_builds_exact_pre_cap_provenance_without_mutating_pack(
    tmp_path: Path,
) -> None:
    pack = tmp_path / "pack.sqlite"
    _pack(pack)
    pack_hash = _hash(pack)
    hard_negatives = tmp_path / "hard-negatives.json.gz"
    document = {
        "partition_counts": {"development": {"replicas": 1}, "tuning": {"replicas": 0}},
        "sealed_partitions_excluded": ["evaluation", "final_held"],
        "cases": [
            {
                "case_id": "case:dev",
                "partition": "development",
                "query": "Who is Alpha?",
                "correct_entity_ids": [canonical_entity_id("Alpha")],
                "replicas": [
                    {
                        "corpus_tier": "fixture",
                        "mentions": [
                            {
                                "surface": "Alpha",
                                "char_start": 7,
                                "char_end": 12,
                                "selected_entity_id": canonical_entity_id("Alpha"),
                                "candidates": [{"entity_id": canonical_entity_id("Alpha")}],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    with gzip.open(hard_negatives, "wt", encoding="utf-8") as stream:
        json.dump(document, stream)

    capture = tmp_path / "capture.jsonl.gz"
    result = export_v11_benchmark_capture(
        pack=pack,
        hard_negatives=hard_negatives,
        corpus_tier="fixture",
        output=capture,
    )
    compiled = compile_benchmark_capture(capture, tmp_path / "benchmark")

    assert _hash(pack) == pack_hash
    assert result["counts"]["exact_single_mention_alignments"] == 1
    assert compiled.counts["exact_alignment_development"] == 1
    runtime = next(iter_jsonl_gzip(tmp_path / "benchmark" / "runtime.jsonl.gz"))
    assert runtime["candidate_count_generated"] == 1
    assert runtime["pre_cap_candidates"][0]["channel"] == "title"
    assert runtime["pre_cap_candidates"][0]["raw_score"] == 1.0
    assert runtime["pre_cap_candidates"][0]["channel_score"] == 1.0
    assert runtime["pre_cap_candidates"][0]["provenance_ids"]


def _document_id_for_split(split: str) -> str:
    bounds = {"fit": range(0, 80), "calibration": range(80, 90), "holdout": range(90, 100)}
    for index in range(10000):
        value = f"source:{split}:{index}"
        bucket = int(hashlib.sha256(value.encode()).hexdigest()[:8], 16) % 100
        if bucket in bounds[split]:
            return value
    raise AssertionError(f"failed to construct {split} document ID")


def _split_pack(path: Path) -> None:
    db = sqlite3.connect(path)
    db.executescript(
        """
        PRAGMA user_version=500;
        CREATE TABLE documents(
          document_id TEXT PRIMARY KEY,title TEXT,normalized_title TEXT,
          redirect_target TEXT,source_text_sha256 TEXT,raw_wikitext TEXT
        );
        CREATE TABLE aliases(alias TEXT,document_id TEXT,kind TEXT);
        CREATE TABLE redirects(
          source_document_id TEXT,target_title TEXT,source_text_sha256 TEXT
        );
        CREATE TABLE anchors(
          anchor_id TEXT,source_document_id TEXT,target_title TEXT,anchor_text TEXT,
          raw_start INTEGER,raw_end INTEGER,raw_text TEXT,source_span_sha256 TEXT
        );
        """
    )
    source_text = {
        "fit": "[[Alpha|Shared]] and [[Alpha|Shared]]",
        "calibration": "[[Beta|Shared]]",
        "holdout": "[[Missing|Shared]] and [[Alpha|OnlyHoldout]]",
    }
    documents = [
        ("target:a", "Alpha", "alpha", None, "a" * 64, "Alpha"),
        ("target:b", "Beta", "beta", None, "b" * 64, "Beta"),
    ]
    sources: dict[str, str] = {}
    for split, text in source_text.items():
        document_id = _document_id_for_split(split)
        sources[split] = document_id
        title = f"Source {split}"
        documents.append(
            (
                document_id,
                title,
                title.casefold(),
                None,
                hashlib.sha256(text.encode()).hexdigest(),
                text,
            )
        )
    db.executemany("INSERT INTO documents VALUES(?,?,?,?,?,?)", documents)
    db.executemany(
        "INSERT INTO aliases VALUES(?,?,?)",
        [(row[1].casefold(), row[0], "title") for row in documents],
    )
    anchors: list[tuple[object, ...]] = []
    for split, target in (("fit", "Alpha"), ("calibration", "Beta"), ("holdout", "Missing")):
        raw = f"[[{target}|Shared]]"
        text = source_text[split]
        start = text.index(raw)
        anchors.append(
            (
                f"anchor:{split}:shared",
                sources[split],
                target,
                "shared",
                start,
                start + len(raw),
                raw,
                hashlib.sha256(raw.encode()).hexdigest(),
            )
        )
        if split == "fit":
            second = text.index(raw, start + 1)
            anchors.append(
                (
                    "anchor:fit:shared:2",
                    sources[split],
                    target,
                    "shared",
                    second,
                    second + len(raw),
                    raw,
                    hashlib.sha256(raw.encode()).hexdigest(),
                )
            )
    raw = "[[Alpha|OnlyHoldout]]"
    start = source_text["holdout"].index(raw)
    anchors.append(
        (
            "anchor:holdout:unseen",
            sources["holdout"],
            "Alpha",
            "onlyholdout",
            start,
            start + len(raw),
            raw,
            hashlib.sha256(raw.encode()).hexdigest(),
        )
    )
    db.executemany("INSERT INTO anchors VALUES(?,?,?,?,?,?,?,?)", anchors)
    db.commit()
    db.close()


def test_statistics_views_recompute_priors_without_split_leakage(tmp_path: Path) -> None:
    pack = tmp_path / "split.sqlite"
    _split_pack(pack)
    output = tmp_path / "compiled"
    manifest = compile_address_pack(pack, output, corpus_tier="fixture")
    rows = list(iter_jsonl_gzip(output / "surface_statistics.jsonl.gz"))

    shared = {row["statistics_view"]: row for row in rows if row["surface"] == "shared"}
    assert shared["fit"]["included_source_splits"] == ["fit"]
    assert shared["fit"]["occurrence_count"] == 2
    assert shared["fit"]["entropy_nats"] == 0.0
    assert shared["fit"]["unresolved_probability_mass"] == 0.0
    assert shared["fit"]["candidates"][0]["source_document_count"] == 1
    assert shared["fit"]["candidates"][0]["source_diversity"] == 0.5
    assert shared["fit+calibration"]["occurrence_count"] == 3
    expected_fit_calibration_entropy = -(2 / 3 * math.log(2 / 3) + 1 / 3 * math.log(1 / 3))
    assert shared["fit+calibration"]["entropy_nats"] == pytest.approx(
        expected_fit_calibration_entropy
    )
    assert shared["all"]["occurrence_count"] == 4
    expected_all_entropy = -(0.5 * math.log(0.5) + 2 * (0.25 * math.log(0.25)))
    assert shared["all"]["entropy_nats"] == pytest.approx(expected_all_entropy)
    assert shared["all"]["unresolved_probability_mass"] == pytest.approx(1 / 4)
    assert sum(item["probability"] for item in shared["all"]["candidates"]) == pytest.approx(1)
    assert all(item["source_document_count"] == 1 for item in shared["all"]["candidates"])

    unseen = [row for row in rows if row["surface"] == "onlyholdout"]
    assert [row["statistics_view"] for row in unseen] == ["all"]
    assert unseen[0]["unseen_surface_in_holdout"] is True
    assert manifest.views["surface_statistics"]["fit"]["usage"] == "fit_and_selection"

    assert list(iter_surface_statistics_view(output, view="fit", consumer_phase="selection"))
    with pytest.raises(AddressArtifactError, match="only the fit prior"):
        list(
            iter_surface_statistics_view(output, view="fit+calibration", consumer_phase="selection")
        )
    with pytest.raises(AddressArtifactError, match="all-data"):
        list(
            iter_surface_statistics_view(output, view="all", consumer_phase="holdout_qualification")
        )
    exact_artifact = compile_verified_exact_address_index(output, tmp_path / "split.fst")
    exact = ExactAddressIndex(Path(exact_artifact.path), Path(exact_artifact.manifest_path)).lookup(
        "shared"
    )
    assert exact is not None
    assert len(exact.postings) == 1
    assert exact.postings[0].anchor_support_count == 2
    assert exact.postings[0].source_document_count == 1
    assert exact.postings[0].source_diversity == 0.5
    assert exact.unresolved_probability_mass == 0.0


def test_bundle_registry_and_exact_adapter_preserve_authority_and_provenance(
    tmp_path: Path,
) -> None:
    pack = tmp_path / "pack.sqlite"
    _pack(pack)
    output = tmp_path / "compiled"
    compile_address_pack(pack, output, corpus_tier="fixture")
    bundle = verify_address_bundle(output)
    registry = load_canonical_registry(output, expected_bundle=bundle)
    registry.require_pair(canonical_entity_id("Alpha"), "Alpha")

    artifact = compile_verified_exact_address_index(
        output, tmp_path / "address.fst", expected_bundle=bundle
    )
    index = ExactAddressIndex(Path(artifact.path), Path(artifact.manifest_path))
    assert index.source_artifact_sha256 == bundle.manifest_sha256
    alias = index.lookup("aleph")
    redirect = index.lookup("a")
    anchor = index.lookup("b.")
    unresolved = index.lookup("missing")
    multi_channel = index.lookup("alpha")
    assert alias is not None and alias.postings[0].channels == (AddressChannel.ALIAS,)
    assert alias.postings[0].provenance_ids[0].startswith("alias:as:v2:record:")
    assert redirect is not None and AddressChannel.REDIRECT in redirect.postings[0].channels
    assert anchor is not None and anchor.postings[0].anchor_support_count == 1
    assert anchor.postings[0].source_document_count == 1
    assert unresolved is not None and unresolved.unresolved_probability_mass == 1.0
    assert unresolved.unresolved_provenance_ids[0].startswith("anchor:as:v2:record:")
    assert multi_channel is not None
    assert multi_channel.postings[0].channels == (AddressChannel.TITLE, AddressChannel.ALIAS)
    assert multi_channel.postings[0].title_support_count == 1
    assert multi_channel.postings[0].alias_support_count == 1
    assert {value.split(":", 1)[0] for value in multi_channel.postings[0].provenance_ids} == {
        "title",
        "alias",
    }

    with pytest.raises(AddressArtifactError, match="only fit occurrences"):
        compile_verified_exact_address_index(
            output,
            tmp_path / "leaky.fst",
            included_source_splits=("fit", "calibration"),
            consumer_phase="fit",
        )


def test_registry_bundle_and_closed_wire_contract_reject_adversarial_inputs(
    tmp_path: Path,
) -> None:
    empty_bundle = AddressBundleIdentity(
        schema_version="aethersparse.semantic-address-manifest.v2",
        manifest_sha256="1" * 64,
        source_pack_sha256="2" * 64,
        corpus_tier="fixture",
        streams=(),
    )
    with pytest.raises(ValueError, match="ID/title"):
        CanonicalAddressRegistry(
            bundle=empty_bundle,
            entries=(
                CanonicalRegistryEntry(
                    record_id="as:v2:record:" + "3" * 64,
                    entity_id=canonical_entity_id("Alpha"),
                    canonical_title="Beta",
                    normalized_title="beta",
                    source_document_id="doc:a",
                ),
            ),
        )
    with pytest.raises(ValueError, match="ID/title"):
        CanonicalAddressRegistry(
            bundle=empty_bundle,
            entries=(
                CanonicalRegistryEntry(
                    record_id="as:v2:record:" + "3" * 64,
                    entity_id="entity:not-canonical",
                    canonical_title="Alpha",
                    normalized_title="alpha",
                    source_document_id="doc:a",
                ),
            ),
        )

    row = with_stable_record_id(
        {
            "schema_version": "aethersparse.semantic-address-export.v2",
            "record_type": "entity",
            "entity_id": canonical_entity_id("Alpha"),
            "title": "Alpha",
            "normalized_title": "alpha",
            "document_id": "doc:a",
            "source_text_sha256": "4" * 64,
            "unexpected": "closed",
        }
    )
    with pytest.raises(ValueError, match="unknown fields"):
        validate_record_contract(row)
    assert fusion_channel_name(AddressChannelV2.TITLE) == "exact_title"
    assert fusion_channel_name(AddressChannelV2.ANCHOR) == "anchor_prior"

    pack = tmp_path / "pack.sqlite"
    _pack(pack)
    first = tmp_path / "first"
    second = tmp_path / "second"
    compile_address_pack(pack, first, corpus_tier="fixture")
    compile_address_pack(pack, second, corpus_tier="other")
    expected = verify_address_bundle(second)
    with pytest.raises(AddressArtifactError, match="does not match"):
        verify_address_bundle(first, expected=expected)

    record_schema = json.loads(
        (Path(__file__).parents[2] / "schemas" / "semantic-address-v2.schema.json").read_text()
    )
    manifest_schema = json.loads(
        (
            Path(__file__).parents[2] / "schemas" / "semantic-address-v2-manifest.schema.json"
        ).read_text()
    )
    assert record_schema["oneOf"]
    assert all(
        record_schema["$defs"][name]["unevaluatedProperties"] is False
        for name in (
            "entity",
            "alias",
            "redirect",
            "hyperlinkOccurrence",
            "surfaceStatistics",
            "duplicateTitle",
            "unresolvedRedirect",
            "benchmarkRuntime",
            "benchmarkLabel",
            "alignmentQuarantine",
        )
    )
    assert manifest_schema["additionalProperties"] is False
