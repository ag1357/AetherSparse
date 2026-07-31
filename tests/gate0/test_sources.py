from __future__ import annotations

from pathlib import Path

import pytest

from aethersparse.gate0.models import AlignmentMethod, FrozenSourceSnapshot
from aethersparse.gate0.sources import (
    SourceIntegrityError,
    SourceRepository,
    align_evidence,
    freeze_source,
    normalize_text,
)


def snapshot(raw_text: str) -> FrozenSourceSnapshot:
    return freeze_source(
        source_doc_id="src_normalization_fixture",
        title="Normalization fixture",
        source_url="https://example.invalid/source",
        source_revision="fixture-v1",
        license="public_domain",
        source_group="fixture",
        raw_text=raw_text,
    )


def test_harmless_normalization_preserves_raw_offsets_and_hashes() -> None:
    raw = "Apollo&nbsp;11  used the “Eagle” lunar module.\nIt returned safely."
    frozen = snapshot(raw)
    alignment = align_evidence(
        frozen,
        'Apollo 11 used the "Eagle" lunar module. It returned safely.',
    )

    assert alignment.alignment_method is AlignmentMethod.NORMALIZED_EQUIVALENT
    assert alignment.raw_text == raw
    assert alignment.raw_char_start == 0
    assert alignment.raw_char_end == len(raw)
    assert alignment.raw_byte_end == len(raw.encode("utf-8"))
    assert alignment.raw_text_hash.startswith("sha256:")


def test_pdf_line_break_hyphenation_is_normalized() -> None:
    frozen = snapshot("The space-\ncraft entered lunar orbit.")

    assert normalize_text(frozen.raw_text) == "The spacecraft entered lunar orbit."
    alignment = align_evidence(frozen, "spacecraft entered lunar orbit")
    assert alignment.raw_text == "space-\ncraft entered lunar orbit"


def test_unicode_combining_sequence_is_normalized_without_offset_loss() -> None:
    frozen = snapshot("The cafe\u0301 display was tested.")
    alignment = align_evidence(frozen, "The café display was tested.")

    assert alignment.normalized_text == "The café display was tested."
    assert alignment.raw_text == "The cafe\u0301 display was tested."


def test_direct_quotation_requires_exact_raw_substring() -> None:
    frozen = snapshot("Armstrong said, “Tranquility Base here.”")

    exact = align_evidence(frozen, "Tranquility Base here.", direct_quotation=True)
    assert exact.alignment_method is AlignmentMethod.EXACT_RAW

    with pytest.raises(SourceIntegrityError, match="exact raw"):
        align_evidence(
            frozen,
            '"Tranquility Base here."',
            direct_quotation=True,
        )


def test_substantive_difference_fails_closed() -> None:
    frozen = snapshot("Apollo 11 launched on July 16, 1969.")

    with pytest.raises(SourceIntegrityError, match="not found"):
        align_evidence(frozen, "Apollo 11 launched on July 17, 1969.")


def test_byte_offsets_account_for_multibyte_characters() -> None:
    frozen = snapshot("“Eagle” landed.")
    alignment = align_evidence(frozen, "Eagle")

    assert alignment.raw_char_start == 1
    assert alignment.raw_byte_start == len("“".encode())
    assert alignment.raw_text == "Eagle"


def test_source_repository_refuses_identity_mutation(tmp_path: Path) -> None:
    repository = SourceRepository(tmp_path)
    original = snapshot("Apollo 11 launched.")
    repository.add(original)

    changed = freeze_source(
        source_doc_id=original.source_doc_id,
        title=original.title,
        source_url=original.source_url,
        source_revision=original.source_revision,
        license="public_domain",
        source_group=original.source_group,
        raw_text="Apollo 11 did not launch.",
    )
    with pytest.raises(SourceIntegrityError, match="different bytes"):
        repository.add(changed)
