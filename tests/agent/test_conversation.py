from __future__ import annotations

from pathlib import Path

from aethersparse.agent.contracts import (
    ConversationActionKind,
    ConversationIntent,
    EntityHypothesis,
    EvidenceHandle,
)
from aethersparse.agent.conversation import ConversationEngine
from aethersparse.agent.session import InMemorySessionStore, JsonSessionStore


def _entity(entity_id: str, label: str, confidence: float = 0.95) -> EntityHypothesis:
    return EntityHypothesis(
        entity_id=entity_id,
        label=label,
        confidence=confidence,
        surface=label,
    )


def test_direct_referent_what_about_correction_and_persistence(tmp_path: Path) -> None:
    store = JsonSessionStore(tmp_path / "sessions")
    engine = ConversationEngine(store)
    state, direct = engine.accept(
        "demo",
        "Who was Alan Turing?",
        candidates=[_entity("entity:turing", "Alan Turing")],
        relation="biography",
    )
    assert direct.intent is ConversationIntent.DIRECT
    assert direct.entity_ids == ("entity:turing",)

    state, referent = engine.accept("demo", "Where was he born?", relation="birth_place")
    assert referent.intent is ConversationIntent.REFERENT
    assert referent.entity_ids == ("entity:turing",)
    assert state.previous_relation == "birth_place"

    state, continuation = engine.accept(
        "demo",
        "What about Ada Lovelace?",
        candidates=[_entity("entity:lovelace", "Ada Lovelace")],
    )
    assert continuation.intent is ConversationIntent.WHAT_ABOUT
    assert continuation.entity_ids == ("entity:lovelace",)
    assert continuation.relation == "birth_place"

    state, correction = engine.accept(
        "demo",
        "No, I meant Grace Hopper.",
        candidates=[_entity("entity:hopper", "Grace Hopper")],
    )
    assert correction.intent is ConversationIntent.CORRECTION
    assert correction.entity_ids == ("entity:hopper",)
    evidence = EvidenceHandle(
        handle_id="e1",
        source_namespace="encyclopedia",
        canonical_object_id="hopper",
        source_version="1",
        source_locator="doc#1",
        exact_text="Grace Hopper was born in New York City.",
        supported_values=("New York City",),
    )
    state = engine.record_answer(
        "demo",
        plan_id="plan-1",
        text="Grace Hopper was born in New York City.",
        evidence_handles=[evidence],
    )
    assert state.previous_answer_plan == "plan-1"
    assert state.evidence_handles == (evidence,)
    assert JsonSessionStore(tmp_path / "sessions").load("demo") == state


def test_genuine_ambiguity_requires_a_typed_choice() -> None:
    engine = ConversationEngine(InMemorySessionStore())
    state, action = engine.accept(
        "ambiguous",
        "Tell me about Mercury",
        candidates=[
            _entity("entity:planet", "Mercury (planet)", 0.81),
            _entity("entity:element", "Mercury (element)", 0.77),
        ],
    )
    assert action.kind is ConversationActionKind.ASK_CLARIFICATION
    assert action.clarification is not None
    assert len(action.clarification.choices) == 2
    assert state.unresolved_hypotheses

    state, selected = engine.accept("ambiguous", "choice-2")
    assert selected.kind is ConversationActionKind.CONTINUE
    assert selected.intent is ConversationIntent.CLARIFICATION_RESPONSE
    assert selected.entity_ids == ("entity:element",)
    assert state.pending_clarification is None


def test_missing_candidate_does_not_trigger_fake_clarification_and_cancel_reset() -> None:
    engine = ConversationEngine(InMemorySessionStore())
    state, action = engine.accept("control", "Unknown subject?")
    assert action.kind is ConversationActionKind.CONTINUE
    assert action.reason == "NO_ADDRESS_CANDIDATE"
    assert state.pending_clarification is None

    state, cancelled = engine.accept("control", "cancel")
    assert cancelled.kind is ConversationActionKind.CANCEL
    assert state.user_requested_task_state.status.value == "CANCELLED"

    state, reset = engine.accept("control", "start over")
    assert reset.kind is ConversationActionKind.RESET
    assert state.current_query is None
    assert state.recent_utterances == ()
