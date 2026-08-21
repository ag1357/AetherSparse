from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from aethersparse.agent.contracts import AnswerKind, EvidenceHandle
from aethersparse.agent.protocol import (
    MessageType,
    MockTactilityClient,
    UserTextPayload,
)
from aethersparse.agent.server import create_vertical_app, tactility_handler
from aethersparse.agent.session import InMemorySessionStore
from aethersparse.agent.vertical import (
    AetherCoreRequest,
    AetherCoreVerticalSlice,
    GroundedKnowledgeRecord,
    load_qualified_policy,
)
from aethersparse.controller.semantic_address import canonical_entity_id

ROOT = Path(__file__).resolve().parents[2]


def _handle(name: str, text: str, values: tuple[str, ...]) -> EvidenceHandle:
    return EvidenceHandle(
        handle_id=f"evidence:{name}",
        source_namespace="encyclopedia",
        canonical_object_id=f"wiki:{name}",
        source_version="2026-07-01",
        source_locator=f"pack://encyclopedia/{name}",
        exact_text=text,
        supported_values=values,
    )


def _runtime() -> AetherCoreVerticalSlice:
    alan_id = canonical_entity_id("Alan Turing")
    records = (
        GroundedKnowledgeRecord(
            entity_id=alan_id,
            canonical_title="Alan Turing",
            address_surfaces=("Alan Turing",),
            relation="description",
            relation_terms=("who",),
            relation_text="was",
            answer_kind=AnswerKind.FACTUAL_VALUE,
            values=("an English mathematician and computer scientist",),
            evidence=_handle(
                "Alan_Turing_bio",
                "Alan Turing was an English mathematician and computer scientist.",
                ("an English mathematician and computer scientist",),
            ),
        ),
        GroundedKnowledgeRecord(
            entity_id=alan_id,
            canonical_title="Alan Turing",
            address_surfaces=("Alan Turing",),
            relation="birth_place",
            relation_terms=("born",),
            relation_text="was born in",
            answer_kind=AnswerKind.FACTUAL_VALUE,
            values=("Maida Vale, London",),
            evidence=_handle(
                "Alan_Turing_birth",
                "Alan Turing was born in Maida Vale, London.",
                ("Maida Vale, London",),
            ),
        ),
        GroundedKnowledgeRecord(
            entity_id=canonical_entity_id("Mercury (planet)"),
            canonical_title="Mercury (planet)",
            address_surfaces=("Mercury",),
            relation="description",
            relation_terms=("what",),
            relation_text="is",
            answer_kind=AnswerKind.FACTUAL_VALUE,
            values=("the first planet from the Sun",),
            evidence=_handle(
                "Mercury_planet",
                "Mercury is the first planet from the Sun.",
                ("the first planet from the Sun",),
            ),
        ),
        GroundedKnowledgeRecord(
            entity_id=canonical_entity_id("Mercury (element)"),
            canonical_title="Mercury (element)",
            address_surfaces=("Mercury",),
            relation="description",
            relation_terms=("what",),
            relation_text="is",
            answer_kind=AnswerKind.FACTUAL_VALUE,
            values=("a chemical element with symbol Hg",),
            evidence=_handle(
                "Mercury_element",
                "Mercury is a chemical element with symbol Hg.",
                ("a chemical element with symbol Hg",),
            ),
        ),
    )
    report = json.loads((ROOT / "reports/droid/v13/policy-qualification.json").read_text())
    policy = load_qualified_policy(report)
    return AetherCoreVerticalSlice(records, policy, InMemorySessionStore())


def test_real_learned_policy_answers_and_carries_referent() -> None:
    runtime = _runtime()
    direct = runtime.query(AetherCoreRequest(session_id="session-1", text="Who was Alan Turing?"))
    assert direct.disposition == "ANSWER"
    assert direct.text == "Alan Turing was an English mathematician and computer scientist."
    assert direct.grounded and direct.verifier_accepted
    assert direct.controller_operations == (32, 43, 55, 59, 60)
    assert len(direct.semantic_address_candidate_ids) == 1

    follow_up = runtime.query(
        AetherCoreRequest(session_id="session-1", text="Where was he born?")
    )
    assert follow_up.disposition == "ANSWER"
    assert follow_up.text == "Alan Turing was born in Maida Vale, London."
    assert follow_up.semantic_address_candidate_ids == ()
    state = runtime.conversation.store.load("session-1")
    assert state.previously_resolved_entities[-1].entity_id == canonical_entity_id("Alan Turing")
    assert len(state.evidence_handles) == 2


def test_real_ambiguity_clarifies_then_answers_choice() -> None:
    runtime = _runtime()
    ambiguous = runtime.query(AetherCoreRequest(session_id="session-2", text="What is Mercury?"))
    assert ambiguous.disposition == "CLARIFY"
    assert len(ambiguous.semantic_address_candidate_ids) == 2
    assert "Which entity did you mean?" in ambiguous.text

    selected = runtime.query(AetherCoreRequest(session_id="session-2", text="choice-1"))
    assert selected.disposition == "ANSWER"
    assert selected.grounded and selected.verifier_accepted
    assert selected.evidence_handle_ids


def test_missing_address_abstains_without_answer() -> None:
    result = _runtime().query(
        AetherCoreRequest(session_id="session-3", text="Who discovered unobtainium?")
    )
    assert result.disposition == "ABSTAIN"
    assert not result.grounded
    assert not result.verifier_accepted
    assert result.evidence_handle_ids == ()


def test_http_and_mock_tactility_use_same_live_runtime() -> None:
    runtime = _runtime()
    with TestClient(create_vertical_app(runtime)) as client:
        health = client.get("/v13/health")
        assert health.status_code == 200
        assert health.json()["policy_parameters"] == 918
        answer = client.post(
            "/v13/query",
            json={"session_id": "http-session", "text": "Who was Alan Turing?"},
        )
        assert answer.status_code == 200
        assert answer.json()["disposition"] == "ANSWER"

    terminal = MockTactilityClient(
        "terminal-session", lambda message: tactility_handler(runtime, message)
    )
    messages = terminal.send(MessageType.USER_TEXT, UserTextPayload(text="Who was Alan Turing?"))
    assert tuple(item.type for item in messages) == (
        MessageType.ASSISTANT_TEXT_DELTA,
        MessageType.EVIDENCE_SUMMARY,
    )
