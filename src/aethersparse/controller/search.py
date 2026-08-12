"""Bounded best-first and beam search over exact AetherCore micro-operations."""

from __future__ import annotations

import hashlib
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
    static_verifier_answer_possible,
)

SearchKind = Literal["best_first", "beam"]


class SearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: SearchKind = "best_first"
    max_depth: int = Field(default=12, ge=1, le=64)
    max_expansions: int = Field(default=5000, ge=1, le=1_000_000)
    beam_width: int = Field(default=64, ge=1, le=4096)
    argument_cap: int = Field(default=32, ge=1, le=64)
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
    selected_claim_ids: tuple[str, ...]
    selection_priority: tuple[int, ...]
    steps: tuple[TrajectoryStep, ...]


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    search_kind: SearchKind
    expansions: int
    visited_states: int
    maximum_branching_factor: int
    verifier_attempts: int
    verifier_rejections: int
    terminal_candidates: tuple[TerminalTrajectory, ...]
    selected_trajectory: TerminalTrajectory | None
    selection_sha256: str | None
    exhausted: bool
    gold_used_during_search: bool


@dataclass(order=True)
class _Node:
    priority: tuple[int, ...]
    serial: int
    state: MicroState = field(compare=False)
    steps: tuple[TrajectoryStep, ...] = field(compare=False)


_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_NUM_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}
_UNIT_ALIASES = {
    "kilometers": "km",
    "kilometres": "km",
    "kilometer": "km",
    "kilometre": "km",
    "meters": "m",
    "metres": "m",
    "meter": "m",
    "metre": "m",
    "miles": "mi",
    "mile": "mi",
    "square kilometers": "km2",
    "square kilometres": "km2",
    "square miles": "mi2",
    "square kilometer": "km2",
    "square kilometre": "km2",
    "per square kilometre": "/km2",
    "per square kilometer": "/km2",
    "per square mile": "/mi2",
}


def canonicalize(value: str) -> str:
    """The shipped v09 canonical value normalizer, promoted for qualification."""

    text = re.sub(r"\s+", " ", str(value).strip().lower())
    text = re.sub(r"^(the|a|an) ", "", text)
    text = text.strip(" .,\"'")
    text = re.sub(r"^\+(?=\d)", "", text)
    match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if match:
        return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    match = re.fullmatch(r"([a-z]+) (\d{1,2})(?:st|nd|rd|th)?,? (\d{4})", text)
    if match and match.group(1) in _MONTHS:
        return f"{match.group(3)}-{_MONTHS[match.group(1)]:02d}-{int(match.group(2)):02d}"
    match = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)? ([a-z]+),? (\d{4})", text)
    if match and match.group(2) in _MONTHS:
        return f"{match.group(3)}-{_MONTHS[match.group(2)]:02d}-{int(match.group(1)):02d}"
    match = re.fullmatch(r"([a-z]+) (\d{4})", text)
    if match and match.group(1) in _MONTHS:
        return f"{match.group(2)}-{_MONTHS[match.group(1)]:02d}"
    match = re.fullmatch(r"(?:in |c\.?\s?|circa )?(\d{4})s?", text)
    if match:
        return match.group(1)
    for word, digit in sorted(_NUM_WORDS.items(), key=lambda item: -len(item[0])):
        text = re.sub(rf"\b{word}\b", str(digit), text)
    match = re.fullmatch(
        r"(?:about |around |approximately |over |nearly |almost )?"
        r"([\d,.]+)\s*(million|billion|thousand)?\s*([a-z/%² ]*)",
        text,
    )
    if match and re.fullmatch(r"[\d,.]+", match.group(1)):
        number = match.group(1).replace(",", "").rstrip(".")
        scale = {"million": "e6", "billion": "e9", "thousand": "e3"}.get(match.group(2), "")
        unit = _UNIT_ALIASES.get((match.group(3) or "").strip(), (match.group(3) or "").strip())
        return f"{number}{scale}{(' ' + unit.replace('²', '2').strip()) if unit else ''}"
    return text


def canonical_answer_match(values: tuple[str, ...], accepted_answers: tuple[str, ...]) -> bool:
    realized = "; ".join(values)
    realized_canonical = canonicalize(realized)
    if any(_canonical_match(realized_canonical, answer) for answer in accepted_answers):
        return True
    # List order is not factual; compare canonical components as a multiset.
    realized_parts = sorted(canonicalize(value) for value in values)
    for answer in accepted_answers:
        accepted_parts = sorted(
            canonicalize(part) for part in re.split(r"\s*;\s*", answer) if part.strip()
        )
        if len(realized_parts) == len(accepted_parts) and all(
            _canonical_match(realized, accepted)
            for realized, accepted in zip(realized_parts, accepted_parts, strict=True)
        ):
            return True
    return False


