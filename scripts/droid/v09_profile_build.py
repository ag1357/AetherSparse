#!/usr/bin/env python3
"""Phase 0A.1: coarse wall-clock profile of pack build and evaluation.

Build: wraps CorpusStore.ingest_mediawiki stages with timers by monkeypatching
the page iterator and DB execute (counts per-statement-kind time), plus
cProfile for function-level attribution.

Eval: cProfile around v050_selector_eval at a small limit; buckets time into
candidate generation vs selection vs harness overhead.

Reports JSON. Diagnostic only; writes packs only to a scratch path.
"""

from __future__ import annotations

import argparse
import cProfile
import io
import json
import pstats
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aethersparse.traversal.corpus import CorpusStore  # noqa: E402


class _TimedConnection:
    """sqlite3.Connection subclass with per-statement-kind timing."""

    def __init__(self, wrapped: sqlite3.Connection):
        self._wrapped = wrapped
        self.buckets: dict[str, float] = defaultdict(float)
        self.counts: dict[str, int] = defaultdict(int)

    def execute(self, sql, parameters=()):
        started = time.perf_counter()
        result = self._wrapped.execute(sql, parameters)
        elapsed = time.perf_counter() - started
        head = " ".join(str(sql).split()[:3])[:60]
        self.buckets[f"sql:{head}"] += elapsed
        self.counts[f"sql:{head}"] += 1
        return result

    def __getattr__(self, name):
        return getattr(self._wrapped, name)


def profile_build(dump: Path, output: Path, limit: int) -> dict:
    store = CorpusStore(output)
    timed = _TimedConnection(store.db)
    store.db = timed  # type: ignore[assignment]

    profiler = cProfile.Profile()
    started = time.perf_counter()
    profiler.enable()
    manifest = store.ingest_mediawiki(dump, limit=limit)
    profiler.disable()
    total = time.perf_counter() - started

    stream = io.StringIO()
    pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(30)
    stats_text = stream.getvalue()

    return {
        "limit": limit,
        "total_seconds": round(total, 2),
        "manifest": manifest,
        "sql_buckets": {
            key: {"seconds": round(value, 2), "calls": timed.counts[key]}
            for key, value in sorted(timed.buckets.items(), key=lambda kv: -kv[1])
        },
        "cprofile_top": stats_text.splitlines()[:45],
    }


def profile_eval(pack: Path, limit: int) -> dict:
    import v050_selector_eval

    argv = sys.argv
    sys.argv = [
        "v050_selector_eval.py",
        "--pack",
        str(pack),
        "--candidate-limit",
        "96",
        "--limit",
        str(limit),
        "--output",
        "/tmp/v09-profile-eval.json",
    ]
    profiler = cProfile.Profile()
    started = time.perf_counter()
    try:
        profiler.enable()
        v050_selector_eval.main()
        profiler.disable()
        rc = 0
    finally:
        sys.argv = argv
    total = time.perf_counter() - started
    stream = io.StringIO()
    pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(24)
    return {
        "limit": limit,
        "total_seconds": round(total, 2),
        "returncode": rc,
        "cprofile_top": stream.getvalue().splitlines()[4:34],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump", type=Path)
    parser.add_argument("--scratch", type=Path, default=Path("/tmp/v09-profile-pack.sqlite"))
    parser.add_argument("--pack", type=Path, help="existing pack for the eval profile")
    parser.add_argument("--build-limit", type=int, default=10000)
    parser.add_argument("--eval-limit", type=int, default=200)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report: dict[str, object] = {}
    if args.dump:
        args.scratch.unlink(missing_ok=True)
        report["build"] = profile_build(args.dump, args.scratch, args.build_limit)
    if args.pack:
        report["eval"] = profile_eval(args.pack, args.eval_limit)
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report.get("build", {}).get("sql_buckets", {}), indent=2)[:1200])
    print(f"report={args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
