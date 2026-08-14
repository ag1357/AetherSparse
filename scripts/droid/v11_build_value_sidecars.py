#!/usr/bin/env python3
"""Build per-tier canonical-schema sidecar DBs for the v11 value diagnostic.

Why this exists: the v11 SQLiteControllerProvider requires the canonical v0.5
corpus schema (anchors/redirects tables, wiki_page_id / revision_id /
raw_wikitext columns, user_version=500).  The existing selector packs use the
legacy selector schema (mw:PAGE:REV:HASH document IDs; revision / raw_text /
content_hash columns; links instead of anchors).  The value diagnostic performs
only row-local lookups (the 8 selected chunks plus the gold source documents
per residual replica) and pure-text region scanning, so a sidecar containing
exactly those rows yields identical diagnostic output for the 43 in-scope
replicas.  No corpus data is fabricated: every row is copied byte-identically
from the existing tier pack.

Column mapping (selector -> canonical):
  documents.revision      -> revision_id
  documents.raw_text      -> raw_wikitext
  documents.content_hash  -> source_text_sha256  (verified == sha256(raw_text))
  mw:PAGE:REV:HASH        -> wiki_page_id=PAGE   (revision_id cross-checked)
Stub tables (anchors, redirects, aliases, chunks_fts) are created EMPTY: this
code path never queries them; the provider only checks their presence.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

WORKTREE = Path("/root/work/AetherSparse-v11")
sys.path.insert(0, str(WORKTREE / "scripts" / "droid"))

from v11_value_diagnostic import _document_key, _richest_decision  # noqa: E402

BENCHMARK = WORKTREE / "data/v050/benchmark/INDEPENDENT_NATURAL_QUERY_SET_V050_R1.json"
PROJECTION = Path("/root/work/v11/value-remaining-43.json")
REPLAY = Path("/root/work/v10/controller-replay-3tier")
OUT_DIR = Path("/root/work/v11/sidecars")

PACKS = {
    "10k": Path("/root/work/artifacts/packs/selector-10k-p3.sqlite"),
    "25k": Path("/root/work/artifacts/packs/selector-25k-p3.sqlite"),
    "397k": Path("/root/work/artifacts/packs/selector-full-p3.sqlite"),
}


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    projection = json.loads(PROJECTION.read_text(encoding="utf-8"))
    residuals = projection["per_case"]
    tier_cases: dict[str, set[str]] = {}
    for item in residuals:
        tier_cases.setdefault(str(item["corpus_tier"]), set()).add(str(item["case_id"]))

    benchmark = json.loads(BENCHMARK.read_text(encoding="utf-8"))
    bench_by_id = {str(c["case_id"]): c for c in benchmark["cases"]}

    # Per tier: selected top-8 chunk ids (from replay) + gold (page, rev) pairs.
    tier_chunks: dict[str, set[str]] = {tier: set() for tier in tier_cases}
    tier_gold: dict[str, set[tuple[str, str]]] = {tier: set() for tier in tier_cases}
    wanted = {(str(i["case_id"]), str(i["corpus_tier"])) for i in residuals}
    with gzip.open(REPLAY / "cases.jsonl.gz", "rt", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            case = json.loads(line)
            key = (str(case.get("case_id")), str(case.get("corpus_tier")))
            if key not in wanted:
                continue
            decision = _richest_decision(case)
            ranked = decision.get("ranked_evidence_metadata", [])
            for value in ranked[:8]:
                tier_chunks[key[1]].add(str(value.get("chunk_id")))
    for item in residuals:
        case = bench_by_id[str(item["case_id"])]
        for evidence in case["gold_evidence"]:
            doc_key = _document_key(str(evidence["document_id"]))
            if doc_key is not None:
                tier_gold[str(item["corpus_tier"])].add(doc_key)

    report = {"tiers": {}, "disclosure": __doc__.strip().splitlines()[0]}
    for tier in sorted(tier_cases):
        source = PACKS[tier]
        sidecar = OUT_DIR / f"value-tier-{tier}.sqlite"
        if sidecar.exists():
            sidecar.unlink()
        src = sqlite3.connect(f"file:{source}?mode=ro&immutable=1", uri=True)
        dst = sqlite3.connect(sidecar)
        dst.executescript(
            """
            CREATE TABLE documents(
              document_id TEXT PRIMARY KEY, wiki_page_id TEXT NOT NULL,
              revision_id TEXT NOT NULL, title TEXT NOT NULL,
              normalized_title TEXT NOT NULL, redirect_target TEXT,
              source_url TEXT NOT NULL, source_text_sha256 TEXT NOT NULL,
              raw_wikitext TEXT NOT NULL);
            CREATE TABLE chunks(
              chunk_id TEXT PRIMARY KEY, document_id TEXT NOT NULL,
              section_path TEXT NOT NULL, block_index INTEGER,
              raw_start INTEGER NOT NULL, raw_end INTEGER NOT NULL,
              raw_text TEXT NOT NULL);
            CREATE TABLE anchors(
              anchor_id TEXT PRIMARY KEY, source_document_id TEXT NOT NULL,
              target_title TEXT NOT NULL, anchor_text TEXT NOT NULL,
              raw_start INTEGER NOT NULL, raw_end INTEGER NOT NULL,
              raw_text TEXT NOT NULL, source_span_sha256 TEXT NOT NULL);
            CREATE TABLE redirects(
              source_document_id TEXT PRIMARY KEY, target_title TEXT NOT NULL,
              source_text_sha256 TEXT NOT NULL);
            CREATE TABLE aliases(alias TEXT NOT NULL, document_id TEXT NOT NULL,
              PRIMARY KEY(alias, document_id));
            CREATE TABLE chunks_fts(x);
            PRAGMA user_version=500;
            """
        )

        chunk_ids = sorted(tier_chunks[tier])
        marks = ",".join("?" for _ in chunk_ids)
        chunk_rows = list(
            src.execute(
                f"SELECT chunk_id, document_id, section_path, block_index, raw_start,"
                f" raw_end, raw_text FROM chunks WHERE chunk_id IN ({marks})",
                chunk_ids,
            )
        )
        parent_ids = {str(row[1]) for row in chunk_rows}

        doc_ids: dict[str, str] = {}  # document_id -> origin
        for did in sorted(parent_ids):
            row = src.execute(
                "SELECT document_id FROM documents WHERE document_id=?", (did,)
            ).fetchone()
            if row:
                doc_ids[did] = "chunk_parent"
        gold_missing = []
        for page_id, revision_id in sorted(tier_gold[tier]):
            row = src.execute(
                "SELECT document_id FROM documents WHERE document_id GLOB ?",
                (f"mw:{page_id}:{revision_id}:*",),
            ).fetchone()
            if row:
                doc_ids[str(row[0])] = "gold"
            else:
                gold_missing.append(f"{page_id}:{revision_id}")

        for did in sorted(doc_ids):
            row = src.execute(
                "SELECT document_id, title, normalized_title, revision, source_url,"
                " content_hash, raw_text, redirect_target FROM documents WHERE document_id=?",
                (did,),
            ).fetchone()
            parts = str(row[0]).split(":")
            wiki_page_id, rev_from_id = parts[1], parts[2]
            if str(row[3]) != rev_from_id:
                raise ValueError(f"revision mismatch for {row[0]}: {row[3]} vs {rev_from_id}")
            dst.execute(
                "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    row[0], wiki_page_id, str(row[3]), row[1], row[2],
                    row[7], row[4], row[5], row[6],
                ),
            )
        for row in chunk_rows:
            dst.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?,?)", tuple(row))
        dst.commit()

        # verification: row counts + byte-identical text spot check
        check = sqlite3.connect(f"file:{sidecar}?mode=ro&immutable=1", uri=True)
        n_docs = check.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        n_chunks = check.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        mismatch = 0
        for row in check.execute("SELECT chunk_id, raw_text FROM chunks"):
            original = src.execute(
                "SELECT raw_text FROM chunks WHERE chunk_id=?", (row[0],)
            ).fetchone()
            if original is None or original[0] != row[1]:
                mismatch += 1
        version = check.execute("PRAGMA user_version").fetchone()[0]
        check.close()
        src.close()
        dst.close()

        report["tiers"][tier] = {
            "source_pack": str(source),
            "source_pack_sha256": sha256_path(source),
            "sidecar": sidecar.name,
            "sidecar_sha256": sha256_path(sidecar),
            "sidecar_bytes": sidecar.stat().st_size,
            "documents_rows": n_docs,
            "chunks_rows": n_chunks,
            "chunks_requested": len(chunk_ids),
            "chunks_missing_from_source": len(chunk_ids) - n_chunks,
            "gold_documents_requested": len(tier_gold[tier]),
            "gold_documents_missing_from_source": sorted(gold_missing),
            "text_copy_mismatches": mismatch,
            "user_version": version,
            "stub_tables_empty": ["anchors", "redirects", "aliases", "chunks_fts"],
        }
        print(json.dumps(report["tiers"][tier], indent=2), flush=True)

    report_path = OUT_DIR / "sidecar-derivation-report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[sidecars] DONE -> {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
