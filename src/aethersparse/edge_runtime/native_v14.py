"""Bit-exact Python reference for the additive V14 cognitive native ABI."""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

COG_MAGIC = b"ACOGV014"
ABI_VERSION = 1
COG_SCHEMA_VERSION = 1
FIVE_C_SCHEMA_VERSION = 1
COG_RUNTIME_SERIALIZED_BYTES = 180
PROGRESS_STAGNATED = 1 << 0
FIVE_C_KNOWN_FLAGS = (1 << 0) | (1 << 1) | (1 << 2)


class NativeV14ContractError(ValueError):
    pass


@dataclass(frozen=True)
class FiveCConstraint:
    constraint_id: int
    kind: int
    effect: int
    flags: int
    action_mask: int
    capability_mask: int
    required_flags: int
    minimum_value: int
    maximum_value: int


def five_c_digest(constraints: tuple[FiveCConstraint, ...]) -> tuple[int, int]:
    """Return the native two-lane FNV integrity fingerprint over canonical fields."""

    mask = 0xFFFFFFFFFFFFFFFF

    def feed(state: int, payload: bytes) -> int:
        for value in payload:
            state = ((state ^ value) * 1_099_511_628_211) & mask
        return state

    low = 14_695_981_039_346_656_037
    high = 7_809_847_782_465_536_322
    for item in constraints:
        fields = (
            struct.pack("<I4B", item.constraint_id, item.kind, item.effect, item.flags, 0),
            struct.pack(
                "<QIIii",
                item.action_mask,
                item.capability_mask,
                item.required_flags,
                item.minimum_value,
                item.maximum_value,
            ),
        )
        low = feed(low, b"".join(fields))
        high = feed(
            high,
            struct.pack(
                "<iiIIQ4BI",
                item.maximum_value,
                item.minimum_value,
                item.required_flags,
                item.capability_mask,
                item.action_mask,
                0,
                item.flags,
                item.effect,
                item.kind,
                item.constraint_id,
            ),
        )
    return low, high


@dataclass(frozen=True)
class CogSummary:
    open_goals: int = 0
    mandatory_open: int = 0
    mandatory_satisfied: int = 0
    blocked_or_failed: int = 0
    invariant_violations: int = 0
    active_hypotheses: int = 0
    competing_hypotheses: int = 0
    contradictions: int = 0
    evidence_count: int = 0
    unresolved_count: int = 0
    open_frontier: int = 0
    observed_state_count: int = 0
    completion_permille: int = 0
    stagnant_steps: int = 0
    repeated_error_count: int = 0
    repeated_action_count: int = 0
    verifier_state_code: int = 0
    halt_success_legal: int = 0
    reserved: tuple[int, int, int] = (0, 0, 0)

    @classmethod
    def from_packed_u16(cls, values: tuple[int, ...]) -> CogSummary:
        """Map `aethersparse.cognitive.CompactCOGView.packed_u16()` exactly."""

        if len(values) != 19 or values[0] != COG_SCHEMA_VERSION:
            raise NativeV14ContractError("CompactCOGView schema/width mismatch")
        return cls(
            open_goals=values[1],
            mandatory_open=values[2],
            mandatory_satisfied=values[3],
            blocked_or_failed=values[4],
            invariant_violations=values[5],
            active_hypotheses=values[6],
            competing_hypotheses=values[7],
            contradictions=values[8],
            evidence_count=values[9],
            unresolved_count=values[10],
            open_frontier=values[11],
            observed_state_count=values[12],
            completion_permille=values[13],
            stagnant_steps=values[14],
            repeated_error_count=values[15],
            repeated_action_count=values[16],
            verifier_state_code=values[17],
            halt_success_legal=values[18],
        )

    def packed_u16(self) -> tuple[int, ...]:
        return (
            COG_SCHEMA_VERSION,
            self.open_goals,
            self.mandatory_open,
            self.mandatory_satisfied,
            self.blocked_or_failed,
            self.invariant_violations,
            self.active_hypotheses,
            self.competing_hypotheses,
            self.contradictions,
            self.evidence_count,
            self.unresolved_count,
            self.open_frontier,
            self.observed_state_count,
            self.completion_permille,
            self.stagnant_steps,
            self.repeated_error_count,
            self.repeated_action_count,
            self.verifier_state_code,
            self.halt_success_legal,
        )

    def pack_without_struct_size(self) -> bytes:
        counts = (*self.packed_u16(), *self.reserved)
        if any(not 0 <= item <= 0xFFFF for item in counts):
            raise NativeV14ContractError("COG count does not fit uint16")
        return struct.pack("<22H", *counts)


@dataclass(frozen=True)
class FiveCState:
    constraint_count: int
    immutable_digest_low: int
    immutable_digest_high: int
    flags: int
    violation_count: int = 0
    last_violation_id: int = 0
    reserved: tuple[int, int, int, int, int, int, int] = (0, 0, 0, 0, 0, 0, 0)

    def pack_without_struct_size(self) -> bytes:
        return struct.pack(
            "<2H2Q10I",
            FIVE_C_SCHEMA_VERSION,
            self.constraint_count,
            self.immutable_digest_low,
            self.immutable_digest_high,
            self.flags,
            self.violation_count,
            self.last_violation_id,
            *self.reserved,
        )


