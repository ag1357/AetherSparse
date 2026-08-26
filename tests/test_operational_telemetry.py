from aethersparse.operational_telemetry import (
    RUNTIME_EVENT_WIRE_SIZE,
    ExactRuntimeObserver,
    RuntimeEventType,
    deserialize_runtime_event,
    serialize_runtime_event,
    stable_hash64,
)


def test_exact_observer_is_bounded_and_counts_drops() -> None:
    observer = ExactRuntimeObserver(capacity=2)
    observer.record(RuntimeEventType.CONTROLLER_OPERATION, operation_id=43, state_id="cog:1")
    observer.record(RuntimeEventType.VERIFIER_DISPOSITION, value=1, latency_us=7)
    observer.record(RuntimeEventType.FAILURE, value=9)
    assert [event.sequence for event in observer.snapshot()] == [1, 2]
    assert observer.dropped_events == 1
    assert observer.counters()["CONTROLLER_OPERATION"] == 0
    assert observer.counters()["FAILURE"] == 1
    assert observer.total_latency_us() == 7


def test_observer_uses_stable_ids_not_text_payloads() -> None:
    first = stable_hash64("cog:stable-state")
    assert first == stable_hash64("cog:stable-state")
    assert first != stable_hash64("cog:other-state")
    event = ExactRuntimeObserver().record(
        RuntimeEventType.SPECIALIST_ACTIVATION,
        component_id=4,
        state_id="cog:stable-state",
    )
    assert event.state_hash64 == first


def test_exact_event_wire_round_trip() -> None:
    event = ExactRuntimeObserver().record(
        RuntimeEventType.CACHE_PAGE,
        component_id=7,
        operation_id=2,
        value=-3,
        latency_us=41,
        state_id="session:opaque-id",
    )
    wire = serialize_runtime_event(event)
    assert len(wire) == RUNTIME_EVENT_WIRE_SIZE
    assert deserialize_runtime_event(wire) == event
