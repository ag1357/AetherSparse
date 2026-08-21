"""Integrated V13 natural-input to grounded-answer vertical slice.

The service composes the qualified V12 address index, bounded conversation
state, the learned legal-mask controller, exact micro-operations, the existing
verifier, and the evidence-copy realizer.  Knowledge is supplied as immutable
grounded records; no fact is stored in policy weights.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from aethersparse.agent.contracts import (
    AnswerKind,
    AnswerValue,
    ConversationActionKind,
    EntityHypothesis,
    EvidenceHandle,
    VerifiedAnswerPlan,
)
from aethersparse.agent.conversation import ConversationEngine
from aethersparse.agent.realization import GroundedAnswerRealizer, GroundingError
from aethersparse.agent.session import SessionStore
from aethersparse.controller.fuzzy_address import (
    AddressSurfaceRecord,
    FuzzyAddressIndex,
    FuzzyChannel,
    union_address_results,
)
from aethersparse.controller.learned_policy import MaskedLinearPolicy
from aethersparse.controller.micro_ops import MicroState, execute_action


class VerticalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GroundedKnowledgeRecord(VerticalModel):
    """One deployable claim bound to a canonical address and exact evidence."""

    entity_id: str
    canonical_title: str
    address_surfaces: tuple[str, ...] = Field(min_length=1)
    relation: str
    relation_terms: tuple[str, ...] = Field(min_length=1)
    relation_text: str
    answer_kind: AnswerKind
    values: tuple[str, ...] = Field(min_length=1)
    evidence: EvidenceHandle
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class AetherCoreRequest(VerticalModel):
    session_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=2048)


class AetherCoreResponse(VerticalModel):
    disposition: Literal["ANSWER", "CLARIFY", "ABSTAIN", "CANCELLED", "RESET"]
    session_id: str
    text: str
    grounded: bool
    evidence_handle_ids: tuple[str, ...] = ()
    semantic_address_candidate_ids: tuple[str, ...] = ()
    controller_operations: tuple[int, ...] = ()
    verifier_accepted: bool = False
    failure_reason: str | None = None


def load_qualified_policy(report: dict[str, object]) -> MaskedLinearPolicy:
    """Load the compact selected policy embedded in the qualification report."""

    policy = report.get("policy")
    if not isinstance(policy, dict):
        raise ValueError("policy qualification lacks a policy object")
    serialized = policy.get("serialized_model")
    if not isinstance(serialized, dict):
        raise ValueError("policy qualification lacks a serialized selected model")
    return MaskedLinearPolicy.model_validate(serialized)


def load_qualified_policy_json(payload: str | bytes) -> MaskedLinearPolicy:
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("policy qualification must be a JSON object")
    return load_qualified_policy(value)


class AetherCoreVerticalSlice:
    """Runnable bounded AetherCore service with persistent session state."""

    def __init__(
        self,
        records: Sequence[GroundedKnowledgeRecord],
        policy: MaskedLinearPolicy,
        session_store: SessionStore,
        *,
        max_controller_steps: int = 12,
    ) -> None:
        if not records:
            raise ValueError("the vertical slice requires at least one grounded record")
        if max_controller_steps < 1 or max_controller_steps > 64:
            raise ValueError("max_controller_steps must be in [1,64]")
        self.records = tuple(records)
        self.policy = policy
        self.conversation = ConversationEngine(session_store)
        self.realizer = GroundedAnswerRealizer()
        self.max_controller_steps = max_controller_steps
        surfaces = []
        for record_index, record in enumerate(self.records):
            for surface_index, surface in enumerate(record.address_surfaces):
                surfaces.append(
                    AddressSurfaceRecord(
                        surface=surface,
                        entity_id=record.entity_id,
                        canonical_title=record.canonical_title,
                        support_count=1,
                        source_document_count=1,
                        source_document_ids=(record.evidence.canonical_object_id,),
                        support_provenance_ids=(
                            f"v13-record:{record_index}:surface:{surface_index}",
                        ),
                        source_channels=("title",),
                        source_provenance=(record.evidence.handle_id,),
                    )
                )
        self.address_index = FuzzyAddressIndex(surfaces)

    @staticmethod
    def _tokens(value: str) -> frozenset[str]:
        return frozenset(re.findall(r"[^\W_]+", value.casefold()))

    def _relation(self, text: str) -> str | None:
        tokens = self._tokens(text)
        scores: dict[str, tuple[int, int, str]] = {}
        for record in self.records:
            matched = sum(term.casefold() in tokens for term in record.relation_terms)
            if not matched:
                continue
            key = (matched, len(record.relation_terms), record.relation)
            if key > scores.get(record.relation, (0, 0, "")):
                scores[record.relation] = key
        return max(scores, key=lambda item: scores[item], default=None)

    def _address(self, text: str) -> tuple[EntityHypothesis, ...]:
        results = (
            self.address_index.lookup(
                text,
                FuzzyChannel.EXACT,
                address_cap=32,
                postings_cap=16_384,
                char_score_threshold=0.8,
            ),
            self.address_index.lookup(
                text,
                FuzzyChannel.CHAR_NGRAM,
                address_cap=32,
                postings_cap=16_384,
                char_score_threshold=0.8,
            ),
        )
        union = union_address_results(results, address_cap=32)
        return tuple(
            EntityHypothesis(
                entity_id=item.entity_id,
                label=item.canonical_title,
                confidence=max(0.0, min(1.0, item.best_score)),
                surface=item.matched_surfaces[0],
            )
            for item in union.address_proposals
        )

    @staticmethod
    def _shape(kind: AnswerKind) -> str:
        return {
            AnswerKind.DATE: "date",
            AnswerKind.QUANTITY: "quantity",
            AnswerKind.LIST: "list",
            AnswerKind.COMPARISON: "comparison",
            AnswerKind.QUOTATION: "quotation",
        }.get(kind, "definition")

    @staticmethod
    def _claim_id(record: GroundedKnowledgeRecord, index: int) -> str:
        digest = hashlib.sha256(
            f"{record.entity_id}\0{record.relation}\0{index}\0{record.values[index]}".encode()
        ).hexdigest()[:24]
        return f"v13:claim:{digest}"

    def _workspace(
        self,
        request: AetherCoreRequest,
        entity_ids: tuple[str, ...],
        relation: str,
    ) -> tuple[MicroState, dict[str, GroundedKnowledgeRecord]]:
        selected = [
            record
            for record in self.records
            if record.entity_id in entity_ids and record.relation == relation
        ]
        claims: list[dict[str, object]] = []
        spans: dict[str, dict[str, object]] = {}
        by_claim: dict[str, GroundedKnowledgeRecord] = {}
        for record in selected:
            handle = record.evidence
            span_id = handle.handle_id
            spans[span_id] = {
                "span_id": span_id,
                "document_id": handle.canonical_object_id,
                "source_title": record.canonical_title,
                "source_revision": handle.source_version,
                "source_url": handle.source_locator,
                "source_family": handle.source_namespace,
                "source_class": "CORPUS",
                "char_start": 0,
                "char_end": len(handle.exact_text),
                "text": handle.exact_text,
                "text_hash": hashlib.sha256(handle.exact_text.encode()).hexdigest(),
            }
            for index, value in enumerate(record.values):
                claim_id = self._claim_id(record, index)
                by_claim[claim_id] = record
                claims.append(
                    {
                        "claim_id": claim_id,
                        "subject_entity_id": record.entity_id,
                        "relation_family": record.relation,
                        "object_value": value,
                        "answer_shape": self._shape(record.answer_kind),
                        "source_span_ids": [span_id],
                        "grounding": "CORPUS_GROUNDED",
                        "confidence": record.confidence,
                    }
                )
        answer_shape = self._shape(selected[0].answer_kind) if selected else "unknown"
        required_facets = ["subject", "relation", "object", "source"]
        if answer_shape == "quantity":
            required_facets = ["subject", "relation", "quantity", "source"]
        elif answer_shape == "quotation":
            required_facets = ["subject", "relation", "quotation", "source"]
        state = MicroState(
            case_id=f"live:{request.session_id}",
            frame={
                "normalized_query": request.text,
                "candidate_entity_ids": list(entity_ids),
                "requested_relation_families": [relation],
                "answer_shape": answer_shape,
                "entity_mentions": [],
                "required_facets": required_facets,
                "temporal_constraints": [],
                "location_constraints": [],
                "attribution_constraints": [],
                "comparison_targets": [],
                "premise_claims": [],
                "discourse_references": [],
                "uncertainty": 0.0,
                "clarification_need": False,
            },
            claims=tuple(claims),
            source_spans=tuple(spans.values()),
        )
        return state, by_claim

    def query(self, request: AetherCoreRequest) -> AetherCoreResponse:
        before = self.conversation.store.load(request.session_id)
        relation = self._relation(request.text)
        if relation is None and before.pending_clarification is not None:
            relation = self._relation(before.pending_clarification.original_query)
        candidates = self._address(request.text)
        _session, action = self.conversation.accept(
            request.session_id,
            request.text,
            candidates=candidates,
            relation=relation,
        )
        candidate_ids = tuple(item.entity_id for item in candidates)
        if action.kind is ConversationActionKind.RESET:
            return AetherCoreResponse(
                disposition="RESET",
                session_id=request.session_id,
                text="Conversation state reset.",
                grounded=False,
            )
        if action.kind is ConversationActionKind.CANCEL:
            return AetherCoreResponse(
                disposition="CANCELLED",
                session_id=request.session_id,
                text="Cancelled.",
                grounded=False,
            )
        if action.kind is ConversationActionKind.ASK_CLARIFICATION:
            assert action.clarification is not None
            plan = VerifiedAnswerPlan(
                plan_id=f"clarify:{request.session_id}",
                kind=AnswerKind.CLARIFICATION,
                clarification=action.clarification,
                verifier_status="ACCEPTED",
            )
            answer = self.realizer.realize(plan, ())
            return AetherCoreResponse(
                disposition="CLARIFY",
                session_id=request.session_id,
                text=answer.text,
                grounded=True,
                semantic_address_candidate_ids=candidate_ids,
                verifier_accepted=True,
            )
        if not action.entity_ids or action.relation is None:
            return AetherCoreResponse(
                disposition="ABSTAIN",
                session_id=request.session_id,
                text="I do not have a grounded address and relation for that request.",
                grounded=False,
                semantic_address_candidate_ids=candidate_ids,
                failure_reason="UNRESOLVED_ADDRESS_OR_RELATION",
            )
        state, by_claim = self._workspace(request, action.entity_ids, action.relation)
        if not state.claims:
            return AetherCoreResponse(
                disposition="ABSTAIN",
                session_id=request.session_id,
                text="I found the entity, but no exact grounded claim for that relation.",
                grounded=False,
                semantic_address_candidate_ids=candidate_ids,
                failure_reason="VALUE_UNAVAILABLE",
            )
        operations: list[int] = []
        for _step in range(self.max_controller_steps):
            selected_action = self.policy.select(state, argument_cap=64)
            if selected_action is None:
                break
            operations.append(selected_action.operation_id)
            state = execute_action(state, selected_action)
            if state.terminal is not None:
                break
        if state.terminal != "ANSWER" or not state.verification_passed:
            return AetherCoreResponse(
                disposition="ABSTAIN",
                session_id=request.session_id,
                text="The learned controller did not produce a verifier-accepted answer plan.",
                grounded=False,
                semantic_address_candidate_ids=candidate_ids,
                controller_operations=tuple(operations),
                failure_reason=state.terminal or "CONTROLLER_INCOMPLETE",
            )
        claim_records = [by_claim[item] for item in state.plan_claim_ids if item in by_claim]
        if not claim_records:
            raise RuntimeError("verified plan lost its grounded claim binding")
        record = claim_records[0]
        values = tuple(
            AnswerValue(text=value, evidence_handle_ids=(record.evidence.handle_id,))
            for value in state.plan_values
        )
        plan_digest = hashlib.sha256((request.session_id + request.text).encode()).hexdigest()[:24]
        plan_id = f"plan:{plan_digest}"
        plan = VerifiedAnswerPlan(
            plan_id=plan_id,
            kind=record.answer_kind,
            subject=record.canonical_title,
            relation=record.relation_text,
            values=values,
            verifier_status="ACCEPTED",
        )
        try:
            answer = self.realizer.realize(plan, (record.evidence,))
        except GroundingError as error:
            return AetherCoreResponse(
                disposition="ABSTAIN",
                session_id=request.session_id,
                text="The verified plan could not be copied from exact evidence.",
                grounded=False,
                semantic_address_candidate_ids=candidate_ids,
                controller_operations=tuple(operations),
                failure_reason=str(error),
            )
        self.conversation.record_answer(
            request.session_id,
            plan_id=plan_id,
            text=answer.text,
            evidence_handles=(record.evidence,),
        )
        return AetherCoreResponse(
            disposition="ANSWER",
            session_id=request.session_id,
            text=answer.text,
            grounded=True,
            evidence_handle_ids=answer.evidence_handle_ids,
            semantic_address_candidate_ids=candidate_ids,
            controller_operations=tuple(operations),
            verifier_accepted=True,
        )