@dataclass
class Progress:
    open_obligations: int = 0
    completed_obligations: int = 0
    new_evidence_count: int = 0
    new_hypothesis_count: int = 0
    frontier_expansion_count: int = 0
    repeated_action_count: int = 0
    verifier_state: int = 0
    rollback_count: int = 0
    repeated_error_signature: int = 0
    stagnation_cycles: int = 0
    flags: int = 0
    last_action: int = 0
    reserved: list[int] = field(default_factory=lambda: [0, 0, 0, 0])

    def record(
        self,
        *,
        action: int,
        error_signature: int,
        open_obligations: int,
        completed_obligations: int,
        new_evidence: int,
        new_hypothesis: int,
        frontier_expansion: int,
        verifier_state: int,
        rollback_count: int,
    ) -> None:
        prior = self.reserved[0] != 0
        repeated = (
            prior
            and self.last_action == action
            and error_signature != 0
            and self.repeated_error_signature == error_signature
        )
        made_progress = (
            completed_obligations > self.completed_obligations
            or open_obligations < self.open_obligations
            or new_evidence != 0
            or new_hypothesis != 0
            or frontier_expansion != 0
            or rollback_count != 0
        )
        if repeated:
            self.repeated_action_count = min(0xFFFF, self.repeated_action_count + 1)
        if repeated and not made_progress:
            self.stagnation_cycles = min(0xFFFF, self.stagnation_cycles + 1)
            if self.stagnation_cycles >= 3:
                self.flags |= PROGRESS_STAGNATED
        else:
            self.stagnation_cycles = 0
            self.flags &= ~PROGRESS_STAGNATED
        self.open_obligations = open_obligations
        self.completed_obligations = completed_obligations
        self.new_evidence_count = min(0xFFFF, self.new_evidence_count + new_evidence)
        self.new_hypothesis_count = min(0xFFFF, self.new_hypothesis_count + new_hypothesis)
        self.frontier_expansion_count = min(
            0xFFFF, self.frontier_expansion_count + frontier_expansion
        )
        self.verifier_state = verifier_state
        self.rollback_count = min(0xFFFF, self.rollback_count + rollback_count)
        self.last_action = action
        self.repeated_error_signature = error_signature
        self.reserved[0] = min(0xFFFFFFFF, self.reserved[0] + 1)

    def pack_without_struct_size(self) -> bytes:
        return struct.pack(
            "<8HI2HI4I",
            self.open_obligations,
            self.completed_obligations,
            self.new_evidence_count,
            self.new_hypothesis_count,
            self.frontier_expansion_count,
            self.repeated_action_count,
            self.verifier_state,
            self.rollback_count,
            self.repeated_error_signature,
            self.stagnation_cycles,
            self.flags,
            self.last_action,
            *self.reserved,
        )


@dataclass(frozen=True)
class SpecialistSummary:
    cold_count: int
    warm_count: int
    hot_count: int
    resident_ram_bytes: int

    def pack(self) -> bytes:
        return struct.pack(
            "<4I", self.cold_count, self.warm_count, self.hot_count, self.resident_ram_bytes
        )


@dataclass(frozen=True)
class Int8PolicyV2:
    weights: tuple[tuple[int, ...], ...]
    bias: tuple[int, ...]
    state_schema_id: int
    model_id: int

    def __post_init__(self) -> None:
        if not self.weights or len(self.weights) != len(self.bias) or len(self.weights) > 64:
            raise NativeV14ContractError("invalid V14 policy action shape")
        width = len(self.weights[0])
        if not 0 < width <= 64 or any(len(row) != width for row in self.weights):
            raise NativeV14ContractError("invalid V14 policy feature shape")
        if any(not -128 <= item <= 127 for row in self.weights for item in row):
            raise NativeV14ContractError("V14 policy weights must fit int8")

    @property
    def parameter_count(self) -> int:
        return len(self.weights) * len(self.weights[0])

    @property
    def model_bytes(self) -> int:
        return self.parameter_count + 4 * len(self.bias)

    def select(self, features: tuple[int, ...], legal_action_mask: int) -> tuple[int, int]:
        if len(features) != len(self.weights[0]):
            raise NativeV14ContractError("feature width mismatch")
        legal = [
            action for action in range(len(self.weights)) if legal_action_mask & (1 << action)
        ]
        if not legal:
            raise NativeV14ContractError("no legal action")
        logits = [
            self.bias[action]
            + sum(
                weight * feature
                for weight, feature in zip(self.weights[action], features, strict=True)
            )
            for action in range(len(self.weights))
        ]
        selected = max(legal, key=lambda action: (logits[action], -action))
        return selected, logits[selected]

    def score_candidate(self, action_index: int, features: tuple[int, ...]) -> int:
        """Score one argument-aware legal candidate with its own compact view."""

        if not 0 <= action_index < len(self.weights):
            raise NativeV14ContractError("action index out of range")
        if len(features) != len(self.weights[0]):
            raise NativeV14ContractError("feature width mismatch")
        return self.bias[action_index] + sum(
            weight * feature
            for weight, feature in zip(self.weights[action_index], features, strict=True)
        )


