from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path

import pytest

from aethersparse.addressing.exact import (
    AddressChannel,
    AddressEvidence,
    AddressIndexError,
    ExactAddressIndex,
    compile_exact_address_index,
    normalize_surface,
)


def _row(
    surface: str,
    entity_id: str | None,
    title: str | None,
    *,
    support: int = 1,
    source: str = "doc:1",
    channel: AddressChannel = AddressChannel.ANCHOR,
    provenance: str = "span:1",
    unresolved_key: str | None = None,
) -> AddressEvidence:
    return AddressEvidence(
        surface=surface,
        entity_id=entity_id,
        canonical_title=title,
        support_count=support,
        source_document_ids=(source,),
        channel=channel,
        provenance_ids=(provenance,),
        unresolved_key=unresolved_key,
    )


def _compile(tmp_path: Path, rows: list[AddressEvidence]) -> tuple[Path, Path]:
    output = tmp_path / "address.fst"
    artifact = compile_exact_address_index(
        rows, output, source_artifact_sha256="a" * 64, source_partitions=("development",)
    )
    assert artifact.total_bytes == output.stat().st_size
    return output, Path(artifact.manifest_path)


def test_round_trip_returns_distribution_offset_and_lossless_prior(tmp_path: Path) -> None:
    rows = [
        _row("Mercury", "entity:planet", "Mercury (planet)", support=3),
        _row(
            "Mercury",
            "entity:element",
            "Mercury (element)",
            support=1,
            source="doc:2",
            provenance="span:2",
        ),
        _row(
            "Mercury",
            None,
            None,
            source="doc:3",
            provenance="span:3",
            unresolved_key="unknown:mercury",
        ),
    ]
    path, manifest = _compile(tmp_path, rows)
    index = ExactAddressIndex(path, manifest)

    result = index.lookup("  MERCURY ")

    assert result is not None
    assert result.normalized_surface == "mercury"
    assert result.posting_offset > 0
    assert [item.entity_id for item in result.postings] == ["entity:planet", "entity:element"]
    assert [item.prior for item in result.postings] == pytest.approx([3 / 5, 1 / 5])
    assert result.unresolved_probability_mass == pytest.approx(1 / 5)
    assert result.unresolved_provenance_ids == ("span:3",)
    assert result.probability_mass == pytest.approx(1.0)
    assert result.total_candidate_count == 2
    assert result.ambiguity_count == 3
    assert result.truncated is False


def test_alias_collision_preserves_all_canonical_ids(tmp_path: Path) -> None:
    path, manifest = _compile(
        tmp_path,
        [
            _row("Washington", "entity:state", "Washington", channel=AddressChannel.ALIAS),
            _row(
                "Washington",
                "entity:person",
                "George Washington",
                source="doc:2",
                channel=AddressChannel.ALIAS,
                provenance="span:2",
            ),
        ],
    )

    result = ExactAddressIndex(path, manifest).lookup("Washington")

    assert result is not None
    assert {item.entity_id for item in result.postings} == {"entity:state", "entity:person"}
    assert all(item.channels == (AddressChannel.ALIAS,) for item in result.postings)


def test_redirect_collision_is_not_forced_to_one_target(tmp_path: Path) -> None:
    path, manifest = _compile(
        tmp_path,
        [
            _row("Mercury", "entity:planet", "Mercury (planet)", channel=AddressChannel.REDIRECT),
            _row(
                "Mercury",
                "entity:element",
                "Mercury (element)",
                source="doc:2",
                channel=AddressChannel.REDIRECT,
                provenance="span:2",
            ),
        ],
    )

    result = ExactAddressIndex(path, manifest).lookup("mercury")

    assert result is not None
    assert result.total_candidate_count == 2
    assert [posting.redirect_support_count for posting in result.postings] == [1, 1]
    assert result.ambiguity_entropy_nats == pytest.approx(math.log(2))


def test_duplicate_normalized_titles_remain_separate_postings(tmp_path: Path) -> None:
    path, manifest = _compile(
        tmp_path,
        [
            _row("Cafe\N{COMBINING ACUTE ACCENT}", "entity:one", "Café"),
            _row(
                "Café",
                "entity:two",
                "Cafe\N{COMBINING ACUTE ACCENT}",
                source="doc:2",
                provenance="span:2",
            ),
        ],
    )

    result = ExactAddressIndex(path, manifest).lookup("CAFÉ")

    assert result is not None
    assert result.total_candidate_count == 2
    assert normalize_surface("Cafe\N{COMBINING ACUTE ACCENT}") == "café"