def _canonical_match(realized: str, accepted: str) -> bool:
    realized_value = canonicalize(realized)
    accepted_value = canonicalize(accepted)
    if realized_value == accepted_value:
        return True
    if re.fullmatch(r"\d{4}", accepted_value) and re.fullmatch(
        rf"{accepted_value}-\d{{2}}(?:-\d{{2}})?", realized_value
    ):
        return True
    return bool(
        re.fullmatch(r"\d{4}-\d{2}", accepted_value)
        and re.fullmatch(rf"{accepted_value}-\d{{2}}", realized_value)
    )


def _state_key(state: MicroState) -> str:
    payload = state.model_dump(
        mode="json",
        exclude={
            "case_id",
            "frame",
            "claims",
            "source_spans",
            "operation_counts",
            "read_actions",
            "total_actions",
        },
    )
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _estimated_p4_cost(state: MicroState) -> int:
    # Stable relative units: read-bearing actions dominate tiny matrix-free ops.
    return state.read_actions * 1000 + state.total_actions * 10 + len(state.claims)


def _priority(
    state: MicroState,
    *,
    accepted_answers: tuple[str, ...] | None,
    allow_gold: bool,
) -> tuple[int, ...]:
    canonical_correct = (
        state.terminal == "ANSWER"
        and accepted_answers is not None
        and canonical_answer_match(state.answer_values, accepted_answers)
    )
    selected_values = tuple(
        next(
            (
                str(claim.get("quotation") or claim.get("object_value") or "")
                for claim in state.claims
                if str(claim.get("claim_id", "")) == claim_id
            ),
            "",
        )
        for claim_id in state.selected_claim_ids
    )
    accepted_components = tuple(
        component
        for answer in (accepted_answers or ())
        for component in re.split(r"\s*;\s*", answer)
        if component
    )
    coverage = sum(
        any(
            _canonical_match(value, component) or canonicalize(value) in canonicalize(component)
            for component in accepted_components
        )
        for value in selected_values
        if value
    )
    correctness_rank = 0 if canonical_correct else max(1, 16 - coverage)
    if not allow_gold:
        correctness_rank = 0
    if state.terminal == "ANSWER" and state.verification_passed:
        progress = 0
    elif state.verification_passed:
        progress = 1
    elif state.plan_values:
        progress = 2
    elif state.bound_claim_ids:
        progress = 3
    elif state.selected_claim_ids:
        progress = 4
    elif state.active_claim_ids:
        progress = 5
    else:
        progress = 6
    claim_order = {str(item.get("claim_id", "")): index for index, item in enumerate(state.claims)}
    selected_rank = sum(
        claim_order.get(item, len(state.claims)) for item in state.selected_claim_ids
    )
    return (
        correctness_rank,
        progress,
        selected_rank,
        state.read_actions,
        _estimated_p4_cost(state),
    )


def _terminal(node: _Node) -> TerminalTrajectory:
    state = node.state
    return TerminalTrajectory(
        terminal=state.terminal or "ABORTED",
        answer_values=state.answer_values,
        verifier_passed=state.verification_passed,
        read_actions=state.read_actions,
        total_actions=state.total_actions,
        estimated_p4_cost=_estimated_p4_cost(state),
        selected_claim_ids=state.selected_claim_ids,
        selection_priority=node.priority,
        steps=node.steps,
    )


