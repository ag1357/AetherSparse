"""Bounded exact operational telemetry with no user-text payloads."""

from __future__ import annotations

import hashlib
import struct
from collections import Counter, deque
from dataclasses import dataclass, field
from enum import IntEnum


class RuntimeEventType(IntEnum):
    CONTROLLER_OPERATION = 1
    SPECIALIST_ACTIVATION = 2
    SPECIALIST_RESIDENCY = 3
    CACHE_PAGE = 4
    MEMORY_TIER_CHANGE = 5
    TOOL_CALL = 6
    VERIFIER_DISPOSITION = 7
    OBLIGATION_PROGRESS = 8
    STAGNATION = 9
    LATENCY = 10
    FAILURE = 11


@dataclass(frozen=True)
class RuntimeEvent:
    sequence: int
    event_type: RuntimeEventType
    component_id: int
    operation_id: int
    value: int
    latency_us: int
    state_hash64: int


RUNTIME_EVENT_WIRE_SIZE = 40
_EVENT_WIRE = struct.Struct("<QHHIIqIQ")


def serialize_runtime_event(event: RuntimeEvent) -> bytes:
    return _EVENT_WIRE.pack(
        event.sequence,
        int(event.event_type),
        0,
        event.component_id,
        event.operation_id,
        event.value,
        event.latency_us,
        event.state_hash64,
    )


def deserialize_runtime_event(payload: bytes) -> RuntimeEvent:
    if len(payload) != RUNTIME_EVENT_WIRE_SIZE:
        raise ValueError("runtime event wire must be exactly 40 bytes")
    sequence, kind, reserved, component, operation, value, latency, state_hash = (
        _EVENT_WIRE.unpack(payload)
    )
    if reserved != 0:
        raise ValueError("runtime event reserved field must be zero")
    try:
        event_type = RuntimeEventType(kind)
    except ValueError as error:
        raise ValueError("unknown runtime event type") from error
    return RuntimeEvent(
        sequence=sequence,
        event_type=event_type,
        component_id=component,
        operation_id=operation,
        value=value,
        latency_us=latency,
        state_hash64=state_hash,
    )


def stable_hash64(value: str) -> int:
    """Hash an opaque identifier; source/user text must never be passed here."""

    return int.from_bytes(hashlib.sha256(value.encode("utf-8")).digest()[:8], "little")


@dataclass
class ExactRuntimeObserver:
    """Observer is append-only, bounded, and unable to influence control flow."""

    capacity: int = 4096
    _events: deque[RuntimeEvent] = field(init=False, repr=False)
    _sequence: int = field(default=0, init=False, repr=False)
    _dropped: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.capacity <= 0:
            raise ValueError("observer capacity must be positive")
        self._events = deque(maxlen=self.capacity)

    @property
    def dropped_events(self) -> int:
        return self._dropped

    def record(
        self,
        event_type: RuntimeEventType,
        *,
        component_id: int = 0,
        operation_id: int = 0,
        value: int = 0,
        latency_us: int = 0,
        state_id: str = "",
    ) -> RuntimeEvent:
        if min(component_id, operation_id, latency_us) < 0:
            raise ValueError("telemetry identifiers and latency must be nonnegative")
        if len(self._events) == self.capacity:
            self._dropped += 1
        event = RuntimeEvent(
            sequence=self._sequence,
            event_type=event_type,
            component_id=component_id,
            operation_id=operation_id,
            value=value,
            latency_us=latency_us,
            state_hash64=stable_hash64(state_id) if state_id else 0,
        )
        self._sequence += 1
        self._events.append(event)
        return event

    def snapshot(self) -> tuple[RuntimeEvent, ...]:
        return tuple(self._events)

    def counters(self) -> dict[str, int]:
        counts = Counter(event.event_type.name for event in self._events)
        return {name: counts.get(name, 0) for name in RuntimeEventType.__members__}

    def total_latency_us(self) -> int:
        return sum(event.latency_us for event in self._events)
