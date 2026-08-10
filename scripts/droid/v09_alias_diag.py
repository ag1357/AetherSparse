#!/usr/bin/env python3
"""Phase 5 (Lane B): alias/redirect pool-collapse diagnostics.

Answers, per tier, for alias- and redirect-category answer cases:
  1. aliases per query anchor (how many alias rows / distinct documents)
  2. multi-pageid resolution: does the anchor map to >1 document
  3. pool absence vs displacement: gold doc absent from the candidate pool
     (candidate generation) vs present but outside top-8 (ranking)
  4. disambiguation contamination: pool docs titled '*(disambiguation)*'
     and whether the gold doc is one

Anchor extraction: question n-grams (1-4 tokens) are point-looked-up in the
pack's aliases table; the longest matching n-gram wins.
Diagnostic only; no behavior changes.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v050_common import load_benchmark, pageid  # noqa: E402


def anchor_ngrams(question: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9']+", question.casefold())
    grams = []
    for n in (4, 3, 2, 1):
        for i in range(len(tokens) - n + 1):
            grams.append(" ".join(tokens[i : i + n]))
    return grams


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", required=True)
    parser.add_argument("--outcomes", required=True, help="rung0 outcomes JSON")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    db = sqlite3.connect(f"file:{args.pack}?mode=ro&immutable=1", uri=True)
    db.execute("PRAGMA cache_size=-65536")
    benchmark = load_benchmark()
    cases_by_id = {c.case_id: c for c in benchmark.cases}
    outcomes = json.loads(Path(args.outcomes).read_text())

    def alias_docs(alias: str) -> list[str]:
        return [
            row[0]
            for row in db.execute(
                "SELECT document_id FROM aliases WHERE alias = ?", (alias,)
            )
        ]

    rows = []
    counts = Counter()
    alias_doc_counts = []
    for outcome in outcomes:
        case = cases_by_id.get(outcome["case_id"])
        if case is None or str(case.accepted_disposition) != "ANSWER":
            continue
        cats = set(outcome.get("categories") or ())
        if not cats & {"alias", "redirect"}:
            continue
        gold_pageids = {
            pageid(g.document_id) for g in case.gold_evidence
        }
        anchor = None
        anchor_docs: list[str] = []
        gold_anchor = None
        gold_anchor_docs: list[str] = []
        ngrams_with_rows = 0
        for gram in anchor_ngrams(case.question):
            docs = alias_docs(gram)
            if not docs:
                continue
            ngrams_with_rows += 1
            if anchor is None:
                anchor = gram
                anchor_docs = docs
            if gold_anchor is None and any(pageid(d) in gold_pageids for d in docs):
                gold_anchor = gram
                gold_anchor_docs = docs
        pool = set(outcome["pool_pageids"])
        if "top8_pageids" in outcome:  # v08 pipeline ladder schema
            top8 = set(outcome["top8_pageids"])
            gold_in_top8 = bool(gold_pageids & top8)
        else:  # v050 selector-eval schema
            gold_in_top8 = bool(outcome.get("lenient"))
        # per-pool disambiguation via per-doc title lookup (pool is <= ~400)
        disambig_pool = 0
        for pid in pool:
            title_rows = db.execute(
                "SELECT title FROM documents WHERE document_id LIKE ?",
                (f"mw:{pid}:%",),
            ).fetchall()
            if any("(disambiguation)" in (t[0] or "").casefold() for t in title_rows):
                disambig_pool += 1
        gold_disambig = False
        for gp in gold_pageids:
            for (title,) in db.execute(
                "SELECT title FROM documents WHERE document_id LIKE ?",
                (f"mw:{gp}:%",),
            ):
                if "(disambiguation)" in (title or "").casefold():
                    gold_disambig = True
        gold_in_pool = bool(gold_pageids & pool)
        status = (
            "present_top8"
            if gold_in_top8
            else "displaced_in_pool"
            if gold_in_pool
            else "absent_from_pool"
        )
        counts[status] += 1
        if ngrams_with_rows == 0:
            counts["no_alias_row_for_any_question_ngram"] += 1
        if gold_anchor is not None:
            counts["question_alias_maps_to_gold"] += 1
            alias_doc_counts.append(len(gold_anchor_docs))
            if len(gold_anchor_docs) > 1:
                counts["gold_anchor_multi_pageid"] += 1
        else:
            counts["question_alias_misses_gold"] += 1
        if disambig_pool:
            counts["pool_has_disambiguation"] += 1
        if gold_disambig:
            counts["gold_is_disambiguation"] += 1
        rows.append(
            {
                "case_id": outcome["case_id"],
                "categories": sorted(cats),
                "anchor": anchor,
                "gold_anchor": gold_anchor,
                "gold_anchor_doc_count": len(gold_anchor_docs),
                "ngrams_with_alias_rows": ngrams_with_rows,
                "status": status,
                "pool_disambiguation_pages": disambig_pool,
                "gold_is_disambiguation": gold_disambig,
            }
        )

    import statistics

    report = {
        "pack": args.pack,
        "outcomes": args.outcomes,
        "n_alias_redirect_answer_cases": len(rows),
        "status_counts": dict(counts.most_common()),
        "anchor_doc_count": {
            "mean": round(statistics.fmean(alias_doc_counts), 2) if alias_doc_counts else 0,
            "p50": sorted(alias_doc_counts)[len(alias_doc_counts) // 2] if alias_doc_counts else 0,
            "max": max(alias_doc_counts) if alias_doc_counts else 0,
        },
        "per_case": rows,
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report["status_counts"], indent=1))
    print("anchor doc counts:", report["anchor_doc_count"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
