#!/usr/bin/env python3
"""Rerun only the 43-row V11 value residual with the bounded search oracle."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import tarfile
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

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
from aethersparse.controller.value_trace import qualify_value_trace

SCHEMA_VERSION = "aethersparse.value-targeted-residual-rerun.v11"
TRAINING_PARTITIONS = frozenset({"development", "tuning"})


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay-archive", type=Path, required=True)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read(path: Path) -> dict[str, Any]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    else:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


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


def _bundle_directory(archive: Path, root: Path) -> Path:
    with tarfile.open(archive, "r:gz") as handle:
        names = {member.name for member in handle.getmembers() if member.isfile()}
        expected = {
            "controller-replay-3tier/cases.jsonl.gz",
            "controller-replay-3tier/manifest.json",
        }
        if names != expected:
            raise ValueError(f"unexpected replay archive members: {sorted(names ^ expected)}")
        handle.extractall(root, filter="data")
    return root / "controller-replay-3tier"


def rerun(
    replay_archive: Path, capture_path: Path, benchmark_path: Path
) -> dict[str, Any]:
    capture = _read(capture_path)
    scope = capture.get("scope", {})
    if not isinstance(scope, dict) or bool(scope.get("evaluation_and_final_held_used")):
        raise ValueError("capture does not prove a training-only scope")
    if set(scope.get("partitions", ())) != TRAINING_PARTITIONS:
        raise ValueError("capture is not restricted to development/tuning")
    replicas = capture.get("replicas")
    unique_cases = capture.get("unique_cases")
    if not isinstance(replicas, list) or len(replicas) != 43:
        raise ValueError("capture must contain exactly 43 replicas")
    if not isinstance(unique_cases, list) or len(unique_cases) != 16:
        raise ValueError("capture must contain exactly 16 unique case groups")
    replica_by_key = {
        (str(item["case_id"]), str(item["corpus_tier"])): item
        for item in replicas
        if isinstance(item, dict) and item.get("partition") in TRAINING_PARTITIONS
    }
    case_capture = {
        str(item["case_id"]): item
        for item in unique_cases
        if isinstance(item, dict) and item.get("partition") in TRAINING_PARTITIONS
    }
    if len(replica_by_key) != 43 or len(case_capture) != 16:
        raise ValueError("protected or duplicate rows entered the targeted residual")
    benchmark = _read(benchmark_path)
    cases = benchmark.get("cases")
    if not isinstance(cases, list):
        raise ValueError("benchmark lacks cases")
    benchmark_by_id = {
        str(item["case_id"]): item for item in cases if isinstance(item, dict)
    }
    if _sha256(benchmark_path) != capture["source_identity"]["benchmark_sha256"]:
        raise ValueError("benchmark identity differs from the capture")
    configs = (
        SearchConfig(
            kind="best_first", max_depth=14, max_expansions=4096, argument_cap=64
        ),
        SearchConfig(
            kind="beam",
            max_depth=14,
            max_expansions=4096,
            beam_width=32,
            argument_cap=64,
        ),
    )
    counts: Counter[str] = Counter()
    partition_counts: Counter[str] = Counter()
    first_success: Counter[str] = Counter()
    residual_trace: Counter[str] = Counter()
    with tempfile.TemporaryDirectory(prefix="v11-value-residual.", dir="/tmp") as raw_temp:
        bundle = _bundle_directory(replay_archive, Path(raw_temp))
        manifest = verify_replay_bundle(bundle)
        if manifest.bundle_sha256 != capture["source_identity"]["replay_bundle_sha256"]:
            raise ValueError("replay logical identity differs from the capture")
        seen: set[tuple[str, str]] = set()
        with gzip.open(bundle / manifest.cases_file, "rt", encoding="utf-8") as handle:
            for line in handle:
                case = ReplayCase.model_validate_json(line)
                key = (case.case_id, case.corpus_tier)
                if key not in replica_by_key:
                    continue
                replica = replica_by_key[key]
                gold = benchmark_by_id[case.case_id]
                if (
                    case.partition not in TRAINING_PARTITIONS
                    or gold.get("partition") not in TRAINING_PARTITIONS
                    or case.partition != replica["partition"]
                    or case.partition != gold["partition"]
                ):
                    raise ValueError(f"protected partition or partition drift: {key}")
                seen.add(key)
                accepted = tuple(str(item) for item in gold.get("accepted_answers", ()))
                shape = str(gold.get("required_answer_shape", ""))
                state = state_from_replay(case)
                repair = repair_state_with_typed_values(state)
                possible = _goal_possible(
                    verifier_eligible_claim_values(repair.state), accepted, shape
                )
                required_entities = {str(item) for item in gold.get("required_entity_ids", ())}
                frame_entities = {
                    str(item) for item in state.frame.get("candidate_entity_ids", ())
                }
                entity_valid = required_entities.issubset(frame_entities)
                reachable = False
                if possible and entity_valid:
                    for config in configs:
                        result = search(
                            repair.state,
                            config,
                            accepted_answers=accepted,
                            allow_gold=True,
                        )
                        if candidate_set_oracle(result, accepted):
                            reachable = True
                            first_success[config.kind] += 1
                            break
                trace = qualify_value_trace(replica, case_capture[case.case_id])
                counts["replicas"] += 1
                counts["goal_possible"] += int(possible)
                counts["semantic_entity_binding_valid"] += int(entity_valid)
                counts["reachable"] += int(reachable)
                counts["capacity_exhausted"] += int(repair.candidate_capacity_exhausted)
                partition_counts[f"{case.partition}_goal_possible"] += int(possible)
                partition_counts[f"{case.partition}_reachable"] += int(reachable)
                if not reachable:
                    residual_trace[trace.failure.value] += 1
                    if possible:
                        residual_trace["TOOLSET_CONTROLLER_SEARCH"] += 1
        if seen != set(replica_by_key):
            raise ValueError("not every targeted residual replica is in the replay bundle")
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_id": "VALUE_TARGETED_RESIDUAL_RERUN_V11",
        "source_identity": {
            "replay_archive_sha256": _sha256(replay_archive),
            "replay_bundle_sha256": capture["source_identity"]["replay_bundle_sha256"],
            "capture_sha256": _sha256(capture_path),
            "benchmark_sha256": _sha256(benchmark_path),
        },
        "scope": {
            "partitions": sorted(TRAINING_PARTITIONS),
            "evaluation_and_final_held_used": False,
            "replicas": len(replica_by_key),
            "unique_cases": len(case_capture),
            "search": {
                "max_depth": 14,
                "max_expansions": 4096,
                "beam_width": 32,
                "argument_cap": 64,
            },
        },
        "counts": dict(sorted(counts.items())),
        "partition_counts": dict(sorted(partition_counts.items())),
        "first_success": dict(sorted(first_success.items())),
        "residual_trace_counts": dict(sorted(residual_trace.items())),
    }


def main() -> int:
    args = _arguments()
    payload = rerun(args.replay_archive, args.capture, args.benchmark)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
