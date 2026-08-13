"""Optional sinks and an out-of-band observer facade."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Protocol

from aethersparse.observer.models import CycleTelemetry, TelemetryRecord
from aethersparse.observer.sampling import DeterministicSampler
from aethersparse.observer.signatures import route_signature, signature_sha256


class ObserverSink(Protocol):
    def write(self, record: TelemetryRecord) -> None: ...


class NullObserverSink:
    """Zero-I/O sink for explicitly disabled research telemetry."""

    def write(self, record: TelemetryRecord) -> None:
        del record


class JsonlObserverSink:
    """Append content-validated compact records to research storage."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def write(self, record: TelemetryRecord) -> None:
        if signature_sha256(record.route_signature) != record.route_sha256:
            raise ValueError("route signature hash does not match record")
        payload = json.dumps(record.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(payload + "\n")


class ResearchObserver:
    """Finalize completed cycles, sample them, and never return a decision."""

    def __init__(self, sink: ObserverSink, sampler: DeterministicSampler | None = None) -> None:
        self._sink = sink
        self._sampler = sampler or DeterministicSampler()

    def observe(
        self,
        *,
        case_id: str,
        partition: str,
        tier: str,
        cycles: tuple[CycleTelemetry, ...],
        final_correctness: bool,
        final_semantic_correctness: bool,
        final_provenance_correctness: bool,
    ) -> TelemetryRecord | None:
        """Observe a completed path; the return is diagnostic, never a control value."""

        if not cycles:
            raise ValueError("observer requires at least one completed cycle")
        signature = route_signature(cycles)
        route_hash = signature_sha256(signature)
        maximum_uncertainty = max(
            max(cycle.entropy_before, cycle.entropy_after) for cycle in cycles
        )
        decision = self._sampler.decide(
            case_id=case_id,
            route_sha256=route_hash,
            final_correctness=final_correctness,
            maximum_uncertainty=maximum_uncertainty,
        )
        if not decision.sampled:
            return None
        record = TelemetryRecord(
            case_id=case_id,
            partition=partition,
            tier=tier,
            cycles=cycles,
            final_correctness=final_correctness,
            final_semantic_correctness=final_semantic_correctness,
            final_provenance_correctness=final_provenance_correctness,
            route_signature=signature,
            route_sha256=route_hash,
            maximum_uncertainty=maximum_uncertainty,
            sampled_because=decision.reasons,
        )
        self._sink.write(record)
        return record


def load_jsonl(path: Path) -> tuple[TelemetryRecord, ...]:
    records: list[TelemetryRecord] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(TelemetryRecord.model_validate_json(line))
    return tuple(records)
