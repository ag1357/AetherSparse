"""Independent graph-derived question authoring and matched retrieval evaluation."""

from __future__ import annotations

import hashlib
import json
import random
import sqlite3
import time
from pathlib import Path
from typing import Any, TypedDict

from aethersparse.traversal.corpus import TOKEN_RE, CorpusStore
from aethersparse.traversal.models import TraversalBudget
from aethersparse.traversal.runtime import TraversalRuntime

COMMON = {
    "about", "after", "also", "and", "are", "article", "from", "has", "have", "into",
    "its", "more", "not", "that", "the", "their", "this", "was", "were", "which", "with",
}


class SystemCounters(TypedDict):
    article_hits: int
    span_hits: int
    bytes: list[int]
    latency_ms: list[float]


def _clue(text: str, *, count: int = 4) -> str:
    words = [
        word.casefold() for word in TOKEN_RE.findall(text)
        if len(word) >= 5 and word.casefold() not in COMMON
    ]
    ranked = sorted(set(words), key=lambda word: (-len(word), word))
    return " ".join(ranked[:count])


def author_questions(
    corpus_path: Path, output: Path, *, count: int = 2000, seed: int = 48_271
) -> dict[str, Any]:
    """Use three isolated deterministic strategies; no runtime output is consulted."""
    store = CorpusStore(corpus_path)
    rng = random.Random(seed)
    links = list(
        store.db.execute(
            """SELECT l.source_document_id, l.target_document_id,
                      s.title source_title, t.title target_title
               FROM links l JOIN documents s ON s.document_id=l.source_document_id
               JOIN documents t ON t.document_id=l.target_document_id
               WHERE l.target_document_id IS NOT NULL
               ORDER BY l.source_document_id, l.target_document_id"""
        )
    )
    sections = list(
        store.db.execute(
            """SELECT c.chunk_id, c.document_id, c.section_path, c.raw_start, c.raw_end,
                      c.summary, d.title, d.revision
               FROM chunks c JOIN documents d ON d.document_id=c.document_id
               WHERE c.section_path <> 'Lead' AND length(c.summary) > 80
               ORDER BY c.chunk_id"""
        )
    )
    if not links or not sections:
        raise ValueError("Corpus needs resolved links and non-lead sections")
    by_source: dict[str, list[sqlite3.Row]] = {}
    for row in links:
        by_source.setdefault(row["source_document_id"], []).append(row)
    questions: list[dict[str, Any]] = []
    seen: set[str] = set()
    attempts = 0
    while len(questions) < count and attempts < count * 30:
        attempts += 1
        strategy = len(questions) % 3
        if strategy == 0:
            row = rng.choice(sections)
            clue = _clue(row["summary"])
            if len(clue.split()) < 2:
                continue
            query = (
                f"In the article about {row['title']}, which section discusses {clue}?"
            )
            gold_docs = [row["document_id"]]
            gold_chunks = [row["chunk_id"]]
            category = "cross_section"
        elif strategy == 1:
            link = rng.choice(links)
            target = store.db.execute(
                """SELECT c.chunk_id, c.summary, c.raw_start, c.raw_end, d.revision
                   FROM chunks c JOIN documents d ON d.document_id=c.document_id
                   WHERE c.document_id=? ORDER BY c.block_index LIMIT 1""",
                (link["target_document_id"],),
            ).fetchone()
            if target is None or len(_clue(target["summary"]).split()) < 2:
                continue
            query = (
                f"Starting from {link['source_title']}, identify the linked topic whose "
                f"article discusses {_clue(target['summary'])}."
            )
            gold_docs = [link["source_document_id"], link["target_document_id"]]
            gold_chunks = [target["chunk_id"]]
            category = "two_article"
        else:
            first = rng.choice(links)
            second_options = by_source.get(first["target_document_id"], [])
            if not second_options:
                continue
            second = rng.choice(second_options)
            target = store.db.execute(
                """SELECT chunk_id, summary FROM chunks
                   WHERE document_id=? ORDER BY block_index LIMIT 1""",
                (second["target_document_id"],),
            ).fetchone()
            if target is None or len(_clue(target["summary"]).split()) < 2:
                continue
            query = (
                f"Traverse from {first['source_title']} through a related article and identify "
                f"the next topic characterized by {_clue(target['summary'])}."
            )
            gold_docs = [
                first["source_document_id"], first["target_document_id"],
                second["target_document_id"],
            ]
            gold_chunks = [target["chunk_id"]]
            category = "three_article"
        identity = hashlib.sha256(query.encode()).hexdigest()[:20]
        if identity in seen:
            continue
        seen.add(identity)
        questions.append(
            {
                "question_id": f"real:{identity}", "method": f"graph_strategy_{strategy + 1}",
                "category": category, "query": query, "gold_document_path": gold_docs,
                "gold_chunk_ids": gold_chunks, "author_seed": seed,
            }
        )
    if len(questions) < count:
        raise ValueError(f"Could author only {len(questions)} unique questions")
    payload = {
        "schema_version": "1.0",
        "corpus_sha256": hashlib.sha256(corpus_path.read_bytes()).hexdigest(),
        "seed": seed, "count": len(questions), "questions": questions,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"count": len(questions), "methods": 3, "output": str(output)}


