from __future__ import annotations

import hashlib
import runpy
import sqlite3
from pathlib import Path

from aethersparse.controller.evaluation import (
    AblationSystem,
    FrozenBenchmark,
    NaturalQueryCase,
    Partition,
    RoleIdentity,
)
from aethersparse.controller.framing import QueryFramer
from aethersparse.controller.models import AnswerShape, ControllerDisposition, ResolutionMethod
from aethersparse.controller.pipeline import StructuredController
from aethersparse.controller.sqlite_provider import SQLiteControllerProvider

SCHEMA = """
PRAGMA user_version=500;
CREATE TABLE documents(
  document_id TEXT PRIMARY KEY, wiki_page_id TEXT, revision_id TEXT, title TEXT,
  normalized_title TEXT, redirect_target TEXT, source_url TEXT, source_text_bytes INTEGER,
  source_text_sha256 TEXT, revision_sha1 TEXT, revision_timestamp TEXT,
  raw_wikitext TEXT, normalized_text TEXT);
CREATE TABLE chunks(
  chunk_id TEXT PRIMARY KEY, document_id TEXT, section_path TEXT, block_index INTEGER,
  raw_start INTEGER, raw_end INTEGER, offset_unit TEXT, raw_text TEXT,
  normalized_text TEXT, source_span_sha256 TEXT);
CREATE VIRTUAL TABLE chunks_fts USING fts5(
  chunk_id UNINDEXED, title, section_path, body,
  tokenize='unicode61 remove_diacritics 2');
CREATE TABLE aliases(alias TEXT, document_id TEXT, kind TEXT,
  PRIMARY KEY(alias,document_id,kind));
CREATE TABLE redirects(
  source_document_id TEXT PRIMARY KEY,target_title TEXT,source_text_sha256 TEXT);
CREATE TABLE anchors(
  anchor_id TEXT PRIMARY KEY,source_document_id TEXT,target_title TEXT,anchor_text TEXT,
  raw_start INTEGER,raw_end INTEGER,raw_text TEXT,source_span_sha256 TEXT);
"""


def _canonical_entity_id(title: str) -> str:
    normalized = " ".join(title.replace("_", " ").strip().split()).casefold()
    digest = hashlib.sha256(normalized.encode()).hexdigest()[:24]
    return f"as:v050:entity:{digest}"


def _add_document(
    db: sqlite3.Connection,
    number: int,
    title: str,
    raw: str,
    *,
    redirect: str | None = None,
) -> str:
    document_id = f"simplewiki:{number}:{number + 100}"
    source_hash = hashlib.sha256(raw.encode()).hexdigest()
    db.execute(
        "INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            document_id,
            str(number),
            str(number + 100),
            title,
            title.casefold(),
            redirect,
            f"https://simple.wikipedia.org/?curid={number}",
            len(raw.encode()),
            source_hash,
            None,
            "2026-08-01T00:00:00Z",
            raw,
            raw,
        ),
    )
    db.execute("INSERT INTO aliases VALUES(?,?,?)", (title.casefold(), document_id, "title"))
    chunk_id = f"chunk:{number}"
    db.execute(
        "INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            chunk_id,
            document_id,
            "Lead",
            0,
            0,
            len(raw),
            "unicode_codepoint",
            raw,
            raw,
            hashlib.sha256(raw.encode()).hexdigest(),
        ),
    )
    db.execute("INSERT INTO chunks_fts VALUES(?,?,?,?)", (chunk_id, title, "Lead", raw))
    if redirect:
        db.execute("INSERT INTO redirects VALUES(?,?,?)", (document_id, redirect, source_hash))
    return document_id


