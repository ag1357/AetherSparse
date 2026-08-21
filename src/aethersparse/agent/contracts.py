"""Closed contracts for the V13 conversation and grounded-answer plane."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConversationIntent(StrEnum):
    DIRECT = "DIRECT"
    FOLLOW_UP = "FOLLOW_UP"
    REFERENT = "REFERENT"
    WHAT_ABOUT = "WHAT_ABOUT"
    CORRECTION = "CORRECTION"
    CLARIFICATION_RESPONSE = "CLARIFICATION_RESPONSE"
    CANCEL = "CANCEL"
    RESET = "RESET"


class ConversationActionKind(StrEnum):
    CONTINUE = "CONTINUE"
    ASK_CLARIFICATION = "ASK_CLARIFICATION"
    CANCEL = "CANCEL"
    RESET = "RESET"


class TaskStatus(StrEnum):
    IDLE = "IDLE"
    REQUESTED = "REQUESTED"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class EntityHypothesis(FrozenModel):
    entity_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    surface: str = Field(min_length=1)


class ResolvedEntity(FrozenModel):
    entity_id: str
    label: str
    turn_id: str


class DiscourseBinding(FrozenModel):
    referent: str
    entity_id: str
    turn_id: str


class EvidenceHandle(FrozenModel):
    """Immutable pointer to exact source support available to the realizer."""

    handle_id: str
    source_namespace: str
    canonical_object_id: str
    source_version: str
    source_locator: str
    exact_text: str
    supported_values: tuple[str, ...] = ()


class ClarificationChoice(FrozenModel):
    choice_id: str
    entity_id: str
    label: str
    description: str = ""


class PendingClarification(FrozenModel):
    question: str
    choices: tuple[ClarificationChoice, ...] = Field(min_length=2, max_length=8)
    original_query: str


class TaskState(FrozenModel):
    status: TaskStatus = TaskStatus.IDLE
    task_id: str | None = None
    summary: str | None = None
    sandbox_id: str | None = None


class Utterance(FrozenModel):
    turn_id: str
    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=2048)


class SessionState(FrozenModel):
    """Bounded durable state; raw dialogue is context, never factual evidence."""

    session_id: str
    current_query: str | None = None
    active_entity_hypotheses: tuple[EntityHypothesis, ...] = ()
    previously_resolved_entities: tuple[ResolvedEntity, ...] = ()
    discourse_referent_bindings: tuple[DiscourseBinding, ...] = ()
    previous_relation: str | None = None
    previous_answer_plan: str | None = None
    evidence_handles: tuple[EvidenceHandle, ...] = ()
    unresolved_hypotheses: tuple[EntityHypothesis, ...] = ()
    pending_clarification: PendingClarification | None = None
    recent_utterances: tuple[Utterance, ...] = ()
    user_requested_task_state: TaskState = Field(default_factory=TaskState)

    @model_validator(mode="after")
    def enforce_bounds(self) -> SessionState:
        bounds = {
            "active entity hypotheses": len(self.active_entity_hypotheses) <= 8,
            "resolved entities": len(self.previously_resolved_entities) <= 16,
            "discourse bindings": len(self.discourse_referent_bindings) <= 16,
            "evidence handles": len(self.evidence_handles) <= 32,
            "unresolved hypotheses": len(self.unresolved_hypotheses) <= 8,
            "recent utterances": len(self.recent_utterances) <= 12,
        }
        failed = [name for name, valid in bounds.items() if not valid]
        if failed:
            raise ValueError(f"session bound exceeded: {', '.join(failed)}")
        return self


class ConversationAction(FrozenModel):
    kind: ConversationActionKind
    intent: ConversationIntent
    query: str | None = None
    entity_ids: tuple[str, ...] = ()
    relation: str | None = None
    clarification: PendingClarification | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def clarification_is_typed(self) -> ConversationAction:
        if self.kind is ConversationActionKind.ASK_CLARIFICATION:
            if self.clarification is None:
                raise ValueError("ASK_CLARIFICATION requires structured choices")
        elif self.clarification is not None:
            raise ValueError("clarification payload requires ASK_CLARIFICATION")
        return self


class AnswerKind(StrEnum):
    FACTUAL_VALUE = "FACTUAL_VALUE"
    ENTITY = "ENTITY"
    DATE = "DATE"
    QUANTITY = "QUANTITY"
    LIST = "LIST"
    COMPARISON = "COMPARISON"
    QUOTATION = "QUOTATION"
    CLARIFICATION = "CLARIFICATION"


class AnswerValue(FrozenModel):
    text: str
    evidence_handle_ids: tuple[str, ...] = Field(min_length=1)


class VerifiedAnswerPlan(FrozenModel):
    plan_id: str
    kind: AnswerKind
    subject: str | None = None
    relation: str | None = None
    values: tuple[AnswerValue, ...] = ()
    comparison_labels: tuple[str, str] | None = None
    clarification: PendingClarification | None = None
    verifier_status: Literal["ACCEPTED", "REJECTED"]

    @model_validator(mode="after")
    def shape_is_complete(self) -> VerifiedAnswerPlan:
        if self.kind is AnswerKind.CLARIFICATION:
            if self.clarification is None:
                raise ValueError("clarification answer requires choices")
        elif not self.values:
            raise ValueError("grounded answer requires at least one value")
        if self.kind is AnswerKind.COMPARISON and self.comparison_labels is None:
            raise ValueError("comparison requires two labels")
        return self


class GroundedAnswer(FrozenModel):
    text: str
    plan_id: str
    evidence_handle_ids: tuple[str, ...]
    grounded: Literal[True] = True
