from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")

from aethersparse.experiments.shared_encoder import (  # noqa: E402
    COG_FIELDS,
    COG_LABELS,
    cog_parameter_count,
    encode_texts,
    encoder_parameter_count,
    hashed_ngrams,
    make_cog_advisor,
    make_encoder,
)


def test_parameter_budgets_and_forward_shapes() -> None:
    encoder = make_encoder()
    advisor = make_cog_advisor()
    assert encoder_parameter_count() == 2_164_416
    assert 2_000_000 <= encoder_parameter_count() <= 4_000_000
    assert cog_parameter_count() == 257_733
    assert 250_000 <= cog_parameter_count() <= 1_000_000
    encoded = encode_texts(encoder, ["When was Ada born?", "Ada was born in 1815."])
    assert encoded.shape == (2, 192)
    logits = advisor(encoded, torch.zeros((2, COG_FIELDS)))
    assert logits.shape == (2, len(COG_LABELS))


def test_hashing_is_deterministic_and_bounded() -> None:
    first = hashed_ngrams("  Café   astronomy ")
    second = hashed_ngrams("café astronomy")
    assert first == second
    assert first
    assert all(0 <= value < 8192 for value in first)
