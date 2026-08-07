#!/usr/bin/env python3
"""Phase 0A gate: content identity between two packs (serial vs parallel).

Compares table counts, corpus_meta manifest, and a sha256 over all rows
(sorted by key) per table.  Exit 0 only if everything matches.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

TABLES = (
    "documents",
    "chunks",
    "aliases",
    "links",
    "categories",
    "time_expressions",
    "corpus_meta",
)


def table_hash(db: sqlite3.Connection, table: str, columns: str = "*") -> str:
    digest = hashlib.sha256()
    for row in db.execute(f"SELECT {columns} FROM {table} ORDER BY 1, 2"):
        digest.update(repr(tuple(row)).encode())
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", type=Path, required=True)
    parser.add_argument("--parallel", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    a = sqlite3.connect(f"file:{args.serial}?mode=ro&immutable=1", uri=True)
    b = sqlite3.connect(f"file:{args.parallel}?mode=ro&immutable=1", uri=True)

    report: dict[str, object] = {"tables": {}, "identical": True}
    for table in TABLES:
        count_a = a.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        count_b = b.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        hash_a = table_hash(a, table)
        hash_b = table_hash(b, table)
        same = count_a == count_b and hash_a == hash_b
        report["tables"][table] = {
            "serial_count": count_a,
            "parallel_count": count_b,
            "content_identical": same,
        }
        if not same:
            report["identical"] = False
    fts_cols = "chunk_id, title, section_path, body"
    fts_same = table_hash(a, "chunks_fts", fts_cols) == table_hash(b, "chunks_fts", fts_cols)
    report["tables"]["chunks_fts"] = {"content_identical": fts_same}
    if not fts_same:
        report["identical"] = False

    print(json.dumps(report, indent=2))
    if args.report:
        args.report.write_text(json.dumps(report, indent=2))
    print("GATE:", "PASS" if report["identical"] else "FAIL")
    return 0 if report["identical"] else 1


if __name__ == "__main__":
    sys.exit(main())
