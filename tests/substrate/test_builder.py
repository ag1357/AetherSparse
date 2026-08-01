from __future__ import annotations

from collections.abc import Callable

import pytest

from aethersparse.substrate import (
    ClaimKind,
    FlatStructuredPack,
    SubstrateBuildError,
    validate_source_bindings,
)


def test_identical_redirect_text_never_collapses_distinct_pages(
    build_fixture_pack: Callable[[], FlatStructuredPack],
) -> None:
    pack = build_fixture_pack()
    redirects = [document for document in pack.documents if document.is_redirect]

    assert len(pack.documents) == 4
    assert len(redirects) == 2
    assert redirects[0].source_sha256 == redirects[1].source_sha256
    assert redirects[0].document_id != redirects[1].document_id
    assert redirects[0].page_id != redirects[1].page_id
    assert len(pack.redirects) == 2
    assert {redirect.target_entity_id for redirect in pack.redirects} == {
        next(entity.entity_id for entity in pack.entities if entity.canonical_title == "Mercury")
    }


def test_bindings_preserve_exact_unicode_character_and_byte_coordinates(
    build_fixture_pack: Callable[[], FlatStructuredPack],
) -> None:
    pack = build_fixture_pack()
    validate_source_bindings(pack)
    document = next(document for document in pack.documents if document.title == "Mercury")
    binding = next(
        binding
        for binding in pack.source_bindings
        if binding.surface
        == "Mercury has a mass of 3.3011\N{MULTIPLICATION SIGN}10^23 kg."
    )

    assert document.text[binding.char_start : binding.char_end] == binding.surface
    assert (
        document.text.encode("utf-8")[binding.byte_start : binding.byte_end].decode("utf-8")
        == binding.surface
    )
    assert binding.byte_start > binding.char_start  # "Café" and the em dash precede it.


def test_all_structured_record_kinds_retain_exact_source_bindings(
    build_fixture_pack: Callable[[], FlatStructuredPack],
) -> None:
    pack = build_fixture_pack()
    binding_ids = {binding.binding_id for binding in pack.source_bindings}

    assert {claim.claim_kind for claim in pack.claims} == {
        ClaimKind.DATE,
        ClaimKind.EVENT,
        ClaimKind.QUANTITY,
        ClaimKind.QUOTATION,
    }
    assert all(set(claim.source_binding_ids) <= binding_ids for claim in pack.claims)
    assert pack.anchors[0].surface == "The innermost planet"
    assert pack.anchors[0].binding_id in binding_ids
    assert {alias.surface for alias in pack.aliases} >= {
        "Mercury",
        "Quick Silver",
        "Hydrargyrum",
        "The innermost planet",
    }


def test_ambiguous_evidence_is_rejected_instead_of_guessed(
    build_fixture_pack: Callable[[], FlatStructuredPack],
) -> None:
    pack = build_fixture_pack()
    binding = pack.source_bindings[0]
    damaged = binding.model_copy(update={"surface": "invented"})
    damaged_pack = pack.model_copy(
        update={
            "source_bindings": tuple(
                damaged if item.binding_id == binding.binding_id else item
                for item in pack.source_bindings
            )
        }
    )

    with pytest.raises(SubstrateBuildError, match="coordinates mismatch"):
        validate_source_bindings(damaged_pack)


def test_build_is_byte_stable(build_fixture_pack: Callable[[], FlatStructuredPack]) -> None:
    first = build_fixture_pack()
    second = build_fixture_pack()

    assert first.manifest_sha256 == second.manifest_sha256
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
