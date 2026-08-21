#!/usr/bin/env python3
"""Fresh 695-state reachability rerun with the real-corpus fuzzy address plane."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from v12_real_corpus_qualify import CharIndex

from aethersparse.controller.micro_ops import state_from_replay, verifier_eligible_claim_values
from aethersparse.controller.replay import ReplayCase, verify_replay_bundle
from aethersparse.controller.search import (
    SearchConfig,
    candidate_set_oracle,
    canonical_answer_match,
    canonicalize,
    search,
)
from aethersparse.controller.value_repair import repair_state_with_typed_values

TRAINING_PARTITIONS = frozenset({"development", "tuning"})
GLOBAL_ADDRESS_CAP = 32


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read(path: Path) -> dict[str, Any]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            value = json.load(stream)
    else:
        value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _goal_possible(values: tuple[str, ...], accepted: tuple[str, ...], shape: str) -> bool:
    if shape == "list":
        parts = [part.strip() for answer in accepted for part in answer.split(";") if part.strip()]
        return bool(parts) and all(
            any(canonical_answer_match((value,), (part,)) for value in values) for part in parts
        )
    if shape == "comparison":
        accepted_canonical = tuple(canonicalize(answer) for answer in accepted)
        matching = {
            canonicalize(value)
            for value in values
            if any(canonicalize(value) in answer for answer in accepted_canonical)
        }
        return len(matching) >= 2
    return any(canonical_answer_match((value,), accepted) for value in values)


def _fuzzy_ids(index: CharIndex, query: str) -> tuple[str, ...]:
    result = index.lookup_query(query, postings_cap=16_384, proposal_cap=128)
    accepted = (
        str(item["entity_id"])
        for item in result["proposals"]
        if float(item["char_score"]) >= 0.80 and int(item.get("span_tokens", 1)) <= 2
    )
    return tuple(dict.fromkeys(accepted))[:GLOBAL_ADDRESS_CAP]


def rerun(
    *,
    bundle_path: Path,
    benchmark_path: Path,
    mission5_path: Path,
    aliases_path: Path,
    max_depth: int,
    max_expansions: int,
    beam_width: int,
) -> dict[str, Any]:
    replay_manifest = verify_replay_bundle(bundle_path)
    benchmark = _read(benchmark_path)
    mission5 = _read(mission5_path)
    cases_value = benchmark.get("cases")
    rows_value = mission5.get("per_case")
    if not isinstance(cases_value, list) or not isinstance(rows_value, list):
        raise ValueError("benchmark or Mission 5 row set is malformed")
    benchmark_by_id = {str(row["case_id"]): row for row in cases_value if isinstance(row, dict)}
    eligible = {
        (str(row["case_id"]), str(row["corpus_tier"])): row
        for row in rows_value
        if isinstance(row, dict) and row.get("partition") in TRAINING_PARTITIONS
    }
    if len(eligible) != 695:
        raise ValueError(f"strict cohort changed: expected 695, observed {len(eligible)}")

    replay_cases: dict[tuple[str, str], ReplayCase] = {}
    with gzip.open(bundle_path / replay_manifest.cases_file, "rt", encoding="utf-8") as stream:
        for line in stream:
            case = ReplayCase.model_validate_json(line)
            key = (case.case_id, case.corpus_tier)
            if key in eligible:
                replay_cases[key] = case
    if set(replay_cases) != set(eligible):
        raise ValueError("replay bundle does not contain the full strict cohort")

    index = CharIndex(aliases_path)
    candidate_cache = {
        case_id: _fuzzy_ids(index, str(benchmark_by_id[case_id]["question"]))
        for case_id in sorted({case_id for case_id, _tier in eligible})
    }
    configurations = (
        SearchConfig(
            kind="best_first",
            max_depth=max_depth,
            max_expansions=max_expansions,
            argument_cap=64,
        ),
        SearchConfig(
            kind="beam",
            max_depth=max_depth,
            max_expansions=max_expansions,
            beam_width=beam_width,
            argument_cap=64,
        ),
    )
    totals: Counter[str] = Counter()
    residuals: Counter[str] = Counter()
    per_case: list[dict[str, Any]] = []
    for key in sorted(eligible):
        case = replay_cases[key]
        gold = benchmark_by_id[case.case_id]
        if gold.get("partition") != case.partition:
            raise ValueError(f"benchmark/replay partition mismatch: {key}")
        required = {str(item) for item in gold.get("required_entity_ids", ())}
        accepted_answers = tuple(str(item) for item in gold.get("accepted_answers", ()))
        shape = str(gold.get("required_answer_shape", ""))
        original = state_from_replay(case)
        original_ids = tuple(
            str(item) for item in original.frame.get("candidate_entity_ids", ()) if str(item)
        )
        fuzzy_ids = candidate_cache[case.case_id]
        union_ids = tuple(dict.fromkeys((*original_ids, *fuzzy_ids)))[:64]
        addressed = original.model_copy(
            update={"frame": {**original.frame, "candidate_entity_ids": union_ids}}
        )
        repaired = repair_state_with_typed_values(addressed).state
        entity_valid = required.issubset(set(union_ids))
        goal_possible = _goal_possible(
            verifier_eligible_claim_values(repaired), accepted_answers, shape
        )
        reachable = False
        searches: list[dict[str, Any]] = []
        if entity_valid and goal_possible:
            for configuration in configurations:
                result = search(
                    repaired,
                    configuration,
                    accepted_answers=accepted_answers,
                    allow_gold=True,
                )
                searches.append(
                    {
                        "kind": configuration.kind,
                        "expansions": result.expansions,
                        "visited_states": result.visited_states,
                        "verifier_attempts": result.verifier_attempts,
                        "verifier_rejections": result.verifier_rejections,
                    }
                )
                if candidate_set_oracle(result, accepted_answers):
                    reachable = True
                    break
        if reachable:
            residual = None
        elif not entity_valid:
            residual = "SEMANTIC_ADDRESS_GENERATION"
        elif not goal_possible:
            residual = "VALUE_AVAILABILITY"
        else:
            residual = "STATE_REPRESENTATION_OR_TOOLSET"
        totals["eligible"] += 1
        totals["reachable"] += int(reachable)
        totals["entity_valid"] += int(entity_valid)
        totals["goal_possible"] += int(goal_possible)
        totals["address_capacity_exhausted"] += int(len(original_ids) + len(fuzzy_ids) > 64)
        totals["candidate_ids_added"] += len(set(union_ids) - set(original_ids))
        if residual is not None:
            residuals[residual] += 1
        per_case.append(
            {
                "case_id": case.case_id,
                "corpus_tier": case.corpus_tier,
                "partition": case.partition,
                "required_entity_count": len(required),
                "original_candidate_count": len(original_ids),
                "fuzzy_candidate_count": len(fuzzy_ids),
                "union_candidate_count": len(union_ids),
                "entity_binding_valid": entity_valid,
                "canonical_goal_present": goal_possible,
                "reachable": reachable,
                "residual_category": residual,
                "searches": searches,
            }
        )
    reachable = totals["reachable"]
    return {
        "schema_version": "aethercore.v12-real-corpus-reachability.v1",
        "status": "POLICY_GATE_OPEN" if reachable > 418 else "POLICY_GATE_CLOSED",
        "decision": {
            "certified_reachable": reachable,
            "eligible": totals["eligible"],
            "rate": reachable / totals["eligible"],
            "strict_gate": ">418/695 and >60%",
            "gate_open": reachable > 418 and reachable / totals["eligible"] > 0.60,
            "published_v11_baseline": {"reachable": 324, "eligible": 695},
        },
        "counts": dict(sorted(totals.items())),
        "residual_limitation": dict(sorted(residuals.items())),
        "address_model": {
            "name": "generic-query-span-char-trigram-dice-osa",
            "threshold": 0.80,
            "max_span_tokens": 2,
            "candidate_cap": GLOBAL_ADDRESS_CAP,
            "frame_cap": 64,
            "canonical_union_before_frame_cap": True,
            "model_logical_bytes": index.logical_bytes,
        },
        "source_identity": {
            "replay_bundle_sha256": replay_manifest.bundle_sha256,
            "benchmark_sha256": _sha256(benchmark_path),
            "mission5_report_sha256": _sha256(mission5_path),
            "aliases_sha256": _sha256(aliases_path),
        },
        "search_limits": {
            "max_depth": max_depth,
            "max_expansions": max_expansions,
            "beam_width": beam_width,
        },
        "scope_caveat": (
            "Fresh replay uses original retained state plus the selected real-corpus fuzzy "
            "union and exact typed-value repair; it does not carry forward the v11 semantic "
            "overlay by aggregation."
        ),
        "per_case": per_case,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--mission5-report", type=Path, required=True)
    parser.add_argument("--aliases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-depth", type=int, default=12)
    parser.add_argument("--max-expansions", type=int, default=5_000)
    parser.add_argument("--beam-width", type=int, default=64)
    args = parser.parse_args()
    result = rerun(
        bundle_path=args.bundle,
        benchmark_path=args.benchmark,
        mission5_path=args.mission5_report,
        aliases_path=args.aliases,
        max_depth=args.max_depth,
        max_expansions=args.max_expansions,
        beam_width=args.beam_width,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "per_case"}))


if __name__ == "__main__":
    main()
