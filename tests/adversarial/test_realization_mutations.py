from __future__ import annotations

import pytest

from aethersparse.models import QueryRequest, QueryResponse
from aethersparse.runtime import AetherSparseRuntime
from aethersparse.verifier import VerificationError, verify_answer


def approved_answer() -> tuple[AetherSparseRuntime, QueryResponse]:
    runtime = AetherSparseRuntime()
    response = runtime.query(
        QueryRequest(
            request_id="mutation",
            session_id="adversarial",
            text="When did Apollo 11 land on the Moon?",
        )
    )
    assert response.sentence is not None
    return runtime, response


@pytest.mark.parametrize(
    "mutation",
    [
        "Apollo 13 landed on the Moon on July 20, 1969.",
        "Apollo 11 landed on the Moon on July 21, 1969.",
        "Apollo 11 did not land on the Moon on July 20, 1969.",
        "Apollo 11 landed on the Moon on July 20, 1969 because it was easy.",
    ],
)
def test_realization_mutations_are_blocked(mutation: str) -> None:
    runtime, raw_response = approved_answer()
    response = raw_response
    assert response.sentence is not None

    with pytest.raises(VerificationError):
        verify_answer(
            sentence=mutation,
            expected_sentence=response.sentence,
            citations=response.citations,
            bindings=response.bindings,
            packets=runtime.store.by_packet_id,
            spans=runtime.store.span_by_id,
        )


def test_source_substitution_is_blocked() -> None:
    runtime, raw_response = approved_answer()
    response = raw_response
    assert response.sentence is not None
    citation = response.citations[0].model_copy(
        update={"source_url": "https://example.invalid/substituted"}
    )

    with pytest.raises(VerificationError, match="URL"):
        verify_answer(
            sentence=response.sentence,
            expected_sentence=response.sentence,
            citations=(citation,),
            bindings=response.bindings,
            packets=runtime.store.by_packet_id,
            spans=runtime.store.span_by_id,
        )
