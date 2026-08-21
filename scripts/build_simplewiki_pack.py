#!/usr/bin/env python3
"""Build one exact progressive AetherSparse v0.5 corpus pack."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast

from aethersparse.real_corpus.builder import PackSettings, build_pack


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dump", required=True, type=Path)
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest-output", required=True, type=Path)
    # The frozen v0.5 series pinned 10k/50k; the Semantic Address v2 Factory
    # handoff additionally requires the canonical 25k and full-dump (397,196)
    # tiers.  The limit remains exact: the builder fails if the dump cannot
    # supply precisely this many namespace-0 articles.
    parser.add_argument("--articles", required=True, type=int, choices=(10_000, 25_000, 50_000, 397_196))
    parser.add_argument("--chunk-chars", type=int, default=480)
    args = parser.parse_args()
    with args.source_manifest.open(encoding="utf-8") as stream:
        source = cast(dict[str, Any], json.load(stream))
    manifest = build_pack(
        args.dump,
        args.output,
        source=source,
        settings=PackSettings(article_limit=args.articles, chunk_chars=args.chunk_chars),
    )
    args.manifest_output.parent.mkdir(parents=True, exist_ok=True)
    args.manifest_output.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