def evaluate_retrieval(
    corpus_path: Path, questions_path: Path, output: Path, *, limit: int | None = None
) -> dict[str, Any]:
    """Evaluator is isolated from authoring and scores only stored gold identifiers."""
    payload = json.loads(questions_path.read_text(encoding="utf-8"))
    questions = payload["questions"][:limit]
    store = CorpusStore(corpus_path)
    runtime = TraversalRuntime(corpus_path)
    systems: dict[str, SystemCounters] = {
        "A_top1": {"article_hits": 0, "span_hits": 0, "bytes": [], "latency_ms": []},
        "B_topk": {"article_hits": 0, "span_hits": 0, "bytes": [], "latency_ms": []},
        "C_packet_only": {"article_hits": 0, "span_hits": 0, "bytes": [], "latency_ms": []},
        "D_iterative": {"article_hits": 0, "span_hits": 0, "bytes": [], "latency_ms": []},
        "E_constrained_rag": {"article_hits": 0, "span_hits": 0, "bytes": [], "latency_ms": []},
    }
    category: dict[str, dict[str, int]] = {}
    for question in questions:
        gold_doc = question["gold_document_path"][-1]
        gold_chunks = set(question["gold_chunk_ids"])
        rows = store.search(question["query"], 8)
        for name, selected in (("A_top1", rows[:1]), ("B_topk", rows), ("E_constrained_rag", rows)):
            docs = {row["document_id"] for row in selected}
            chunks = {row["chunk_id"] for row in selected}
            systems[name]["article_hits"] += int(gold_doc in docs)
            systems[name]["span_hits"] += int(bool(gold_chunks & chunks))
            systems[name]["bytes"].append(sum(len(row["raw_text"].encode()) for row in selected))
            systems[name]["latency_ms"].append(0.0)
        begun = time.perf_counter_ns()
        result = runtime.query(
            question["query"],
            budget=TraversalBudget(max_steps=10, max_articles=12, max_chunks=32, max_bytes=262_144),
        )
        docs = {node.document_id for node in result.retrieved_chunks}
        chunks = {node.chunk_id for node in result.retrieved_chunks}
        systems["D_iterative"]["article_hits"] += int(gold_doc in docs)
        systems["D_iterative"]["span_hits"] += int(bool(gold_chunks & chunks))
        systems["D_iterative"]["bytes"].append(result.bytes_read)
        systems["D_iterative"]["latency_ms"].append(
            (time.perf_counter_ns() - begun) / 1_000_000
        )
        systems["C_packet_only"]["bytes"].append(0)
        systems["C_packet_only"]["latency_ms"].append(0.0)
        bucket = category.setdefault(question["category"], {"count": 0, "D_hits": 0})
        bucket["count"] += 1
        bucket["D_hits"] += int(gold_doc in docs)
    count = len(questions)
    rendered_systems: dict[str, Any] = {}
    for name, values in systems.items():
        byte_values = sorted(values["bytes"])
        latency_values = sorted(values["latency_ms"])
        rendered_systems[name] = {
            "article_recall": values["article_hits"] / count,
            "evidence_span_recall": values["span_hits"] / count,
            "mean_bytes_read": sum(byte_values) / count,
            "p95_bytes_read": byte_values[min(count - 1, int(count * 0.95))],
            "mean_latency_ms": sum(latency_values) / count,
        }
    hard = [q for q in questions if q["category"] in {"two_article", "three_article"}]
    decision = "MORE_EMULATION_REQUIRED"
    report = {
        "status": "SCALABLE_KNOWLEDGE_ARCHITECTURE_UNVALIDATED",
        "question_count": count, "hard_question_count": len(hard),
        "systems": rendered_systems, "category_results": category,
        "thresholds": {
            "article_recall_at_k": 0.95, "evidence_span_recall": 0.90,
            "grounded_multi_article_accuracy": 0.85, "traversal_margin_over_static": 0.10,
        },
        "decision": decision,
        "limitations": [
            "This run measures retrieval recall, not complete grounded answer accuracy.",
            "The constrained-RAG row is retrieval-equivalent until a small verified "
            "realizer is added.",
            "Packet-only has no real-corpus packets and therefore correctly scores zero.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report
