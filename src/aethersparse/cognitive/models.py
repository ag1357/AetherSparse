"""Authoritative bounded contracts for the Cognitive Obligation Graph (COG) v1."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InputType(StrEnum):
    NATURAL_LANGUAGE = "NATURAL_LANGUAGE"
    STRUCTURED_EXTERNAL_EVENT = "STRUCTURED_EXTERNAL_EVENT"


class ProvenanceKind(StrEnum):
    USER_INPUT = "USER_INPUT"
    OBSERVATION = "OBSERVATION"
    INFERENCE = "INFERENCE"
    CORPUS_EVIDENCE = "CORPUS_EVIDENCE"
    SYSTEM_RULE = "SYSTEM_RULE"


class GoalType(StrEnum):
    QUESTION_ANSWERING = "QUESTION_ANSWERING"
    SOFTWARE_CHANGE = "SOFTWARE_CHANGE"
    EMBODIED_CONTROL = "EMBODIED_CONTROL"
    GENERAL = "GENERAL"


class GoalStatus(StrEnum):
    OPEN = "OPEN"
    SATISFIED = "SATISFIED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ObligationStatus(StrEnum):
    OPEN = "OPEN"
    SATISFIED = "SATISFIED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    SUPERSEDED = "SUPERSEDED"


class InvariantStatus(StrEnum):
    ACTIVE = "ACTIVE"
    VIOLATED = "VIOLATED"


class FrontierStatus(StrEnum):
    OPEN = "OPEN"
    SUSPENDED = "SUSPENDED"
    PRUNED = "PRUNED"
    COMPLETE = "COMPLETE"


class RecoveryAction(StrEnum):
    REASSESS_HYPOTHESIS = "REASSESS_HYPOTHESIS"
    EXPAND_FRONTIER = "EXPAND_FRONTIER"
    TRY_ALTERNATIVE = "TRY_ALTERNATIVE"
    ROLLBACK = "ROLLBACK"
    ASK_CLARIFICATION = "ASK_CLARIFICATION"
    ABSTAIN_BLOCKED = "ABSTAIN_BLOCKED"


class CognitiveOperationKind(StrEnum):
    """Generic graph operations shared by QA, coding, and future embodiment."""

    DISCOVER_DEPENDENTS = "DISCOVER_DEPENDENTS"
    DISCOVER_REFERENCES = "DISCOVER_REFERENCES"
    ADD_OBLIGATION = "ADD_OBLIGATION"
    SATISFY_OBLIGATION = "SATISFY_OBLIGATION"
    VERIFY_INVARIANT = "VERIFY_INVARIANT"
    REOPEN_OBLIGATION = "REOPEN_OBLIGATION"
    EXPAND_SCOPE = "EXPAND_SCOPE"


class Provenance(FrozenModel):
    kind: ProvenanceKind
    source_id: str = Field(min_length=1, max_length=256)
    detail: str = Field(default="", max_length=512)


class Goal(FrozenModel):
    goal_id: str = Field(min_length=1, max_length=96)
    goal_type: GoalType
    description: str = Field(min_length=1, max_length=512)
    status: GoalStatus = GoalStatus.OPEN
    priority: int = Field(default=50, ge=0, le=100)
    provenance: Provenance
    parent_goal_id: str | None = None


class Obligation(FrozenModel):
    obligation_id: str = Field(min_length=1, max_length=96)
    goal_id: str = Field(min_length=1, max_length=96)
    kind: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=512)
    status: ObligationStatus = ObligationStatus.OPEN
    mandatory: bool = True
    provenance: Provenance
    depends_on: tuple[str, ...] = Field(default=(), max_length=8)
    satisfied_by: tuple[str, ...] = Field(default=(), max_length=8)


class Invariant(FrozenModel):
    invariant_id: str = Field(min_length=1, max_length=96)
    kind: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=512)
    status: InvariantStatus = InvariantStatus.ACTIVE
    provenance: Provenance
    violation_evidence_ids: tuple[str, ...] = Field(default=(), max_length=8)


class Hypothesis(FrozenModel):
    hypothesis_id: str = Field(min_length=1, max_length=96)
    kind: str = Field(min_length=1, max_length=64)
    interpretation: str = Field(min_length=1, max_length=512)
    confidence_milli: int = Field(ge=0, le=1000)
    provenance: Provenance
    evidence_for_ids: tuple[str, ...] = Field(default=(), max_length=16)
    evidence_against_ids: tuple[str, ...] = Field(default=(), max_length=16)
    unresolved_obligation_ids: tuple[str, ...] = Field(default=(), max_length=16)
    contradiction: bool = False
    active: bool = True


class Evidence(FrozenModel):
    evidence_id: str = Field(min_length=1, max_length=96)
    subject: str = Field(min_length=1, max_length=192)
    predicate: str = Field(min_length=1, max_length=96)
    value: str = Field(min_length=1, max_length=512)
    provenance: Provenance
    immutable: bool = True


class UnresolvedVariable(FrozenModel):
    variable_id: str = Field(min_length=1, max_length=96)
    kind: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=512)
    candidate_ids: tuple[str, ...] = Field(default=(), max_length=16)
    required_by_obligation_ids: tuple[str, ...] = Field(default=(), max_length=16)


class FrontierItem(FrozenModel):
    frontier_id: str = Field(min_length=1, max_length=96)
    kind: str = Field(min_length=1, max_length=64)
    target: str = Field(min_length=1, max_length=256)
    priority: int = Field(default=50, ge=0, le=100)
    status: FrontierStatus = FrontierStatus.OPEN
    hypothesis_id: str | None = None
    obligation_ids: tuple[str, ...] = Field(default=(), max_length=16)


class ObservedState(FrozenModel):
    state_id: str = Field(min_length=1, max_length=96)
    event_type: str = Field(min_length=1, max_length=96)
    entity: str = Field(min_length=1, max_length=192)
    attributes: tuple[tuple[str, str], ...] = Field(max_length=32)
    provenance: Provenance


class ProgressState(FrozenModel):
    step_count: int = Field(default=0, ge=0)
    obligations_completed: int = Field(default=0, ge=0)
    evidence_added: int = Field(default=0, ge=0)
    hypotheses_added: int = Field(default=0, ge=0)
    frontier_expansions: int = Field(default=0, ge=0)
    rollback_count: int = Field(default=0, ge=0)
    stagnant_steps: int = Field(default=0, ge=0)
    repeated_error_count: int = Field(default=0, ge=0)
    repeated_action_count: int = Field(default=0, ge=0)
    last_error_signature: str | None = Field(default=None, max_length=128)
    last_action: str | None = Field(default=None, max_length=64)
    verifier_state: str = Field(default="NOT_RUN", max_length=32)
    recent_actions: tuple[str, ...] = Field(default=(), max_length=8)
    recent_error_signatures: tuple[str, ...] = Field(default=(), max_length=8)


class CompactCOGView(FrozenModel):
    """Fixed integer-only view presented to a learned controller."""

    schema_version: int = 1
    open_goals: int = Field(ge=0, le=255)
    mandatory_open: int = Field(ge=0, le=255)
    mandatory_satisfied: int = Field(ge=0, le=255)
    blocked_or_failed: int = Field(ge=0, le=255)
    invariant_violations: int = Field(ge=0, le=255)
    active_hypotheses: int = Field(ge=0, le=255)
    competing_hypotheses: int = Field(ge=0, le=1)
    contradictions: int = Field(ge=0, le=255)
    evidence_count: int = Field(ge=0, le=255)
    unresolved_count: int = Field(ge=0, le=255)
    open_frontier: int = Field(ge=0, le=255)
    observed_state_count: int = Field(ge=0, le=255)
    completion_permille: int = Field(ge=0, le=1000)
    stagnant_steps: int = Field(ge=0, le=255)
    repeated_error_count: int = Field(ge=0, le=255)
    repeated_action_count: int = Field(ge=0, le=255)
    verifier_state_code: int = Field(ge=0, le=255)
    halt_success_legal: int = Field(ge=0, le=1)

    def packed_u16(self) -> tuple[int, ...]:
        return tuple(int(value) for value in self.model_dump().values())


class CognitiveObligationGraph(FrozenModel):
    """C_t = (G, O, I, H, E, U, F, S), with bounded progress state."""

    cog_id: str = Field(min_length=1, max_length=96)
    schema_version: str = "aethercore.cog.v1"
    goals: tuple[Goal, ...] = Field(default=(), max_length=8)
    obligations: tuple[Obligation, ...] = Field(default=(), max_length=48)
    invariants: tuple[Invariant, ...] = Field(default=(), max_length=16)
    hypotheses: tuple[Hypothesis, ...] = Field(default=(), max_length=16)
    evidence: tuple[Evidence, ...] = Field(default=(), max_length=64)
    unresolved: tuple[UnresolvedVariable, ...] = Field(default=(), max_length=32)
    frontier: tuple[FrontierItem, ...] = Field(default=(), max_length=32)
    observed_state: tuple[ObservedState, ...] = Field(default=(), max_length=16)
    progress: ProgressState = Field(default_factory=ProgressState)

    @model_validator(mode="after")
    def validate_graph(self) -> CognitiveObligationGraph:
        collections: tuple[tuple[str, tuple[Any, ...], str], ...] = (
            ("goal", self.goals, "goal_id"),
            ("obligation", self.obligations, "obligation_id"),
            ("invariant", self.invariants, "invariant_id"),
            ("hypothesis", self.hypotheses, "hypothesis_id"),
            ("evidence", self.evidence, "evidence_id"),
            ("unresolved", self.unresolved, "variable_id"),
            ("frontier", self.frontier, "frontier_id"),
            ("state", self.observed_state, "state_id"),
        )
        for label, values, key in collections:
            ids = [str(getattr(item, key)) for item in values]
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate {label} ID")
        goal_ids = {item.goal_id for item in self.goals}
        if any(
            item.parent_goal_id is not None and item.parent_goal_id not in goal_ids
            for item in self.goals
        ):
            raise ValueError("goal refers to unknown parent goal")
        if any(item.goal_id not in goal_ids for item in self.obligations):
            raise ValueError("obligation refers to unknown goal")
        obligation_ids = {item.obligation_id for item in self.obligations}
        if any(not set(item.depends_on).issubset(obligation_ids) for item in self.obligations):
            raise ValueError("obligation dependency is unknown")
        if any(
            not set(item.unresolved_obligation_ids).issubset(obligation_ids)
            for item in self.hypotheses
        ):
            raise ValueError("hypothesis refers to unknown obligation")
        if any(
            not set(item.required_by_obligation_ids).issubset(obligation_ids)
            for item in self.unresolved
        ):
            raise ValueError("unresolved variable refers to unknown obligation")
        hypothesis_ids = {item.hypothesis_id for item in self.hypotheses}
        if any(
            item.hypothesis_id is not None and item.hypothesis_id not in hypothesis_ids
            for item in self.frontier
        ):
            raise ValueError("frontier refers to unknown hypothesis")
        if any(not set(item.obligation_ids).issubset(obligation_ids) for item in self.frontier):
            raise ValueError("frontier refers to unknown obligation")
        return self

    def canonical_bytes(self) -> bytes:
        raw = self.model_dump(mode="json")
        return json.dumps(raw, sort_keys=True, separators=(",", ":")).encode("utf-8")