def _freeze_selection(
    terminals: list[TerminalTrajectory],
) -> tuple[TerminalTrajectory | None, str | None]:
    eligible = [item for item in terminals if item.terminal == "ANSWER" and item.verifier_passed]
    selected = min(
        eligible,
        key=lambda item: (
            item.selection_priority[2:],
            item.total_actions,
            tuple(step.operation_id for step in item.steps),
            item.selected_claim_ids,
        ),
        default=None,
    )
    if selected is None:
        return None, None
    payload = json.dumps(
        selected.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
    ).encode()
    return selected, hashlib.sha256(payload).hexdigest()


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
    if not static_verifier_answer_possible(initial):
        return SearchResult(
            case_id=initial.case_id,
            search_kind=config.kind,
            expansions=0,
            visited_states=1,
            maximum_branching_factor=0,
            verifier_attempts=0,
            verifier_rejections=0,
            terminal_candidates=(),
            selected_trajectory=None,
            selection_sha256=None,
            exhausted=True,
            gold_used_during_search=allow_gold,
        )
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
    terminal_keys: set[tuple[str, tuple[str, ...], bool]] = set()
    terminals: list[TerminalTrajectory] = []
    expansions = 0
    max_branching = 0
    verifier_attempts = 0
    verifier_rejections = 0
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
            terminal = _terminal(node)
            terminal_key = (
                terminal.terminal,
                terminal.answer_values,
                terminal.verifier_passed,
            )
            if terminal_key not in terminal_keys:
                terminal_keys.add(terminal_key)
                terminals.append(terminal)
            if not allow_gold and terminal.terminal == "ANSWER" and terminal.verifier_passed:
                break
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
                child = _next(node, action, serial, accepted=accepted, allow_gold=allow_gold)
                if action.operation_id == 59:
                    verifier_attempts += 1
                    verifier_rejections += int(not child.state.verification_passed)
                heapq.heappush(queue, child)
            except ValueError:
                continue
    selected, selection_sha256 = _freeze_selection(terminals)
    return SearchResult(
        case_id=initial.case_id,
        search_kind="best_first",
        expansions=expansions,
        visited_states=len(visited),
        maximum_branching_factor=max_branching,
        verifier_attempts=verifier_attempts,
        verifier_rejections=verifier_rejections,
        terminal_candidates=tuple(terminals),
        selected_trajectory=selected,
        selection_sha256=selection_sha256,
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
    terminal_keys: set[tuple[str, tuple[str, ...], bool]] = set()
    terminals: list[TerminalTrajectory] = []
    expansions = 0
    max_branching = 0
    verifier_attempts = 0
    verifier_rejections = 0
    stop_search = False
    for _depth in range(config.max_depth + 1):
        candidates: list[_Node] = []
        for node in frontier:
            key = _state_key(node.state)
            if key in visited:
                continue
            visited.add(key)
            if node.state.terminal is not None:
                terminal = _terminal(node)
                terminal_key = (
                    terminal.terminal,
                    terminal.answer_values,
                    terminal.verifier_passed,
                )
                if terminal_key not in terminal_keys:
                    terminal_keys.add(terminal_key)
                    terminals.append(terminal)
                correct_terminal = bool(
                    allow_gold
                    and accepted is not None
                    and terminal.verifier_passed
                    and canonical_answer_match(terminal.answer_values, accepted)
                )
                blind_terminal = bool(
                    not allow_gold and terminal.terminal == "ANSWER" and terminal.verifier_passed
                )
                stop_search = stop_search or correct_terminal or blind_terminal
                continue
            if expansions >= config.max_expansions:
                break
            actions = legal_actions(node.state, argument_cap=config.argument_cap)
            max_branching = max(max_branching, len(actions))
            expansions += 1
            for action in actions:
                serial += 1
                try:
                    child = _next(node, action, serial, accepted=accepted, allow_gold=allow_gold)
                    if action.operation_id == 59:
                        verifier_attempts += 1
                        verifier_rejections += int(not child.state.verification_passed)
                    candidates.append(child)
                except ValueError:
                    continue
        if stop_search:
            frontier = []
            break
        if expansions >= config.max_expansions or not candidates:
            frontier = candidates
            break
        candidates.sort()
        frontier = candidates[: config.beam_width]
        if len(terminals) >= config.max_terminal_candidates:
            break
    terminals = terminals[: config.max_terminal_candidates]
    selected, selection_sha256 = _freeze_selection(terminals)
    return SearchResult(
        case_id=initial.case_id,
        search_kind="beam",
        expansions=expansions,
        visited_states=len(visited),
        maximum_branching_factor=max_branching,
        verifier_attempts=verifier_attempts,
        verifier_rejections=verifier_rejections,
        terminal_candidates=tuple(terminals),
        selected_trajectory=selected,
        selection_sha256=selection_sha256,
        exhausted=not frontier,
        gold_used_during_search=allow_gold,
    )


def posthoc_reachable(result: SearchResult, accepted_answers: tuple[str, ...]) -> bool:
    """Score the one frozen gold-independent selected output after search."""

    if result.gold_used_during_search:
        raise ValueError("post-hoc evaluation expects a gold-blind search result")
    selected = result.selected_trajectory
    return bool(
        selected is not None
        and selected.terminal == "ANSWER"
        and selected.verifier_passed
        and canonical_answer_match(selected.answer_values, accepted_answers)
    )


def candidate_set_oracle(result: SearchResult, accepted_answers: tuple[str, ...]) -> bool:
    """Whether any certified output matches gold; never call this blind accuracy."""

    return any(
        terminal.terminal == "ANSWER"
        and terminal.verifier_passed
        and canonical_answer_match(terminal.answer_values, accepted_answers)
        for terminal in result.terminal_candidates
    )
