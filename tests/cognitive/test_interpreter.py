from __future__ import annotations

from aethersparse.cognitive.interpreter import InputStateInterpreter
from aethersparse.cognitive.models import InputType, InvariantStatus, ProvenanceKind
from aethersparse.controller.linking import EntityRegistry
from aethersparse.controller.models import CanonicalEntity


def _interpreter() -> InputStateInterpreter:
    registry = EntityRegistry(
        (
            CanonicalEntity(
                entity_id="entity:alan_turing",
                title="Alan Turing",
                entity_types=("person",),
                aliases=("Turing",),
                relation_families=("birth", "definition"),
            ),
            CanonicalEntity(
                entity_id="entity:mercury_planet",
                title="Mercury",
                entity_types=("planet",),
                aliases=("Mercury",),
                relation_families=("location", "definition"),
            ),
            CanonicalEntity(
                entity_id="entity:mercury_element",
                title="Mercury",
                entity_types=("element",),
                aliases=("Mercury",),
                relation_families=("quantity", "definition"),
            ),
        )
    )
    return InputStateInterpreter(address_resolver=registry)


def test_qa_interpretation_emits_problem_obligations_not_an_answer() -> None:
    result = _interpreter().interpret(
        InputType.NATURAL_LANGUAGE,
        "Where was Alan Turing born?",
        input_id="turn-1",
    )
    kinds = {item.kind: item.status.value for item in result.graph.obligations}
    assert kinds["IDENTIFY_SUBJECT"] == "SATISFIED"
    assert kinds["ESTABLISH_RELATION"] == "SATISFIED"
    assert kinds["LOCATE_GROUNDED_CLAIM"] == "OPEN"
    assert kinds["MATCH_ANSWER_TYPE"] == "OPEN"
    assert kinds["BIND_CLAIM_TO_SUBJECT"] == "OPEN"
    assert kinds["VERIFY_EVIDENCE"] == "OPEN"
    assert result.query_frame is not None
    assert result.query_frame.answer_shape.value == "entity"
    assert result.graph.invariants[-1].kind == "VERIFIER_REQUIRED"


def test_mercury_ambiguity_remains_competing_hypotheses() -> None:
    result = _interpreter().interpret(
        InputType.NATURAL_LANGUAGE,
        "What is Mercury?",
        input_id="turn-mercury",
    )
    assert len(result.graph.hypotheses) == 2
    assert len({item.confidence_milli for item in result.graph.hypotheses}) == 1
    assert result.graph.unresolved[0].kind == "SUBJECT_ENTITY"
    assert result.candidate_action_classes[0] == "ASK_CLARIFICATION"
    assert all(item.active for item in result.graph.hypotheses)


def test_prior_entity_resolves_follow_up_pronoun_without_cold_retrieval() -> None:
    result = _interpreter().interpret(
        InputType.NATURAL_LANGUAGE,
        "Where was he born?",
        input_id="turn-2",
        prior_entity_ids=("entity:alan_turing",),
    )
    subject = next(
        item for item in result.graph.obligations if item.kind == "IDENTIFY_SUBJECT"
    )
    assert subject.status.value == "SATISFIED"
    assert result.query_frame is not None
    assert result.query_frame.discourse_references[0].antecedent_entity_ids == (
        "entity:alan_turing",
    )


def test_negation_and_premise_are_explicit_interpretation_outputs() -> None:
    result = _interpreter().interpret(
        InputType.NATURAL_LANGUAGE,
        "Was Alan Turing not born in Paris?",
        input_id="turn-premise",
    )
    assert result.negated
    assert result.intent == "VERIFY_PREMISE"
    assert result.premise_relationships == ("Was Alan Turing not born in Paris",)


def test_actuator_observations_and_fault_inference_have_distinct_provenance() -> None:
    result = _interpreter().interpret(
        InputType.STRUCTURED_EXTERNAL_EVENT,
        {
            "event_type": "ACTUATOR_STATUS",
            "entity": "joint_4",
            "observed_position": 3.5,
            "requested_position": 5.0,
            "position_tolerance": 0.1,
            "temperature": 82.0,
            "maximum_temperature": 75.0,
            "torque": 0.7,
        },
        input_id="sensor-frame-7",
    )
    graph = result.graph
    assert graph.observed_state[0].provenance.kind is ProvenanceKind.OBSERVATION
    assert all(item.provenance.kind is ProvenanceKind.OBSERVATION for item in graph.evidence)
    fault = next(item for item in graph.hypotheses if item.kind == "THERMAL_ANOMALY")
    assert fault.provenance.kind is ProvenanceKind.INFERENCE
    limit = next(item for item in graph.invariants if item.kind == "PHYSICAL_HARD_LIMIT")
    assert limit.status is InvariantStatus.VIOLATED
    temperature = next(item for item in graph.evidence if item.predicate == "temperature")
    assert limit.violation_evidence_ids == (temperature.evidence_id,)
    assert "BLOCK_ACTUATION" in result.candidate_action_classes
    assert any(item.kind == "REACH_REQUESTED_POSITION" for item in graph.obligations)


def test_external_event_missing_position_is_blocked_not_invented() -> None:
    result = _interpreter().interpret(
        InputType.STRUCTURED_EXTERNAL_EVENT,
        {
            "event_type": "ACTUATOR_STATUS",
            "entity": "joint_4",
            "temperature": 40.0,
            "maximum_temperature": 75.0,
        },
        input_id="sensor-frame-8",
    )
    obligation = next(
        item for item in result.graph.obligations if item.kind == "ESTABLISH_POSITION_ERROR"
    )
    assert obligation.status.value == "BLOCKED"
    assert result.graph.unresolved[0].kind == "MISSING_OBSERVATION"


def test_generic_external_event_remains_supported() -> None:
    result = _interpreter().interpret(
        InputType.STRUCTURED_EXTERNAL_EVENT,
        {"event_type": "BATTERY_STATUS", "entity": "pack_1", "voltage": 3.72},
        input_id="battery-1",
    )
    assert result.graph.observed_state[0].event_type == "BATTERY_STATUS"
    assert result.graph.obligations[0].kind == "ASSESS_EXTERNAL_EVENT"
