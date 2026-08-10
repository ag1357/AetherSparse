#!/usr/bin/env python3
"""Four-stage failure-decomposition + oracle-ladder diagnostic harness (v08).

Mission 3 Lane B.  Runs each benchmark case through four stages and
attributes every failed ANSWER case to the EARLIEST failing stage:

  stage 1  candidate generation   EvidenceSelector.candidates(question) on a
                                  p3 corpus pack (candidate_limit=96)
  stage 2  ranking                EvidenceSelector.select(stage="reranker");
                                  the trace's selected_evidence is the top-8
  stage 3  evidence selection     controller-package EvidenceRecord
                                  construction over the p3 chunks of the
                                  top-8 documents
  stage 4  answer composition     StructuredController._complete(frame,
                                  records, corpus_coverage=..., ...) ->
                                  disposition + realized answer

Attribution labels (failed ANSWER cases only).  E is evaluated independently
of the pipeline outcome; an E-flagged case that also fails is attributed E:

  E_BENCHMARK_DEFECT    gold invalid against the pack: a gold document is
                        absent, gold exact_text is found in neither the gold
                        document's raw_text nor normalized_text, or no
                        accepted-answer component appears in any gold
                        document's text (component-wise; see below)
  A_CANDIDATE_MISSING   any gold pageid absent from the candidate pool
  B_CANDIDATE_MISRANKED all gold pageids in the pool but gold set not a
                        subset of the top-8
  C_EVIDENCE_FAILED     strict article recall held, but no gold span projects
                        onto the evidence records fed to the controller
  D_CONTROLLER_FAILED   gold span fed to the controller, but the final result
                        is wrong (wrong span/value selected, wrong
                        disposition, unsupported binding, plan/realization
                        failure)

Gold/pageid matching: gold document ids are simplewiki:{pageid}:{revid}; pack
ids are mw:{pageid}:{revid}:{hash}.  All document matching is at the pageid
component only (v050_common.pageid).  A runtime span projects onto a gold
span when the pageid matches and the runtime span is contained within the
gold char bounds (same projection as _project_retrieval_ids in
scripts/run_v050_qualification.py, relaxed to pageid matching because exact
ids never match across pack schemas).

p3 schema substitutions (the controller's SQLiteControllerProvider targets
the v0.5 canonical schema; the p3 packs differ).  All other linking,
extraction, and scoring logic is inherited unchanged so record fit scores
are computed by byte-identical deterministic code:

  * aliases.kind column does not exist in p3 -> the EXACT_TITLE vs ALIAS
    resolution method is decided by comparing the probed alias with the
    document's own normalized_title (the p3 builder folds every document's
    own title into aliases, which is exactly what kind='title' encoded).
  * the anchors table does not exist in p3 -> anchor aliases are folded into
    the aliases table by the p3 builder, so the aliases probe subsumes the
    old anchors fallback and no separate anchor probe is issued.
  * the redirects table does not exist in p3 -> documents.redirect_target
    holds the target TITLE and is resolved through the same normalized_title
    lookup the old provider uses (_target_document).
  * documents.raw_wikitext -> raw_text, d.revision_id -> d.revision,
    d.source_text_sha256 -> d.content_hash, c.source_span_sha256 ->
    c.content_hash.  These renames are applied as exact SQL token
    substitutions in P3ControllerAdapter._rows (with AS aliases preserving
    result keys) so the inherited provider SQL runs otherwise unchanged.
  * evidence retrieval: the old provider's FTS/entity-row retrieval is
    replaced by the selector's top-8 documents (that is the system under
    test).  ALL chunks of those documents pass through the inherited
    _records_for_row extraction/scoring, are ordered by the provider's
    record sort key, and are fed to the controller with no additional
    record-count cap (the controller's own graph bounds apply).  The
    provider's corpus-coverage gate is preserved: when a frame mention
    remains unknown_but_copyable, no evidence is fed (shipped fail-closed
    behavior).
  * E-check answer presence is component-wise: LIST/COMPARISON accepted
    answers are deterministic compositions joined by "; ", " compared with ",
    " < ", " > ", " = " glue and never appear verbatim in source text, so
    each component must appear in some gold document's raw or normalized
    text (casefolded, whitespace-collapsed).  A literal whole-answer check
    would false-flag every compositional case (measured: 19/100 at 10k).

Oracles (diagnostic scaffolding — default OFF, never shipped, never reported
as system accuracy).  Gold data never enters persisted artifacts: outcomes
carry booleans/counts only, oracle-injected span ids are filtered from
persisted span lists, and the controller-oracle answer text is redacted.

  candidate  gold documents absent from the pool are injected as
             CandidateScore objects built through the selector's own feature
             path: the gold document's best chunk comes from the selector's
             doc-scoped carry probe (_carry_rows: query-term FTS scoped to
             the document, falling back to its first chunk), features are
             computed by EvidenceSelector._feature_vector with anchors and
             query-categories recomputed exactly as candidates() computes
             them, lexical_position is appended after the real pool, and
             bm25 is min-max normalized over the counterfactual
             pool+injected range (raw bm25 recomputed per chunk with
             CorpusStore.search's exact term selection).  Pool candidates
             are not re-scored: the rung measures the marginal effect of
             gold-chunk presence under the shipped ranking.
  ranking    the strongest candidate of each gold document is forced to the
             top of the selected evidence (per-document promotion, so one
             gold document's many chunks cannot crowd another gold document
             out of the cutoff), preserving the relative order of the rest.
  evidence   for each gold evidence item, the gold passage is located in the
             pack document's raw_text (the exact slice at the gold bounds
             when they match, else the first exact occurrence; unlocatable
             items are counted as uninjectable), run through the inherited
             _records_for_row extraction, and each resulting record is
             re-anchored to a single ExactSourceSpan that IS exactly the
             gold span (exact_text at the located bounds) with maximal fit
             scores and full facet coverage.  Claim values and confidences
             remain the controller's own extractions; accepted_answers are
             never read.  When extraction yields nothing, one whole-passage
             claim record is injected as a spec-literal fallback.
  controller disposition is forced to accepted_disposition and, for ANSWER
             cases, the answer text to accepted_answers[0], bypassing
             selection/plan/verification (unsupported surfaces count as 0).

Flags combine cumulatively for the oracle ladder (rung 1 = candidate,
rung 2 = candidate+ranking, rung 3 = +evidence, rung 4 = +controller).
Oracle runs are reported with
accuracy_class=ORACLE_DIAGNOSTIC_NOT_SYSTEM_ACCURACY.

Determinism: same inputs produce a byte-identical report except
elapsed/latency fields.  The outcomes file additionally carries per-case
latency and /proc/self/io deltas, which are inherently run-dependent.
Tuning discipline: nothing here fits anything; evaluation/final_held
partitions are only read for reporting.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v050_common import (
    BENCHMARK_PATH,
    case_gold_pageids,
    conversation_order,
    latency_summary,
    load_benchmark,
    pageid,
    write_report,
)

from aethersparse.controller.framing import QueryFramer
from aethersparse.controller.models import (
    ControllerDisposition,
    EntityMention,
    EvidenceRecord,
    ExactSourceSpan,
    QueryFrame,
    RealizedAnswer,
    ResolutionMethod,
    StructuredClaim,
    SurfaceBinding,
)
from aethersparse.controller.pipeline import StructuredController
from aethersparse.controller.sqlite_provider import (
    DYNAMIC_STOP,
    TOKEN_RE,
    SQLiteControllerProvider,
    _edit_similarity,
    _entity_id,
    _normalize,
)
from aethersparse.selection.models import CandidateScore
from aethersparse.selection.selector import EvidenceSelector, _sigmoid

ORACLE_CHOICES = ("candidate", "ranking", "evidence", "controller")
ORACLE_SET = frozenset(ORACLE_CHOICES)

STAGE_LABELS = (
    "A_CANDIDATE_MISSING",
    "B_CANDIDATE_MISRANKED",
    "C_EVIDENCE_FAILED",
    "D_CONTROLLER_FAILED",
    "E_BENCHMARK_DEFECT",
)

P3_REQUIRED_TABLES = {
    "documents",
    "chunks",
    "chunks_fts",
    "aliases",
    "links",
    "categories",
    "time_expressions",
    "corpus_meta",
}

# Deterministic composition glue used by the controller's LIST/COMPARISON
# realizations (and mirrored by the benchmark's accepted answers).
_ANSWER_GLUE_RE = re.compile(r"\s*(?:;|\s+compared\s+with\s+|\s+[<>]\s+|\s+=\s+)\s*")


def _normalize_answer(value: str) -> str:
    """The exact-answer normalizer: casefold + whitespace-collapse."""

    return " ".join(value.casefold().split())


def _answer_components(answer: str) -> tuple[str, ...]:
    """Split an accepted answer into its factual components.

    LIST answers join surfaces with "; "; COMPARISON answers join two values
    with " compared with " or " < "/" = "/" > " and a trailing period.  Single
    answers return a single component.
    """

    text = answer.strip()
    if text.endswith("."):
        text = text[:-1]
    return tuple(part for part in (item.strip() for item in _ANSWER_GLUE_RE.split(text)) if part)


def _attribute_stage(
    *,
    defect: str | None,
    gold_in_pool: bool,
    strict_recall: bool,
    evidence_hit: bool,
    exact_answer: bool,
) -> str | None:
    """Earliest failing stage for a failed ANSWER case (None when passing)."""

    if exact_answer:
        return None
    if defect is not None:
        return "E_BENCHMARK_DEFECT"
    if not gold_in_pool:
        return "A_CANDIDATE_MISSING"
    if not strict_recall:
        return "B_CANDIDATE_MISRANKED"
    if not evidence_hit:
        return "C_EVIDENCE_FAILED"
    return "D_CONTROLLER_FAILED"


def _io_counters() -> tuple[int, int, int]:
    """Return (rchar, read_bytes, syscr) from /proc/self/io (Linux)."""

    try:
        fields: dict[str, int] = {}
        with open("/proc/self/io", encoding="ascii") as handle:
            for line in handle:
                key, _, value = line.partition(":")
                fields[key.strip()] = int(value.strip())
        return (
            fields.get("rchar", 0),
            fields.get("read_bytes", 0),
            fields.get("syscr", 0),
        )
    except (OSError, ValueError):
        return (0, 0, 0)


def _record_sort_key(record: EvidenceRecord) -> tuple[float, float, float, float, float, str]:
    """The provider's deterministic record ordering (sqlite_provider._retrieve)."""

    return (
        -record.entity_fit,
        -record.relation_fit,
        -record.answer_shape_fit,
        -record.temporal_fit,
        -record.claim.confidence,
        record.claim.claim_id,
    )


