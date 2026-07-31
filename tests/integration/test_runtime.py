from __future__ import annotations

from aethersparse.models import Disposition, FailureCode, QueryRequest
from aethersparse.runtime import AetherSparseRuntime


def request(text: str, request_id: str = "test") -> QueryRequest:
    return QueryRequest(
        request_id=request_id,
        session_id="integration",
        text=text,
        trace=True,
    )


def test_full_vertical_slice_answers_with_bindings_citation_and_trace() -> None:
    runtime = AetherSparseRuntime()
    response = runtime.query(request("When did Apollo 11 land on the Moon?"))

    assert response.disposition is Disposition.ANSWER
    assert response.sentence == "Apollo 11 landed on the Moon on July 20, 1969."
    assert response.citations
    assert response.bindings
    assert response.confidence is not None
    assert response.confidence.source_independence == 0.5
    assert {entry.operation for entry in response.trace} >= {
        "PARSE_PROVISIONAL",
        "RETRIEVE_FACTS",
        "SELECT_EVIDENCE_SET",
        "REALIZE_TEMPLATE",
        "VERIFY_CLAIM",
    }
    assert response.cost.bytes_read < runtime.store.pack.manifest.logical_query_pack_bytes


def test_wrong_entity_never_returns_apollo_11_answer() -> None:
    runtime = AetherSparseRuntime()
    response = runtime.query(request("When did Apollo 13 land on the Moon?"))

    assert response.disposition is Disposition.ABSTAIN
    assert response.reason_code is FailureCode.INSUFFICIENT_EVIDENCE
    assert response.reason is not None and "Apollo 13" in response.reason
    assert response.sentence is None


def test_out_of_domain_is_distinct_from_missing_evidence() -> None:
    runtime = AetherSparseRuntime()
    ood = runtime.query(request("Who won the 1969 World Series?", "ood"))
    missing = runtime.query(request("What fuel mixture did Apollo 11 use?", "missing"))

    assert ood.disposition is Disposition.OUT_OF_DOMAIN
    assert ood.reason_code is FailureCode.OUT_OF_DOMAIN
    assert missing.disposition is Disposition.ABSTAIN
    assert missing.reason_code is FailureCode.OUT_OF_ONTOLOGY


def test_trace_can_be_omitted_without_disabling_cost_accounting() -> None:
    runtime = AetherSparseRuntime()
    response = runtime.query(
        QueryRequest(
            request_id="no-trace",
            session_id="integration",
            text="When did Apollo 11 launch?",
            trace=False,
        )
    )

    assert response.disposition is Disposition.ANSWER
    assert response.trace == ()
    assert response.cost.operation_count > 0

