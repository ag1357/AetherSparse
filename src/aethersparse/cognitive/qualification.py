"""Small structural stress and multi-file coding qualifications for COG v1."""

from __future__ import annotations

import difflib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from aethersparse.agent.tools import SandboxedToolExecutor, ToolKind, ToolRequest
from aethersparse.cognitive.graph import (
    can_halt_success,
    record_progress,
    transition_obligation,
    verify_invariant,
)
from aethersparse.cognitive.interpreter import InputStateInterpreter
from aethersparse.cognitive.models import (
    CognitiveObligationGraph,
    Goal,
    GoalType,
    InputType,
    Invariant,
    Obligation,
    ObligationStatus,
    Provenance,
    ProvenanceKind,
)
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


def qualify_reasoning_stress() -> dict[str, object]:
    """Exercise unresolved-state bookkeeping; this is not an HLE score."""

    interpreter = _interpreter()
    direct = interpreter.interpret(
        InputType.NATURAL_LANGUAGE,
        "Where was Alan Turing born?",
        input_id="stress-direct",
    )
    ambiguous = interpreter.interpret(
        InputType.NATURAL_LANGUAGE,
        "What is Mercury?",
        input_id="stress-ambiguity",
    )
    follow_up = interpreter.interpret(
        InputType.NATURAL_LANGUAGE,
        "Where was he born?",
        input_id="stress-follow-up",
        prior_entity_ids=("entity:alan_turing",),
    )
    comparison = interpreter.interpret(
        InputType.NATURAL_LANGUAGE,
        "Which is larger, Mercury or Earth?",
        input_id="stress-comparison",
    )
    temporal = interpreter.interpret(
        InputType.NATURAL_LANGUAGE,
        "When was Alan Turing born in 1912?",
        input_id="stress-temporal",
    )
    premise = interpreter.interpret(
        InputType.NATURAL_LANGUAGE,
        "Was Alan Turing not born in Paris?",
        input_id="stress-premise",
    )
    missing = interpreter.interpret(
        InputType.STRUCTURED_EXTERNAL_EVENT,
        {"event_type": "ACTUATOR_STATUS", "entity": "joint_4"},
        input_id="stress-missing",
    )
    thermal = interpreter.interpret(
        InputType.STRUCTURED_EXTERNAL_EVENT,
        {
            "event_type": "ACTUATOR_STATUS",
            "entity": "joint_4",
            "temperature": 82,
            "maximum_temperature": 75,
            "observed_position": 1,
            "requested_position": 1,
            "position_tolerance": 0.1,
        },
        input_id="stress-thermal",
    )
    cases = (
        ("multi_obligation", len(direct.graph.obligations) >= 6),
        ("competing_grounded_claims", len(ambiguous.graph.hypotheses) == 2),
        ("two_hop_discourse_composition", not follow_up.graph.unresolved),
        (
            "comparison",
            comparison.query_frame is not None
            and comparison.query_frame.answer_shape.value == "comparison",
        ),
        (
            "temporal_constraint",
            temporal.query_frame is not None and bool(temporal.query_frame.temporal_constraints),
        ),
        ("ambiguity_clarification", ambiguous.candidate_action_classes[0] == "ASK_CLARIFICATION"),
        ("premise_and_negation", premise.negated and bool(premise.premise_relationships)),
        (
            "missing_premise",
            any(item.status is ObligationStatus.BLOCKED for item in missing.graph.obligations),
        ),
        (
            "impossible_or_unsafe_state",
            any(item.status.value == "VIOLATED" for item in thermal.graph.invariants)
            and not can_halt_success(thermal.graph),
        ),
    )
    return {
        "schema": "aethercore.v14.reasoning-stress.v1",
        "scope": "small structural development/tuning-style stress set; not Humanity's Last Exam",
        "passed": sum(passed for _, passed in cases),
        "total": len(cases),
        "cases": [{"case": name, "passed": passed} for name, passed in cases],
    }


@dataclass(frozen=True)
class _CodingTask:
    task_id: str
    symbol: str
    before: dict[str, str]
    after: dict[str, str]
    expected_frontier: tuple[str, ...]


