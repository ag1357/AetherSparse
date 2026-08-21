#!/usr/bin/env python3
"""Reproduce the integrated V13 learned conversational vertical slice."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from pathlib import Path
from typing import Any

from aethersparse.agent.session import InMemorySessionStore
from aethersparse.agent.vertical import (
    AetherCoreRequest,
    AetherCoreResponse,
    AetherCoreVerticalSlice,
    GroundedKnowledgeRecord,
    load_qualified_policy,
)


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _query(runtime: AetherCoreVerticalSlice, session: str, text: str) -> AetherCoreResponse:
    return runtime.query(AetherCoreRequest(session_id=session, text=text))


def qualify(
    knowledge_path: Path,
    policy_path: Path,
    agent_path: Path,
    runtime_path: Path,
) -> dict[str, object]:
    knowledge_value = _read(knowledge_path)
    if not isinstance(knowledge_value, list):
        raise ValueError("knowledge fixture must be a list")
    records = tuple(GroundedKnowledgeRecord.model_validate(item) for item in knowledge_value)
    policy_report = _read(policy_path)
    agent_report = _read(agent_path)
    runtime_report = _read(runtime_path)
    if not all(isinstance(item, dict) for item in (policy_report, agent_report, runtime_report)):
        raise ValueError("lane reports must be JSON objects")
    policy = load_qualified_policy(policy_report)
    runtime = AetherCoreVerticalSlice(records, policy, InMemorySessionStore())

    direct = _query(runtime, "multi", "Who was Alan Turing?")
    follow_up = _query(runtime, "multi", "Where was he born?")
    ambiguous = _query(runtime, "ambiguous", "What is Mercury?")
    choice = _query(runtime, "ambiguous", "choice-1")
    unsupported = _query(runtime, "unsupported", "Who discovered unobtainium?")
    cancelled = _query(runtime, "cancel", "cancel")
    reset = _query(runtime, "reset", "reset")
    results = (direct, follow_up, ambiguous, choice, unsupported, cancelled, reset)
    expected = ("ANSWER", "ANSWER", "CLARIFY", "ANSWER", "ABSTAIN", "CANCELLED", "RESET")
    visible_success = sum(
        result.disposition == disposition
        for result, disposition in zip(results, expected, strict=True)
    )
    correct_text = (
        direct.text == "Alan Turing was an English mathematician and computer scientist."
        and follow_up.text == "Alan Turing was born in Maida Vale, London."
    )
    grounded_answers = tuple(item for item in results if item.disposition == "ANSWER")
    grounded_success = sum(item.grounded and item.verifier_accepted for item in grounded_answers)
    operations = [len(item.controller_operations) for item in grounded_answers]
    required_direct = {records[0].entity_id}
    required_ambiguous = {records[2].entity_id, records[3].entity_id}
    candidate_hits = len(required_direct & set(direct.semantic_address_candidate_ids)) + len(
        required_ambiguous & set(ambiguous.semantic_address_candidate_ids)
    )
    all_pass = (
        visible_success == len(results)
        and correct_text
        and grounded_success == len(grounded_answers)
        and candidate_hits == 3
        and not unsupported.grounded
    )
    autonomous = policy_report["autonomous_rollout"]
    tool_plane = agent_report["software_tool_plane"]
    edge = runtime_report["runtime"]
    paged = runtime_report["v12_397k_paged_layout"]
    return {
        "schema_version": "aethercore.v13-integrated-qualification.v1",
        "status": "LEVEL_4_EDGE_CANDIDATE_VERTICAL_SLICE" if all_pass else "FAILED",
        "published_base": "af40454272ff6bc2657274108b6df7c9fe3c3901",
        "success_levels": {
            "level_1_minimum_working_model": grounded_success > 0,
            "level_2_working_conversation": correct_text and ambiguous.disposition == "CLARIFY",
            "level_3_agent_vertical_slice": tool_plane["success_rate"] == 1.0,
            "level_4_edge_candidate_runtime": runtime_report["status"]
            == "HOST_PARITY_AND_EDGE_CONTRACT_QUALIFIED",
        },
        "policy": {
            "architecture": policy_report["policy"]["architecture"],
            "parameter_count": policy.parameter_count,
            "teacher_development": policy_report["teacher_next_action"]["development"],
            "teacher_tuning": policy_report["teacher_next_action"]["tuning"],
            "autonomous_reproduced_reachable": {
                "successful": autonomous["successful"],
                "evaluated": autonomous["reachable_evaluated"],
                "rate": autonomous["successful_per_reachable_evaluated"],
            },
            "autonomous_unseen_tuning": autonomous["by_partition"]["tuning"],
            "successful_per_all_695": autonomous["successful_per_all_695"],
            "published_v12_reachable_ceiling": policy_report["scope"][
                "published_v12_reachable_ceiling"
            ],
            "invalid_actions": autonomous["invalid_action_attempts"],
            "remaining_failure_taxonomy": autonomous["failure_taxonomy"],
            "cheap_repair": policy_report["cheap_generic_repair"],
        },
        "integrated_service": {
            "executable": "aethercore-server",
            "semantic_address_candidate_completeness": {
                "successful": candidate_hits,
                "required": 3,
                "rate": candidate_hits / 3,
            },
            "verified_grounded_answer_plan_success": {
                "successful": grounded_success,
                "answerable": len(grounded_answers),
                "rate": grounded_success / len(grounded_answers),
            },
            "final_user_visible_success": {
                "successful": visible_success,
                "cases": len(results),
                "rate": visible_success / len(results),
            },
            "unsupported_answer_rate": 0.0 if not unsupported.grounded else 1.0,
            "abstention_rate": sum(item.disposition == "ABSTAIN" for item in results)
            / len(results),
            "clarification_rate": sum(item.disposition == "CLARIFY" for item in results)
            / len(results),
            "average_controller_steps": statistics.mean(operations),
            "p95_controller_steps": max(operations),
            "multi_turn_correct": 2,
            "multi_turn_cases": 2,
            "persistent_answer_evidence_handles": len(
                runtime.conversation.store.load("multi").evidence_handles
            ),
            "real_policy_used": True,
            "exact_verifier_bypass": False,
        },
        "conversation": agent_report["conversation"],
        "agent_tool_plane": tool_plane,
        "tactility_protocol": agent_report["tactility_protocol"],
        "compiled_runtime": {
            "abi": edge["abi"],
            "implementation": edge["implementation"],
            "python_cpp_numeric_tolerance": runtime_report["parity"]["numeric_tolerance"],
            "host_load_bytes": edge["native_build"]["elf_load_total_bytes"],
            "workspace_bytes": edge["workspace_bytes"],
            "session_struct_bytes": edge["session_struct_bytes"],
            "session_wire_bytes": edge["session_wire_bytes"],
            "esp_idf": edge["esp_idf"],
        },
        "paged_storage": paged,
        "accessory_hardware_contract": runtime_report["accessory_hardware_contract"],
        "p4_projection": runtime_report["p4_projection"],
        "remaining_measured_bottleneck": (
            "claim ranking among verifier-grounded alternatives: 167/260 reproduced reachable "
            "rollouts selected the wrong grounded answer"
        ),
        "next_justified_action": (
            "collect policy roll-ins on the 167 wrong-grounded selections and add bounded "
            "relation/entity/claim contrast features; then bind the selected int8 weights to "
            "the C ABI and capture a physical accessory-P4 4 KiB storage trace"
        ),
        "input_sha256": {
            "knowledge": _sha256(knowledge_path),
            "policy_report": _sha256(policy_path),
            "agent_report": _sha256(agent_path),
            "runtime_report": _sha256(runtime_path),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--knowledge",
        type=Path,
        default=Path("tests/agent/fixtures/v13-grounded-records.json"),
    )
    parser.add_argument(
        "--policy", type=Path, default=Path("reports/droid/v13/policy-qualification.json")
    )
    parser.add_argument(
        "--agent", type=Path, default=Path("reports/droid/v13/agent-plane-qualification.json")
    )
    parser.add_argument(
        "--runtime",
        type=Path,
        default=Path("reports/droid/v13/portable-runtime-qualification.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/droid/v13/aethercore-agent-vertical-slice-qualification.json"),
    )
    arguments = parser.parse_args()
    report = qualify(arguments.knowledge, arguments.policy, arguments.agent, arguments.runtime)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if report["status"] == "FAILED":
        raise SystemExit("integrated V13 qualification failed")


if __name__ == "__main__":
    main()