class P3ControllerAdapter(SQLiteControllerProvider):
    """SQLiteControllerProvider ported to the p3 pack schema.

    Only schema-bearing methods are overridden; every scoring, extraction,
    linking-threshold, and disposition-relevant computation is inherited
    unchanged.  See the module docstring for the substitution list.
    """

    def __init__(self, path: Path) -> None:
        if not path.is_file():
            raise FileNotFoundError(path)
        self.path = path
        self.maximum_limit = 64
        self.db = sqlite3.connect(f"file:{path.resolve()}?mode=ro&immutable=1", uri=True)
        self.db.row_factory = sqlite3.Row
        self.page_bytes = int(self.db.execute("PRAGMA page_size").fetchone()[0])
        self.last_workload = None
        self._entity_documents: dict[str, str] = {}
        self._query_started = 0
        self._query_probes = 0
        self._link_payload_bytes = 0
        self._validate_schema()

    def _validate_schema(self) -> None:
        tables = {
            str(row[0])
            for row in self.db.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            )
        }
        missing = P3_REQUIRED_TABLES - tables
        if missing:
            raise ValueError(f"not a p3 corpus pack; missing {sorted(missing)}")

    def _rows(self, sql: str, parameters: tuple[object, ...]) -> list[sqlite3.Row]:
        # p3 column renames as exact token substitutions; AS aliases keep the
        # inherited code's result keys unchanged.
        sql = sql.replace("substr(raw_wikitext", "substr(raw_text")
        sql = sql.replace("d.revision_id", "d.revision AS revision_id")
        sql = sql.replace("d.source_text_sha256", "d.content_hash AS source_text_sha256")
        sql = sql.replace("c.source_span_sha256", "c.content_hash AS source_span_sha256")
        return super()._rows(sql, parameters)

    def _candidate_rows(self, surface: str) -> list[tuple[sqlite3.Row, ResolutionMethod, float]]:
        key = _normalize(surface)
        # p3: aliases has no kind column; a document's own title is folded in
        # as an alias, so alias == normalized_title identifies EXACT_TITLE.
        exact = self._rows(
            """SELECT d.document_id,d.title,d.normalized_title,d.redirect_target,
                      d.source_url
                 FROM aliases AS a JOIN documents AS d USING(document_id)
                WHERE a.alias=? ORDER BY d.document_id LIMIT 16""",
            (key,),
        )
        candidates: dict[str, tuple[sqlite3.Row, ResolutionMethod, float]] = {}
        for row in exact:
            redirect_target = row["redirect_target"]
            if redirect_target:
                target = self._target_document(str(redirect_target))
                if target is not None:
                    candidates[str(target["document_id"])] = (
                        target,
                        ResolutionMethod.REDIRECT,
                        0.99,
                    )
                continue
            method = (
                ResolutionMethod.EXACT_TITLE
                if str(row["normalized_title"]) == key
                else ResolutionMethod.ALIAS
            )
            score = 1.0 if method is ResolutionMethod.EXACT_TITLE else 0.97
            candidates.setdefault(str(row["document_id"]), (row, method, score))
        # p3 folds anchor aliases into aliases; the old anchors-table fallback
        # probe is subsumed by the aliases probe above and is not re-issued.
        if not candidates and len(key) >= 4:
            first = key[0]
            lower_length = max(1, len(key) - 3)
            upper_length = len(key) + 3
            fuzzy = self._rows(
                """SELECT document_id,title,normalized_title,source_url,
                          redirect_target,'fuzzy' AS kind
                     FROM documents
                    WHERE substr(normalized_title,1,1)=?
                      AND length(normalized_title) BETWEEN ? AND ?
                    ORDER BY abs(length(normalized_title)-?),normalized_title LIMIT 128""",
                (first, lower_length, upper_length, len(key)),
            )
            for row in fuzzy:
                similarity = _edit_similarity(key, str(row["normalized_title"]))
                if similarity >= 0.78 and not row["redirect_target"]:
                    candidates[str(row["document_id"])] = (
                        row,
                        ResolutionMethod.FUZZY,
                        0.88 * similarity,
                    )
        return sorted(
            candidates.values(), key=lambda item: (-item[2], str(item[0]["document_id"]))
        )[:8]

    def _implicit_exact_mentions(self, frame: QueryFrame) -> tuple[EntityMention, ...]:
        """Longest non-overlapping alias matches; p3 folds anchors into aliases."""

        tokens = list(TOKEN_RE.finditer(frame.normalized_query))
        candidates: list[tuple[int, int, str, str]] = []
        for width in range(min(5, len(tokens)), 0, -1):
            for index in range(0, len(tokens) - width + 1):
                window = tokens[index : index + width]
                folded_tokens = {item.group(0).casefold() for item in window}
                if folded_tokens <= DYNAMIC_STOP:
                    continue
                start = window[0].start()
                end = window[-1].end()
                surface = frame.normalized_query[start:end]
                normalized = _normalize(surface)
                candidates.append((start, end, surface, normalized))
                if len(candidates) >= 32:
                    break
            if len(candidates) >= 32:
                break
        if not candidates:
            return ()
        keys = tuple(dict.fromkeys(item[3] for item in candidates))
        marks = ",".join("?" for _ in keys)
        alias_rows = self._rows(
            f"SELECT DISTINCT alias AS surface FROM aliases WHERE alias IN ({marks})",
            keys,
        )
        matches = {str(row["surface"]) for row in alias_rows}
        attribution = {_normalize(item) for item in frame.attribution_constraints}
        eligible = [
            item for item in candidates if item[3] in matches and item[3] not in attribution
        ]
        if not eligible:
            return ()
        eligible.sort(key=lambda item: (-len(item[3].split()), -len(item[3]), item[0]))
        chosen_mentions: list[EntityMention] = []
        for start, end, surface, _normalized in eligible:
            if any(
                not (end <= mention.char_start or start >= mention.char_end)
                for mention in chosen_mentions
            ):
                continue
            chosen_mentions.append(EntityMention(surface=surface, char_start=start, char_end=end))
            if len(chosen_mentions) >= 4:
                break
        return tuple(sorted(chosen_mentions, key=lambda item: (item.char_start, item.char_end)))

    def records_for_documents(
        self,
        frame: QueryFrame,
        document_ids: tuple[str, ...],
    ) -> tuple[EvidenceRecord, ...]:
        """Build EvidenceRecords from every chunk of the given documents.

        Replaces the provider's FTS/entity-row retrieval with the selector's
        retrieved document set; extraction, scoring, the corpus-coverage
        gate, and the record ordering are the provider's own.
        """

        if not document_ids or not self.corpus_coverage(frame):
            return ()
        marks = ",".join("?" for _ in document_ids)
        rows = self._rows(
            f"""SELECT c.chunk_id,c.document_id,c.section_path,c.raw_start,c.raw_end,
                      c.raw_text,c.normalized_text,d.title,
                      d.revision AS revision_id,d.source_url
                 FROM chunks AS c JOIN documents AS d USING(document_id)
                WHERE c.document_id IN ({marks})
                ORDER BY c.document_id,c.block_index,c.raw_start""",
            tuple(document_ids),
        )
        records: list[EvidenceRecord] = []
        for row in rows:
            records.extend(self._records_for_row(frame, row))
        records.sort(key=_record_sort_key)
        return tuple(records)


