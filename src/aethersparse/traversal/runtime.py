"""Budgeted, evolving-state retrieval without per-question programs."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from aethersparse.traversal.corpus import TOKEN_RE, CorpusStore
from aethersparse.traversal.models import (
    AnswerGoal,
    EvidenceNode,
    OperationTrace,
    QueryState,
    TraversalBudget,
    TraversalOperation,
    TraversalResult,
)

QUESTION_WORDS = {"what", "which", "who", "whom", "whose", "when", "where", "why", "how"}
STOP = QUESTION_WORDS | {"the", "a", "an", "is", "was", "were", "are", "did", "do", "does",
                         "to", "of", "in", "on", "and", "or", "for", "from", "with", "that"}


def interpret(query: str, discourse: tuple[str, ...] = ()) -> QueryState:
    words = TOKEN_RE.findall(query)
    lower = [word.casefold() for word in words]
    first = lower[0] if lower else ""
    goal = {
        "who": AnswerGoal.IDENTIFY, "what": AnswerGoal.DESCRIBE,
        "when": AnswerGoal.LOCATE, "where": AnswerGoal.LOCATE,
        "why": AnswerGoal.EXPLAIN, "how": AnswerGoal.EXPLAIN,
    }.get(first, AnswerGoal.VERIFY)
    if any(word in lower for word in ("compare", "difference", "versus")):
        goal = AnswerGoal.COMPARE
    entity_candidates = re.findall(
        r"\b(?:[A-Z][\w'-]*(?:\s+[A-Z0-9][\w'-]*)*)", query
    )
    entities = tuple(
        candidate for candidate in dict.fromkeys(entity_candidates)
        if candidate.casefold() not in QUESTION_WORDS
    )
    unresolved = tuple(
        word for word in lower if word in {"it", "its", "they", "them", "their", "he", "she"}
    )
    times = tuple(re.findall(r"\b(?:1[5-9]\d{2}|20\d{2}|2100)\b", query))
    info_type = first if first in QUESTION_WORDS else "verification"
    return QueryState(
        query=query, requested_information_type=info_type, answer_goal=goal, entities=entities,
        unresolved_references=unresolved, time_context=times,
        required_evidence_facets=("source_support", "entity_resolution")
        if entities else ("source_support",),
        candidate_interpretations=(f"{goal.value}:{info_type}",),
        unknown_spans=tuple(
            word for word in words if word.casefold() not in STOP and len(word) > 2
        ),
        discourse_context=discourse,
    )


class TraversalRuntime:
    def __init__(self, corpus_path: Path):
        self.store = CorpusStore(corpus_path)

    def query(
        self, query: str, *, budget: TraversalBudget | None = None,
        discourse: tuple[str, ...] = (),
    ) -> TraversalResult:
        started = time.perf_counter_ns()
        budget = budget or TraversalBudget()
        state = interpret(query, discourse)
        traces: list[OperationTrace] = []
        evidence: dict[str, EvidenceNode] = {}
        visited_docs: set[str] = set()
        bytes_read = 0
        unresolved = set(state.required_evidence_facets)

        def add_trace(op: TraversalOperation, before: int, outputs: list[str],
                      elapsed: int, input_ids: list[str] | None = None) -> None:
            gain = min(1.0, max(0, len(evidence) - before) / max(1, len(evidence)))
            traces.append(OperationTrace(
                step=len(traces) + 1, operation=op, input_ids=tuple(input_ids or ()),
                output_ids=tuple(outputs), bytes_read=bytes_read,
                elapsed_us=max(0, elapsed // 1000), marginal_evidence_gain=gain,
                unresolved_facets=tuple(sorted(unresolved)),
            ))

        title_started = time.perf_counter_ns()
        title_rows = []
        for entity in state.entities[:3]:
            title_rows.extend(self.store.title_search(entity, 5))
        add_trace(
            TraversalOperation.SEARCH_TITLE_ALIAS, 0,
            [row["document_id"] for row in title_rows], time.perf_counter_ns() - title_started,
        )
        search_started = time.perf_counter_ns()
        rows = self.store.search(query, min(budget.max_chunks, 20))
        add_trace(
            TraversalOperation.SEARCH_LEXICAL, 0, [row["chunk_id"] for row in rows],
            time.perf_counter_ns() - search_started,
        )

        query_terms = {word.casefold() for word in TOKEN_RE.findall(query)} - STOP

        def consume(candidate_rows: list[Any], operation: TraversalOperation) -> None:
            nonlocal bytes_read
            before = len(evidence)
            begun = time.perf_counter_ns()
            output_ids: list[str] = []
            for row in candidate_rows:
                if len(evidence) >= budget.max_chunks or len(visited_docs) >= budget.max_articles:
                    break
                raw = row["raw_text"]
                if bytes_read + len(raw.encode()) > budget.max_bytes:
                    break
                words = {word.casefold() for word in TOKEN_RE.findall(row["normalized_text"])}
                overlap = len(query_terms & words) / max(1, len(query_terms))
                rank = float(row["rank"]) if "rank" in row else 0.0
                score = overlap + max(0.0, min(0.25, -rank / 100))
                sentences = re.split(r"(?<=[.!?])\s+", row["normalized_text"])
                claims = tuple(
                    sentence for sentence in sentences
                    if query_terms & {word.casefold() for word in TOKEN_RE.findall(sentence)}
                )[:3]
                node = EvidenceNode(
                    chunk_id=row["chunk_id"], document_id=row["document_id"],
                    title=row["title"], section_path=row["section_path"],
                    source_revision=row["revision"], source_url=row["source_url"],
                    raw_start=row["raw_start"], raw_end=row["raw_end"], raw_text=raw,
                    normalized_text=row["normalized_text"], score=round(score, 6),
                    temporary_claims=claims, verified=bool(claims and overlap >= 0.2),
                )
                evidence[node.chunk_id] = node
                visited_docs.add(node.document_id)
                bytes_read += len(raw.encode())
                output_ids.append(node.chunk_id)
            add_trace(operation, before, output_ids, time.perf_counter_ns() - begun)

        consume(rows, TraversalOperation.FETCH_SECTION)
        if evidence:
            unresolved.discard("source_support")
        if state.entities and any(
            entity.casefold() in node.normalized_text.casefold()
            for entity in state.entities for node in evidence.values()
        ):
            unresolved.discard("entity_resolution")

        # The next operation is selected from current evidence gaps, not a fixed question program.
        depth = 1
        while (
            unresolved
            and len(traces) < budget.max_steps
            and len(visited_docs) < budget.max_articles
        ):
            linked = self.store.linked_documents(
                list(visited_docs), budget.max_articles - len(visited_docs)
            )
            linked = [doc_id for doc_id in linked if doc_id not in visited_docs]
            if not linked:
                break
            linked_rows = self.store.chunks_for_documents(
                linked, budget.max_chunks - len(evidence)
            )
            consume(linked_rows, TraversalOperation.FOLLOW_HYPERLINK)
            depth += 1
            if evidence:
                unresolved.discard("source_support")
            if state.entities and any(
                entity.casefold() in node.normalized_text.casefold()
                for entity in state.entities for node in evidence.values()
            ):
                unresolved.discard("entity_resolution")

        verify_started = time.perf_counter_ns()
        ranked = sorted(evidence.values(), key=lambda item: (-item.score, item.chunk_id))
        verified = [node for node in ranked if node.verified]
        contradictions: list[str] = []
        answer: str | None = None
        disposition = "ABSTAIN"
        failure: str | None = "INSUFFICIENT_EVIDENCE"
        if not unresolved and verified and verified[0].temporary_claims:
            answer = verified[0].temporary_claims[0]
            disposition = "ANSWER"
            failure = None
        add_trace(
            TraversalOperation.VERIFY_SOURCE_SUPPORT, len(evidence),
            [node.chunk_id for node in verified[:3]], time.perf_counter_ns() - verify_started,
        )
        stop = "SUPPORTED" if disposition == "ANSWER" else (
            "BUDGET_EXHAUSTED" if bytes_read >= budget.max_bytes else "EVIDENCE_GAP"
        )
        elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
        return TraversalResult(
            query_state=state, disposition=disposition, answer=answer, failure_reason=failure,
            citations=tuple(verified[:3]), retrieved_chunks=tuple(ranked),
            operations=tuple(traces), unresolved_facets=tuple(sorted(unresolved)),
            contradictions=tuple(contradictions), stop_reason=stop, retrieval_depth=depth,
            unique_articles_visited=len(visited_docs),
            unique_sections_visited=len(
                {(n.document_id, n.section_path) for n in evidence.values()}
            ),
            source_families=len({n.source_url.split("/")[2] for n in evidence.values()}),
            bytes_read=bytes_read, measured_latency_ms=round(elapsed_ms, 3),
        )
