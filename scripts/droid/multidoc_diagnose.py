#!/usr/bin/env python3
"""Phase 3 diagnostic: why do multi-source cases fail at 25k?

For every failing two_source / three_to_six_source case, classifies each
missing gold pageid into the mission's cause taxonomy:

  (a) sub-queries retrieve the same document repeatedly (need pageid diversity)
  (b) decomposition/resolution fails: no sub-query targets the gold entity
      (b2: a sub-query targets it but cannot find it even in a deep top-24)
  (c) the gold is in the entity's sub-search but beyond the per-entity budget
  (d) the gold is in the 96-candidate pool but ranked out of the top-8

Provenance is exact: ``store.search`` is spy-wrapped during the real
``candidates()`` call, so every pool row maps to the probe that produced it.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v050_common import (  # noqa: E402
    BENCHMARK_PATH,
    answer_cases,
    case_gold_pageids,
    load_benchmark,
    pageid,
    write_report,
)

from aethersparse.selection.selector import EvidenceSelector  # noqa: E402
from aethersparse.traversal.corpus import TOKEN_RE  # noqa: E402

MULTI = ("two_source", "three_to_six_source")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, default=BENCHMARK_PATH)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate-limit", type=int, default=96)
    args = parser.parse_args()

    benchmark = load_benchmark(args.benchmark)
    cases = [
        case
        for case in answer_cases(benchmark)
        if any(cat in MULTI for cat in case.categories)
    ]
    selector = EvidenceSelector(args.pack, candidate_limit=args.candidate_limit)

    title_by_pageid = {
        pageid(str(row[0])): str(row[1])
        for row in selector.store.db.execute("SELECT document_id, title FROM documents")
    }

    cause_counts: Counter[str] = Counter()
    case_causes: Counter[str] = Counter()
    records: list[dict[str, object]] = []
    n_pass = 0

    for index, case in enumerate(cases, start=1):
        gold = case_gold_pageids(case)
        candidates = selector.candidates(case.question)
        trace = selector.select(case.question, stage="reranker", initial_candidates=candidates)
        selected = [pageid(item.document_id) for item in trace.selected_evidence]
        missing = gold - set(selected)
        if not missing:
            n_pass += 1
            continue

        # Re-run generation with a provenance spy.
        calls: list[tuple[str, int, list]] = []
        original = selector.store.search

        def spy(query_text, limit, _orig=original):
            rows = _orig(query_text, limit)
            calls.append((query_text, limit, rows))
            return rows

        selector.store.search = spy  # type: ignore[method-assign]
        pool = selector.candidates(case.question)
        selector.store.search = original  # type: ignore[method-assign]

        pool_pageids = [pageid(c.document_id) for c in pool]
        pool_set = set(pool_pageids)

        anchors = list(
            dict.fromkeys(
                [
                    *selector._anchor_documents(case.question),
                    *selector._alias_probed_documents(case.question),
                ]
            )
        )[:8]
        anchor_titles = [
            title_by_pageid.get(pageid(a), "") for a in anchors
        ]
        decomposed = selector._is_multi_entity_query(case.question, anchors)

        # Rebuild the per-entity sub-queries exactly as candidates() does.
        entity_titles = anchor_titles[:6]
        anchor_tokens = {
            t for title in entity_titles for t in TOKEN_RE.findall(title.casefold())
        }
        context_terms = sorted(
            t
            for t in set(TOKEN_RE.findall(case.question.casefold()))
            if len(t) > 2 and t not in anchor_tokens
        )[:6]
        lexical_limit = min(48, args.candidate_limit)
        per_entity = max(6, lexical_limit // (2 * max(1, len(entity_titles))))
        sub_queries = {title: " ".join([title, *context_terms]) for title in entity_titles}

        record: dict[str, object] = {
            "case_id": case.case_id,
            "categories": list(case.categories),
            "question": case.question,
            "gold": sorted(gold),
            "selected": selected,
            "decomposed": decomposed,
            "anchor_titles": anchor_titles,
            "missing": {},
        }
        case_tags: set[str] = set()
        for g in sorted(missing):
            g_title = title_by_pageid.get(g, "")
            tags: list[str] = []
            detail: dict[str, object] = {"title": g_title}
            if g in pool_set:
                tags.append("d")
                detail["pool_rank"] = pool_pageids.index(g)
                detail["selected_gold_slots"] = sum(1 for s in selected if s in gold)
            else:
                # Which entity sub-query (if any) targets this gold doc?
                target_title = next(
                    (t for t in entity_titles if t.casefold() == g_title.casefold()),
                    None,
                )
                if not decomposed:
                    tags.append("b-nodecomp")
                elif target_title is None:
                    tags.append("b-unresolved")
                else:
                    deep = selector.store.search(sub_queries[target_title], 24)
                    deep_ids = [pageid(str(r["document_id"])) for r in deep]
                    detail["sub_rank_deep24"] = (
                        deep_ids.index(g) if g in deep_ids else None
                    )
                    detail["sub_distinct_pageids"] = len(set(deep_ids[:per_entity]))
                    if g in deep_ids[:per_entity]:
                        tags.append("inconsistent")  # should have been in the pool
                    elif g in deep_ids:
                        tags.append("c")
                    else:
                        tags.append("b2-lexical-miss")
                    # (a): the entity's own sub-query slots are spent on a
                    # single other pageid (same doc retrieved repeatedly).
                    head = deep_ids[:per_entity]
                    if head and head.count(head[0]) >= max(2, per_entity // 2) and head[0] != g:
                        tags.append("a")
            for tag in tags:
                cause_counts[tag] += 1
            case_tags.update(t.split("-")[0] for t in tags)
            record["missing"][g] = {**detail, "causes": tags}  # type: ignore[index]
        for tag in case_tags:
            case_causes[tag] += 1
        records.append(record)
        if index % 20 == 0:
            print(f"diagnosed {index}/{len(cases)}", file=sys.stderr, flush=True)

    report = {
        "tool": "scripts/droid/multidoc_diagnose.py",
        "pack": str(args.pack),
        "multi_cases": len(cases),
        "strict_pass": n_pass,
        "strict_fail": len(records),
        "missing_gold_causes": dict(cause_counts),
        "cases_touched_by_cause": dict(case_causes),
        "failures": records,
    }
    write_report(args.output, report)
    print(json.dumps({"pass": n_pass, "fail": len(records),
                      "causes": dict(cause_counts)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