class _GoldDocumentResolver:
    """Resolve gold simplewiki:{pageid}:{revid} ids to p3 pack documents.

    Matching is at the pageid component only; the pack revision is preferred
    when it equals the gold revision, otherwise the first document_id wins
    (deterministic).  Normalized document text is cached for the E-check.
    """

    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db
        self._rows_by_pageid: dict[str, list[sqlite3.Row]] = {}
        self._normalized_cache: dict[str, tuple[str, str]] = {}

    def document_for(self, gold_document_id: str) -> sqlite3.Row | None:
        parts = gold_document_id.split(":")
        pid = pageid(gold_document_id)
        revid = parts[2] if len(parts) >= 3 else None
        if pid not in self._rows_by_pageid:
            escaped = pid.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            self._rows_by_pageid[pid] = list(
                self.db.execute(
                    "SELECT document_id,title,revision,source_url,raw_text,normalized_text "
                    "FROM documents WHERE document_id LIKE ? ESCAPE '\\' ORDER BY document_id",
                    (f"mw:{escaped}:%",),
                )
            )
        rows = self._rows_by_pageid[pid]
        if not rows:
            return None
        for row in rows:
            if revid is not None and str(row["revision"]) == revid:
                return row
        return rows[0]

    def pack_document_ids(self, case: Any) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    str(doc["document_id"])
                    for gold in case.gold_evidence
                    if (doc := self.document_for(gold.document_id)) is not None
                }
            )
        )

    def normalized_texts(self, document_id: str) -> tuple[str, str]:
        if document_id not in self._normalized_cache:
            row = self.db.execute(
                "SELECT raw_text,normalized_text FROM documents WHERE document_id=?",
                (document_id,),
            ).fetchone()
            if row is None:
                self._normalized_cache[document_id] = ("", "")
            else:
                self._normalized_cache[document_id] = (
                    _normalize_answer(str(row["raw_text"])),
                    _normalize_answer(str(row["normalized_text"])),
                )
        return self._normalized_cache[document_id]


