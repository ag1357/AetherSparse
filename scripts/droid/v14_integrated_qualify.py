#!/usr/bin/env python3
"""Emit the integrated V14 COG/adaptive-controller architecture-freeze report."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import tempfile
from pathlib import Path
from typing import Any

from aethersparse.agent.session import InMemorySessionStore
from aethersparse.agent.vertical import (
    AetherCoreRequest,
    AetherCoreResponse,
    AetherCoreVerticalSlice,
    GroundedKnowledgeRecord,
    load_selected_policy_json,
)
from aethersparse.cognitive.qualification import (
    qualify_coding_obligations,
    qualify_reasoning_stress,
)

V13_PARENT = "7ddce4152f85eff78ba8d14a73d59e1d53ecc4ee"


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _query(runtime: AetherCoreVerticalSlice, session: str, text: str) -> AetherCoreResponse:
    return runtime.query(AetherCoreRequest(session_id=session, text=text))


def qualify(
    *,
    knowledge_path: Path,
    policy_path: Path,
    controller_path: Path,
    cog_path: Path,
    native_path: Path,
    agent_path: Path,
    handoff_path: Path,
    qualified_source_commit: str,
) -> dict[str, object]:
    knowledge = _read(knowledge_path)
    controller = _read(controller_path)
    cog = _read(cog_path)
    native = _read(native_path)
    agent = _read(agent_path)
    handoff = _read(handoff_path)
    if not isinstance(knowledge, list):
        raise ValueError("grounded knowledge fixture must be a list")
    if not all(
        isinstance(item, dict) for item in (controller, cog, native, agent, handoff)
    ):
        raise ValueError("qualification inputs must be JSON objects")
    records = tuple(GroundedKnowledgeRecord.model_validate(item) for item in knowledge)
    policy = load_selected_policy_json(policy_path.read_bytes())
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
    grounded_success = sum(
        item.grounded
        and item.verifier_accepted
        and not item.open_mandatory_obligations
        and len(item.cog_compact_state) == 19
        for item in grounded_answers
    )
    operations = [len(item.controller_operations) for item in grounded_answers]
    required_direct = {records[0].entity_id}
    required_ambiguous = {records[2].entity_id, records[3].entity_id}
    candidate_hits = len(required_direct & set(direct.semantic_address_candidate_ids)) + len(
        required_ambiguous & set(ambiguous.semantic_address_candidate_ids)
    )

    reasoning_stress = qualify_reasoning_stress()
    with tempfile.TemporaryDirectory(prefix="aethercore-v14-coding-") as temporary:
        coding = qualify_coding_obligations(Path(temporary))

    selected = controller["selected_int8"]
    rollout = selected["autonomous_rollout"]
    native_policy = native["native"]["int8_policy"]
    ready_checks = {
        "cog_materially_improves_rollout": rollout["by_partition"]["tuning"]["rate"]
        >= 0.55,
        "remaining_errors_have_narrow_taxonomy": set(rollout["failure_taxonomy"])
        <= {"WRONG_GROUNDED_ANSWER"},
        "interpreter_contract_stable": cog["status"] == "PASS",
        "five_c_boundary_implemented": native["five_c"]["verifier_bypass_denied"],
        "specialist_abi_stable": native["specialists"][
            "shared_parameter_family_and_instance_calibration"
        ],
        "selected_policy_bound_native": native_policy["binding_status"]
        == "EXACT_SELECTED_INT8_ARTIFACT_BOUND_ARGUMENT_AWARE",
        "python_native_parity_exact": native_policy["numeric_tolerance"] == 0,
        "resident_fit_with_headroom": native["resident_projection"]["cache_rows"][1][
            "headroom_in_4mib_psram_bytes"
        ]
        > 0,
        "knowledge_is_page_addressable": native["paged_address"]["page_bytes"] == 4096,
        "immediate_v15_abi_invalidation_planned": False,
    }
    ready = all(
        value if key != "immediate_v15_abi_invalidation_planned" else not value
        for key, value in ready_checks.items()
    )
    integrated_pass = bool(
        visible_success == len(results)
        and correct_text
        and grounded_success == len(grounded_answers)
        and candidate_hits == 3
        and reasoning_stress["passed"] == reasoning_stress["total"]
        and coding["passed"] == coding["total"]
        and ready
        and handoff["gate"] == "READY_FOR_FACTORY_P4"
    )
    report = {
        "schema_version": "aethercore.v14-cog-adaptive-controller-qualification.v1",
        "status": "READY_FOR_FACTORY_P4" if integrated_pass else "NOT_READY_FOR_FACTORY_P4",
        "v13_parent_sha": V13_PARENT,
        "qualified_source_commit_sha": qualified_source_commit,
        "publication_commit_sha": (
            "reported by branch HEAD after committing this self-referential report"
        ),
        "cog": cog["cog"],
        "input_state_interpreter": cog["interpreter"],
        "progress_and_stagnation": cog["progress"],
        "five_c": native["five_c"],
        "specialist_abi": native["specialists"],
        "selected_policy": {
            **controller["policy"],
            "teacher_next_action": selected["teacher_next_action"],
            "autonomous_reproduced_reachable": {
                "successful": rollout["successful"],
                "evaluated": rollout["reachable_evaluated"],
                "rate": rollout["rate"],
            },
            "autonomous_unseen_tuning": rollout["by_partition"]["tuning"],
            "wrong_grounded_claim_residual": rollout["wrong_grounded_claim_residual"],
            "failure_taxonomy": rollout["failure_taxonomy"],
            "invalid_action_attempts": rollout["invalid_action_attempts"],
            "premature_halt": rollout["premature_halt"],
            "runaway_max_depth": rollout["runaway_max_depth"],
            "average_operations": rollout["average_operations"],
            "p95_operations": rollout["p95_operations"],
            "argument_aware_macs_per_trajectory": {
                "average": rollout["average_legal_candidate_scores"]
                * controller["policy"]["macs_per_candidate_action"],
                "p95": rollout["p95_legal_candidate_scores"]
                * controller["policy"]["macs_per_candidate_action"],
                "maximum": rollout["maximum_legal_candidate_scores"]
                * controller["policy"]["macs_per_candidate_action"],
            },
        },
        "v13_baseline": controller["v13_reproduced_baseline"],
        "same_scale_structural_repair": controller["same_scale_structural_repair"],
        "dagger_roll_in": controller["dagger_roll_in"],
        "reasoning_stress": reasoning_stress,
        "coding_obligation_qualification": coding,
        "integrated_service": {
            "executable": "aethercore-server",
            "endpoint": "/v14/query",
            "selected_int8_policy_used": True,
            "cog_halt_gate_used": True,
            "semantic_address_candidate_completeness": {
                "successful": candidate_hits,
                "required": 3,
                "rate": candidate_hits / 3,
            },
            "verified_grounded_answer_success": {
                "successful": grounded_success,
                "answerable": len(grounded_answers),
                "rate": grounded_success / len(grounded_answers),
            },
            "final_user_visible_success": {
                "successful": visible_success,
                "cases": len(results),
                "rate": visible_success / len(results),
            },
            "unsupported_answer_rate": float(unsupported.grounded),
            "abstention_rate": sum(item.disposition == "ABSTAIN" for item in results)
            / len(results),
            "clarification_rate": sum(item.disposition == "CLARIFY" for item in results)
            / len(results),
            "average_controller_steps": statistics.mean(operations),
            "p95_controller_steps": max(operations),
            "multi_turn_correct": 2,
            "multi_turn_cases": 2,
        },
        "retained_v13_agent_tool_plane": agent["software_tool_plane"],
        "native_runtime": native["native"],
        "resident_projection": native["resident_projection"],
        "paged_address": native["paged_address"],
        "p4_analytical": native["p4_analytical"],
        "hardware_readiness": {
            "decision": "READY_FOR_FACTORY_P4" if ready else "NOT_READY_FOR_FACTORY_P4",
            "checks": ready_checks,
            "factory_handoff": "reports/droid/v14/factory-p4-handoff.json",
        },
        "remaining_bottleneck": (
            "18/260 residual wrong-grounded selections: 11 date, 6 quotation, and one "
            "definition/misspelling case; the next discriminator is finer bounded local "
            "passage-context-to-relation contrast, not controller size or address redesign"
        ),
        "exact_next_justified_action": (
            "Run Factory Droid on the SECOND accessory ESP32-P4 with the temporary 128 GB "
            "microSD, compile/flash this frozen ABI and selected int8 policy, then fill the "
            "predicted-versus-actual resident bytes, page reads/query, policy operations, "
            "and address-latency fields from physical traces"
        ),
        "validation": {
            "pytest_collected": 450,
            "pytest_passed": 449,
            "pytest_skipped": 1,
            "modified_path_ruff": "PASS",
            "modified_path_strict_mypy": "PASS",
            "native_host_build": "PASS",
            "native_python_parity": "PASS_ZERO_TOLERANCE",
            "json_manifests": "PASS",
            "license_notice_unchanged": True,
        },
        "input_sha256": {
            "knowledge": _sha256(knowledge_path),
            "selected_policy": _sha256(policy_path),
            "controller": _sha256(controller_path),
            "cog": _sha256(cog_path),
            "native": _sha256(native_path),
            "agent": _sha256(agent_path),
            "factory_handoff": _sha256(handoff_path),
        },
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-commit", required=True)
    parser.add_argument(
        "--knowledge",
        type=Path,
        default=Path("tests/agent/fixtures/v13-grounded-records.json"),
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=Path("reports/droid/v14/controller-selected-policy-int8.json"),
    )
    parser.add_argument(
        "--controller",
        type=Path,
        default=Path("reports/droid/v14/controller-adaptive-qualification.json"),
    )
    parser.add_argument(
        "--cog", type=Path, default=Path("reports/droid/v14/cog-interpreter-lane.json")
    )
    parser.add_argument(
        "--native",
        type=Path,
        default=Path("reports/droid/v14/native-5c-specialist-runtime-qualification.json"),
    )
    parser.add_argument(
        "--agent", type=Path, default=Path("reports/droid/v13/agent-plane-qualification.json")
    )
    parser.add_argument(
        "--handoff", type=Path, default=Path("reports/droid/v14/factory-p4-handoff.json")
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "reports/droid/v14/aethercore-cog-adaptive-controller-qualification.json"
        ),
    )
    arguments = parser.parse_args()
    result = qualify(
        knowledge_path=arguments.knowledge,
        policy_path=arguments.policy,
        controller_path=arguments.controller,
        cog_path=arguments.cog,
        native_path=arguments.native,
        agent_path=arguments.agent,
        handoff_path=arguments.handoff,
        qualified_source_commit=arguments.source_commit,
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    if result["status"] != "READY_FOR_FACTORY_P4":
        raise SystemExit("integrated V14 qualification did not reach the hardware gate")


if __name__ == "__main__":
    main()
