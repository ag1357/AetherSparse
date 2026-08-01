from __future__ import annotations

from collections.abc import Callable

import pytest

from aethersparse.substrate import (
    ClaimAttribute,
    ClaimKind,
    ClaimSeed,
    FlatStructuredPack,
    ObjectKind,
    SourcePage,
    StructuredSubstrateBuilder,
    SubstrateMetadata,
)


def _page(page_id: str, title: str, text: str) -> SourcePage:
    return SourcePage(
        page_id=page_id,
        namespace=0,
        revision_id=f"r-{page_id}",
        revision_timestamp="2026-08-01T00:00:00Z",
        title=title,
        source_url=f"https://simple.wikipedia.org/?curid={page_id}",
        license="CC-BY-SA-4.0",
        text=text,
    )


@pytest.fixture
def build_fixture_pack() -> Callable[[], FlatStructuredPack]:
    def build() -> FlatStructuredPack:
        mercury = (
            "Café astronomy describes Mercury as the closest planet to the Sun.\n\n"
            "== Physical characteristics ==\n"
            "Mercury has a mass of 3.3011\N{MULTIPLICATION SIGN}10^23 kg.\n\n"
            "== Observation ==\n"
            "A transit of Mercury occurred on 7 November 1631.\n\n"
            'Astronomer Ada said, “Mercury moves quickly.”'
        )
        planet = (
            "A planet is an astronomical body. "
            "[[Mercury|The innermost planet]] is a planet in the Solar System."
        )
        pages = (
            _page("1", "Mercury", mercury),
            _page("2", "Quick Silver", "#REDIRECT [[Mercury]]"),
            _page("3", "Hydrargyrum", "#REDIRECT [[Mercury]]"),
            _page("4", "Planet", planet),
        )
        claims = (
            ClaimSeed(
                page_id="1",
                subject_title="Mercury",
                relation_family="mass",
                object_value="3.3011\N{MULTIPLICATION SIGN}10^23 kg",
                object_kind=ObjectKind.QUANTITY,
                claim_kind=ClaimKind.QUANTITY,
                evidence_text=(
                    "Mercury has a mass of 3.3011\N{MULTIPLICATION SIGN}10^23 kg."
                ),
                attributes=(ClaimAttribute(key="unit", value="kg"),),
            ),
            ClaimSeed(
                page_id="1",
                subject_title="Mercury",
                relation_family="transit date",
                object_value="7 November 1631",
                object_kind=ObjectKind.DATE,
                claim_kind=ClaimKind.DATE,
                evidence_text="A transit of Mercury occurred on 7 November 1631.",
            ),
            ClaimSeed(
                page_id="1",
                subject_title="Mercury",
                relation_family="transit event",
                object_value="transit of Mercury",
                object_kind=ObjectKind.EVENT,
                claim_kind=ClaimKind.EVENT,
                evidence_text="A transit of Mercury occurred on 7 November 1631.",
            ),
            ClaimSeed(
                page_id="1",
                subject_title="Mercury",
                relation_family="quotation",
                object_value="Mercury moves quickly.",
                object_kind=ObjectKind.QUOTATION,
                claim_kind=ClaimKind.QUOTATION,
                evidence_text='Astronomer Ada said, “Mercury moves quickly.”',
                attributes=(ClaimAttribute(key="speaker", value="Astronomer Ada"),),
            ),
        )
        metadata = SubstrateMetadata(
            series_id="simplewiki_v050_fixture_r1",
            source_dump_id="simplewiki-20260801-pages-articles",
            source_dump_sha256="sha256:" + "1" * 64,
            parser_identity="mediawiki-xml-v050-distinct-source-pages",
            normalization_identity="unicode-nfkc-casefold-v1",
            build_command="aethersparse corpus build --limit 4",
        )
        return StructuredSubstrateBuilder(metadata, max_chunk_chars=256).build(
            pages,
            claim_seeds=claims,
            entity_types={"Mercury": "planet", "Planet": "class"},
        )

    return build
