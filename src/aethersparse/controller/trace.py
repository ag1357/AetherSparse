"""Per-operation trajectory tracer (Mission 4 Amendment A2-A5).

Diagnostic artifact only: never an input to the controller at runtime, never
in any deployable path.  For every operator invocation the tracer appends one
record with the state view before/after, the legal-action set derived from
the A1 registry preconditions, the action taken, typed arguments with
provenance, the result or failure code, block reads, and wall microseconds.

Retention (A3): every attempted step is kept, including failures; sequences
are marked outcome in {correct, incorrect, aborted} against frozen gold.
Partition flagging (A5): ``training_eligible`` is False for evaluation and
final_held partitions.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from aethersparse.selection.models import FrozenModel

from aethersparse.controller.operators import OPERATORS, OperatorSpec

BLOCK_BYTES = 4096
TRAINING_PARTITIONS = frozenset({"development", "tuning"})


def io_read_bytes() -> int:
    """Bytes actually read from the storage layer by this process."""
    try:
        with open("/proc/self/io", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("read_bytes:"):
                    return int(line.split()[1])
    except OSError:
        return 0
    return 0


class TraceRecord(FrozenModel):
    """One operator invocation (A2)."""

    case_id: str
    step_index: int
    state_before: dict[str, Any]
    legal_actions: tuple[int, ...]
    action_taken: int
    arguments: dict[str, Any]
    result: dict[str, Any]
    state_after: dict[str, Any]
    block_reads: int
    wall_us: int
    terminal: str | None = None


class CaseTrace(FrozenModel):
    """One case's retained sequence with outcome and cost vector (A3/A4)."""

    case_id: str
    partition: str
    training_eligible: bool
    outcome: str  # correct | incorrect | aborted
    records: tuple[TraceRecord, ...]
    total_block_reads: int
    total_steps: int
    max_step_block_reads: int
    wall_us: int


class TrajectoryTracer:
    """Collects A2 records inside the harness; writes JSONL per case."""

    def __init__(self, *, path: Path) -> None:
        self._path = path
        self._records: list[TraceRecord] = []
        self._case_id: str | None = None
        self._case_started_us = 0
        path.parent.mkdir(parents=True, exist_ok=True)

    def begin_case(self, case_id: str) -> None:
        self._case_id = case_id
        self._records = []
        self._case_started_us = time.perf_counter_ns() // 1000

    @staticmethod
    def legal_actions(state: dict[str, Any]) -> tuple[int, ...]:
        """Operator ids whose typed preconditions all hold in ``state``."""
        legal: list[int] = []
        for spec in OPERATORS:
            if all(_slot_holds(state, slot) for slot in spec.typed_preconditions):
                legal.append(spec.operator_id)
        return tuple(sorted(legal))

    def record(
        self,
        *,
        operator: OperatorSpec,
        state_before: dict[str, Any],
        arguments: dict[str, Any],
        result: dict[str, Any],
        state_after: dict[str, Any],
        io_before: int,
        started_us: int,
        terminal: str | None = None,
    ) -> None:
        now_us = time.perf_counter_ns() // 1000
        io_after = io_read_bytes()
        self._records.append(
            TraceRecord(
                case_id=self._case_id or "unknown",
                step_index=len(self._records),
                state_before=state_before,
                legal_actions=self.legal_actions(state_before),
                action_taken=operator.operator_id,
                arguments=arguments,
                result=result,
                state_after=state_after,
                block_reads=max(0, (io_after - io_before) // BLOCK_BYTES),
                wall_us=now_us - started_us,
                terminal=terminal,
            )
        )

    def end_case(
        self,
        *,
        partition: str,
        outcome: str,
        terminal: str | None,
    ) -> CaseTrace:
        """Close the case, mark its outcome, append one JSONL line (A3/A5)."""
        if self._records:
            last = self._records[-1]
            if terminal is not None and last.terminal is None:
                self._records[-1] = last.model_copy(update={"terminal": terminal})
        now_us = time.perf_counter_ns() // 1000
        reads = [record.block_reads for record in self._records]
        trace = CaseTrace(
            case_id=self._case_id or "unknown",
            partition=partition,
            training_eligible=partition in TRAINING_PARTITIONS,
            outcome=outcome,
            records=tuple(self._records),
            total_block_reads=sum(reads),
            total_steps=len(self._records),
            max_step_block_reads=max(reads) if reads else 0,
            wall_us=now_us - self._case_started_us,
        )
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(trace.model_dump(), sort_keys=True) + "\n")
        self._records = []
        return trace


def _slot_holds(state: dict[str, Any], slot: str) -> bool:
    """A precondition slot holds when the state view carries a truthy value."""
    value = state.get(slot)
    if value is None:
        return False
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return bool(value) if not isinstance(value, str) else value != ""


def trace_path_for(cache_path: Path, controller_commit: str) -> Path:
    """A5 keying: (tier, retrieval config hash, benchmark version, commit)."""
    cache_path = Path(cache_path)
    return cache_path.with_name(
        f"{cache_path.stem}-traces-{controller_commit}.jsonl"
    )


def current_commit() -> str:
    try:
        import subprocess

        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        return out.stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def default_clock_us() -> int:
    return time.perf_counter_ns() // 1000
