#!/usr/bin/env python3
"""Analyze compact Mission 6 observer JSONL without loading full activations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aethersparse.observer.analysis import analyze_records
from aethersparse.observer.store import load_jsonl


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("telemetry", type=Path, help="sampled observer JSONL")
    parser.add_argument("--output", type=Path, required=True, help="compact analysis JSON")
    args = parser.parse_args()
    records = load_jsonl(args.telemetry)
    report = analyze_records(records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "case_count": report["case_count"],
                "output": str(args.output),
                "route_count": len(report["correctness_and_compute_by_route"]),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
