#!/usr/bin/env python3
"""Build reranker training questions from the frozen V050 benchmark.

Emits the legacy ``questions.json`` schema consumed by
``aethersparse.selection.qualification.train_reranker``, restricted to the
benchmark's tuning+development partitions.  Gold chunk IDs are mapped into the
selector pack by pageid + raw-wikitext offset overlap (both packs store the
same revision bytes, so gold char offsets transfer exactly).

The benchmark is read-only input; nothing here modifies it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v050_common import (  # noqa: E402
    BENCHMARK_PATH,
    FIT_PARTITIONS,
    answer_cases,
    case_gold_pageids,
    load_benchmark,
    pageid,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, default=BENCHMARK_PATH)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    benchmark = load_benchmark(args.benchmark)
    cases = [
        case for case in answer_cases(benchmark) if str(case.partition) in FIT_PARTITIONS
    ]
    db = sqlite3.connect(f"file:{args.pack.resolve()}?mode=ro&immutable=1", uri=True)
    db.row_factory = sqlite3.Row
    chunks_by_pageid: dict[str, list[sqlite3.Row]] = {}
    doc_by_pageid: dict[str, str] = {}
    for row in db.execute("SELECT document_id FROM documents"):
        doc_by_pageid[pageid(str(row["document_id"]))] = str(row["document_id"])
    for row in db.execute(
        "SELECT chunk_id,document_id,raw_start,raw_end FROM chunks ORDER BY document_id,raw_start"
    ):
        chunks_by_pageid.setdefault(pageid(str(row["document_id"])), []).append(row)

    questions: list[dict[str, object]] = []
    dropped_no_document = dropped_no_chunk = 0
    for case in cases:
        gold_pageids = case_gold_pageids(case)
        gold_documents = [
            doc_by_pageid[pid] for pid in sorted(gold_pageids) if pid in doc_by_pageid
        ]
        if not gold_documents:
            dropped_no_document += 1
            continue
        gold_chunks: set[str] = set()
        for evidence in case.gold_evidence:
            pid = pageid(evidence.document_id)
            for chunk in chunks_by_pageid.get(pid, []):
                if (
                    int(chunk["raw_start"]) < evidence.char_end
                    and evidence.char_start < int(chunk["raw_end"])
                ):
                    gold_chunks.add(str(chunk["chunk_id"]))
        if not gold_chunks:
            dropped_no_chunk += 1
            continue
        questions.append(
            {
                "question_id": case.case_id,
                "query": case.question,
                "category": (
                    "two_article" if len(gold_documents) == 2 else
                    "three_article" if len(gold_documents) > 2 else
                    str(case.categories[0])
                ),
                "categories": list(case.categories),
                "partition": str(case.partition),
                "gold_document_path": gold_documents,
                "gold_chunk_ids": sorted(gold_chunks),
                "author_seed": 0,
                "method": "v050_r1_pageid_offset_projection",
            }
        )

    payload = {
        "corpus_sha256": hashlib.sha256(args.pack.read_bytes()).hexdigest(),
        "count": len(questions),
        "questions": questions,
        "schema_version": "3",
        "seed": 0,
        "source_benchmark": benchmark.benchmark_identity,
        "source_benchmark_sha256": benchmark.content_sha256,
        "partitions": list(FIT_PARTITIONS),
        "dropped_no_document": dropped_no_document,
        "dropped_no_chunk": dropped_no_chunk,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "questions": len(questions),
                "dropped_no_document": dropped_no_document,
                "dropped_no_chunk": dropped_no_chunk,
                "output": str(args.output),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
