"""Reference orchestration for compact cognition over externally retrieved evidence."""

from __future__ import annotations

from typing import Protocol

from aethersparse.controller.answering import make_answer_plan, realize_plan, select_answer
from aethersparse.controller.disposition import choose_disposition
from aethersparse.controller.evidence import (
    build_evidence_graph,
    evaluate_frame_premise,
    make_evidence_rank_trace,
)
from aethersparse.controller.framing import QueryFramer
from aethersparse.controller.models import (
    ControllerResult,
    EvidenceRecord,
    QueryFrame,
)
from aethersparse.controller.verification import verify_realization


class FrameLinker(Protocol):
    """Lazy adapters may resolve a frame directly from on-disk indexes."""

    def link_frame(self, frame: QueryFrame) -> QueryFrame: ...


class EvidenceProvider(Protocol):
    """Bounded flat-substrate boundary used by progressive SQLite packs."""

    def retrieve(self, frame: QueryFrame, *, limit: int) -> tuple[EvidenceRecord, ...]: ...

    def corpus_coverage(self, frame: QueryFrame) -> bool: ...


class StructuredController:
    """Bounded controller; retrieval remains an external flat-substrate concern."""

    def __init__(self, registry: FrameLinker, framer: QueryFramer | None = None) -> None:
        self.registry = registry
        self.framer = framer or QueryFramer()

    def query(
        self,
        query_id: str,
        query: str,
        provider: EvidenceProvider,
        *,
        prior_entity_ids: tuple[str, ...] = (),
        premise_status: str = "UNKNOWN",
        evidence_limit: int = 64,
    ) -> ControllerResult:
        """Run against a lazy flat pack without materializing corpus-wide objects."""

        if evidence_limit < 1 or evidence_limit > 64:
            raise ValueError("evidence provider limit must remain between one and 64")
        frame = self.registry.link_frame(
            self.framer.frame(query, prior_entity_ids=prior_entity_ids)
        )
        evidence = provider.retrieve(frame, limit=evidence_limit)
        return self._complete(
            query_id,
            frame,
            evidence,
            corpus_coverage=provider.corpus_coverage(frame),
            premise_status=premise_status,
        )

    def answer(
        self,
        query_id: str,
        query: str,
        evidence: tuple[EvidenceRecord, ...],
        *,
        prior_entity_ids: tuple[str, ...] = (),
        corpus_coverage: bool = True,
        premise_status: str = "UNKNOWN",
    ) -> ControllerResult:
        frame = self.registry.link_frame(
            self.framer.frame(query, prior_entity_ids=prior_entity_ids)
        )
        return self._complete(
            query_id,
            frame,
            evidence,
            corpus_coverage=corpus_coverage,
            premise_status=premise_status,
        )

    @staticmethod
    def _complete(
        query_id: str,
        frame: QueryFrame,
        evidence: tuple[EvidenceRecord, ...],
        *,
        corpus_coverage: bool,
        premise_status: str,
    ) -> ControllerResult:
        graph = build_evidence_graph(query_id, frame, evidence)
        if premise_status == "UNKNOWN":
            premise_status = evaluate_frame_premise(frame, graph)
        evidence_trace = make_evidence_rank_trace(evidence, graph)
        selection = select_answer(frame, graph)
        verification = None
        realized = None
        plan = None
        if selection is not None:
            plan = make_answer_plan(selection, graph)
            realized = realize_plan(plan)
            verification = verify_realization(frame, graph, plan, realized)
        disposition, reason = choose_disposition(
            frame,
            graph,
            selection,
            verification,
            corpus_coverage=corpus_coverage,
            premise_status=premise_status,
        )
        if disposition.value != "ANSWER":
            realized = None
        return ControllerResult(
            frame=frame,
            graph=graph,
            evidence_trace=evidence_trace,
            selection=selection,
            plan=plan,
            disposition=disposition,
            answer=realized,
            verification=verification,
            reason=reason,
        )
