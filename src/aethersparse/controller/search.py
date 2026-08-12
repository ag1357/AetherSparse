"""Bounded best-first and beam search over exact AetherCore micro-operations."""

from __future__ import annotations

import heapq
import json
import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from aethersparse.controller.micro_ops import (
    MICRO_OPS_BY_ID,
    MicroAction,
    MicroState,
    execute_action,
    legal_actions,
)

SearchKind = Literal["best_first", "beam"]


class SearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: SearchKind = "best_first"
    max_depth: int = Field(default=12, ge=1, le=64)
    max_expansions: int = Field(default=5000, ge=1, le=1_000_000)
    beam_width: int = Field(default=64, ge=1, le=4096)
    argument_cap: int = Field(default=16, ge=1, le=64)
    max_terminal_candidates: int = Field(default=128, ge=1, le=4096)


class TrajectoryStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: int
    operation_name: str
    arguments: dict[str, str]


class TerminalTrajectory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    terminal: str
    answer_values: tuple[str, ...]
    verifier_passed: bool
    read_actions: int
    total_actions: int
    estimated_p4_cost: int
    steps: tuple[TrajectoryStep, ...]


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    search_kind: SearchKind
    expansions: int
    visited_states: int
    maximum_branching_factor: int
    terminal_candidates: tuple[TerminalTrajectory, ...]
    exhausted: bool
    gold_used_during_search: bool


@dataclass(order=True)
class _Node:
    priority: tuple[int, int, int, int]
    serial: int
    state: MicroState = field(compare=False)
    steps: tuple[TrajectoryStep, ...] = field(compare=False)


def canonicalize(value: str) -> str:
    text = re.sub(r"\s+", " ", value.strip().lower()).strip(" .,\"'")
    text = re.sub(r"^(the|a|an) ", "", text)
    return text


def canonical_answer_match(values: tuple[str, ...], accepted_answers: tuple[str, ...]) -> bool:
    realized = "; ".join(values)
    realized_canonical = canonicalize(realized)
    if any(realized_canonical == canonicalize(answer) for answer in accepted_answers):
        return True
    # List order is not factual; compare canonical components as a multiset.
    realized_parts = sorted(canonicalize(value) for value in values)
    for answer in accepted_answers:
        accepted_parts = sorted(
            canonicalize(part) for part in re.split(r"\s*;\s*", answer) if part.strip()
        )
        if realized_parts == accepted_parts:
            return True
    return False


def _state_key(state: MicroState) -> str:
    payload = state.model_dump(mode="json", exclude={"read_actions", "total_actions"})
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _estimated_p4_cost(state: MicroState) -> int:
    # Stable relative units: read-bearing actions dominate tiny matrix-free ops.
    return state.read_actions * 1000 + state.total_actions * 10 + len(state.claims)


def _priority(
    state: MicroState,
    *,
    accepted_answers: tuple[str, ...] | None,
    allow_gold: bool,
) -> tuple[int, int, int, int]:
    canonical_correct = (
        state.terminal == "ANSWER"
        and accepted_answers is not None
        and canonical_answer_match(state.answer_values, accepted_answers)
    )
    correctness_rank = (0 if canonical_correct else 1) if allow_gold else 0
    verifier_rank = 0 if state.verification_passed else 1
    return correctness_rank, verifier_rank, state.read_actions, _estimated_p4_cost(state)


def _terminal(node: _Node) -> TerminalTrajectory:
    state = node.state
    return TerminalTrajectory(
        terminal=state.terminal or "ABORTED",
        answer_values=state.answer_values,
        verifier_passed=state.verification_passed,
        read_actions=state.read_actions,
        total_actions=state.total_actions,
        estimated_p4_cost=_estimated_p4_cost(state),
        steps=node.steps,
    )


def _next(
    node: _Node,
    action: MicroAction,
    serial: int,
    *,
    accepted: tuple[str, ...] | None,
    allow_gold: bool,
) -> _Node:
    state = execute_action(node.state, action)
    spec = MICRO_OPS_BY_ID[action.operation_id]
    step = TrajectoryStep(
        operation_id=action.operation_id,
        operation_name=spec.name.value,
        arguments=action.arguments,
    )
    return _Node(
        priority=_priority(state, accepted_answers=accepted, allow_gold=allow_gold),
        serial=serial,
        state=state,
        steps=(*node.steps, step),
    )


