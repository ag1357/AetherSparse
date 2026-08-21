"""Bit-exact Python reference for the portable C ABI runtime.

This module is intentionally small. It is an executable specification for the
runtime-critical state transition boundary, not an alternate controller.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from enum import IntEnum

ABI_VERSION = 1
MAX_CANDIDATES = 32
MAX_SELECTED = 8
MAX_FEATURES = 64
MAX_ACTIONS = 32
SESSION_ID_BYTES = 40
SESSION_SERIALIZED_BYTES = 836
SESSION_MAGIC = b"AESSV013"

PLAN_READY = 1 << 0
VERIFIED = 1 << 1
TERMINAL = 1 << 2


class RuntimeContractError(ValueError):
    """An input violates the frozen portable-runtime contract."""


class Action(IntEnum):
    SEARCH_KNOWLEDGE = 0
    SELECT_EVIDENCE = 1
    BUILD_PLAN = 2
    VERIFY_PLAN = 3
    ANSWER = 4
    ASK_CLARIFICATION = 5
    ABSTAIN = 6


class Terminal(IntEnum):
    NONE = 0
    ANSWER = 1
    CLARIFICATION = 2
    ABSTAIN = 3


@dataclass(frozen=True)
class Candidate:
    entity_id: int
    score_q15: int
    evidence_mask: int

    def __post_init__(self) -> None:
        if not 0 < self.entity_id <= 0xFFFFFFFFFFFFFFFF:
            raise RuntimeContractError("entity_id must fit nonzero uint64")
        if not -(1 << 31) <= self.score_q15 < (1 << 31):
            raise RuntimeContractError("score_q15 must fit int32")
        if not 0 <= self.evidence_mask <= 0xFFFFFFFF:
            raise RuntimeContractError("evidence_mask must fit uint32")


@dataclass
class Workspace:
    candidates: list[Candidate] = field(default_factory=list)
    selected_entity_ids: list[int] = field(default_factory=list)
    last_action: int = 0
    step_count: int = 0
    invalid_action_count: int = 0
    flags: int = 0
    terminal_disposition: Terminal = Terminal.NONE

    def union_candidates(self, incoming: list[Candidate]) -> None:
        """Union by canonical ID, then apply exactly one global K=32 cap."""

        union = {candidate.entity_id: candidate for candidate in self.candidates}
        for candidate in incoming:
            previous = union.get(candidate.entity_id)
            if previous is None:
                union[candidate.entity_id] = candidate
            else:
                union[candidate.entity_id] = Candidate(
                    entity_id=candidate.entity_id,
                    score_q15=max(previous.score_q15, candidate.score_q15),
                    evidence_mask=previous.evidence_mask | candidate.evidence_mask,
                )
        self.candidates = sorted(
            union.values(), key=lambda item: (-item.score_q15, item.entity_id)
        )[:MAX_CANDIDATES]

    def legal_action_mask(self) -> int:
        if self.flags & TERMINAL or self.step_count >= 64:
            return 0
        mask = (1 << Action.SEARCH_KNOWLEDGE) | (1 << Action.ABSTAIN)
        if self.candidates and len(self.selected_entity_ids) < MAX_SELECTED:
            mask |= 1 << Action.SELECT_EVIDENCE
        if self.selected_entity_ids:
            mask |= 1 << Action.BUILD_PLAN
        if self.flags & PLAN_READY:
            mask |= 1 << Action.VERIFY_PLAN
        if self.flags & VERIFIED:
            mask |= 1 << Action.ANSWER
        if len(self.candidates) > 1 and not self.selected_entity_ids:
            mask |= 1 << Action.ASK_CLARIFICATION
        return mask

    def execute(self, action: Action, argument_id: int = 0) -> None:
        if not self.legal_action_mask() & (1 << action):
            self.invalid_action_count += 1
            raise RuntimeContractError("action is illegal in the current workspace")
        if action is Action.SELECT_EVIDENCE:
            candidate = next(
                (item for item in self.candidates if item.entity_id == argument_id), None
            )
            if candidate is None or candidate.evidence_mask == 0:
                self.invalid_action_count += 1
                raise RuntimeContractError("selected entity has no exact evidence handle")
            if argument_id in self.selected_entity_ids:
                self.invalid_action_count += 1
                raise RuntimeContractError("entity is already selected")
            self.selected_entity_ids.append(argument_id)
        elif action is Action.BUILD_PLAN:
            self.flags = (self.flags | PLAN_READY) & ~VERIFIED
        elif action is Action.VERIFY_PLAN:
            by_id = {item.entity_id: item for item in self.candidates}
            if not self.selected_entity_ids or any(
                item not in by_id or by_id[item].evidence_mask == 0
                for item in self.selected_entity_ids
            ):
                self.invalid_action_count += 1
                raise RuntimeContractError("plan is not exactly evidence-backed")
            self.flags |= VERIFIED
        elif action is Action.ANSWER:
            self.flags |= TERMINAL
            self.terminal_disposition = Terminal.ANSWER
        elif action is Action.ASK_CLARIFICATION:
            self.flags |= TERMINAL
            self.terminal_disposition = Terminal.CLARIFICATION
        elif action is Action.ABSTAIN:
            self.flags |= TERMINAL
            self.terminal_disposition = Terminal.ABSTAIN
        self.last_action = action
        self.step_count += 1


@dataclass(frozen=True)
class LinearPolicy:
    weights: tuple[tuple[int, ...], ...]
    bias: tuple[int, ...]

    def __post_init__(self) -> None:
        if not self.weights or len(self.weights) != len(self.bias):
            raise RuntimeContractError("policy requires one bias per action")
        if len(self.weights) > MAX_ACTIONS:
            raise RuntimeContractError("policy exceeds action capacity")
        width = len(self.weights[0])
        if not 0 < width <= MAX_FEATURES or any(len(row) != width for row in self.weights):
            raise RuntimeContractError("policy feature matrix is ragged or out of bounds")
        if any(not -128 <= value <= 127 for row in self.weights for value in row):
            raise RuntimeContractError("policy weights must fit int8")

    def select(self, features: tuple[int, ...], legal_action_mask: int) -> tuple[int, int]:
        if len(features) != len(self.weights[0]):
            raise RuntimeContractError("policy feature width mismatch")
        logits = [
            self.bias[action]
            + sum(weight * value for weight, value in zip(row, features, strict=True))
            for action, row in enumerate(self.weights)
        ]
        legal = [index for index in range(len(logits)) if legal_action_mask & (1 << index)]
        if not legal:
            raise RuntimeContractError("no legal action")
        selected = max(legal, key=lambda index: (logits[index], -index))
        return selected, logits[selected]


@dataclass
class Session:
    session_id: str
    turn_id: int = 0
    active_entity_ids: list[int] = field(default_factory=list)
    pending_clarification_ids: list[int] = field(default_factory=list)
    recent_utterance_hashes: list[int] = field(default_factory=list)
    workspace: Workspace = field(default_factory=Workspace)

    def __post_init__(self) -> None:
        encoded = self.session_id.encode("utf-8")
        if not encoded or len(encoded) >= SESSION_ID_BYTES or b"\0" in encoded:
            raise RuntimeContractError("session_id must be 1..39 non-NUL UTF-8 bytes")

    def serialize(self) -> bytes:
        if len(self.active_entity_ids) > 8 or len(self.pending_clarification_ids) > 4:
            raise RuntimeContractError("session entity capacity exceeded")
        if len(self.recent_utterance_hashes) > 8:
            raise RuntimeContractError("recent utterance capacity exceeded")
        if len(self.workspace.candidates) > MAX_CANDIDATES:
            raise RuntimeContractError("candidate capacity exceeded")
        if len(self.workspace.selected_entity_ids) > MAX_SELECTED:
            raise RuntimeContractError("selection capacity exceeded")
        payload = bytearray(SESSION_MAGIC)
        payload.extend(struct.pack("<I", ABI_VERSION))
        session_id = self.session_id.encode("utf-8")
        payload.extend(session_id + bytes(SESSION_ID_BYTES - len(session_id)))
        payload.extend(struct.pack("<Q", self.turn_id))
        payload.extend(struct.pack("<I", len(self.active_entity_ids)))
        payload.extend(_fixed_u64(self.active_entity_ids, 8))
        payload.extend(struct.pack("<I", len(self.pending_clarification_ids)))
        payload.extend(_fixed_u64(self.pending_clarification_ids, 4))
        payload.extend(_fixed_u64(self.recent_utterance_hashes, 8))
        payload.extend(struct.pack("<I", len(self.workspace.candidates)))
        for index in range(MAX_CANDIDATES):
            candidate = (
                self.workspace.candidates[index]
                if index < len(self.workspace.candidates)
                else Candidate(1, 0, 0)
            )
            if index >= len(self.workspace.candidates):
                payload.extend(bytes(16))
            else:
                payload.extend(
                    struct.pack(
                        "<QiI", candidate.entity_id, candidate.score_q15, candidate.evidence_mask
                    )
                )
        payload.extend(struct.pack("<I", len(self.workspace.selected_entity_ids)))
        payload.extend(_fixed_u64(self.workspace.selected_entity_ids, MAX_SELECTED))
        payload.extend(
            struct.pack(
                "<IIIII",
                int(self.workspace.last_action),
                self.workspace.step_count,
                self.workspace.invalid_action_count,
                self.workspace.flags,
                int(self.workspace.terminal_disposition),
            )
        )
        payload.extend(struct.pack("<I", zlib.crc32(payload)))
        if len(payload) != SESSION_SERIALIZED_BYTES:
            raise AssertionError("session wire-size drift")
        return bytes(payload)

    @classmethod
    def deserialize(cls, payload: bytes) -> Session:
        if len(payload) != SESSION_SERIALIZED_BYTES or payload[:8] != SESSION_MAGIC:
            raise RuntimeContractError("invalid session framing")
        if zlib.crc32(payload[:-4]) != struct.unpack_from("<I", payload, len(payload) - 4)[0]:
            raise RuntimeContractError("session checksum mismatch")
        cursor = 8

        def unpack(format_: str) -> tuple[int, ...]:
            nonlocal cursor
            values = struct.unpack_from(format_, payload, cursor)
            cursor += struct.calcsize(format_)
            return values

        if unpack("<I")[0] != ABI_VERSION:
            raise RuntimeContractError("session ABI mismatch")
        raw_session_id = payload[cursor : cursor + SESSION_ID_BYTES]
        cursor += SESSION_ID_BYTES
        try:
            session_id = raw_session_id.split(b"\0", 1)[0].decode("utf-8")
        except UnicodeDecodeError as error:
            raise RuntimeContractError("session ID is not UTF-8") from error
        turn_id = unpack("<Q")[0]
        active_count = unpack("<I")[0]
        active_ids = list(unpack("<8Q")[:active_count])
        pending_count = unpack("<I")[0]
        pending_ids = list(unpack("<4Q")[:pending_count])
        recent = [value for value in unpack("<8Q") if value]
        candidate_count = unpack("<I")[0]
        if candidate_count > MAX_CANDIDATES or active_count > 8 or pending_count > 4:
            raise RuntimeContractError("session count exceeds ABI capacity")
        raw_candidates = [unpack("<QiI") for _ in range(MAX_CANDIDATES)]
        candidates = [Candidate(*values) for values in raw_candidates[:candidate_count]]
        selected_count = unpack("<I")[0]
        selected = list(unpack("<8Q")[:selected_count])
        last_action, steps, invalid, flags, disposition = unpack("<IIIII")
        workspace = Workspace(
            candidates=candidates[:candidate_count],
            selected_entity_ids=selected,
            last_action=last_action,
            step_count=steps,
            invalid_action_count=invalid,
            flags=flags,
            terminal_disposition=Terminal(disposition),
        )
        return cls(
            session_id=session_id,
            turn_id=turn_id,
            active_entity_ids=active_ids,
            pending_clarification_ids=pending_ids,
            recent_utterance_hashes=recent,
            workspace=workspace,
        )


def _fixed_u64(values: list[int], count: int) -> bytes:
    if any(not 0 <= value <= 0xFFFFFFFFFFFFFFFF for value in values):
        raise RuntimeContractError("value does not fit uint64")
    return struct.pack(f"<{count}Q", *(values + [0] * (count - len(values))))
