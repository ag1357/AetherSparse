"""Deterministic bounded conversation transitions over Semantic Address candidates."""

from __future__ import annotations

import re
from collections.abc import Sequence

from aethersparse.agent.contracts import (
    ClarificationChoice,
    ConversationAction,
    ConversationActionKind,
    ConversationIntent,
    DiscourseBinding,
    EntityHypothesis,
    EvidenceHandle,
    PendingClarification,
    ResolvedEntity,
    SessionState,
    TaskState,
    TaskStatus,
    Utterance,
)
from aethersparse.agent.session import SessionStore

_REFERENT = re.compile(r"\b(he|him|his|she|her|hers|it|its|they|them|their)\b", re.I)
_WHAT_ABOUT = re.compile(r"^\s*what\s+about\b", re.I)
_CORRECTION = re.compile(r"^\s*(?:no[, ]+)?(?:i\s+meant|rather|actually)\b", re.I)
_CANCEL = re.compile(r"^\s*(?:cancel|stop|never\s*mind)\s*[.!]?\s*$", re.I)
_RESET = re.compile(r"^\s*(?:reset|start\s+over|new\s+conversation)\s*[.!]?\s*$", re.I)


class ConversationEngine:
    """Updates session state without treating dialogue text as corpus evidence."""

    def __init__(self, store: SessionStore) -> None:
        self.store = store

    @staticmethod
    def _append_utterance(
        state: SessionState, text: str
    ) -> tuple[tuple[Utterance, ...], tuple[str, ...]]:
        prior_numbers = (
            int(item.turn_id.split("-", maxsplit=1)[1].split("-", maxsplit=1)[0])
            for item in state.recent_utterances
            if item.turn_id.startswith("turn-")
        )
        turn_number = 1 + max(prior_numbers, default=0)
        turn = Utterance(turn_id=f"turn-{turn_number}", role="user", text=text)
        combined = (*state.recent_utterances, turn)
        removed = combined[:-12]
        summary = (
            *state.conversation_summary,
            *(f"{item.role}:{item.text[:160]}" for item in removed),
        )[-8:]
        return combined[-12:], summary

    def record_answer(
        self,
        session_id: str,
        *,
        plan_id: str,
        text: str,
        evidence_handles: Sequence[EvidenceHandle],
    ) -> SessionState:
        """Persist only verifier-accepted answer metadata supplied by the caller."""

        state = self.store.load(session_id)
        if not state.recent_utterances:
            raise ValueError("cannot record an answer before a user turn")
        assistant = Utterance(
            turn_id=f"{state.recent_utterances[-1].turn_id}-assistant",
            role="assistant",
            text=text,
        )
        handles = {item.handle_id: item for item in state.evidence_handles}
        handles.update({item.handle_id: item for item in evidence_handles})
        updated = state.model_copy(
            update={
                "previous_answer_plan": plan_id,
                "evidence_handles": tuple(handles.values())[-32:],
                "recent_utterances": (*state.recent_utterances, assistant)[-12:],
            }
        )
        self.store.save(updated)
        return updated

    def record_task_state(self, session_id: str, task: TaskState) -> SessionState:
        state = self.store.load(session_id)
        updated = state.model_copy(update={"user_requested_task_state": task})
        self.store.save(updated)
        return updated

    @staticmethod
    def _resolved(
        state: SessionState, hypothesis: EntityHypothesis, turn_id: str
    ) -> tuple[tuple[ResolvedEntity, ...], tuple[DiscourseBinding, ...]]:
        entity = ResolvedEntity(
            entity_id=hypothesis.entity_id, label=hypothesis.label, turn_id=turn_id
        )
        resolved = tuple(
            item
            for item in state.previously_resolved_entities
            if item.entity_id != entity.entity_id
        )
        resolved = (*resolved, entity)[-16:]
        bindings = (
            *state.discourse_referent_bindings,
            DiscourseBinding(
                referent="most_recent_entity", entity_id=entity.entity_id, turn_id=turn_id
            ),
        )[-16:]
        return resolved, bindings

    @staticmethod
    def _ambiguity(candidates: Sequence[EntityHypothesis]) -> bool:
        plausible = sorted(
            (item for item in candidates if item.confidence >= 0.5),
            key=lambda item: -item.confidence,
        )
        return len(plausible) >= 2 and plausible[0].confidence - plausible[1].confidence <= 0.12

    def accept(
        self,
        session_id: str,
        text: str,
        *,
        candidates: Sequence[EntityHypothesis] = (),
        relation: str | None = None,
    ) -> tuple[SessionState, ConversationAction]:
        state = self.store.load(session_id)
        query = text.strip()
        if not query:
            raise ValueError("user text must not be empty")

        if _RESET.match(query):
            reset = self.store.reset(session_id)
            return reset, ConversationAction(
                kind=ConversationActionKind.RESET,
                intent=ConversationIntent.RESET,
                reason="USER_RESET",
            )

        utterances, summary = self._append_utterance(state, query)
        turn_id = utterances[-1].turn_id
        if _CANCEL.match(query):
            cancelled = state.model_copy(
                update={
                    "current_query": None,
                    "pending_clarification": None,
                    "unresolved_hypotheses": (),
                    "recent_utterances": utterances,
                    "conversation_summary": summary,
                    "user_requested_task_state": TaskState(status=TaskStatus.CANCELLED),
                }
            )
            self.store.save(cancelled)
            return cancelled, ConversationAction(
                kind=ConversationActionKind.CANCEL,
                intent=ConversationIntent.CANCEL,
                reason="USER_CANCEL",
            )

        correction = bool(_CORRECTION.match(query))
        what_about = bool(_WHAT_ABOUT.match(query))
        referent = _REFERENT.search(query)
        intent = ConversationIntent.DIRECT
        if state.pending_clarification is not None:
            intent = ConversationIntent.CLARIFICATION_RESPONSE
        elif correction:
            intent = ConversationIntent.CORRECTION
        elif what_about:
            intent = ConversationIntent.WHAT_ABOUT
        elif referent:
            intent = ConversationIntent.REFERENT
        elif state.current_query is not None:
            intent = ConversationIntent.FOLLOW_UP

        candidate_tuple = tuple(candidates[:8])
        if state.pending_clarification is not None and not candidate_tuple:
            normalized = query.casefold().strip(" .")
            selected_choice = next(
                (
                    choice
                    for choice in state.pending_clarification.choices
                    if normalized in {choice.choice_id.casefold(), choice.label.casefold()}
                ),
                None,
            )
            if selected_choice is not None:
                candidate_tuple = (
                    EntityHypothesis(
                        entity_id=selected_choice.entity_id,
                        label=selected_choice.label,
                        confidence=1.0,
                        surface=query,
                    ),
                )
            else:
                repeated = state.model_copy(
                    update={
                        "current_query": query,
                        "recent_utterances": utterances,
                        "conversation_summary": summary,
                    }
                )
                self.store.save(repeated)
                return repeated, ConversationAction(
                    kind=ConversationActionKind.ASK_CLARIFICATION,
                    intent=intent,
                    query=query,
                    clarification=state.pending_clarification,
                    reason="CLARIFICATION_CHOICE_NOT_RECOGNIZED",
                )

        if self._ambiguity(candidate_tuple):
            choices = tuple(
                ClarificationChoice(
                    choice_id=f"choice-{index + 1}",
                    entity_id=item.entity_id,
                    label=item.label,
                )
                for index, item in enumerate(
                    sorted(candidate_tuple, key=lambda item: -item.confidence)
                )
            )
            pending = PendingClarification(
                question="Which entity did you mean?",
                choices=choices,
                original_query=query,
            )
            ambiguous = state.model_copy(
                update={
                    "current_query": query,
                    "active_entity_hypotheses": candidate_tuple,
                    "unresolved_hypotheses": candidate_tuple,
                    "pending_clarification": pending,
                    "recent_utterances": utterances,
                    "conversation_summary": summary,
                }
            )
            self.store.save(ambiguous)
            return ambiguous, ConversationAction(
                kind=ConversationActionKind.ASK_CLARIFICATION,
                intent=intent,
                query=query,
                relation=relation or state.previous_relation,
                clarification=pending,
                reason="COMPETING_ADDRESS_HYPOTHESES",
            )

        selected = max(candidate_tuple, key=lambda item: item.confidence, default=None)
        resolved = state.previously_resolved_entities
        bindings = state.discourse_referent_bindings
        entity_ids: tuple[str, ...] = ()
        if selected is not None:
            resolved, bindings = self._resolved(state, selected, turn_id)
            entity_ids = (selected.entity_id,)
        elif referent and resolved:
            entity_ids = (resolved[-1].entity_id,)

        effective_relation = relation
        if effective_relation is None and (
            what_about or referent or intent is ConversationIntent.FOLLOW_UP
        ):
            effective_relation = state.previous_relation
        updated = state.model_copy(
            update={
                "current_query": query,
                "active_entity_hypotheses": candidate_tuple,
                "previously_resolved_entities": resolved,
                "discourse_referent_bindings": bindings,
                "previous_relation": effective_relation,
                "unresolved_hypotheses": () if selected is not None else candidate_tuple,
                "pending_clarification": None,
                "recent_utterances": utterances,
                "conversation_summary": summary,
            }
        )
        self.store.save(updated)
        return updated, ConversationAction(
            kind=ConversationActionKind.CONTINUE,
            intent=intent,
            query=query,
            entity_ids=entity_ids,
            relation=effective_relation,
            reason="RESOLVED" if entity_ids else "NO_ADDRESS_CANDIDATE",
        )