def serialize_cognitive_runtime(
    cog: CogSummary,
    five_c: FiveCState,
    progress: Progress,
    specialists: SpecialistSummary,
) -> bytes:
    payload = bytearray(COG_MAGIC)
    payload.extend(struct.pack("<I", ABI_VERSION))
    payload.extend(cog.pack_without_struct_size())
    payload.extend(five_c.pack_without_struct_size())
    payload.extend(progress.pack_without_struct_size())
    payload.extend(specialists.pack())
    payload.extend(struct.pack("<I", zlib.crc32(payload)))
    if len(payload) != COG_RUNTIME_SERIALIZED_BYTES:
        raise AssertionError(f"cognitive runtime wire-size drift: {len(payload)}")
    return bytes(payload)


def deserialize_cognitive_runtime(
    payload: bytes,
) -> tuple[CogSummary, FiveCState, Progress, SpecialistSummary]:
    """Decode the exact frozen 180-byte V14 projection and validate its semantics."""

    if len(payload) != COG_RUNTIME_SERIALIZED_BYTES or payload[:8] != COG_MAGIC:
        raise NativeV14ContractError("invalid cognitive runtime framing")
    expected_crc = struct.unpack_from("<I", payload, len(payload) - 4)[0]
    if zlib.crc32(payload[:-4]) != expected_crc:
        raise NativeV14ContractError("cognitive runtime checksum mismatch")
    abi_version = struct.unpack_from("<I", payload, 8)[0]
    if abi_version != ABI_VERSION:
        raise NativeV14ContractError("cognitive runtime ABI mismatch")
    cursor = 12
    cog_values = struct.unpack_from("<22H", payload, cursor)
    cursor += struct.calcsize("<22H")
    if cog_values[0] != COG_SCHEMA_VERSION:
        raise NativeV14ContractError("COG schema mismatch")
    cog = CogSummary(
        open_goals=cog_values[1],
        mandatory_open=cog_values[2],
        mandatory_satisfied=cog_values[3],
        blocked_or_failed=cog_values[4],
        invariant_violations=cog_values[5],
        active_hypotheses=cog_values[6],
        competing_hypotheses=cog_values[7],
        contradictions=cog_values[8],
        evidence_count=cog_values[9],
        unresolved_count=cog_values[10],
        open_frontier=cog_values[11],
        observed_state_count=cog_values[12],
        completion_permille=cog_values[13],
        stagnant_steps=cog_values[14],
        repeated_error_count=cog_values[15],
        repeated_action_count=cog_values[16],
        verifier_state_code=cog_values[17],
        halt_success_legal=cog_values[18],
        reserved=tuple(cog_values[19:22]),
    )
    five_values = struct.unpack_from("<2H2Q10I", payload, cursor)
    cursor += struct.calcsize("<2H2Q10I")
    if five_values[0] != FIVE_C_SCHEMA_VERSION:
        raise NativeV14ContractError("5C schema mismatch")
    five_c = FiveCState(
        constraint_count=five_values[1],
        immutable_digest_low=five_values[2],
        immutable_digest_high=five_values[3],
        flags=five_values[4],
        violation_count=five_values[5],
        last_violation_id=five_values[6],
        reserved=tuple(five_values[7:14]),
    )
    progress_values = struct.unpack_from("<8HI2HI4I", payload, cursor)
    cursor += struct.calcsize("<8HI2HI4I")
    progress = Progress(
        open_obligations=progress_values[0],
        completed_obligations=progress_values[1],
        new_evidence_count=progress_values[2],
        new_hypothesis_count=progress_values[3],
        frontier_expansion_count=progress_values[4],
        repeated_action_count=progress_values[5],
        verifier_state=progress_values[6],
        rollback_count=progress_values[7],
        repeated_error_signature=progress_values[8],
        stagnation_cycles=progress_values[9],
        flags=progress_values[10],
        last_action=progress_values[11],
        reserved=list(progress_values[12:16]),
    )
    specialist_values = struct.unpack_from("<4I", payload, cursor)
    cursor += struct.calcsize("<4I")
    specialists = SpecialistSummary(*specialist_values)
    if cursor != len(payload) - 4:
        raise NativeV14ContractError("cognitive runtime trailing payload")
    if cog.completion_permille > 1000 or cog.halt_success_legal not in (0, 1):
        raise NativeV14ContractError("invalid COG projection semantics")
    if progress.flags & ~PROGRESS_STAGNATED:
        raise NativeV14ContractError("unknown progress flags")
    if five_c.flags & ~FIVE_C_KNOWN_FLAGS:
        raise NativeV14ContractError("unknown 5C state flags")
    return cog, five_c, progress, specialists
