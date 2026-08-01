from aethersparse.substrate.extraction import extract_claim_seeds
from aethersparse.substrate.models import ClaimKind, ObjectKind, SourcePage


def _page(text: str, *, title: str = "Ada Example") -> SourcePage:
    return SourcePage(
        page_id="42",
        revision_id="7",
        revision_timestamp="2026-07-01T00:00:00Z",
        title=title,
        source_url="https://simple.wikipedia.org/?curid=42",
        license="CC-BY-SA-4.0",
        text=text,
    )


def test_extracts_typed_infobox_and_prose_claims_with_exact_offsets() -> None:
    text = """{{Infobox person
| birth_date = 1815
| population = 12,500 people
| birth_place = [[Example Town]]
| image = Example portrait.jpg
}}
Ada Example was a mathematician born in 1815.
Ada Example said "exact words remain copied".
"""

    seeds = extract_claim_seeds((_page(text),))

    assert any(seed.claim_kind is ClaimKind.PROPOSITION for seed in seeds)
    assert any(seed.claim_kind is ClaimKind.DATE for seed in seeds)
    assert any(seed.claim_kind is ClaimKind.QUANTITY for seed in seeds)
    assert any(seed.claim_kind is ClaimKind.QUOTATION for seed in seeds)
    assert any(seed.object_kind is ObjectKind.LOCATION for seed in seeds)
    assert all("image" not in seed.relation_family for seed in seeds)
    for seed in seeds:
        assert seed.char_start is not None
        assert seed.char_end is not None
        assert text[seed.char_start : seed.char_end] == seed.evidence_text


def test_redirects_do_not_create_structured_claims() -> None:
    page = _page("#REDIRECT [[Ada Example]]", title="A. Example")

    assert extract_claim_seeds((page,)) == ()


def test_claim_extraction_is_bounded_and_deterministic() -> None:
    page = _page("Ada Example was created in 1901 and ended in 1902.\n" * 20)

    first = extract_claim_seeds((page,), max_claims_per_page=4)
    second = extract_claim_seeds((page,), max_claims_per_page=4)

    assert first == second
    assert 1 <= len(first) <= 4
