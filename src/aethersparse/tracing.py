"""Bounded AetherIR operation trace collection."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from time import perf_counter_ns

from aethersparse.models import CostSummary, OperationCategory, TraceEntry


class TraceRecorder:
    def __init__(self) -> None:
        self.entries: list[TraceEntry] = []

    @contextmanager
    def operation(
        self,
        name: str,
        category: OperationCategory,
        *,
        input_count: int = 0,
        output_count: int = 0,
        bytes_read: int = 0,
        storage_reads: int = 0,
        integer_ops: int = 0,
        working_ram_bytes: int = 0,
    ) -> Iterator[None]:
        start = perf_counter_ns()
        yield
        elapsed_us = max(0, (perf_counter_ns() - start) // 1000)
        self.entries.append(
            TraceEntry(
                cycle=len(self.entries),
                operation=name,
                category=category,
                input_count=input_count,
                output_count=output_count,
                bytes_read=bytes_read,
                storage_reads=storage_reads,
                integer_ops=integer_ops,
                working_ram_bytes=working_ram_bytes,
                host_latency_us=elapsed_us,
                measurement="measured_host",
            )
        )

    def summary(self) -> CostSummary:
        return CostSummary(
            operation_count=len(self.entries),
            bytes_read=sum(entry.bytes_read for entry in self.entries),
            storage_reads=sum(entry.storage_reads for entry in self.entries),
            integer_ops=sum(entry.integer_ops for entry in self.entries),
            peak_working_ram_bytes=max(
                (entry.working_ram_bytes for entry in self.entries),
                default=0,
            ),
            measured_host_latency_us=sum(entry.host_latency_us for entry in self.entries),
        )
