"""Bounded read-only interface for v0.5 flat real-corpus packs."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from aethersparse.real_corpus.builder import normalize_text

TOKEN_RE = re.compile(r"[\w'-]{2,}", re.UNICODE)


@dataclass(frozen=True)
class WorkloadTrace:
    operation: str
    requested_limit: int
    records_returned: int
    index_probes: int
    payload_bytes: int
    estimated_payload_blocks: int
    sqlite_page_bytes: int
    elapsed_ns: int

    def to_dict(self) -> dict[str, int | str]:
        return asdict(self)


class RealCorpusPack:
    """Query a frozen pack without permitting writes or unbounded result sets."""

    def __init__(self, path: Path, *, maximum_limit: int = 256):
        if not path.is_file():
            raise FileNotFoundError(path)
        if maximum_limit < 1:
            raise ValueError("maximum_limit must be positive")
        self.path = path
        self.maximum_limit = maximum_limit
        self.db = sqlite3.connect(f"file:{path.resolve()}?mode=ro&immutable=1", uri=True)
        self.db.row_factory = sqlite3.Row
        self._page_bytes = int(self.db.execute("PRAGMA page_size").fetchone()[0])
        self._traces: list[WorkloadTrace] = []

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> RealCorpusPack:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _limit(self, limit: int) -> int:
        if limit < 1 or limit > self.maximum_limit:
            raise ValueError(f"limit must be in [1,{self.maximum_limit}]")
        return limit

    def _record(
        self,
        operation: str,
        limit: int,
        rows: list[dict[str, object]],
        index_probes: int,
        started_ns: int,
    ) -> list[dict[str, object]]:
        payload_bytes = len(
            json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str).encode()
        )
        self._traces.append(
            WorkloadTrace(
                operation=operation,
                requested_limit=limit,
                records_returned=len(rows),
                index_probes=index_probes,
                payload_bytes=payload_bytes,
                estimated_payload_blocks=math.ceil(payload_bytes / self._page_bytes),
                sqlite_page_bytes=self._page_bytes,
                elapsed_ns=time.perf_counter_ns() - started_ns,
            )
        )
        return rows

    @staticmethod
    def _dicts(rows: list[sqlite3.Row]) -> list[dict[str, object]]:
        return [dict(row) for row in rows]

    @property
    def last_trace(self) -> WorkloadTrace | None:
        return self._traces[-1] if self._traces else None

    def workload_trace(self, *, clear: bool = False) -> list[dict[str, int | str]]:
        result = [item.to_dict() for item in self._traces]
        if clear:
            self._traces.clear()
        return result

    def metadata(self) -> dict[str, object]:
        rows = self.db.execute("SELECT key,value FROM corpus_meta ORDER BY key").fetchall()
        return {str(row["key"]): json.loads(str(row["value"])) for row in rows}

    def title_lookup(self, query: str, limit: int = 10) -> list[dict[str, object]]:
        limit = self._limit(limit)
        started = time.perf_counter_ns()
        key = normalize_text(query).casefold()
        if not key:
            return self._record("title_lookup", limit, [], 0, started)
        exact = self.db.execute(
            """SELECT d.*,a.kind AS match_kind FROM aliases AS a
               JOIN documents AS d USING(document_id)
               WHERE a.alias=? ORDER BY a.kind,d.normalized_title,d.document_id LIMIT ?""",
            (key, limit),
        ).fetchall()
        rows = self._dicts(exact)
        probes = 1
        if len(rows) < limit:
            seen = {str(row["document_id"]) for row in rows}
            prefix = self.db.execute(
                """SELECT d.*,'title_prefix' AS match_kind FROM documents AS d
                   WHERE normalized_title>=? AND normalized_title<?
                   ORDER BY normalized_title,document_id LIMIT ?""",
                (key, key + "\U0010ffff", limit),
            ).fetchall()
            rows.extend(
                row
                for row in self._dicts(prefix)
                if str(row["document_id"]) not in seen
            )
            rows = rows[:limit]
            probes += 1
        return self._record("title_lookup", limit, rows, probes, started)

    def alias_lookup(self, alias: str, limit: int = 10) -> list[dict[str, object]]:
        limit = self._limit(limit)
        started = time.perf_counter_ns()
        key = normalize_text(alias).casefold()
        rows = self._dicts(
            self.db.execute(
                """SELECT a.alias,a.kind,d.document_id,d.title,d.redirect_target,
                          d.source_text_sha256,d.source_url
                   FROM aliases AS a JOIN documents AS d USING(document_id)
                   WHERE a.alias=? ORDER BY a.kind,d.document_id LIMIT ?""",
                (key, limit),
            ).fetchall()
        )
        return self._record("alias_lookup", limit, rows, 1, started)

    def anchor_lookup(self, text: str, limit: int = 24) -> list[dict[str, object]]:
        limit = self._limit(limit)
        started = time.perf_counter_ns()
        key = normalize_text(text).casefold()
        rows = self._dicts(
            self.db.execute(
                """SELECT anchor_id,source_document_id,target_title,anchor_text,
                          raw_start,raw_end,raw_text,source_span_sha256
                   FROM anchors WHERE anchor_text=?
                   ORDER BY target_title,source_document_id,raw_start LIMIT ?""",
                (key, limit),
            ).fetchall()
        )
        return self._record("anchor_lookup", limit, rows, 1, started)

    def search_chunks(self, query: str, limit: int = 12) -> list[dict[str, object]]:
        limit = self._limit(limit)
        started = time.perf_counter_ns()
        terms = sorted(set(TOKEN_RE.findall(query.casefold())), key=lambda item: (-len(item), item))
        terms = [term.replace('"', '""') for term in terms if len(term) > 2][:7]
        if not terms:
            return self._record("search_chunks", limit, [], 0, started)
        fts_query = " OR ".join(f'"{term}"' for term in terms)
        rows = self._dicts(
            self.db.execute(
                """SELECT c.chunk_id,c.document_id,c.section_path,c.block_index,
                          c.raw_start,c.raw_end,c.offset_unit,c.raw_text,c.normalized_text,
                          c.source_span_sha256,d.title,d.revision_id AS revision,
                          d.source_url,d.source_text_sha256,
                          bm25(chunks_fts,1.8,1.2,1.0) AS rank
                   FROM chunks_fts AS f JOIN chunks AS c ON c.chunk_id=f.chunk_id
                   JOIN documents AS d USING(document_id)
                   WHERE chunks_fts MATCH ? ORDER BY rank,c.chunk_id LIMIT ?""",
                (fts_query, limit),
            ).fetchall()
        )
        return self._record("search_chunks", limit, rows, 1, started)

    def document(self, document_id: str) -> dict[str, object] | None:
        started = time.perf_counter_ns()
        row = self.db.execute(
            "SELECT * FROM documents WHERE document_id=?", (document_id,)
        ).fetchone()
        rows = self._record("document", 1, self._dicts([row]) if row else [], 1, started)
        return rows[0] if rows else None

    def chunk(self, chunk_id: str) -> dict[str, object] | None:
        started = time.perf_counter_ns()
        row = self.db.execute(
            """SELECT c.*,d.title,d.revision_id AS revision,d.source_url,d.source_text_sha256
               FROM chunks AS c JOIN documents AS d USING(document_id)
               WHERE c.chunk_id=?""",
            (chunk_id,),
        ).fetchone()
        rows = self._record("chunk", 1, self._dicts([row]) if row else [], 1, started)
        return rows[0] if rows else None

    def source_binding(self, chunk_id: str) -> dict[str, object] | None:
        started = time.perf_counter_ns()
        row = self.db.execute(
            """SELECT c.chunk_id,c.document_id,c.raw_start,c.raw_end,c.offset_unit,c.raw_text,
                      c.source_span_sha256,d.source_text_sha256,d.raw_wikitext,d.source_url
               FROM chunks AS c JOIN documents AS d USING(document_id)
               WHERE c.chunk_id=?""",
            (chunk_id,),
        ).fetchone()
        if row is None:
            self._record("source_binding", 1, [], 1, started)
            return None
        item = dict(row)
        raw = str(item.pop("raw_wikitext"))
        raw_start = int(item["raw_start"])
        raw_end = int(item["raw_end"])
        selected = raw[raw_start:raw_end]
        item["slice_matches"] = selected == item["raw_text"]
        item["span_hash_matches"] = (
            hashlib.sha256(selected.encode()).hexdigest() == item["source_span_sha256"]
        )
        item["document_hash_matches"] = (
            hashlib.sha256(raw.encode()).hexdigest() == item["source_text_sha256"]
        )
        return self._record("source_binding", 1, [item], 1, started)[0]

    def chunks_for_documents(
        self, document_ids: list[str], limit: int = 32
    ) -> list[dict[str, object]]:
        limit = self._limit(limit)
        started = time.perf_counter_ns()
        bounded_ids = list(dict.fromkeys(document_ids))[:32]
        if not bounded_ids:
            return self._record("chunks_for_documents", limit, [], 0, started)
        marks = ",".join("?" for _ in bounded_ids)
        rows = self._dicts(
            self.db.execute(
                f"""SELECT c.*,d.title,d.revision_id AS revision,d.source_url,
                           d.source_text_sha256,0.0 AS rank
                    FROM chunks AS c JOIN documents AS d USING(document_id)
                    WHERE c.document_id IN ({marks})
                    ORDER BY c.document_id,c.raw_start LIMIT ?""",
                (*bounded_ids, limit),
            ).fetchall()
        )
        return self._record("chunks_for_documents", limit, rows, 1, started)
