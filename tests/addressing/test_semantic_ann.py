from __future__ import annotations

import pytest

from aethersparse.addressing.compiler_v2 import canonical_entity_id, normalize_surface
from aethersparse.addressing.semantic_ann import (
    BinaryVariant,
    CorpusSourceSplit,
    HyperlinkSupervision,
    Int8Vector,
    StaticSubwordEncoder,
    binary_code,
    build_binary_ivf,
    calibration_supervision,
    fit_product_quantizer,
    fit_supervision,
    hamming_distance,
    holdout_qualification,
    progressive_ivf_search,
    training_readiness,
)


def _supervision(
    index: int,
    mention: str,
    source_split: CorpusSourceSplit,
    *,
    benchmark_partition: str | None = None,
) -> HyperlinkSupervision:
    context = f"[[{mention}]]"
    return HyperlinkSupervision(
        occurrence_record_id=f"as:v2:occurrence:{index:064x}",
        compiler_bundle_id="as:v2:compiler-bundle:" + "1" * 64,
        corpus_tier="fixture",
        anchor_id=f"anchor:{index}",
        source_document_id=f"doc:{index}",
        source_text_sha256=f"{index + 1:064x}",
        source_span_sha256=f"{index + 101:064x}",
        source_split=source_split,
        mention=mention,
        normalized_mention=normalize_surface(mention),
        mention_start=2,
        mention_end=2 + len(mention),
        link_start=0,
        link_end=len(context),
        context=context,
        context_start=0,
        context_end=len(context),
        raw_target_title="Mercury",
        target_entity_id=canonical_entity_id("Mercury"),
        canonical_title="Mercury",
        resolution_state="canonical",
        redirect_path=("mercury",),
        provenance_ids=(f"source:doc:{index}", f"span:anchor:{index}"),
        benchmark_partition=benchmark_partition,
    )


def _rows() -> tuple[HyperlinkSupervision, ...]:
    return (
        *(_supervision(index, "Mercury", CorpusSourceSplit.FIT) for index in range(4)),
        _supervision(4, "singleton tail", CorpusSourceSplit.FIT),
        _supervision(5, "unseen planet", CorpusSourceSplit.CALIBRATION),
        _supervision(6, "hidden comet", CorpusSourceSplit.HOLDOUT),
    )


def test_training_readiness_requires_occurrence_rows() -> None:
    audit = training_readiness(())
    assert not audit.learned_training_authorized
    assert "resolved occurrence-level hyperlink labels are absent" in audit.blockers


def test_training_readiness_preserves_document_and_surface_holdouts() -> None:
    audit = training_readiness(_rows())
    assert audit.learned_training_authorized
    assert audit.has_source_document_holdout
    assert audit.has_unseen_surface_calibration
    assert audit.has_unseen_surface_holdout
    assert audit.has_head_tail_support
    assert {row.source_split for row in fit_supervision(_rows())} == {CorpusSourceSplit.FIT}
    assert {row.source_split for row in calibration_supervision(_rows())} == {
        CorpusSourceSplit.CALIBRATION
    }
    assert {row.source_split for row in holdout_qualification(_rows())} == {
        CorpusSourceSplit.HOLDOUT
    }


def test_protected_partition_cannot_enter_training() -> None:
    with pytest.raises(ValueError, match="protected-partition"):
        _supervision(
            99,
            "sealed",
            CorpusSourceSplit.FIT,
            benchmark_partition="evaluation",
        )


def test_benchmark_partition_is_not_a_corpus_source_split() -> None:
    row = _supervision(
        100,
        "separate provenance",
        CorpusSourceSplit.CALIBRATION,
        benchmark_partition="development",
    )
    assert row.source_split is CorpusSourceSplit.CALIBRATION
    assert row.benchmark_partition == "development"
    assert fit_supervision((row,)) == ()
    assert calibration_supervision((row,)) == (row,)


def test_static_encoder_is_deterministic_and_normalized() -> None:
    encoder = StaticSubwordEncoder()
    first = encoder.encode("Bette Davis")
    second = encoder.encode("  BETTE   Davis  ")
    assert first == second
    assert sum(value * value for value in first) == pytest.approx(1.0)


def test_global_fwht_is_not_mislabeled_as_prefix_compatible() -> None:
    vector = tuple((index - 128) / 128.0 for index in range(256))
    global_64 = binary_code(vector, variant=BinaryVariant.GLOBAL_FWHT, bits=64)
    block_64 = binary_code(vector, variant=BinaryVariant.PREFIX_BLOCK_FWHT, bits=64)
    block_256 = binary_code(vector, variant=BinaryVariant.PREFIX_BLOCK_FWHT, bits=256)
    assert global_64 != block_64
    assert block_64 == block_256[:8]


def test_binary_code_and_hamming_distance() -> None:
    positive = (1.0,) * 256
    negative = (-1.0,) * 256
    left = binary_code(positive)
    right = binary_code(negative)
    assert len(left) == 32
    assert hamming_distance(left, right) == 256


def test_int8_rerank_preserves_obvious_dot_order() -> None:
    query = (1.0, 0.0, -1.0, 0.5)
    close = Int8Vector.encode(query)
    far = Int8Vector.encode(tuple(-value for value in query))
    assert close.approximate_dot(query) > far.approximate_dot(query)


@pytest.mark.parametrize("code_bytes", [8, 16])
def test_pq_adc_emits_requested_byte_width(code_bytes: int) -> None:
    encoder = StaticSubwordEncoder()
    vectors = [encoder.encode(f"entity title {index}") for index in range(20)]
    quantizer = fit_product_quantizer(
        vectors, code_bytes=code_bytes, centroid_count=4, iterations=2
    )
    code = quantizer.encode(vectors[0])
    assert len(code) == code_bytes
    assert quantizer.adc_distance(vectors[0], code) >= 0.0


@pytest.mark.parametrize("nlist", [256, 512, 1024])
def test_binary_ivf_and_progressive_bitplane_io(nlist: int) -> None:
    encoder = StaticSubwordEncoder()
    identifiers = tuple(f"entity:{index}" for index in range(40))
    vectors = [encoder.encode(f"entity title {index}") for index in range(40)]
    codes = tuple(binary_code(vector) for vector in vectors)
    index = build_binary_ivf(identifiers, codes, nlist=nlist)
    result = progressive_ivf_search(
        index,
        codes[0],
        nprobe=32,
        top_k=4,
        retain_128=16,
        retain_256=8,
    )
    assert result.identifiers
    assert "entity:0" in result.identifiers
    assert result.total_bytes_read == result.coarse_bytes_read + result.extension_bytes_read
    assert result.pages_4k >= 1


def test_corrupt_ivf_inputs_are_rejected() -> None:
    with pytest.raises(ValueError, match="aligned"):
        build_binary_ivf(("entity:one",), (), nlist=256)