def _pack(tmp_path: Path) -> Path:
    path = tmp_path / "v050.sqlite"
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    ada = _add_document(
        db,
        1,
        "Ada Lovelace",
        "{{Infobox person\n| award_year = 1903\n"
        "| birth_date = December 10, 1815\n}}\n"
        "'''Ada Lovelace''' was an English mathematician. "
        "Ada Lovelace was born on December 10, 1815. "
        'Ada Lovelace wrote: "The Analytical Engine weaves algebraic patterns."',
    )
    _add_document(db, 2, "Augusta Ada King", "#REDIRECT [[Ada Lovelace]]", redirect="Ada Lovelace")
    biography_raw = "She was called [[Ada Lovelace|Enchantress of Numbers]]."
    biography = _add_document(db, 3, "Computing history", biography_raw)
    link = "[[Ada Lovelace|Enchantress of Numbers]]"
    start = biography_raw.index(link)
    db.execute(
        "INSERT INTO anchors VALUES(?,?,?,?,?,?,?,?)",
        (
            "anchor:ada",
            biography,
            "Ada Lovelace",
            "enchantress of numbers",
            start,
            start + len(link),
            link,
            hashlib.sha256(link.encode()).hexdigest(),
        ),
    )
    db.execute("INSERT INTO aliases VALUES(?,?,?)", ("countess of lovelace", ada, "explicit"))
    _add_document(db, 4, "Tower Alpha", "Tower Alpha is a tower with a height of 10 m.")
    _add_document(db, 5, "Tower Beta", "Tower Beta is a tower with a height of 9 m.")
    db.commit()
    db.close()
    return path


def test_provider_uses_canonical_title_derived_entity_ids(tmp_path: Path) -> None:
    with SQLiteControllerProvider(_pack(tmp_path)) as provider:
        frame = provider.link_frame(QueryFramer().frame("When was Ada Lovelace born?"))
        assert frame.candidate_entity_ids == (_canonical_entity_id("Ada Lovelace"),)
        assert frame.entity_mentions[0].resolution_method is ResolutionMethod.EXACT_TITLE
        records = provider.retrieve(frame, limit=8)
        assert records
        assert {record.claim.subject_entity_id for record in records} == {
            _canonical_entity_id("Ada Lovelace")
        }


def test_redirect_anchor_alias_fuzzy_and_unknown_resolution(tmp_path: Path) -> None:
    with SQLiteControllerProvider(_pack(tmp_path)) as provider:
        redirect = provider.link_frame(QueryFramer().frame("When was Augusta Ada King born?"))
        assert redirect.candidate_entity_ids == (_canonical_entity_id("Ada Lovelace"),)
        assert redirect.entity_mentions[0].resolution_method is ResolutionMethod.REDIRECT

        anchor = provider.link_frame(
            QueryFramer().frame("What does enchantress of numbers refer to?")
        )
        assert anchor.candidate_entity_ids == (_canonical_entity_id("Ada Lovelace"),)
        assert anchor.entity_mentions[0].surface == "enchantress of numbers"
        assert anchor.entity_mentions[0].resolution_method is ResolutionMethod.ANCHOR

        alias = provider.link_frame(QueryFramer().frame("What does countess of lovelace refer to?"))
        assert alias.candidate_entity_ids == (_canonical_entity_id("Ada Lovelace"),)
        assert alias.entity_mentions[0].surface == "countess of lovelace"
        assert alias.entity_mentions[0].resolution_method is ResolutionMethod.ALIAS

        fuzzy = provider.link_frame(QueryFramer().frame("When was Ada Lovelce born?"))
        assert fuzzy.candidate_entity_ids == (_canonical_entity_id("Ada Lovelace"),)
        assert fuzzy.entity_mentions[0].resolution_method is ResolutionMethod.FUZZY

        query = "Who invented Qzzyxx-999?"
        unknown = provider.link_frame(QueryFramer().frame(query))
        assert unknown.candidate_entity_ids == ()
        assert unknown.entity_mentions[0].surface == "Qzzyxx-999"
        assert (
            query[unknown.entity_mentions[0].char_start : unknown.entity_mentions[0].char_end]
            == "Qzzyxx-999"
        )
        assert not provider.corpus_coverage(unknown)
        assert provider.retrieve(unknown, limit=8) == ()
        assert provider.last_workload is not None
        assert provider.last_workload.candidate_rows == 0


