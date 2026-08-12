"""Reference orchestration for compact cognition over externally retrieved evidence."""

from __future__ import annotations

from collections.abc import Callable
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
        _trace_step: Callable[..., None] | None = None,
    ) -> ControllerResult:
        """Run the deterministic controller pipeline.

        ``_trace_step`` is the Amendment A diagnostic hook: when not None it
        receives (operator_id, arguments=..., result=..., updates=...) per
        stage.  It never influences control flow.
        """
        graph = build_evidence_graph(query_id, frame, evidence)
        if _trace_step is not None:
            _trace_step(
                5,
                arguments={"evidence_records": len(evidence)},
                result={"claims": len(graph.claims)},
                updates={
                    "claims": [claim.object_value for claim in graph.claims],
                    "structured_claims": [claim.model_dump(mode="json") for claim in graph.claims],
                    "source_spans": [span.model_dump(mode="json") for span in graph.source_spans],
                    "required_facets": [str(f) for f in graph.required_facets],
                    "missing_facets": [str(f) for f in graph.missing_facets],
                    "facets_open": [str(f) for f in graph.missing_facets],
                },
            )
        if premise_status == "UNKNOWN":
            premise_status = evaluate_frame_premise(frame, graph)
        evidence_trace = make_evidence_rank_trace(evidence, graph)
        selection = select_answer(frame, graph)
        if _trace_step is not None:
            _trace_step(
                6,
                arguments={"claims": len(graph.claims)},
                result={
                    "selected_claim_ids": (list(selection.selected_claim_ids) if selection else [])
                },
                updates={
                    "selection": (list(selection.selected_claim_ids) if selection else None),
                    "selection_state": (selection.model_dump(mode="json") if selection else {}),
                },
            )
        verification = None
        realized = None
        plan = None
        if selection is not None:
            plan = make_answer_plan(selection, graph)
            if _trace_step is not None:
                _trace_step(
                    7,
                    arguments={"selection": list(selection.selected_claim_ids)},
                    result={"planned_claims": len(plan.planned_claims)},
                    updates={
                        "plan": [c.surface for c in plan.planned_claims],
                        "plan_state": plan.model_dump(mode="json"),
                    },
                )
            realized = realize_plan(plan)
            if _trace_step is not None:
                _trace_step(
                    8,
                    arguments={"planned_claims": len(plan.planned_claims)},
                    result={"text_chars": len(realized.text)},
                    updates={"realized": realized.text},
                )
            verification = verify_realization(frame, graph, plan, realized)
            if _trace_step is not None:
                _trace_step(
                    9,
                    arguments={"realized_chars": len(realized.text)},
                    result={"passed": verification.passed},
                    updates={
                        "verification": verification.passed,
                        "verification_state": verification.model_dump(mode="json"),
                    },
                )
        disposition, reason = choose_disposition(
            frame,
            graph,
            selection,
            verification,
            corpus_coverage=corpus_coverage,
            premise_status=premise_status,
        )
        if _trace_step is not None:
            _trace_step(
                10,
                arguments={"premise_status": premise_status},
                result={"disposition": str(disposition), "reason": reason},
                updates={
                    "disposition": str(disposition),
                    "disposition_state": {
                        "disposition": str(disposition),
                        "reason": reason,
                    },
                },
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