def _gold_integrity(case: Any, resolver: _GoldDocumentResolver) -> tuple[str | None, bool]:
    """Return (defect_reason|None, all_bounds_exact) for an ANSWER case.

    E_BENCHMARK_DEFECT conditions: gold document missing from the pack, gold
    exact_text absent from both text forms, or no accepted answer's
    components all present across the case's gold documents.
    """

    document_ids: list[str] = []
    bounds_exact = True
    for gold in case.gold_evidence:
        doc = resolver.document_for(gold.document_id)
        if doc is None:
            return "gold_document_missing", False
        raw = str(doc["raw_text"])
        if raw[gold.char_start : gold.char_end] != gold.exact_text:
            bounds_exact = False
        if gold.exact_text not in raw and gold.exact_text not in str(doc["normalized_text"]):
            return "gold_exact_text_absent", bounds_exact
        document_ids.append(str(doc["document_id"]))
    for accepted in case.accepted_answers:
        components = _answer_components(accepted)
        if all(
            any(
                _normalize_answer(component) in normalized_raw
                or _normalize_answer(component) in normalized_text
                for normalized_raw, normalized_text in (
                    resolver.normalized_texts(document_id) for document_id in document_ids
                )
            )
            for component in components
        ):
            return None, bounds_exact
    return "accepted_answer_absent", bounds_exact


def _project_gold_span_ids(spans: Any, case: Any) -> set[str]:
    """Gold span ids covered by runtime spans (pageid match + containment)."""

    hits: set[str] = set()
    for gold in case.gold_evidence:
        gold_pageid = pageid(gold.document_id)
        if any(
            pageid(span.document_id) == gold_pageid
            and gold.char_start <= span.char_start
            and span.char_end <= gold.char_end
            for span in spans
        ):
            hits.add(gold.span_id)
    return hits


def _selector_anchors(selector: EvidenceSelector, query: str) -> tuple[list[str], set[str]]:
    """Recompute anchors/query-categories exactly as candidates() does."""

    anchors = list(
        dict.fromkeys(
            [*selector._anchor_documents(query), *selector._alias_probed_documents(query)]
        )
    )[:8]
    if anchors:
        marks = ",".join("?" for _ in anchors)
        title_by_doc = {
            str(row[0]): str(row[1])
            for row in selector.store.db.execute(
                f"SELECT document_id, title FROM documents WHERE document_id IN ({marks})",
                anchors,
            )
        }
        seen_titles: set[str] = set()
        deduped: list[str] = []
        for document_id in anchors:
            key = title_by_doc.get(document_id, "").casefold()
            if key and key in seen_titles:
                continue
            seen_titles.add(key)
            deduped.append(document_id)
        anchors = deduped
    return anchors, selector._query_categories(anchors)


def _raw_bm25(selector: EvidenceSelector, query: str, chunk_id: str) -> float:
    """bm25 of one chunk under CorpusStore.search's exact FTS term selection."""

    terms = [term for term in TOKEN_RE.findall(query.casefold()) if len(term) > 2]
    if not terms:
        return 0.0
    selected = sorted(set(terms), key=lambda term: (-len(term), term))[:7]
    fts_query = " OR ".join(f'"{term}"' for term in selected)
    row = selector.store.db.execute(
        "SELECT bm25(chunks_fts, 1.8, 1.2, 1.0) AS rank FROM chunks_fts "
        "WHERE chunks_fts MATCH ? AND chunk_id=?",
        (fts_query, chunk_id),
    ).fetchone()
    return float(row["rank"]) if row is not None else 0.0


def _candidate_oracle_pool(
    selector: EvidenceSelector,
    question: str,
    pool: list[CandidateScore],
    gold_document_ids: tuple[str, ...],
) -> tuple[list[CandidateScore], int]:
    """Append missing gold documents' best chunks via the selector's feature path."""

    pool_docs = {candidate.document_id for candidate in pool}
    pool_chunk_ids = {candidate.chunk_id for candidate in pool}
    missing = [doc for doc in gold_document_ids if doc not in pool_docs]
    if not missing:
        return list(pool), 0
    anchors, query_categories = _selector_anchors(selector, question)
    raw_scores = {
        candidate.chunk_id: _raw_bm25(selector, question, candidate.chunk_id) for candidate in pool
    }
    injected_rows: list[sqlite3.Row] = []
    for document_id in missing:
        rows = selector._carry_rows(question, document_id)
        if not rows:
            continue
        row = rows[0]
        chunk_id = str(row["chunk_id"])
        if chunk_id in pool_chunk_ids:
            continue
        injected_rows.append(row)
        raw_scores[chunk_id] = _raw_bm25(selector, question, chunk_id)
    if not injected_rows:
        return list(pool), 0
    inverted = [-value for value in raw_scores.values()]
    floor = min(inverted)
    ceiling = max(inverted)
    spread = (ceiling - floor) or 1.0
    injected: list[CandidateScore] = []
    for offset, row in enumerate(injected_rows):
        chunk_id = str(row["chunk_id"])
        bm25_score = (-raw_scores[chunk_id] - floor) / spread
        features = selector._feature_vector(
            question,
            row,
            len(pool) + offset,
            anchors,
            query_categories,
            bm25_score=bm25_score,
        )
        deterministic = selector._fusion(features)
        reranker = _sigmoid(selector.model.score(features))
        injected.append(
            CandidateScore(
                chunk_id=chunk_id,
                document_id=str(row["document_id"]),
                title=str(row["title"]),
                section_path=str(row["section_path"]),
                raw_text=str(row["raw_text"]),
                normalized_text=str(row["normalized_text"]),
                source_url=str(row["source_url"]),
                source_revision=str(row["revision"]),
                lexical_position=len(pool) + offset,
                features=features,
                deterministic_score=deterministic,
                reranker_score=reranker,
                final_score=0.45 * deterministic + 0.55 * reranker,
            )
        )
    return [*pool, *injected], len(injected)


def _ranking_oracle_top8(
    reranked: tuple[CandidateScore, ...],
    gold_pageids: set[str],
    selected_limit: int,
) -> list[CandidateScore]:
    """Force gold DOCUMENTS to the top, preserving relative order of the rest.

    The strongest candidate of each gold document leads (in reranked order);
    promoting every gold chunk instead would let one gold document's many
    chunks crowd another gold document out of the selected cutoff (measured:
    3/100 two-source cases at 10k).
    """

    best_per_gold: dict[str, CandidateScore] = {}
    rest: list[CandidateScore] = []
    for item in reranked:
        pid = pageid(item.document_id)
        if pid in gold_pageids and pid not in best_per_gold:
            best_per_gold[pid] = item
        else:
            rest.append(item)
    return [*best_per_gold.values(), *rest][:selected_limit]