def search(
    initial: MicroState,
    config: SearchConfig,
    *,
    accepted_answers: tuple[str, ...] | None = None,
    allow_gold: bool = False,
) -> SearchResult:
    """Search without gold unless an authorized development/tuning caller opts in."""

    if allow_gold and accepted_answers is None:
        raise ValueError("gold-guided search requires accepted answers")
    if config.kind == "beam":
        return _beam_search(initial, config, accepted_answers, allow_gold)
    return _best_first_search(initial, config, accepted_answers, allow_gold)


def _best_first_search(
    initial: MicroState,
    config: SearchConfig,
    accepted: tuple[str, ...] | None,
    allow_gold: bool,
) -> SearchResult:
    serial = 0
    queue = [
        _Node(
            _priority(initial, accepted_answers=accepted, allow_gold=allow_gold),
            serial,
            initial,
            (),
        )
    ]
    visited: set[str] = set()
    terminals: list[TerminalTrajectory] = []
    expansions = 0
    max_branching = 0
    while (
        queue
        and expansions < config.max_expansions
        and len(terminals) < config.max_terminal_candidates
    ):
        node = heapq.heappop(queue)
        key = _state_key(node.state)
        if key in visited:
            continue
        visited.add(key)
        if node.state.terminal is not None:
            terminals.append(_terminal(node))
            if (
                allow_gold
                and node.state.terminal == "ANSWER"
                and accepted is not None
                and canonical_answer_match(node.state.answer_values, accepted)
                and node.state.verification_passed
            ):
                break
            continue
        if len(node.steps) >= config.max_depth:
            continue
        actions = legal_actions(node.state, argument_cap=config.argument_cap)
        max_branching = max(max_branching, len(actions))
        expansions += 1
        for action in actions:
            serial += 1
            try:
                heapq.heappush(
                    queue,
                    _next(node, action, serial, accepted=accepted, allow_gold=allow_gold),
                )
            except ValueError:
                continue
    terminals.sort(
        key=lambda item: (
            not item.verifier_passed,
            item.read_actions,
            item.total_actions,
            item.answer_values,
        )
    )
    return SearchResult(
        case_id=initial.case_id,
        search_kind="best_first",
        expansions=expansions,
        visited_states=len(visited),
        maximum_branching_factor=max_branching,
        terminal_candidates=tuple(terminals),
        exhausted=not queue,
        gold_used_during_search=allow_gold,
    )


def _beam_search(
    initial: MicroState,
    config: SearchConfig,
    accepted: tuple[str, ...] | None,
    allow_gold: bool,
) -> SearchResult:
    serial = 0
    frontier = [
        _Node(
            _priority(initial, accepted_answers=accepted, allow_gold=allow_gold),
            serial,
            initial,
            (),
        )
    ]
    visited: set[str] = set()
    terminals: list[TerminalTrajectory] = []
    expansions = 0
    max_branching = 0
    for _depth in range(config.max_depth + 1):
        candidates: list[_Node] = []
        for node in frontier:
            key = _state_key(node.state)
            if key in visited:
                continue
            visited.add(key)
            if node.state.terminal is not None:
                terminals.append(_terminal(node))
                continue
            if expansions >= config.max_expansions:
                break
            actions = legal_actions(node.state, argument_cap=config.argument_cap)
            max_branching = max(max_branching, len(actions))
            expansions += 1
            for action in actions:
                serial += 1
                try:
                    candidates.append(
                        _next(node, action, serial, accepted=accepted, allow_gold=allow_gold)
                    )
                except ValueError:
                    continue
        if expansions >= config.max_expansions or not candidates:
            frontier = candidates
            break
        candidates.sort()
        frontier = candidates[: config.beam_width]
        if len(terminals) >= config.max_terminal_candidates:
            break
    terminals.sort(
        key=lambda item: (
            not item.verifier_passed,
            item.read_actions,
            item.total_actions,
            item.answer_values,
        )
    )
    return SearchResult(
        case_id=initial.case_id,
        search_kind="beam",
        expansions=expansions,
        visited_states=len(visited),
        maximum_branching_factor=max_branching,
        terminal_candidates=tuple(terminals[: config.max_terminal_candidates]),
        exhausted=not frontier,
        gold_used_during_search=allow_gold,
    )


def posthoc_reachable(result: SearchResult, accepted_answers: tuple[str, ...]) -> bool:
    """Blind evaluator: compare terminal outputs only after search completes."""

    if result.gold_used_during_search:
        raise ValueError("post-hoc evaluation expects a gold-blind search result")
    return any(
        terminal.terminal == "ANSWER"
        and terminal.verifier_passed
        and canonical_answer_match(terminal.answer_values, accepted_answers)
        for terminal in result.terminal_candidates
    )