_CODING_TASKS = (
    _CodingTask(
        "api-schema-field",
        "make_payload",
        {
            "model.py": "def make_payload(name):\n    return {'name': name}\n",
            "service.py": (
                "from model import make_payload\ndef serve(name):\n"
                "    return make_payload(name)\n"
            ),
            "client.py": (
                "from model import make_payload\ndef read(name):\n"
                "    return make_payload(name)['name']\n"
            ),
            "test_task.py": (
                "from service import serve\nfrom client import read\n"
                "assert serve('x') == {'name': 'x', 'schema_version': 1}\n"
                "assert read('x') == 'x'\n"
            ),
        },
        {
            "model.py": (
                "def make_payload(name):\n    return {'name': name, 'schema_version': 1}\n"
            ),
            "service.py": (
                "from model import make_payload\ndef serve(name):\n"
                "    return make_payload(name)\n"
            ),
            "client.py": (
                "from model import make_payload\ndef read(name):\n"
                "    return make_payload(name)['name']\n"
            ),
            "test_task.py": (
                "from service import serve\nfrom client import read\n"
                "assert serve('x') == {'name': 'x', 'schema_version': 1}\n"
                "assert read('x') == 'x'\n"
            ),
        },
        ("client.py", "model.py", "service.py"),
    ),
    _CodingTask(
        "parser-delimiter",
        "parse_field",
        {
            "parser.py": "def parse_field(text):\n    return tuple(text.split(':'))\n",
            "consumer.py": (
                "from parser import parse_field\ndef consume(v):\n    return parse_field(v)\n"
            ),
            "api.py": "from consumer import consume\ndef parse(v):\n    return consume(v)\n",
            "test_task.py": (
                "from api import parse\nassert parse('url:https://a') == ('url', 'https://a')\n"
            ),
        },
        {
            "parser.py": "def parse_field(text):\n    return tuple(text.split(':', 1))\n",
            "consumer.py": (
                "from parser import parse_field\ndef consume(v):\n    return parse_field(v)\n"
            ),
            "api.py": "from consumer import consume\ndef parse(v):\n    return consume(v)\n",
            "test_task.py": (
                "from api import parse\nassert parse('url:https://a') == ('url', 'https://a')\n"
            ),
        },
        ("consumer.py", "parser.py"),
    ),
    _CodingTask(
        "compatibility-clamp",
        "clamp",
        {
            "limits.py": "def clamp(value, low, high):\n    return value\n",
            "motor.py": "from limits import clamp\ndef command(v):\n    return clamp(v, 0, 10)\n",
            "status.py": "from motor import command\ndef safe(v):\n    return command(v) <= 10\n",
            "test_task.py": (
                "from motor import command\nfrom status import safe\n"
                "assert command(12) == 10\nassert safe(12)\n"
            ),
        },
        {
            "limits.py": ("def clamp(value, low, high):\n    return max(low, min(high, value))\n"),
            "motor.py": "from limits import clamp\ndef command(v):\n    return clamp(v, 0, 10)\n",
            "status.py": "from motor import command\ndef safe(v):\n    return command(v) <= 10\n",
            "test_task.py": (
                "from motor import command\nfrom status import safe\n"
                "assert command(12) == 10\nassert safe(12)\n"
            ),
        },
        ("limits.py", "motor.py"),
    ),
)


