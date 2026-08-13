from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from aethersparse.controller.framing import QueryFramer
from aethersparse.controller.models import AnswerShape, ExactSourceSpan
from aethersparse.controller.sqlite_provider import SQLiteControllerProvider
from aethersparse.controller.value_lattice import (
    SourceValueRegion,
    TypedValueCandidate,
    ValueType,
    scan_typed_value_region,
)
from aethersparse.substrate.extraction import diagnose_value_enumeration
from aethersparse.substrate.models import SourcePage


def _region(text: str) -> SourceValueRegion:
    return SourceValueRegion(
        document_id="doc:1",
        source_title="Example",
        source_revision="rev:1",
        source_url="https://example.test/1",
        source_family="example",
        char_start=100,
        text=text,
        section="Lead",
    )


def test_typed_lattice_retains_exact_competing_quantity_surfaces() -> None:
    text = "At -78 days there were 1,102,656 people; cite 1999.01.0162%."
    lattice = scan_typed_value_region(
        _region(text),
        answer_shape=AnswerShape.QUANTITY,
        subject_entity_id="entity:example",
        relation="quantity",
    )
    surfaces = {candidate.raw_surface for candidate in lattice.candidates}

    assert {"-78 days", "78 days", "1,102,656 people", "102,656 people"} <= surfaces
    assert {"1999.01.0162%", "01.0162%", "0162%"} <= surfaces
    for candidate in lattice.candidates:
        span = candidate.source_span
        local_start = span.char_start - 100
        local_end = span.char_end - 100
        assert text[local_start:local_end] == candidate.raw_surface
        assert span.text_hash == f"sha256:{hashlib.sha256(span.text.encode()).hexdigest()}"


def test_typed_candidate_rejects_non_source_surface() -> None:
    span = ExactSourceSpan(
        span_id="span:1",
        document_id="doc:1",
        source_title="Example",
        source_revision="rev:1",
        source_url="https://example.test/1",
        source_family="example",
        char_start=10,
        char_end=14,
        text="fact",
        text_hash=f"sha256:{hashlib.sha256(b'fact').hexdigest()}",
    )
    with pytest.raises(ValidationError, match="must equal its exact source span"):
        TypedValueCandidate(
            source_span=span,
            raw_surface="fiction",
            canonical_representation="fiction",
            value_type=ValueType.TEXT,
            source_document_id="doc:1",
            confidence=0.5,
            provenance=("span:1",),
        )


def test_runtime_diagnostic_exposes_region_and_value_caps() -> None:
    provider = object.__new__(SQLiteControllerProvider)
    frame = QueryFramer().frame("Which quantity is stated about Example?").model_copy(
        update={"answer_shape": AnswerShape.QUANTITY}
    )
    raw = "\n".join(f"Example value {number} people." for number in range(1, 13))

    diagnostic = provider.diagnose_value_enumeration(frame, raw)

    assert len(diagnostic.regions) == 12
    assert sum(region.selected_top8 for region in diagnostic.regions) == 8
    assert len(diagnostic.all_matches_before_region_pruning) == 12
    assert len(diagnostic.top8_matches_before_deduplication) == 8
    assert len(diagnostic.pre_cap_values) == 8
    assert len(diagnostic.post_cap_values) == 4
    assert all(match.exact_surface_bound for match in diagnostic.all_matches_before_region_pruning)


def test_compiler_diagnostic_exposes_matches_before_type_cap() -> None:
    text = "The values are 1 people, 2 people, 3 people, 4 people, and 5 people."
    page = SourcePage(
        page_id="1",
        revision_id="2",
        revision_timestamp="2026-01-01T00:00:00Z",
        title="Example",
        source_url="https://example.test/1",
        license="CC BY-SA 4.0",
        text=text,
    )

    diagnostic = diagnose_value_enumeration(page)
    all_quantities = [
        match
        for match in diagnostic.all_typed_matches_before_type_caps
        if match.object_kind.value == "quantity"
    ]
    capped_quantities = [
        match
        for match in diagnostic.typed_matches_after_type_caps
        if match.object_kind.value == "quantity"
    ]

    assert len(all_quantities) == 5
    assert len(capped_quantities) == 3
    assert all(match.exact_surface_bound for match in diagnostic.all_typed_matches_before_type_caps)