def _evidence_oracle_records(
    adapter: P3ControllerAdapter,
    frame: QueryFrame,
    case: Any,
    resolver: _GoldDocumentResolver,
) -> tuple[tuple[EvidenceRecord, ...], int]:
    """Inject records whose span IS the gold span, with maximal fit scores.

    Claim values/confidences come from the controller's own extraction over
    the gold passage; accepted_answers are never read.  Returns (records,
    uninjectable_count).
    """

    records: list[EvidenceRecord] = []
    uninjectable = 0
    for gold in case.gold_evidence:
        doc = resolver.document_for(gold.document_id)
        if doc is None:
            uninjectable += 1
            continue
        raw = str(doc["raw_text"])
        if raw[gold.char_start : gold.char_end] == gold.exact_text:
            start = gold.char_start
        else:
            start = raw.find(gold.exact_text)
        if start < 0:
            uninjectable += 1
            continue
        end = start + len(gold.exact_text)
        digest = hashlib.sha256(f"{doc['document_id']}:{start}:{end}".encode()).hexdigest()[:24]
        span = ExactSourceSpan(
            span_id=f"span:oracle:{digest}",
            document_id=str(doc["document_id"]),
            source_title=str(doc["title"]),
            source_revision=str(doc["revision"]),
            source_url=str(doc["source_url"]),
            source_family=str(doc["source_url"]),
            char_start=start,
            char_end=end,
            text=gold.exact_text,
            text_hash=f"sha256:{hashlib.sha256(gold.exact_text.encode()).hexdigest()}",
        )
        pseudo_row = {
            "chunk_id": f"chunk:oracle:{digest}",
            "document_id": str(doc["document_id"]),
            "section_path": "Oracle",
            "raw_start": start,
            "raw_end": end,
            "raw_text": gold.exact_text,
            "title": str(doc["title"]),
            "revision_id": str(doc["revision"]),
            "source_url": str(doc["source_url"]),
        }
        extracted = adapter._records_for_row(frame, pseudo_row)
        if not extracted:
            relation = (
                frame.requested_relation_families[0]
                if frame.requested_relation_families
                else "unknown"
            )
            claim_digest = hashlib.sha256(
                f"oracle:{doc['document_id']}:{relation}:{start}:{end}".encode()
            ).hexdigest()[:24]
            extracted = [
                EvidenceRecord(
                    claim=StructuredClaim(
                        claim_id=f"claim:oracle:{claim_digest}",
                        subject_entity_id=_entity_id(str(doc["title"])),
                        relation_family=relation,
                        object_value=gold.exact_text,
                        answer_shape=frame.answer_shape,
                        source_span_ids=(span.span_id,),
                        confidence=1.0,
                    ),
                    source_spans=(span,),
                    entity_fit=1.0,
                    relation_fit=1.0,
                    answerability=1.0,
                    answer_shape_fit=1.0,
                    temporal_fit=1.0,
                    attribution_fit=1.0,
                    source_quality=1.0,
                    facet_coverage=frame.required_facets,
                )
            ]
        for record in extracted:
            claim = record.claim.model_copy(update={"source_span_ids": (span.span_id,)})
            records.append(
                record.model_copy(
                    update={
                        "claim": claim,
                        "source_spans": (span,),
                        "entity_fit": 1.0,
                        "relation_fit": 1.0,
                        "answerability": 1.0,
                        "answer_shape_fit": 1.0,
                        "temporal_fit": 1.0,
                        "attribution_fit": 1.0,
                        "source_quality": 1.0,
                        "facet_coverage": frame.required_facets,
                    }
                )
            )
    return tuple(records), uninjectable


def _controller_oracle_result(result: Any, case: Any) -> Any:
    """Force the accepted disposition (and answer text for ANSWER cases)."""

    updates: dict[str, Any] = {
        "disposition": case.accepted_disposition,
        "reason": "controller oracle: disposition forced to the accepted value (diagnostic)",
    }
    if case.accepted_disposition is ControllerDisposition.ANSWER:
        text = case.accepted_answers[0]
        updates["answer"] = RealizedAnswer(
            text=text,
            bindings=(
                SurfaceBinding(
                    plan_claim_id="plan:oracle:controller:0",
                    start=0,
                    end=len(text),
                    surface=text,
                    structured_claim_ids=("claim:oracle:controller",),
                    source_span_ids=("span:oracle:controller",),
                ),
            ),
        )
        updates["selection"] = None
        updates["plan"] = None
        updates["verification"] = None
    else:
        updates["answer"] = None
    return result.model_copy(update=updates)


def _replay_linked_entities(
    case_id: str,
    cases_by_id: dict[str, Any],
    linker: P3ControllerAdapter,
    framer: QueryFramer,
    memo: dict[str, tuple[str, ...]],
    active: set[str],
) -> tuple[str, ...]:
    """Replay declared parent turns so conversational state is order-invariant."""

    if case_id in memo:
        return memo[case_id]
    if case_id in active:
        raise ValueError(f"conversational dependency cycle at {case_id}")
    case = cases_by_id.get(case_id)
    if case is None:
        raise ValueError(f"unknown prior case {case_id}")
    active.add(case_id)
    prior_ids = tuple(
        dict.fromkeys(
            entity_id
            for parent_id in case.prior_case_ids
            for entity_id in _replay_linked_entities(
                parent_id, cases_by_id, linker, framer, memo, active
            )
        )
    )
    frame = framer.frame(case.question, prior_entity_ids=prior_ids)
    memo[case_id] = linker.link_frame(frame).candidate_entity_ids
    active.remove(case_id)
    return memo[case_id]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, default=BENCHMARK_PATH)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--outcomes",
        type=Path,
        default=None,
        help="per-case diagnostic JSON (booleans/counts only; never gold content)",
    )
    parser.add_argument(
        "--oracle",
        action="append",
        choices=ORACLE_CHOICES,
        default=None,
        help="diagnostic oracle substitution; repeatable and combinable. "
        "Default off; oracle runs are never system accuracy.",
    )
    parser.add_argument("--limit", type=int, help="evaluate only the first N cases")
    parser.add_argument(
        "--partitions",
        nargs="+",
        default=None,
        help="restrict to these partitions (tuning development evaluation final_held)",
    )
    parser.add_argument("--candidate-limit", type=int, default=96)
    parser.add_argument("--selected-limit", type=int, default=8)
    parser.add_argument(
        "--trace-cache",
        type=Path,
        default=None,
        help="replay candidate pools from a Phase 0B trace cache instead of "
        "running retrieval (counterfactual controller iteration)",
    )
    parser.add_argument(
        "--trajectory-trace",
        type=Path,
        default=None,
        help="Amendment A: write per-operation trajectory records (JSONL) to "
        "this path; diagnostic artifact, never a runtime input",
    )
    return parser.parse_args(argv)