def _patch(before: dict[str, str], after: dict[str, str]) -> str:
    return "".join(
        line
        for path in sorted(before)
        if before[path] != after[path]
        for line in difflib.unified_diff(
            before[path].splitlines(keepends=True),
            after[path].splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def _coding_graph(task: _CodingTask) -> CognitiveObligationGraph:
    source = Provenance(kind=ProvenanceKind.USER_INPUT, source_id=task.task_id)
    obligations = [
        Obligation(
            obligation_id=f"file:{path}",
            goal_id="goal",
            kind="AFFECTED_OBJECT",
            description=f"Inspect and preserve {path}",
            provenance=source,
        )
        for path in task.expected_frontier
    ]
    file_ids = tuple(item.obligation_id for item in obligations)
    obligations.extend(
        (
            Obligation(
                obligation_id="build",
                goal_id="goal",
                kind="BUILD",
                description="Build the changed repository",
                provenance=source,
                depends_on=file_ids,
            ),
            Obligation(
                obligation_id="tests",
                goal_id="goal",
                kind="RUN_TESTS",
                description="Run repository tests",
                provenance=source,
                depends_on=("build",),
            ),
            Obligation(
                obligation_id="verify",
                goal_id="goal",
                kind="VERIFY_INVARIANT",
                description="Verify compatibility and provenance invariants",
                provenance=source,
                depends_on=("tests",),
            ),
        )
    )
    return CognitiveObligationGraph(
        cog_id=f"cog:coding:{task.task_id}",
        goals=(
            Goal(
                goal_id="goal",
                goal_type=GoalType.SOFTWARE_CHANGE,
                description=task.task_id,
                provenance=source,
            ),
        ),
        obligations=tuple(obligations),
        invariants=(
            Invariant(
                invariant_id="compatibility",
                kind="PUBLIC_API_COMPATIBILITY",
                description="Existing callers remain valid",
                provenance=source,
            ),
            Invariant(
                invariant_id="verifier",
                kind="VERIFIER_REQUIRED",
                description="HALT_SUCCESS requires build/test acceptance",
                provenance=source,
            ),
        ),
    )


def qualify_coding_obligations(root: Path) -> dict[str, object]:
    """Execute three deterministic multi-file repository transformations."""

    root.mkdir(parents=True, exist_ok=True)
    outcomes: list[dict[str, object]] = []
    for task in _CODING_TASKS:
        executor = SandboxedToolExecutor(
            root,
            command_allowlist={
                "build": (sys.executable, "-m", "compileall", "-q", "."),
                "tests": (sys.executable, "test_task.py"),
            },
        )
        created = executor.execute(
            ToolRequest(
                request_id=f"{task.task_id}:create",
                kind=ToolKind.CREATE_SANDBOX,
                arguments={"name": task.task_id},
            )
        )
        workspace = Path(created.output)
        for path, content in task.before.items():
            (workspace / path).write_text(content, encoding="utf-8")
        search = executor.execute(
            ToolRequest(
                request_id=f"{task.task_id}:discover",
                kind=ToolKind.SEARCH_SOURCE,
                workspace=str(workspace),
                arguments={"query": task.symbol},
            )
        )
        discovered = tuple(
            sorted({line.split(":", 1)[0] for line in search.output.splitlines() if line})
        )
        graph = _coding_graph(task)
        for path in task.expected_frontier:
            graph = transition_obligation(
                graph,
                f"file:{path}",
                ObligationStatus.SATISFIED,
                satisfied_by=(f"source:{path}",),
            )
        patch_text = _patch(task.before, task.after)
        staged = executor.execute(
            ToolRequest(
                request_id=f"{task.task_id}:write",
                kind=ToolKind.WRITE_PATCH,
                workspace=str(workspace),
                arguments={"name": "change", "patch": patch_text},
            )
        )
        applied = executor.execute(
            ToolRequest(
                request_id=f"{task.task_id}:apply",
                kind=ToolKind.APPLY_PATCH,
                workspace=str(workspace),
                arguments={"path": ".aether-patches/change.patch"},
            )
        )
        build = executor.execute(
            ToolRequest(
                request_id=f"{task.task_id}:build",
                kind=ToolKind.BUILD,
                workspace=str(workspace),
                arguments={"profile": "build"},
            )
        )
        tests = executor.execute(
            ToolRequest(
                request_id=f"{task.task_id}:tests",
                kind=ToolKind.RUN_TESTS,
                workspace=str(workspace),
                arguments={"profile": "tests"},
            )
        )
        if build.success:
            graph = transition_obligation(graph, "build", ObligationStatus.SATISFIED)
        if tests.success:
            graph = transition_obligation(graph, "tests", ObligationStatus.SATISFIED)
        invariant_ok = build.success and tests.success
        graph = verify_invariant(graph, "compatibility", passed=invariant_ok)
        if invariant_ok:
            graph = transition_obligation(graph, "verify", ObligationStatus.SATISFIED)
            graph = record_progress(
                graph,
                graph,
                action="VERIFY_INVARIANT",
                verifier_state="ACCEPTED",
            )
        expected = set(task.expected_frontier)
        found = expected & set(discovered)
        recall = len(found) / len(expected)
        success = bool(
            created.success
            and search.success
            and staged.success
            and applied.success
            and build.success
            and tests.success
            and recall == 1.0
            and can_halt_success(graph)
        )
        outcomes.append(
            {
                "task_id": task.task_id,
                "success": success,
                "affected_object_recall": recall,
                "expected_objects": sorted(expected),
                "discovered_objects": list(discovered),
                "mandatory_obligations": len(graph.obligations),
                "mandatory_open": sum(
                    item.status is ObligationStatus.OPEN for item in graph.obligations
                ),
                "invariant_violations": sum(
                    item.status.value == "VIOLATED" for item in graph.invariants
                ),
                "redundant_cycles": graph.progress.repeated_action_count,
                "stagnation_detections": int(graph.progress.stagnant_steps >= 3),
                "build_passed": build.success,
                "tests_passed": tests.success,
            }
        )
    return {
        "schema": "aethercore.v14.coding-obligation-qualification.v1",
        "passed": sum(1 for item in outcomes if item["success"] is True),
        "total": len(outcomes),
        "mean_affected_object_recall": sum(
            cast(float, item["affected_object_recall"]) for item in outcomes
        )
        / len(outcomes),
        "invariant_violations": sum(
            cast(int, item["invariant_violations"]) for item in outcomes
        ),
        "redundant_cycles": sum(cast(int, item["redundant_cycles"]) for item in outcomes),
        "stagnation_detections": sum(
            cast(int, item["stagnation_detections"]) for item in outcomes
        ),
        "tasks": outcomes,
    }
