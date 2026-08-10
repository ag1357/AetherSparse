#!/usr/bin/env python3
"""Build the bounded edit-distance <=2 spelling sidecar for a pack (Lane C)."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from aethersparse.selection.spelling import build_sidecar  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    out = args.output or args.pack.with_name(f"{args.pack.stem}.ed2.sqlite")
    started = time.time()
    stats = build_sidecar(args.pack, out)
    stats["build_seconds"] = round(time.time() - started, 2)
    stats["sidecar"] = str(out)
    print(json.dumps(stats, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