def _resolve_oracles(args: argparse.Namespace) -> frozenset[str]:
    """Oracles are off unless explicitly requested via repeated --oracle flags."""

    requested = frozenset(args.oracle or ())
    assert requested <= ORACLE_SET, f"unknown oracle flags: {sorted(requested - ORACLE_SET)}"
    return requested


def run_evaluation(
    *,
    pack: Path,
    benchmark_path: Path,
    limit: int | None,
    partitions: list[str] | None,
    oracles: frozenset[str],
    candidate_limit: int = 96,
    selected_limit: int = 8,
    progress: bool = False,
    trace_cache: Path | None = None,
    _collect_results: bool = False,
    _frame_shape_overrides: dict[str, str] | None = None,
    _tracer: Any = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run the four-stage harness; return (report, per-case outcomes).

    _frame_shape_overrides (diagnostic only): per-case gold answer-shape
    override for the mode-1 condition in Phase 2.  Never shipped behavior.
    """

    if not isinstance(oracles, frozenset) or not oracles <= ORACLE_SET:
        raise AssertionError("oracles must be an explicit subset of ORACLE_CHOICES")
    benchmark = load_benchmark(benchmark_path)
    cases = list(benchmark.cases)
    if partitions:
        wanted = set(partitions)
        cases = [case for case in cases if case.partition.value in wanted]
    if limit is not None:
        cases = cases[:limit]
    cases = conversation_order(cases)
    cases_by_id = {case.case_id: case for case in benchmark.cases}

    cached_pools: dict[str, dict] | None = None
    if trace_cache is not None:
        from v09_trace_cache import load_cache

        payload = load_cache(trace_cache)
        cached_pools = {entry["case_id"]: entry for entry in payload["cases"]}

    selector = EvidenceSelector(
        pack, None, candidate_limit=candidate_limit, selected_limit=selected_limit
    )
    linker = P3ControllerAdapter(pack)
    framer = QueryFramer()
    resolver = _GoldDocumentResolver(linker.db)

    prior_memo: dict[str, tuple[str, ...]] = {}
    outcomes: list[dict[str, Any]] = []
    collected_results: list[Any] | None = [] if _collect_results else None
    latencies: dict[str, list[float]] = {
        "candidates": [],
        "select": [],
        "link": [],
        "evidence": [],
        "controller": [],
        "case_total": [],
    }
    injection_totals = {"candidate": 0, "evidence": 0, "evidence_uninjectable": 0}
    started = time.time()

    for index, case in enumerate(cases, start=1):
        io_before = _io_counters()
        case_started = time.perf_counter_ns()
        is_answer = case.accepted_disposition is ControllerDisposition.ANSWER
        if _tracer is not None:
            _tracer.begin_case(case.case_id)
        _trace_state: dict[str, Any] = {"query": case.question}

        def _trace_step(
            operator_id: int,
            *,
            arguments: dict[str, Any],
            result: dict[str, Any],
            updates: dict[str, Any] | None = None,
            io_start_bytes: int | None = None,
            started_us: int | None = None,
        ) -> None:
            """Amendment A2: append one per-operation record (diagnostic only)."""
            if _tracer is None:
                return
            from aethersparse.controller.operators import OPERATORS_BY_ID
            from aethersparse.controller.trace import io_read_bytes

            state_before = dict(_trace_state)
            _trace_state.update(updates or {})
            _tracer.record(
                operator=OPERATORS_BY_ID[operator_id],
                state_before=state_before,
                arguments=arguments,
                result=result,
                state_after=dict(_trace_state),
                io_before=(
                    io_start_bytes if io_start_bytes is not None else io_read_bytes()
                ),
                started_us=(
                    started_us
                    if started_us is not None
                    else time.perf_counter_ns() // 1000
                ),
            )
        gold_pageids = case_gold_pageids(case) if is_answer else set()
        defect: str | None = None
        bounds_exact: bool | None = None
        if is_answer:
            defect, bounds_exact = _gold_integrity(case, resolver)
        prior_ids = tuple(
            dict.fromkeys(
                entity_id
                for parent_id in case.prior_case_ids
                for entity_id in _replay_linked_entities(
                    parent_id, cases_by_id, linker, framer, prior_memo, set()
                )
            )
        )

        # Stage 1: candidate generation (current v07 EvidenceSelector), or a
        # replayed pool from the trace cache (Phase 0B counterfactual replay).
        stage_started = time.perf_counter_ns()
        if cached_pools is not None:
            entry = cached_pools[case.case_id]
            pool = [CandidateScore.model_validate(item) for item in entry["pool"]]
        else:
            pool: list[CandidateScore] = selector.candidates(case.question)
        latencies["candidates"].append((time.perf_counter_ns() - stage_started) / 1_000_000)
        injected_candidates = 0
        if "candidate" in oracles and case.gold_evidence:
            pool, injected_candidates = _candidate_oracle_pool(
                selector, case.question, pool, resolver.pack_document_ids(case)
            )
            injection_totals["candidate"] += injected_candidates
        pool_pageids = [pageid(item.document_id) for item in pool]
        pool_set = set(pool_pageids)
        _trace_step(
            1,
            arguments={"question_chars": len(case.question)},
            result={"pool_size": len(pool)},
            updates={"pool": pool_pageids},
        )

        # Stage 2: ranking; the reranker stage's selected evidence is the top-8.
        stage_started = time.perf_counter_ns()
        trace = selector.select(case.question, stage="reranker", initial_candidates=pool)
        latencies["select"].append((time.perf_counter_ns() - stage_started) / 1_000_000)
        if "ranking" in oracles and case.gold_evidence:
            top8 = _ranking_oracle_top8(
                trace.reranked_candidates,
                {pageid(gold.document_id) for gold in case.gold_evidence},
                selected_limit,
            )
        else:
            top8 = list(trace.selected_evidence)
        top8_pageids = [pageid(item.document_id) for item in top8]
        scores = [item.final_score for item in trace.reranked_candidates[:2]]
        margin = scores[0] - scores[1] if len(scores) == 2 else 0.0
        top1_score = scores[0] if scores else 0.0
        _trace_step(
            2,
            arguments={"pool_size": len(pool)},
            result={"reranked": len(trace.reranked_candidates)},
            updates={"ranking": [c.chunk_id for c in trace.reranked_candidates[:8]]},
        )

        # Stage 3: controller-package evidence construction over top-8 chunks.
        stage_started = time.perf_counter_ns()
        frame = linker.link_frame(framer.frame(case.question, prior_entity_ids=prior_ids))
        if _frame_shape_overrides is not None and case.case_id in _frame_shape_overrides:
            from aethersparse.controller.models import AnswerShape

            frame = frame.model_copy(
                update={"answer_shape": AnswerShape(_frame_shape_overrides[case.case_id])}
            )
        prior_memo[case.case_id] = frame.candidate_entity_ids
        link_ms = (time.perf_counter_ns() - stage_started) / 1_000_000
        latencies["link"].append(link_ms)
        _trace_step(
            3,
            arguments={"question_chars": len(case.question)},
            result={"answer_shape": str(frame.answer_shape)},
            updates={
                "frame": {
                    "answer_shape": str(frame.answer_shape),
                    "uncertainty": frame.uncertainty,
                }
            },
        )
        _trace_step(
            4,
            arguments={"mentions": len(frame.entity_mentions)},
            result={"bound_entities": list(frame.candidate_entity_ids)},
            updates={"entity_bindings": list(frame.candidate_entity_ids)},
        )
        top8_document_ids = tuple(sorted({item.document_id for item in top8}))
        records = linker.records_for_documents(frame, top8_document_ids)
        injected_evidence = 0
        uninjectable = 0
        if "evidence" in oracles and case.gold_evidence:
            oracle_records, uninjectable = _evidence_oracle_records(linker, frame, case, resolver)
            injected_evidence = len(oracle_records)
            injection_totals["evidence"] += injected_evidence
            injection_totals["evidence_uninjectable"] += uninjectable
            records = tuple(sorted([*records, *oracle_records], key=_record_sort_key))
        latencies["evidence"].append((time.perf_counter_ns() - stage_started) / 1_000_000 - link_ms)

        # Stage 4: answer composition/disposition via the controller package.
        stage_started = time.perf_counter_ns()
        result = StructuredController._complete(
            case.case_id,
            frame,
            records,
            corpus_coverage=linker.corpus_coverage(frame),
            premise_status="UNKNOWN",
            _trace_step=_trace_step if _tracer is not None else None,
        )
        controller_oracle_applied = "controller" in oracles
        if controller_oracle_applied:
            result = _controller_oracle_result(result, case)
        latencies["controller"].append((time.perf_counter_ns() - stage_started) / 1_000_000)

        answer_text = result.answer.text if result.answer is not None else None
        factual_surfaces = len(result.answer.bindings) if result.answer is not None else 0
        if controller_oracle_applied:
            unsupported_surfaces = 0
        else:
            verified = result.verification is not None and result.verification.passed
            unsupported_surfaces = 0 if verified else factual_surfaces
        disposition_correct = result.disposition is case.accepted_disposition

        lenient = strict = evidence_hit = exact_answer = None
        projected_fed: set[str] = set()
        projected_graph: set[str] = set()
        stage = None
        fed_spans = [span for record in records for span in record.source_spans]
        if is_answer:
            top8_set = set(top8_pageids)
            lenient = bool(gold_pageids & top8_set)
            strict = bool(gold_pageids) and gold_pageids <= top8_set
            projected_fed = _project_gold_span_ids(fed_spans, case)
            evidence_hit = bool(projected_fed)
            projected_graph = _project_gold_span_ids(result.graph.source_spans, case)
            normalized_answer = _normalize_answer(answer_text or "")
            exact_answer = (
                result.disposition is ControllerDisposition.ANSWER
                and any(
                    _normalize_answer(accepted) == normalized_answer
                    for accepted in case.accepted_answers
                )
                and unsupported_surfaces == 0
            )
            stage = _attribute_stage(
                defect=defect,
                gold_in_pool=bool(gold_pageids) and gold_pageids <= pool_set,
                strict_recall=strict,
                evidence_hit=evidence_hit,
                exact_answer=exact_answer,
            )

        io_after = _io_counters()
        latency_ms = (time.perf_counter_ns() - case_started) / 1_000_000
        latencies["case_total"].append(latency_ms)
        if collected_results is not None:
            collected_results.append(result)
        outcomes.append(
            {
                "case_id": case.case_id,
                "partition": str(case.partition),
                "categories": list(case.categories),
                "accepted_disposition": str(case.accepted_disposition),
                "pool_size": len(pool),
                "pool_pageids": pool_pageids,
                "gold_in_pool_lenient": (bool(gold_pageids & pool_set) if is_answer else None),
                "gold_in_pool_strict": (
                    (bool(gold_pageids) and gold_pageids <= pool_set) if is_answer else None
                ),
                "top8_pageids": top8_pageids,
                "retrieved_span_ids": [
                    span.span_id
                    for span in dict.fromkeys(
                        span for span in fed_spans if not span.span_id.startswith("span:oracle:")
                    )
                ],
                "fed_record_count": len(records),
                "linked_entity_ids": list(result.frame.candidate_entity_ids),
                "disposition": str(result.disposition),
                "answer_text": None if controller_oracle_applied else answer_text,
                "controller_oracle_applied": controller_oracle_applied,
                "disposition_correct": disposition_correct,
                "exact_answer": exact_answer,
                "article_recall_lenient": lenient,
                "article_recall_strict": strict,
                "evidence_recall": evidence_hit,
                "gold_span_fed": bool(projected_fed) if is_answer else None,
                "gold_span_in_graph": bool(projected_graph) if is_answer else None,
                "e_benchmark_defect": defect is not None,
                "benchmark_defect_reason": defect,
                "gold_bounds_exact": bounds_exact,
                "stage_attribution": stage,
                "oracle_injected": {
                    "candidates": injected_candidates,
                    "evidence_records": injected_evidence,
                    "evidence_uninjectable": uninjectable,
                },
                "margin": margin,
                "top1_score": top1_score,
                "latency_ms": latency_ms,
                "io_rchar": io_after[0] - io_before[0],
                "io_read_bytes": io_after[1] - io_before[1],
                "io_syscr": io_after[2] - io_before[2],
            }
        )
        if _tracer is not None:
            # Amendment A3: retain every sequence with its gold-scored outcome.
            if is_answer:
                outcome_label = "correct" if exact_answer else "incorrect"
                if result.disposition is ControllerDisposition.VERIFICATION_FAILURE:
                    outcome_label = "aborted"
            else:
                outcome_label = "correct" if disposition_correct else "incorrect"
            _tracer.end_case(
                partition=str(case.partition),
                outcome=outcome_label,
                terminal=str(result.disposition).split(".")[-1],
            )
        if progress and (index % 25 == 0 or index == len(cases)):
            print(f"evaluated {index}/{len(cases)} cases", file=sys.stderr, flush=True)

    answer_outcomes = [row for row in outcomes if row["accepted_disposition"] == "ANSWER"]
    n_answer = len(answer_outcomes)
    stage_counts = {label: 0 for label in STAGE_LABELS}
    for row in answer_outcomes:
        if row["stage_attribution"] is not None:
            stage_counts[row["stage_attribution"]] += 1
    partitions_seen = sorted({row["partition"] for row in outcomes})
    by_partition: dict[str, dict[str, Any]] = {}
    for partition in partitions_seen:
        rows = [row for row in outcomes if row["partition"] == partition]
        answer_rows = [row for row in rows if row["accepted_disposition"] == "ANSWER"]
        n_p_answer = len(answer_rows)
        by_partition[partition] = {
            "n": len(rows),
            "answer_cases": n_p_answer,
            "article_recall_strict": (
                sum(1 for row in answer_rows if row["article_recall_strict"]) / n_p_answer
                if n_p_answer
                else 0.0
            ),
            "exact_answer_accuracy": (
                sum(1 for row in answer_rows if row["exact_answer"]) / n_p_answer
                if n_p_answer
                else 0.0
            ),
            "disposition_accuracy": (
                sum(1 for row in rows if row["disposition_correct"]) / len(rows) if rows else 0.0
            ),
        }
    report: dict[str, Any] = {
        "harness": "scripts/droid/v08_pipeline_eval.py",
        "diagnostic_purpose": "four-stage failure decomposition + oracle ladder (Mission 3 Lane B)",
        "benchmark_identity": benchmark.benchmark_identity,
        "benchmark_sha256": benchmark.content_sha256,
        "pack": str(pack),
        "pack_sha256": hashlib.sha256(pack.read_bytes()).hexdigest(),
        "config": {
            "candidate_limit": candidate_limit,
            "selected_limit": selected_limit,
            "model_identity": selector.model.training_identity,
            "oracles": sorted(oracles),
            "oracle_free": not oracles,
            "accuracy_class": (
                "SYSTEM" if not oracles else "ORACLE_DIAGNOSTIC_NOT_SYSTEM_ACCURACY"
            ),
            "gold_matching": "pageid",
            "cases_evaluated": len(cases),
            "answer_cases": n_answer,
            "limit": limit,
            "partitions": partitions,
        },
        "elapsed_seconds": time.time() - started,
        "metrics": {
            "answer_cases": {
                "n": n_answer,
                "article_recall_strict": (
                    sum(1 for row in answer_outcomes if row["article_recall_strict"]) / n_answer
                    if n_answer
                    else 0.0
                ),
                "article_recall_lenient": (
                    sum(1 for row in answer_outcomes if row["article_recall_lenient"]) / n_answer
                    if n_answer
                    else 0.0
                ),
                "evidence_recall": (
                    sum(1 for row in answer_outcomes if row["evidence_recall"]) / n_answer
                    if n_answer
                    else 0.0
                ),
                "exact_answer_accuracy": (
                    sum(1 for row in answer_outcomes if row["exact_answer"]) / n_answer
                    if n_answer
                    else 0.0
                ),
            },
            "all_cases": {
                "n": len(outcomes),
                "disposition_accuracy": (
                    sum(1 for row in outcomes if row["disposition_correct"]) / len(outcomes)
                    if outcomes
                    else 0.0
                ),
            },
            "stage_attribution_failed_answer_cases": {
                **stage_counts,
                "failed_answer_cases": sum(stage_counts.values()),
            },
            "benchmark_defect_flagged_answer_cases": sum(
                1 for row in answer_outcomes if row["e_benchmark_defect"]
            ),
            "by_partition": by_partition,
        },
        "oracle_injections": injection_totals,
        "latency": {name: latency_summary(values) for name, values in latencies.items()},
    }
    return report, outcomes, collected_results


def run_evaluation_with_results(
    *,
    pack: Path,
    benchmark_path: Path,
    limit: int | None,
    partitions: list[str] | None,
    oracles: frozenset[str],
    candidate_limit: int = 96,
    selected_limit: int = 8,
    trace_cache: Path | None = None,
    cached: dict[str, dict] | None = None,
    _frame_shape_overrides: dict[str, str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[Any]]:
    """Phase 1 taxonomy: run the harness and also collect ControllerResults."""
    del cached  # cache is loaded inside run_evaluation
    report, outcomes, results = run_evaluation(
        pack=pack,
        benchmark_path=benchmark_path,
        limit=limit,
        partitions=partitions,
        oracles=oracles,
        candidate_limit=candidate_limit,
        selected_limit=selected_limit,
        trace_cache=trace_cache,
        _collect_results=True,
        _frame_shape_overrides=_frame_shape_overrides,
    )
    return report, outcomes, results


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    oracles = _resolve_oracles(args)
    tracer = None
    if args.trajectory_trace is not None:
        from aethersparse.controller.trace import TrajectoryTracer

        tracer = TrajectoryTracer(path=args.trajectory_trace)
    report, outcomes, _ = run_evaluation(
        pack=args.pack,
        benchmark_path=args.benchmark,
        limit=args.limit,
        partitions=args.partitions,
        oracles=oracles,
        candidate_limit=args.candidate_limit,
        selected_limit=args.selected_limit,
        progress=True,
        trace_cache=args.trace_cache,
        _tracer=tracer,
    )
    write_report(args.output, report)
    if args.outcomes is not None:
        args.outcomes.parent.mkdir(parents=True, exist_ok=True)
        args.outcomes.write_text(
            json.dumps(outcomes, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    metrics = report["metrics"]
    answer = metrics["answer_cases"]
    print(
        f"answer n={answer['n']} strict={answer['article_recall_strict']:.4f} "
        f"lenient={answer['article_recall_lenient']:.4f} "
        f"evidence={answer['evidence_recall']:.4f} "
        f"exact={answer['exact_answer_accuracy']:.4f}"
    )
    print(
        f"disposition_accuracy={metrics['all_cases']['disposition_accuracy']:.4f} "
        f"stages={metrics['stage_attribution_failed_answer_cases']}"
    )
    if oracles:
        print("ORACLE RUN — NOT SYSTEM ACCURACY", file=sys.stderr)
    print(f"report={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
