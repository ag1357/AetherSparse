#!/usr/bin/env python3
"""Phase 4: semantic channel as a candidate generator (diagnostic arms).

Runs six candidate-generation arms against the EvidenceSelector ranking stage
on one pack:

  lexical_only    shipped probes (baseline)
  semantic_only   top-k chunks by model2vec+PCA96+int8 cosine
  union           lexical pool + semantic top-k, deduped, capped at k
  weighted        union pool rescored w*selector + (1-w)*cosine (w grid)
  rrf             rank-fusion (k=60) of lexical and semantic lists -> top-k
  margin_gated    lexical unless calibrated P(top1 correct) < tau -> union

The ranking stage is always the shipped selector; only the candidate pool
varies.  Answer cases only.  Reports per-arm strict/lenient article recall,
pool recall, and the lexical/semantic recovery split.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v050_common import (  # noqa: E402
    answer_cases,
    case_gold_pageids,
    load_benchmark,
    pageid,
)
from v08_calibration import _fit_lookup, _predict  # noqa: E402
from v08_pipeline_eval import TOKEN_RE, _selector_anchors  # noqa: E402

from aethersparse.selection.models import CandidateScore  # noqa: E402
from aethersparse.selection.selector import EvidenceSelector  # noqa: E402


def _sigmoid(x: float) -> float:
    import math

    return 1.0 / (1.0 + math.exp(-x))


class SemanticIndex:
    def __init__(self, meta_path: Path):
        meta = json.loads(meta_path.read_text())
        stem = meta_path.name.replace(".semantic.meta.json", "")
        directory = meta_path.parent
        self.q = np.load(directory / f"{stem}.semantic.int8.npy")
        self.scales = np.load(directory / f"{stem}.semantic.scales.npy")
        self.rowids = meta["rowids"]
        self.mean = np.asarray(meta["pca_mean"], dtype=np.float32)
        self.components = np.asarray(meta["pca_components"], dtype=np.float32)
        self.rowid_to_pos = {r: i for i, r in enumerate(self.rowids)}
        self._dequant = self.q.astype(np.float32) * self.scales
        norms = np.linalg.norm(self._dequant, axis=1, keepdims=True)
        self._unit = self._dequant / np.maximum(norms, 1e-8)
        from model2vec import StaticModel

        self.model = StaticModel.from_pretrained(meta["model"])

    def rank(self, question: str, top_k: int) -> list[tuple[int, float]]:
        vec = self.model.encode([question])[0].astype(np.float32)
        reduced = (vec - self.mean) @ self.components.T
        reduced /= max(float(np.linalg.norm(reduced)), 1e-8)
        sims = self._unit @ reduced
        if top_k >= len(sims):
            order = np.argsort(-sims)
        else:
            part = np.argpartition(-sims, top_k)[:top_k]
            order = part[np.argsort(-sims[part])]
        return [(self.rowids[int(i)], float(sims[int(i)])) for i in order]


def _rows_for_rowids(selector: EvidenceSelector, rowids: list[int]):
    marks = ",".join("?" for _ in rowids)
    rows = selector.store.db.execute(
        f"""SELECT c.rowid AS chunk_rowid, c.*, d.title, d.revision, d.source_url
            FROM chunks c JOIN documents d ON d.document_id=c.document_id
            WHERE c.rowid IN ({marks})""",
        rowids,
    ).fetchall()
    by_rowid = {}
    for row in rows:
        by_rowid[int(row["chunk_rowid"])] = row
    return by_rowid


def _make_candidate(
    selector: EvidenceSelector,
    question: str,
    row,
    position: int,
    anchors,
    query_categories,
    bm25_score: float,
) -> CandidateScore:
    features = selector._feature_vector(
        question, row, position, anchors, query_categories, bm25_score=bm25_score
    )
    deterministic = selector._fusion(features)
    reranker = _sigmoid(selector.model.score(features))
    return CandidateScore(
        chunk_id=str(row["chunk_id"]),
        document_id=str(row["document_id"]),
        title=str(row["title"]),
        section_path=str(row["section_path"]),
        raw_text=str(row["raw_text"]),
        normalized_text=str(row["normalized_text"]),
        source_url=str(row["source_url"]),
        source_revision=str(row["revision"]),
        lexical_position=position,
        features=features,
        deterministic_score=deterministic,
        reranker_score=reranker,
        final_score=0.45 * deterministic + 0.55 * reranker,
    )


def _batch_bm25(
    selector: EvidenceSelector, question: str, chunk_ids: list[str]
) -> dict[str, float]:
    """bm25 of many chunks in one FTS query (same term selection as _raw_bm25)."""

    terms = [term for term in TOKEN_RE.findall(question.casefold()) if len(term) > 2]
    out = {cid: 0.0 for cid in chunk_ids}
    if not terms or not chunk_ids:
        return out
    selected = sorted(set(terms), key=lambda term: (-len(term), term))[:7]
    fts_query = " OR ".join(f'"{term}"' for term in selected)
    marks = ",".join("?" for _ in chunk_ids)
    rows = selector.store.db.execute(
        f"SELECT chunk_id, bm25(chunks_fts, 1.8, 1.2, 1.0) AS rank FROM chunks_fts "
        f"WHERE chunks_fts MATCH ? AND chunk_id IN ({marks})",
        (fts_query, *chunk_ids),
    ).fetchall()
    for row in rows:
        out[str(row["chunk_id"])] = float(row["rank"])
    return out


def _semantic_pool(
    selector: EvidenceSelector,
    question: str,
    ranked: list[tuple[int, float]],
    anchors,
    query_categories,
) -> list[CandidateScore]:
    by_rowid = _rows_for_rowids(selector, [rid for rid, _ in ranked])
    pool: list[tuple[int, object]] = []
    chunk_ids: list[str] = []
    for position, (rid, _sim) in enumerate(ranked):
        row = by_rowid.get(rid)
        if row is None:
            continue
        pool.append((position, row))
        chunk_ids.append(str(row["chunk_id"]))
    raw = _batch_bm25(selector, question, chunk_ids)
    inverted = [-v for v in raw.values()] or [0.0]
    floor, ceiling = min(inverted), max(inverted)
    spread = (ceiling - floor) or 1.0
    out = []
    for position, row in pool:
        cid = str(row["chunk_id"])
        bm25_score = (-raw[cid] - floor) / spread
        out.append(
            _make_candidate(
                selector, question, row, position, anchors, query_categories, bm25_score
            )
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", required=True)
    parser.add_argument("--sidecar-meta", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--per-case-output")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--candidate-limit", type=int, default=96)
    parser.add_argument("--tau", type=float, default=0.5)
    parser.add_argument("--weights", default="0.25,0.5,0.75")
    args = parser.parse_args()

    benchmark = load_benchmark()
    cases = answer_cases(benchmark)
    if args.limit:
        cases = cases[: args.limit]

    selector = EvidenceSelector(Path(args.pack), candidate_limit=args.candidate_limit)
    index = SemanticIndex(Path(args.sidecar_meta))

    weights = [float(w) for w in args.weights.split(",")]
    arms = ["lexical_only", "semantic_only", "union", "rrf", "margin_gated"] + [
        f"weighted_{w}" for w in weights
    ]
    stats = {
        arm: {"strict": [], "lenient": [], "pool_recall": []} for arm in arms
    }
    per_case: list[dict] = []
    # Calibration lookup for the margin-gated arm: fit on this run's own
    # lexical margins would leak; instead fit on the lexical arm's margins
    # collected in a first pass below (fit partitions only).
    margins_fit: list[float] = []
    labels_fit: list[int] = []
    started = time.time()

    first_pass: list[dict] = []
    for i, case in enumerate(cases):
        question = case.question
        gold = set(case_gold_pageids(case))
        lexical_pool = list(selector.candidates(question))
        anchors, query_categories = _selector_anchors(selector, question)
        ranked = index.rank(question, args.candidate_limit)
        semantic_pool = _semantic_pool(
            selector, question, ranked, anchors, query_categories
        )
        trace = selector.select(question, initial_candidates=lexical_pool)
        scores = [c.final_score for c in trace.reranked_candidates[:2]]
        margin = scores[0] - scores[1] if len(scores) == 2 else 0.0
        first_pass.append(
            {
                "case": case,
                "gold": gold,
                "lexical_pool": lexical_pool,
                "semantic_pool": semantic_pool,
                "ranked": ranked,
                "margin": margin,
            }
        )
        if (i + 1) % 100 == 0:
            print(f"pools {i + 1}/{len(cases)}", flush=True)

    # Fit the lookup on fit-partition margins vs lexical strict labels.
    margins_fit = []
    labels_fit = []
    for rec in first_pass:
        case = rec["case"]
        if str(case.partition) not in {"tuning", "development"}:
            continue
        trace = selector.select(
            case.question, initial_candidates=rec["lexical_pool"]
        )
        hit = bool(
            trace.reranked_candidates
            and pageid(str(trace.reranked_candidates[0].document_id)) in rec["gold"]
        )
        margins_fit.append(rec["margin"])
        labels_fit.append(int(hit))
    lookup = _fit_lookup(margins_fit, labels_fit)

    for i, rec in enumerate(first_pass):
        case = rec["case"]
        gold = rec["gold"]
        lexical_pool = rec["lexical_pool"]
        semantic_pool = rec["semantic_pool"]
        question = case.question

        seen: set[str] = set()
        union_pool: list[CandidateScore] = []
        for cand in [*lexical_pool, *semantic_pool]:
            if cand.chunk_id in seen:
                continue
            seen.add(cand.chunk_id)
            union_pool.append(cand)
            if len(union_pool) >= args.candidate_limit:
                break

        # RRF fusion of lexical and semantic orderings -> top-k pool.
        rrf_score: dict[str, float] = {}
        by_chunk: dict[str, CandidateScore] = {}
        for rank, cand in enumerate(lexical_pool):
            rrf_score[cand.chunk_id] = rrf_score.get(cand.chunk_id, 0.0) + 1.0 / (
                60 + rank + 1
            )
            by_chunk[cand.chunk_id] = cand
        for rank, cand in enumerate(semantic_pool):
            rrf_score[cand.chunk_id] = rrf_score.get(cand.chunk_id, 0.0) + 1.0 / (
                60 + rank + 1
            )
            by_chunk.setdefault(cand.chunk_id, cand)
        rrf_order = sorted(rrf_score, key=lambda c: (-rrf_score[c], c))
        rrf_pool = [by_chunk[c] for c in rrf_order[: args.candidate_limit]]

        p_top1 = _predict(lookup, rec["margin"])
        gated_pool = (
            lexical_pool if p_top1 >= args.tau else union_pool
        )

        arm_pools = {
            "lexical_only": lexical_pool,
            "semantic_only": semantic_pool,
            "union": union_pool,
            "rrf": rrf_pool,
            "margin_gated": gated_pool,
        }
        # Weighted arms rescore the union pool by cosine similarity.
        by_rowid = {rid: sim for rid, sim in rec["ranked"]}
        rowids_needed = _rows_for_rowids(selector, list(by_rowid))
        chunk_sim = {}
        for rid, sim in by_rowid.items():
            row = rowids_needed.get(rid)
            if row is not None:
                chunk_sim[str(row["chunk_id"])] = sim
        sims = list(chunk_sim.values()) or [0.0]
        smin, smax = min(sims), max(sims)
        sspread = (smax - smin) or 1.0
        for w in weights:
            rescored = []
            for cand in union_pool:
                cos_norm = (chunk_sim.get(cand.chunk_id, smin) - smin) / sspread
                clone = CandidateScore(
                    **{
                        **cand.__dict__,
                        "final_score": w * cand.final_score + (1 - w) * cos_norm,
                    }
                )
                rescored.append(clone)
            arm_pools[f"weighted_{w}"] = rescored

        case_result = {"case_id": case.case_id, "categories": case.categories}
        for arm, pool in arm_pools.items():
            pool_docs = {pageid(c.document_id) for c in pool}
            trace = selector.select(question, initial_candidates=pool)
            strict = bool(
                trace.reranked_candidates
                and pageid(str(trace.reranked_candidates[0].document_id)) in gold
            )
            lenient = bool(
                any(pageid(str(c.document_id)) in gold for c in trace.selected_evidence)
            )
            stats[arm]["strict"].append(strict)
            stats[arm]["lenient"].append(lenient)
            stats[arm]["pool_recall"].append(bool(pool_docs & gold))
            case_result[arm] = {
                "strict": strict,
                "lenient": lenient,
                "pool_recall": bool(pool_docs & gold),
            }
        per_case.append(case_result)
        if (i + 1) % 100 == 0:
            print(f"evaluated {i + 1}/{len(cases)}", flush=True)

    # Recovery analysis: cases where semantic_only succeeds and lexical fails.
    recovered = sum(
        1
        for r in per_case
        if r["semantic_only"]["strict"] and not r["lexical_only"]["strict"]
    )
    lost = sum(
        1
        for r in per_case
        if r["lexical_only"]["strict"] and not r["semantic_only"]["strict"]
    )
    import hashlib

    digest = hashlib.sha256(Path(args.pack).read_bytes()).hexdigest()
    report = {
        "pack": str(args.pack),
        "pack_sha256": digest,
        "n_cases": len(cases),
        "candidate_limit": args.candidate_limit,
        "tau": args.tau,
        "arms": {
            arm: {
                "strict": sum(v["strict"]) / max(len(v["strict"]), 1),
                "lenient": sum(v["lenient"]) / max(len(v["lenient"]), 1),
                "pool_recall": sum(v["pool_recall"]) / max(len(v["pool_recall"]), 1),
            }
            for arm, v in stats.items()
        },
        "recovery": {
            "semantic_recovers_lexical_miss": recovered,
            "semantic_loses_lexical_hit": lost,
        },
        "elapsed_seconds": round(time.time() - started, 2),
    }
    Path(args.output).write_text(json.dumps(report, indent=2))
    if args.per_case_output:
        Path(args.per_case_output).write_text(json.dumps(per_case, indent=2))
    for arm in arms:
        a = report["arms"][arm]
        print(f"{arm:16s} strict={a['strict']:.4f} pool={a['pool_recall']:.4f}")
    print(f"report={args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
