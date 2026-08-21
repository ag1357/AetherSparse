"""Input/state interpretation into COG v1 without performing policy selection."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from aethersparse.cognitive.models import (
    CognitiveObligationGraph,
    Evidence,
    FrontierItem,
    Goal,
    GoalType,
    Hypothesis,
    InputType,
    Invariant,
    InvariantStatus,
    Obligation,
    ObligationStatus,
    ObservedState,
    Provenance,
    ProvenanceKind,
    UnresolvedVariable,
)
from aethersparse.controller.framing import QueryFramer
from aethersparse.controller.models import AnswerShape, QueryFrame


class FrameAddressResolver(Protocol):
    """Existing linker/Semantic Address consumers satisfy this narrow adapter."""

    def link_frame(self, frame: QueryFrame) -> QueryFrame: ...


class InterpretationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_type: InputType
    intent: str = Field(min_length=1, max_length=64)
    graph: CognitiveObligationGraph
    query_frame: QueryFrame | None = None
    negated: bool = False
    premise_relationships: tuple[str, ...] = ()
    candidate_action_classes: tuple[str, ...] = ()


def _slug(value: str, length: int = 12) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def _provenance(kind: ProvenanceKind, source_id: str, detail: str = "") -> Provenance:
    return Provenance(kind=kind, source_id=source_id, detail=detail)


def _obligation(
    graph_id: str,
    index: int,
    kind: str,
    description: str,
    provenance: Provenance,
    *,
    status: ObligationStatus = ObligationStatus.OPEN,
) -> Obligation:
    return Obligation(
        obligation_id=f"{graph_id}:o{index}",
        goal_id=f"{graph_id}:goal",
        kind=kind,
        description=description,
        status=status,
        provenance=provenance,
    )


class InputStateInterpreter:
    """Deterministic v1 meaning interpreter; it never selects the next operation."""

    def __init__(
        self,
        *,
        framer: QueryFramer | None = None,
        address_resolver: FrameAddressResolver | None = None,
    ) -> None:
        self.framer = framer or QueryFramer()
        self.address_resolver = address_resolver

    def interpret(
        self,
        input_type: InputType,
        payload: str | Mapping[str, object],
        *,
        input_id: str,
        prior_entity_ids: tuple[str, ...] = (),
    ) -> InterpretationResult:
        if input_type is InputType.NATURAL_LANGUAGE:
            if not isinstance(payload, str):
                raise TypeError("NATURAL_LANGUAGE payload must be text")
            return self._natural_language(payload, input_id, prior_entity_ids)
        if isinstance(payload, str):
            raise TypeError("STRUCTURED_EXTERNAL_EVENT payload must be a mapping")
        return self._external_event(payload, input_id)

    def _natural_language(
        self, text: str, input_id: str, prior_entity_ids: tuple[str, ...]
    ) -> InterpretationResult:
        query = " ".join(text.strip().split())
        if not query:
            raise ValueError("natural-language input must not be empty")
        graph_id = f"cog:nl:{_slug(input_id + ':' + query)}"
        source = _provenance(ProvenanceKind.USER_INPUT, input_id, "exact user utterance")
        frame = self.framer.frame(query, prior_entity_ids=prior_entity_ids)
        if self.address_resolver is not None:
            frame = self.address_resolver.link_frame(frame)

        obligations: list[Obligation] = []
        evidence: list[Evidence] = []
        hypotheses: list[Hypothesis] = []
        unresolved: list[UnresolvedVariable] = []
        frontier: list[FrontierItem] = []

        entity_ids = set(frame.candidate_entity_ids)
        ambiguous_candidates: list[tuple[str, str, int]] = []
        for mention in frame.entity_mentions:
            if mention.selected_entity_id:
                entity_ids.add(mention.selected_entity_id)
                evidence.append(
                    Evidence(
                        evidence_id=f"{graph_id}:e-entity-{len(evidence)}",
                        subject=mention.surface,
                        predicate="semantic_address",
                        value=mention.selected_entity_id,
                        provenance=_provenance(
                            ProvenanceKind.INFERENCE,
                            "semantic-address-v2",
                            mention.resolution_method.value,
                        ),
                    )
                )
            elif mention.copy_status == "ambiguous":
                for candidate in mention.candidates[:8]:
                    ambiguous_candidates.append(
                        (
                            candidate.entity_id,
                            candidate.title,
                            round(candidate.confidence * 1000),
                        )
                    )

        subject_resolved = bool(entity_ids) and not ambiguous_candidates
        obligations.append(
            _obligation(
                graph_id,
                len(obligations),
                "IDENTIFY_SUBJECT",
                "Bind the requested subject to a canonical entity.",
                source,
                status=(
                    ObligationStatus.SATISFIED if subject_resolved else ObligationStatus.OPEN
                ),
            )
        )
        subject_obligation = obligations[-1].obligation_id
        relation_resolved = bool(frame.requested_relation_families)
        obligations.append(
            _obligation(
                graph_id,
                len(obligations),
                "ESTABLISH_RELATION",
                "Establish the requested relation family.",
                source,
                status=(
                    ObligationStatus.SATISFIED if relation_resolved else ObligationStatus.OPEN
                ),
            )
        )
        obligations.extend(
            (
                _obligation(
                    graph_id,
                    len(obligations),
                    "LOCATE_GROUNDED_CLAIM",
                    "Locate a provenance-bound claim for the subject and relation.",
                    source,
                ),
                _obligation(
                    graph_id,
                    len(obligations) + 1,
                    "MATCH_ANSWER_TYPE",
                    f"Confirm that the value has answer shape {frame.answer_shape.value}.",
                    source,
                ),
                _obligation(
                    graph_id,
                    len(obligations) + 2,
                    "BIND_CLAIM_TO_SUBJECT",
                    "Verify that the selected claim belongs to the requested subject.",
                    source,
                ),
                _obligation(
                    graph_id,
                    len(obligations) + 3,
                    "VERIFY_EVIDENCE",
                    "Require exact verifier acceptance before answer completion.",
                    _provenance(ProvenanceKind.SYSTEM_RULE, "exact-verifier"),
                ),
            )
        )
        constraint_groups: tuple[tuple[str, Sequence[str]], ...] = (
            ("TEMPORAL_CONSTRAINT", frame.temporal_constraints),
            ("LOCATION_CONSTRAINT", frame.location_constraints),
            ("ATTRIBUTION_CONSTRAINT", frame.attribution_constraints),
        )
        for kind, values in constraint_groups:
            if values:
                obligations.append(
                    _obligation(
                        graph_id,
                        len(obligations),
                        kind,
                        f"Satisfy {kind.casefold()}: {', '.join(values)}.",
                        source,
                    )
                )

        if ambiguous_candidates:
            for index, (entity_id, title, confidence) in enumerate(ambiguous_candidates):
                hypotheses.append(
                    Hypothesis(
                        hypothesis_id=f"{graph_id}:h{index}",
                        kind="ENTITY_INTERPRETATION",
                        interpretation=f"subject={title} ({entity_id})",
                        confidence_milli=confidence,
                        provenance=_provenance(
                            ProvenanceKind.INFERENCE, "semantic-address-v2", "competing address"
                        ),
                        unresolved_obligation_ids=(subject_obligation,),
                    )
                )
            unresolved.append(
                UnresolvedVariable(
                    variable_id=f"{graph_id}:u-subject",
                    kind="SUBJECT_ENTITY",
                    description="Competing canonical entity hypotheses require resolution.",
                    candidate_ids=tuple(item[0] for item in ambiguous_candidates),
                    required_by_obligation_ids=(subject_obligation,),
                )
            )
        elif not subject_resolved:
            candidate_ids = tuple(
                item
                for reference in frame.discourse_references
                for item in reference.antecedent_entity_ids
            )
            unresolved.append(
                UnresolvedVariable(
                    variable_id=f"{graph_id}:u-subject",
                    kind="SUBJECT_ENTITY",
                    description="No unambiguous subject address is currently bound.",
                    candidate_ids=candidate_ids,
                    required_by_obligation_ids=(subject_obligation,),
                )
            )
        if frame.clarification_need:
            unresolved.append(
                UnresolvedVariable(
                    variable_id=f"{graph_id}:u-reference",
                    kind="DISCOURSE_REFERENCE",
                    description="The utterance contains an unresolved or incomplete reference.",
                    candidate_ids=tuple(entity_ids),
                    required_by_obligation_ids=(subject_obligation,),
                )
            )

        frontier.append(
            FrontierItem(
                frontier_id=f"{graph_id}:f-claims",
                kind="CLAIM_SEARCH",
                target="|".join(sorted(entity_ids)) or query,
                priority=90,
                obligation_ids=tuple(
                    item.obligation_id
                    for item in obligations
                    if item.status is ObligationStatus.OPEN
                ),
            )
        )
        invariants = (
            Invariant(
                invariant_id=f"{graph_id}:i-subject",
                kind="SUBJECT_BINDING",
                description="An answer claim must remain bound to the requested subject.",
                provenance=_provenance(ProvenanceKind.SYSTEM_RULE, "cog-v1"),
            ),
            Invariant(
                invariant_id=f"{graph_id}:i-provenance",
                kind="PROVENANCE_INTEGRITY",
                description="Grounded evidence provenance may not be discarded or rewritten.",
                provenance=_provenance(ProvenanceKind.SYSTEM_RULE, "cog-v1"),
            ),
            Invariant(
                invariant_id=f"{graph_id}:i-verifier",
                kind="VERIFIER_REQUIRED",
                description="HALT_SUCCESS requires exact verifier acceptance.",
                provenance=_provenance(ProvenanceKind.SYSTEM_RULE, "exact-verifier"),
            ),
        )
        graph = CognitiveObligationGraph(
            cog_id=graph_id,
            goals=(
                Goal(
                    goal_id=f"{graph_id}:goal",
                    goal_type=GoalType.QUESTION_ANSWERING,
                    description=f"Produce a grounded answer to: {query}",
                    priority=90,
                    provenance=source,
                ),
            ),
            obligations=tuple(obligations),
            invariants=invariants,
            hypotheses=tuple(hypotheses),
            evidence=tuple(evidence),
            unresolved=tuple(dict.fromkeys(unresolved)),
            frontier=tuple(frontier),
        )
        folded = query.casefold()
        intent = self._intent(frame)
        action_classes = (
            ("ASK_CLARIFICATION",) if graph.unresolved and ambiguous_candidates else ()
        ) + ("SEARCH_KNOWLEDGE", "INSPECT_CLAIM", "VERIFY")
        return InterpretationResult(
            input_type=InputType.NATURAL_LANGUAGE,
            intent=intent,
            graph=graph,
            query_frame=frame,
            negated=bool(re.search(r"\b(no|not|never|without|except)\b", folded)),
            premise_relationships=frame.premise_claims,
            candidate_action_classes=action_classes,
        )

    @staticmethod
    def _intent(frame: QueryFrame) -> str:
        if frame.clarification_need:
            return "RESOLVE_AMBIGUITY"
        if frame.answer_shape is AnswerShape.COMPARISON:
            return "COMPARE"
        if frame.answer_shape is AnswerShape.VERIFICATION:
            return "VERIFY_PREMISE"
        return "ANSWER_QUESTION"

    def _external_event(
        self, payload: Mapping[str, object], input_id: str
    ) -> InterpretationResult:
        event_type = str(payload.get("event_type", "")).strip()
        entity = str(payload.get("entity", "")).strip()
        if not event_type or not entity:
            raise ValueError("external event requires non-empty event_type and entity")
        graph_id = f"cog:event:{_slug(input_id + ':' + event_type + ':' + entity)}"
        observed_source = _provenance(ProvenanceKind.OBSERVATION, input_id, event_type)
        inferred_source = _provenance(ProvenanceKind.INFERENCE, "external-event-interpreter-v1")
        attributes = tuple(
            sorted(
                (str(key), str(value))
                for key, value in payload.items()
                if key not in {"event_type", "entity"}
            )
        )
        observed = ObservedState(
            state_id=f"{graph_id}:s0",
            event_type=event_type,
            entity=entity,
            attributes=attributes,
            provenance=observed_source,
        )
        evidence = tuple(
            Evidence(
                evidence_id=f"{graph_id}:e{index}",
                subject=entity,
                predicate=key,
                value=value,
                provenance=observed_source,
            )
            for index, (key, value) in enumerate(attributes)
        )
        evidence_ids = {
            key: item.evidence_id for (key, _), item in zip(attributes, evidence, strict=True)
        }
        obligations: list[Obligation] = []
        invariants: list[Invariant] = []
        hypotheses: list[Hypothesis] = []
        unresolved: list[UnresolvedVariable] = []
        actions = ["RECORD_OBSERVATION", "VERIFY_CONSTRAINTS"]

        if event_type == "ACTUATOR_STATUS":
            self._interpret_actuator(
                graph_id,
                payload,
                observed_source,
                inferred_source,
                obligations,
                invariants,
                hypotheses,
                unresolved,
                actions,
                evidence_ids,
            )
        if not obligations:
            obligations.append(
                _obligation(
                    graph_id,
                    0,
                    "ASSESS_EXTERNAL_EVENT",
                    f"Assess {event_type} state without rewriting the observation.",
                    observed_source,
                )
            )
        graph = CognitiveObligationGraph(
            cog_id=graph_id,
            goals=(
                Goal(
                    goal_id=f"{graph_id}:goal",
                    goal_type=(
                        GoalType.EMBODIED_CONTROL
                        if event_type == "ACTUATOR_STATUS"
                        else GoalType.GENERAL
                    ),
                    description=f"Interpret externally observed {event_type} for {entity}.",
                    priority=95,
                    provenance=observed_source,
                ),
            ),
            obligations=tuple(obligations),
            invariants=tuple(invariants),
            hypotheses=tuple(hypotheses),
            evidence=evidence,
            unresolved=tuple(unresolved),
            frontier=(
                FrontierItem(
                    frontier_id=f"{graph_id}:f0",
                    kind="EXTERNAL_STATE_ASSESSMENT",
                    target=entity,
                    priority=95,
                    obligation_ids=tuple(item.obligation_id for item in obligations),
                ),
            ),
            observed_state=(observed,),
        )
        return InterpretationResult(
            input_type=InputType.STRUCTURED_EXTERNAL_EVENT,
            intent="ASSESS_EXTERNAL_STATE",
            graph=graph,
            candidate_action_classes=tuple(actions),
        )

    @staticmethod
    def _interpret_actuator(
        graph_id: str,
        payload: Mapping[str, object],
        observed_source: Provenance,
        inferred_source: Provenance,
        obligations: list[Obligation],
        invariants: list[Invariant],
        hypotheses: list[Hypothesis],
        unresolved: list[UnresolvedVariable],
        actions: list[str],
        evidence_ids: Mapping[str, str],
    ) -> None:
        temperature = _optional_float(payload, "temperature")
        maximum = _optional_float(payload, "maximum_temperature")
        position = _optional_float(payload, "observed_position")
        requested = _optional_float(payload, "requested_position")
        tolerance = _optional_float(payload, "position_tolerance")
        if maximum is not None:
            violated = temperature is not None and temperature > maximum
            invariants.append(
                Invariant(
                    invariant_id=f"{graph_id}:i-temperature",
                    kind="PHYSICAL_HARD_LIMIT",
                    description=f"Temperature must not exceed {maximum:g}.",
                    status=InvariantStatus.VIOLATED if violated else InvariantStatus.ACTIVE,
                    provenance=observed_source,
                    violation_evidence_ids=(
                        (evidence_ids["temperature"],) if violated else ()
                    ),
                )
            )
            if violated:
                obligations.append(
                    _obligation(
                        graph_id,
                        len(obligations),
                        "RESOLVE_THERMAL_ANOMALY",
                        "Reach a safe thermal state before further actuation.",
                        inferred_source,
                    )
                )
                hypotheses.append(
                    Hypothesis(
                        hypothesis_id=f"{graph_id}:h-thermal",
                        kind="THERMAL_ANOMALY",
                        interpretation="Thermal fault is likely.",
                        confidence_milli=950,
                        provenance=inferred_source,
                        unresolved_obligation_ids=(obligations[-1].obligation_id,),
                    )
                )
                actions.extend(("BLOCK_ACTUATION", "INSPECT_THERMAL_STATE"))
        if position is not None and requested is not None:
            max_error = 0.0 if tolerance is None else tolerance
            error = abs(position - requested)
            status = (
                ObligationStatus.SATISFIED if error <= max_error else ObligationStatus.OPEN
            )
            obligations.append(
                _obligation(
                    graph_id,
                    len(obligations),
                    "REACH_REQUESTED_POSITION",
                    f"Position error {error:g} must be <= {max_error:g}.",
                    observed_source,
                    status=status,
                )
            )
            if status is ObligationStatus.OPEN:
                actions.append("CORRECT_POSITION")
        elif position is None or requested is None:
            obligation = _obligation(
                graph_id,
                len(obligations),
                "ESTABLISH_POSITION_ERROR",
                "Observed and requested position are required.",
                observed_source,
                status=ObligationStatus.BLOCKED,
            )
            obligations.append(obligation)
            unresolved.append(
                UnresolvedVariable(
                    variable_id=f"{graph_id}:u-position",
                    kind="MISSING_OBSERVATION",
                    description="Position comparison lacks an observed or requested value.",
                    required_by_obligation_ids=(obligation.obligation_id,),
                )
            )


def _optional_float(payload: Mapping[str, object], key: str) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be numeric")
    return float(value)