def test_radix_fst_preserves_terminal_prefixes(tmp_path: Path) -> None:
    path, manifest = _compile(
        tmp_path,
        [
            _row("A", "entity:a", "A"),
            _row("Alpha", "entity:alpha", "Alpha", source="doc:2", provenance="span:2"),
        ],
    )
    index = ExactAddressIndex(path, manifest)

    short = index.lookup("a")
    long = index.lookup("alpha")

    assert short is not None and short.postings[0].entity_id == "entity:a"
    assert long is not None and long.postings[0].entity_id == "entity:alpha"
    assert index.lookup("alphabet") is None


def test_duplicate_rows_merge_channels_and_exact_provenance(tmp_path: Path) -> None:
    path, manifest = _compile(
        tmp_path,
        [
            _row("Alpha", "entity:alpha", "Alpha", channel=AddressChannel.TITLE),
            _row(
                "Alpha",
                "entity:alpha",
                "Alpha",
                support=2,
                source="doc:2",
                channel=AddressChannel.ALIAS,
                provenance="span:2",
            ),
        ],
    )

    result = ExactAddressIndex(path, manifest).lookup("alpha")

    assert result is not None
    posting = result.postings[0]
    assert posting.support_count == 3
    assert posting.source_document_count == 2
    assert posting.source_diversity == pytest.approx(2 / 3)
    assert posting.title_support_count == 1
    assert posting.alias_support_count == 2
    assert posting.provenance_ids == ("span:1", "span:2")


def test_cap_saturation_reports_omitted_mass(tmp_path: Path) -> None:
    rows = [
        _row(
            "shared",
            f"entity:{index}",
            f"Entity {index}",
            support=8 - index,
            source=f"doc:{index}",
            provenance=f"span:{index}",
        )
        for index in range(4)
    ]
    path, manifest = _compile(tmp_path, rows)

    result = ExactAddressIndex(path, manifest).lookup("shared", max_postings=2)

    assert result is not None
    assert result.truncated is True
    assert result.total_candidate_count == 4
    assert result.omitted_candidate_count == 2
    assert result.omitted_probability_mass > 0
    assert result.probability_mass == pytest.approx(1.0)


def test_serialization_is_deterministic_and_content_addressed(tmp_path: Path) -> None:
    rows = [
        _row("Alpha", "entity:a", "Alpha", channel=AddressChannel.TITLE),
        _row("Beta", "entity:b", "Beta", source="doc:2", provenance="span:2"),
    ]
    first = tmp_path / "first.fst"
    second = tmp_path / "second.fst"
    first_artifact = compile_exact_address_index(rows, first, source_artifact_sha256="b" * 64)
    second_artifact = compile_exact_address_index(
        reversed(rows), second, source_artifact_sha256="b" * 64
    )

    assert first.read_bytes() == second.read_bytes()
    assert first_artifact.root_sha256 == second_artifact.root_sha256
    assert first_artifact.file_sha256 == second_artifact.file_sha256


def test_manifest_corruption_is_rejected(tmp_path: Path) -> None:
    path, manifest_path = _compile(tmp_path, [_row("Alpha", "entity:a", "Alpha")])
    manifest = json.loads(manifest_path.read_text())
    manifest["file_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(AddressIndexError, match="file_sha256"):
        ExactAddressIndex(path, manifest_path)


def test_payload_corruption_is_rejected_by_section_hash(tmp_path: Path) -> None:
    path, manifest = _compile(tmp_path, [_row("Alpha", "entity:a", "Alpha")])
    payload = bytearray(path.read_bytes())
    payload[-1] ^= 1
    path.write_bytes(payload)

    with pytest.raises(AddressIndexError, match="section checksum"):
        ExactAddressIndex(path, manifest)


def test_conflicting_canonical_titles_are_rejected(tmp_path: Path) -> None:
    rows = [
        _row("Alpha", "entity:a", "Alpha"),
        _row("A", "entity:a", "Entirely Different", source="doc:2", provenance="span:2"),
    ]

    with pytest.raises(AddressIndexError, match="conflicting titles"):
        _compile(tmp_path, rows)


def test_sealed_partition_cannot_enter_compilation(tmp_path: Path) -> None:
    with pytest.raises(AddressIndexError, match="sealed"):
        compile_exact_address_index(
            [_row("Alpha", "entity:a", "Alpha")],
            tmp_path / "address.fst",
            source_artifact_sha256="a" * 64,
            source_partitions=("development", "evaluation"),
        )


def test_evidence_rejects_unbound_unresolved_and_duplicate_sources() -> None:
    valid = _row("Alpha", "entity:a", "Alpha")
    with pytest.raises(AddressIndexError, match="unresolved_key"):
        replace(valid, entity_id=None, canonical_title=None)
    with pytest.raises(AddressIndexError, match="duplicates"):
        replace(valid, source_document_ids=("doc:1", "doc:1"), support_count=2)