def test_exact_date_definition_quote_and_comparison_answers(tmp_path: Path) -> None:
    with SQLiteControllerProvider(_pack(tmp_path)) as provider:
        controller = StructuredController(provider)
        date = controller.query("q:date", "When was Ada Lovelace born?", provider, evidence_limit=8)
        assert date.disposition is ControllerDisposition.ANSWER
        assert date.answer is not None and date.answer.text == "December 10, 1815"
        assert date.verification is not None and date.verification.passed

        definition = controller.query(
            "q:definition", "What is Ada Lovelace?", provider, evidence_limit=8
        )
        assert definition.disposition is ControllerDisposition.ANSWER
        assert definition.answer is not None
        assert definition.answer.text == "an English mathematician"

        quotation = controller.query(
            "q:quote",
            'Who said "The Analytical Engine weaves algebraic patterns"?',
            provider,
            evidence_limit=8,
        )
        assert quotation.disposition is ControllerDisposition.ANSWER
        assert quotation.answer is not None and quotation.answer.text == "Ada Lovelace"

        comparison = controller.query(
            "q:comparison",
            "Compare Tower Alpha and Tower Beta by height.",
            provider,
            evidence_limit=16,
        )
        assert comparison.disposition is ControllerDisposition.ANSWER
        assert comparison.answer is not None
        assert comparison.answer.text in {"10 m > 9 m.", "9 m < 10 m."}

        list_frame = provider.link_frame(
            QueryFramer().frame("List Tower Alpha and Tower Beta.")
        )
        list_records = provider.retrieve(list_frame, limit=16)
        assert {_canonical_entity_id("Tower Alpha"), _canonical_entity_id("Tower Beta")} <= {
            record.claim.subject_entity_id for record in list_records
        }


def test_workload_is_bounded_and_exact_offsets_reproduce(tmp_path: Path) -> None:
    path = _pack(tmp_path)
    with SQLiteControllerProvider(path) as provider:
        frame = provider.link_frame(QueryFramer().frame("When was Ada Lovelace born?"))
        records = provider.retrieve(frame, limit=8)
        workload = provider.last_workload
        assert workload is not None
        assert workload.requested_limit == 8
        assert workload.evidence_records <= 8
        assert workload.index_probes <= 32
        assert workload.payload_bytes > 0
        assert workload.estimated_sqlite_blocks >= 1
        assert workload.source_document_hashes

        db = sqlite3.connect(path)
        for record in records:
            for span in record.source_spans:
                raw = db.execute(
                    "SELECT raw_wikitext FROM documents WHERE document_id=?",
                    (span.document_id,),
                ).fetchone()[0]
                assert raw[span.char_start : span.char_end] == span.text
                assert span.text_hash == f"sha256:{hashlib.sha256(span.text.encode()).hexdigest()}"
        db.close()


def test_qualification_runner_emits_one_distinct_outcome_per_ablation(tmp_path: Path) -> None:
    roles = tuple(
        RoleIdentity(identity=f"author-{index}", role="author", process_identity=f"proc-{index}")
        for index in range(3)
    )
    case = NaturalQueryCase(
        case_id="case:unknown",
        partition=Partition.EVALUATION,
        question="Who invented Qzzyxx-999?",
        categories=("out_of_corpus",),
        author_identity=roles[0].identity,
        adjudicator_identity="adjudicator",
        accepted_disposition=ControllerDisposition.OUT_OF_CORPUS,
        required_answer_shape=AnswerShape.ENTITY,
        required_facets=(),
    )
    benchmark = FrozenBenchmark(
        author_roles=roles,
        adjudicator_role=RoleIdentity(
            identity="adjudicator", role="adjudicator", process_identity="proc-adjudicator"
        ),
        evaluator_role=RoleIdentity(
            identity="evaluator", role="evaluator", process_identity="proc-evaluator"
        ),
        auditor_role=RoleIdentity(
            identity="auditor", role="auditor", process_identity="proc-auditor"
        ),
        cases=(case,),
        content_sha256="0" * 64,
    )
    runner = runpy.run_path(
        str(Path(__file__).parents[2] / "scripts" / "run_v050_qualification.py")
    )
    outcomes, full_results = runner["_run"](
        benchmark,
        _pack(tmp_path),
        evidence_limit=8,
        case_limit=1,
    )
    assert len(outcomes) == len(AblationSystem)
    assert {(row.case_id, row.system) for row in outcomes} == {
        (case.case_id, system) for system in AblationSystem
    }
    assert len(full_results) == 1
    rag = next(row for row in outcomes if row.system is AblationSystem.VERIFIED_RAG)
    assert rag.disposition is ControllerDisposition.ABSTAIN
