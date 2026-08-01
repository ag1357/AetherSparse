"""Bounded deterministic claim extraction from immutable MediaWiki source pages.

The extractor is deliberately conservative.  It emits a structured value only
when that value is an exact contiguous surface in the original wikitext.  The
flat source remains authoritative and every seed carries explicit character
coordinates for the substrate builder to verify.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Sequence

from aethersparse.substrate.builder import normalize_surface
from aethersparse.substrate.models import (
    ClaimAttribute,
    ClaimKind,
    ClaimSeed,
    ObjectKind,
    SourcePage,
)

REDIRECT_RE = re.compile(r"^\s*#redirect\b", re.IGNORECASE)
INFOBOX_START_RE = re.compile(r"\{\{\s*infobox\b", re.IGNORECASE)
INFOBOX_FIELD_RE = re.compile(
    r"(?m)^\s*\|\s*([A-Za-z][A-Za-z0-9 _-]{0,63})\s*=\s*([^\n]*?)\s*$"
)
PROPOSITION_RE = re.compile(
    r"(?mi)(?:^|\n)([^{}\n]{3,120}?\b(?:is|are|was|were)\b[^{}\n]{4,360}?[.!?])"
)
EVENT_RE = re.compile(
    r"(?mi)(?:^|\n)([^{}\n]{3,360}?\b(?:occurred|happened|began|ended|"
    r"was founded|was opened|was launched|was created)\b[^{}\n]{0,240}?[.!?])"
)
YEAR_RE = re.compile(r"\b(?:1[0-9]{3}|20[0-9]{2}|2100)\b")
DATE_RE = re.compile(
    r"\b(?:[0-3]?\d\s+)?(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+(?:[0-3]?\d,?\s+)?(?:1[0-9]{3}|20[0-9]{2})\b",
    re.IGNORECASE,
)
QUANTITY_RE = re.compile(
    r"\b[-+]?\d+(?:[,.]\d+)?(?:\s*(?:-|to)\s*\d+(?:[,.]\d+)?)?\s*"
    r"(?:%|percent|km|kilomet(?:er|re)s?|miles?|m|met(?:er|re)s?|cm|mm|kg|"
    r"kilograms?|g|grams?|tonnes?|tons?|lit(?:er|re)s?|people|inhabitants?|"
    r"years?|months?|days?|hours?|minutes?|seconds?|million|billion)\b",
    re.IGNORECASE,
)
QUOTATION_RE = re.compile(r"[\"“]([^\"”\n]{5,240})[\"”]")
WIKI_LINK_RE = re.compile(r"^\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]$")
MARKUP_ONLY_RE = re.compile(r"^(?:<!--.*?-->|<ref\b.*?</ref>|<ref\b[^>]*/>)$", re.I)

DATE_FIELD_CUES = frozenset(
    {
        "birth date",
        "death date",
        "date",
        "date formed",
        "date founded",
        "date opened",
        "established date",
        "formation",
        "founded",
        "start date",
        "end date",
        "year",
        "year start",
        "year end",
    }
)
QUANTITY_FIELD_CUES = frozenset(
    {
        "area",
        "area total",
        "distance",
        "elevation",
        "height",
        "length",
        "mass",
        "population",
        "population total",
        "weight",
        "width",
    }
)
EVENT_FIELD_CUES = frozenset({"event", "formation", "founded", "opened", "established"})
LOCATION_FIELD_PARTS = ("birth place", "death place", "location", "country", "capital")
NON_FACT_FIELD_PARTS = (
    "alt",
    "caption",
    "image",
    "logo",
    "map",
    "module",
    "pushpin",
    "style",
    "template",
)


def _template_end(text: str, start: int) -> int:
    """Return the balanced end of a template, or a small fail-closed window."""

    depth = 0
    cursor = start
    limit = min(len(text), start + 64 * 1024)
    while cursor < limit - 1:
        pair = text[cursor : cursor + 2]
        if pair == "{{":
            depth += 1
            cursor += 2
            continue
        if pair == "}}":
            depth -= 1
            cursor += 2
            if depth == 0:
                return cursor
            continue
        cursor += 1
    return start


def _object_kind(field: str, value: str) -> tuple[ObjectKind, ClaimKind]:
    key = normalize_surface(field).replace("_", " ")
    if key in DATE_FIELD_CUES or DATE_RE.fullmatch(value) or YEAR_RE.fullmatch(value):
        return ObjectKind.DATE, ClaimKind.DATE
    if key in QUANTITY_FIELD_CUES or QUANTITY_RE.fullmatch(value):
        return ObjectKind.QUANTITY, ClaimKind.QUANTITY
    if key in EVENT_FIELD_CUES:
        return ObjectKind.EVENT, ClaimKind.EVENT
    if any(part in key for part in LOCATION_FIELD_PARTS):
        return ObjectKind.LOCATION, ClaimKind.PROPOSITION
    if QUOTATION_RE.fullmatch(value):
        return ObjectKind.QUOTATION, ClaimKind.QUOTATION
    if WIKI_LINK_RE.fullmatch(value):
        return ObjectKind.ENTITY, ClaimKind.PROPOSITION
    return ObjectKind.TEXT, ClaimKind.PROPOSITION


def _visible_object(value: str) -> str:
    link = WIKI_LINK_RE.fullmatch(value)
    if link is None:
        return value
    return (link.group(2) or link.group(1)).strip()


def _seed(
    page: SourcePage,
    *,
    relation: str,
    value: str,
    start: int,
    end: int,
    object_kind: ObjectKind,
    claim_kind: ClaimKind,
    attributes: tuple[ClaimAttribute, ...] = (),
) -> ClaimSeed:
    return ClaimSeed(
        page_id=page.page_id,
        subject_title=page.title,
        relation_family=relation,
        object_value=value,
        object_kind=object_kind,
        claim_kind=claim_kind,
        evidence_text=page.text[start:end],
        char_start=start,
        char_end=end,
        attributes=attributes,
    )


def _infobox_seeds(page: SourcePage) -> Iterator[ClaimSeed]:
    match = INFOBOX_START_RE.search(page.text)
    if match is None:
        return
    end = _template_end(page.text, match.start())
    if end <= match.start():
        return
    body = page.text[match.start() : end]
    for field_match in INFOBOX_FIELD_RE.finditer(body):
        raw_value = field_match.group(2)
        value = raw_value.strip()
        if not value or value in {"-", "—", "N/A", "n/a"} or MARKUP_ONLY_RE.fullmatch(value):
            continue
        local_start = field_match.start(2) + (len(raw_value) - len(raw_value.lstrip()))
        start = match.start() + local_start
        end_offset = start + len(value)
        field = normalize_surface(field_match.group(1)).replace("_", " ")
        if any(part in field for part in NON_FACT_FIELD_PARTS):
            continue
        object_kind, claim_kind = _object_kind(field, value)
        visible = _visible_object(value)
        yield _seed(
            page,
            relation=f"infobox {field}",
            value=visible,
            start=start,
            end=end_offset,
            object_kind=object_kind,
            claim_kind=claim_kind,
            attributes=(ClaimAttribute(key="infobox_field", value=field),),
        )


def _sentence_seeds(page: SourcePage) -> Iterator[ClaimSeed]:
    # Keep active extraction bounded to the lead-sized source prefix.
    text = page.text[:16_384]
    proposition_count = 0
    for match in PROPOSITION_RE.finditer(text):
        sentence = match.group(1).strip()
        if page.title.casefold() not in re.sub(r"'{2,5}", "", sentence).casefold():
            continue
        raw = match.group(1)
        start = match.start(1) + (len(raw) - len(raw.lstrip()))
        end = start + len(sentence)
        yield _seed(
            page,
            relation="definition",
            value=sentence,
            start=start,
            end=end,
            object_kind=ObjectKind.TEXT,
            claim_kind=ClaimKind.PROPOSITION,
        )
        proposition_count += 1
        if proposition_count >= 2:
            break

    for event_index, match in enumerate(EVENT_RE.finditer(text)):
        sentence = match.group(1).strip()
        raw = match.group(1)
        start = match.start(1) + (len(raw) - len(raw.lstrip()))
        end = start + len(sentence)
        yield _seed(
            page,
            relation="event",
            value=sentence,
            start=start,
            end=end,
            object_kind=ObjectKind.EVENT,
            claim_kind=ClaimKind.EVENT,
        )
        if event_index >= 1:
            break

    # Dates, quantities, and quotations retain the exact matched surface.
    typed_patterns: tuple[
        tuple[re.Pattern[str], str, ObjectKind, ClaimKind, int], ...
    ] = (
        (DATE_RE, "date mentioned", ObjectKind.DATE, ClaimKind.DATE, 2),
        (YEAR_RE, "year mentioned", ObjectKind.DATE, ClaimKind.DATE, 3),
        (QUANTITY_RE, "quantity mentioned", ObjectKind.QUANTITY, ClaimKind.QUANTITY, 3),
        (QUOTATION_RE, "quotation", ObjectKind.QUOTATION, ClaimKind.QUOTATION, 2),
    )
    for pattern, relation, object_kind, claim_kind, limit in typed_patterns:
        emitted = 0
        for match in pattern.finditer(text):
            group = 1 if pattern is QUOTATION_RE else 0
            value = match.group(group).strip()
            start, end = match.span(group)
            if not value:
                continue
            yield _seed(
                page,
                relation=relation,
                value=value,
                start=start,
                end=end,
                object_kind=object_kind,
                claim_kind=claim_kind,
            )
            emitted += 1
            if emitted >= limit:
                break


def iter_claim_seeds(
    pages: Iterable[SourcePage],
    *,
    max_claims_per_page: int = 32,
) -> Iterator[ClaimSeed]:
    """Stream bounded exact claim seeds without retaining a corpus-wide graph."""

    if max_claims_per_page < 1 or max_claims_per_page > 128:
        raise ValueError("max_claims_per_page must be between 1 and 128")
    for page in pages:
        if REDIRECT_RE.match(page.text):
            continue
        seen: set[tuple[str, str, int | None, int | None]] = set()
        emitted = 0
        for seed in (*tuple(_infobox_seeds(page)), *tuple(_sentence_seeds(page))):
            key = (seed.relation_family, seed.object_value, seed.char_start, seed.char_end)
            if key in seen:
                continue
            seen.add(key)
            yield seed
            emitted += 1
            if emitted >= max_claims_per_page:
                break


def extract_claim_seeds(
    pages: Sequence[SourcePage],
    *,
    max_claims_per_page: int = 32,
) -> tuple[ClaimSeed, ...]:
    """Materialize deterministic seeds for explicitly bounded substrate builds."""

    return tuple(iter_claim_seeds(pages, max_claims_per_page=max_claims_per_page))
