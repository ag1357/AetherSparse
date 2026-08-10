#!/usr/bin/env python3
"""Phase 7 (Lane C): misspelling ranking diagnostics.

For misspelling-category answer cases with gold evidence, split into:
  present_top8        gold pageid in the ranked top-8 (no rankloss)
  displaced_in_pool   gold in candidate pool but outside top-8 (rankloss)
  absent_from_pool    candidate-generation miss (out of scope for Phase 7)

For each displaced case, classify the suspect query token(s) — content tokens
(len>=4) absent verbatim from the gold document text:
  - dual_normalization_sufficient: the suspect token appears in the gold text
    after casefold/space normalization (raw-surface trigrams would find it)
  - edit_distance_needed: no verbatim hit, but an edit-distance <=2 token
    exists in the gold text vocabulary
  - unrecoverable_token: neither

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

_STOP = frozenset(
    "what which when where who whom whose why how many much does did do is are was were "
    "the a an of in on at for to from by with about into over after before between "
    "this that these those there here it its his her their our your my".split()
)


def trigrams(token: str) -> set[str]:
    padded = f"  {token}  "
    return {padded[i : i + 3] for i in range(len(padded) - 2)}


def edit_distance_le2(a: str, b: str) -> bool:
    if abs(len(a) - len(b)) > 2:
        return False
    # classic DP with early width cap of 2
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        row_min = i
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            row_min = min(row_min, cur[j])
        if row_min > 2:
            return False
        prev = cur
    return prev[-1] <= 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", required=True)
    parser.add_argument("--outcomes", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    db = sqlite3.connect(f"file:{args.pack}?mode=ro&immutable=1", uri=True)
    db.execute("PRAGMA cache_size=-65536")
    benchmark = load_benchmark()
    cases_by_id = {c.case_id: c for c in benchmark.cases}
    outcomes = json.loads(Path(args.outcomes).read_text())

    status_counts: Counter[str] = Counter()
    token_classes: Counter[str] = Counter()
    rows = []
    for outcome in outcomes:
        case = cases_by_id.get(outcome["case_id"])
        if case is None or str(case.accepted_disposition) != "ANSWER":
            continue
        if "misspelling" not in set(outcome.get("categories") or ()):
            continue
        gold_pageids = {pageid(g.document_id) for g in case.gold_evidence}
        pool = set(outcome["pool_pageids"])
        if "top8_pageids" in outcome:  # v08 pipeline ladder schema
            gold_in_top8 = bool(gold_pageids & set(outcome["top8_pageids"]))
        else:  # v050 selector-eval schema
            gold_in_top8 = bool(outcome.get("lenient"))
        if gold_in_top8:
            status_counts["present_top8"] += 1
            continue
        if gold_pageids & pool:
            status = "displaced_in_pool"
        else:
            status = "absent_from_pool"
        status_counts[status] += 1
        if status != "displaced_in_pool":
            continue

        tokens = [
            t
            for t in re.findall(r"[a-z']+", case.question.casefold())
            if len(t) >= 4 and t not in _STOP
        ]
        # gold document text vocabulary (titles + bodies of all its chunks)
        vocab: set[str] = set()
        text_blobs: list[str] = []
        for gp in gold_pageids:
            for (body,) in db.execute(
                "SELECT body FROM chunks_fts WHERE chunk_id IN "
                "(SELECT chunk_id FROM chunks WHERE document_id LIKE ?)",
                (f"mw:{gp}:%",),
            ):
                text_blobs.append(body.casefold())
        gold_text = " ".join(text_blobs)
        vocab = set(re.findall(r"[a-z']+", gold_text))

        suspects = [t for t in tokens if t not in vocab]
        case_classes = []
        for token in suspects:
            if f" {token} " in f" {gold_text} ":
                token_classes["dual_normalization_sufficient"] += 1
                case_classes.append("dual_normalization_sufficient")
                continue
            t3 = trigrams(token)
            candidates = [
                v
                for v in vocab
                if abs(len(v) - len(token)) <= 2 and len(t3 & trigrams(v)) >= 2
            ]
            if any(edit_distance_le2(token, v) for v in candidates):
                token_classes["edit_distance_needed"] += 1
                case_classes.append("edit_distance_needed")
            else:
                token_classes["unrecoverable_token"] += 1
                case_classes.append("unrecoverable_token")
        rows.append(
            {
                "case_id": outcome["case_id"],
                "question": case.question,
                "suspect_tokens": suspects,
                "classes": case_classes,
            }
        )

    report = {
        "pack": args.pack,
        "outcomes": args.outcomes,
        "status_counts": dict(status_counts.most_common()),
        "token_classification": dict(token_classes.most_common()),
        "displaced_cases": rows,
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report["status_counts"], indent=1))
    print(json.dumps(report["token_classification"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
