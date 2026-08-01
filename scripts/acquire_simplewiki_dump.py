#!/usr/bin/env python3
"""Resolve, resume, and verify one official SimpleWiki pages-articles archive."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aethersparse.real_corpus.acquisition import (
    acquire_dump,
    dump_object_from_status,
    load_status,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dump-date", required=True)
    parser.add_argument("--status", required=True, help="dumpstatus.json URL or local path")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--progress-log", type=Path)
    parser.add_argument("--manifest-output", type=Path)
    args = parser.parse_args()
    status = load_status(args.status)
    spec = dump_object_from_status(status, dump_date=args.dump_date, status_url=args.status)
    result = acquire_dump(
        spec,
        args.output_dir / spec.filename,
        progress_log=args.progress_log,
    )
    if args.manifest_output is not None:
        args.manifest_output.parent.mkdir(parents=True, exist_ok=True)
        args.manifest_output.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
