"""Export the witnessed 260-case V14 cohort as an on-device trace bundle.

Authenticates every input by hash, replays the selected frozen int8 policy over
the 260 witnessed Mission-5 cases exactly like the V14 qualifier, and packs the
per-decision candidate sets (features + expected scores + chosen action) plus
the per-case address workload (mention surfaces with expected pack lookups)
into a compact binary trace for the ESP32-P4 firmware.

Fail-closed: the exporter refuses to emit a bundle unless the replayed rollout
statistics reproduce the committed V14 qualification numbers exactly
(242/260 successes, 18 WRONG_GROUNDED_ANSWER, 1329 total operations).

On-device semantics contract (ACP1TRC1, all integers little-endian):

  header page 0: magic, version, counts, section offsets/lengths
  string pool:   u32 count, then u16 len + utf-8 bytes each
  cases:         fixed 40 B records
  decisions:     fixed 24 B records (case_idx, step, candidate span, chosen)
  candidates:    90 B records: u16 row, i64 expected score, u32 args string,
                 38 x int16 features, u16 op_id
  queries:       24 B records (surface string, candidate id span, entity key
                 span, expected occurrence total)
  query_ids:     u32 surface-id array (sorted), shared by queries
  query_entities: u64 entity-key array (sorted), shared by queries
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import struct
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "src"))

from aethersparse.controller.adaptive_policy import (  # noqa: E402
    QuantizedAdaptivePolicy,
    _arguments_key,
    quantized_action_features,
)
from aethersparse.controller.micro_ops import (  # noqa: E402
    execute_action,
    legal_actions,
    state_from_replay,
)
from aethersparse.controller.replay import ReplayCase, verify_replay_bundle  # noqa: E402
from aethersparse.controller.search import canonical_answer_match  # noqa: E402

PAGE = 4096
TRACE_MAGIC = b"ACP1TRC1"
TRACE_VERSION = 1
FEATURES = 38

EXPECTED_HASHES = {
    "replay_bundle": "099cd28b5c2a090d1b08d1e07d39e59ae6583854971c539085741fbb369f0246",
    "benchmark": "1e8b89427898df3c3e5efef55135192cf6d48240f0f568c95eb1570470ead113",
    "mission5": "280b314b313b69c72583702898bf135b614d725405587725d4d5f047601327cd",
}
# Committed V14 qualification anchors (reports/droid/v14).
EXPECTED_SUCCESS = 242
EXPECTED_FAILURE_TAXONOMY = {"WRONG_GROUNDED_ANSWER": 18}
EXPECTED_OPERATIONS_TOTAL = 1329  # average_operations 5.111538461538461 x 260

IDX_MAGIC = b"ACP1IDX1"
EVD_MAGIC = b"ACP1EVD1"
NO_ENTITY = 0xFFFFFFFF


def _fnv1a(payload: bytes) -> int:
    value = 14695981039346656037
    for byte in payload:
        value ^= byte
        value = (value * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return value

TIER_CODES = {"10k": 0, "25k": 1, "397k": 2}
PARTITION_CODES = {"development": 0, "tuning": 1}
FAILURE_CODES = {None: 0, "WRONG_GROUNDED_ANSWER": 1}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _norm(value: str) -> str:
    return " ".join(value.casefold().replace("_", " ").split())


def _trigrams(normalized: str) -> tuple[str, ...]:
    padded = f"  {normalized}  "
    return tuple(sorted({padded[index : index + 3] for index in range(len(padded) - 2)}))


class PackAddressReader:
    """Host reader for the ACP1IDX1/ACP1EVD1 pack regions (mirror of the builder)."""

    def __init__(self, pack_dir: Path) -> None:
        self._idx_path = pack_dir / "regions/addressing-index.bin"
        self._evd_path = pack_dir / "regions/evidence.bin"
        with self._idx_path.open("rb") as stream:
            header = stream.read(PAGE)
            fields = struct.unpack_from("<8sIIIIQQQQQQQII", header, 0)
            if fields[0] != IDX_MAGIC:
                raise ValueError("bad addressing-index magic")
            (
                _magic,
                _version,
                page,
                self.surface_count,
                self.gram_count,
                postings_bytes,
                gram_dir_off,
                gram_dir_bytes,
                postings_off,
                postings_bytes_2,
                surface_off,
                surface_dir_bytes,
                gram_entry_size,
                surface_entry_size,
            ) = fields
            if page != PAGE or postings_bytes != postings_bytes_2:
                raise ValueError("addressing-index header inconsistency")
            self._postings_off = postings_off
            self._surface_off = surface_off
            stream.seek(gram_dir_off)
            gram_dir = stream.read(gram_dir_bytes)
            entries_bytes = self.gram_count * gram_entry_size
            self._gram_entries = gram_dir[:entries_bytes]
            self._gram_pool = gram_dir[entries_bytes:]
        with self._evd_path.open("rb") as stream:
            header = stream.read(PAGE)
            efields = struct.unpack_from("<8sIQIQQQQQQ", header, 0)
            if efields[0] != EVD_MAGIC:
                raise ValueError("bad evidence magic")
            self._evd_directory_off = efields[4]
            self._evd_directory_len = efields[5]
            self._evd_blobs_off = efields[6]
            self._evd_directory_entries = self._evd_directory_len // 16
            stream.seek(self._evd_directory_off)
            self._evd_directory = stream.read(self._evd_directory_len)

    def _gram_entry(self, index: int) -> tuple[int, int, int, int]:
        return struct.unpack_from("<IIIH", self._gram_entries, index * 16)[:4]

    def _find_gram(self, gram: str) -> tuple[int, int] | None:
        target = gram.encode("utf-8")
        low, high = 0, self.gram_count - 1
        while low <= high:
            mid = (low + high) // 2
            postings_offset, postings_len, pool_offset, name_len = self._gram_entry(mid)
            name = self._gram_pool[pool_offset : pool_offset + name_len]
            if name == target:
                return postings_offset, postings_len
            if name < target:
                low = mid + 1
            else:
                high = mid - 1
        return None

    def query_candidates(self, normalized_surface: str) -> tuple[tuple[int, ...], "Counter[int]"]:
        """Union of posting surface ids plus per-id gram overlap counts."""

        grams = _trigrams(normalized_surface)
        decorated = []
        for gram in grams:
            entry = self._find_gram(gram)
            if entry is not None:
                decorated.append((entry[1], gram, entry[0]))
        decorated.sort()
        overlaps: Counter[int] = Counter()
        with self._idx_path.open("rb") as stream:
            for length, _gram, postings_offset in decorated:
                stream.seek(self._postings_off + postings_offset)
                payload = stream.read(length)
                count = length // 4
                for surface_id in struct.unpack(f"<{count}I", payload):
                    overlaps[surface_id] += 1
        return tuple(sorted(overlaps)), overlaps

    def surface_entity(self, surface_id: int) -> tuple[int, int]:
        """Return (entity_idx, state_code) for a 1-based surface id."""

        with self._idx_path.open("rb") as stream:
            stream.seek(self._surface_off + (surface_id - 1) * 16)
            entry = stream.read(16)
        entity_idx, state, _name_len, _pool_off, _reserved = struct.unpack("<IHHI I", entry)
        return entity_idx, state

    def evidence_occurrences(self, entity_idx: int) -> int:
        """Occurrence count for an entity index via the evidence directory."""

        low, high = 0, self._evd_directory_entries - 1
        while low <= high:
            mid = (low + high) // 2
            idx, _off, _length, count = struct.unpack_from(
                "<IIII", self._evd_directory, mid * 16
            )
            if idx == entity_idx:
                return count
            if idx < entity_idx:
                low = mid + 1
            else:
                high = mid - 1
        return 0


def load_witnessed_cases(bundle: Path, mission5: Path) -> list[ReplayCase]:
    report = json.load(gzip.open(mission5, "rt", encoding="utf-8"))
    strict: set[tuple[str, str]] = set()
    witnessed: set[tuple[str, str]] = set()
    for row in report["per_case"]:
        key = (str(row["case_id"]), str(row["corpus_tier"]))
        if row.get("partition") in ("development", "tuning"):
            strict.add(key)
            if row.get("training_oracle_reachable") is True:
                witnessed.add(key)
    if len(strict) != 695 or len(witnessed) != 260:
        raise SystemExit(f"cohort mismatch: strict={len(strict)} witnessed={len(witnessed)}")
    manifest = verify_replay_bundle(bundle)
    cases = []
    with gzip.open(bundle / manifest.cases_file, "rt", encoding="utf-8") as stream:
        for line in stream:
            case = ReplayCase.model_validate_json(line)
            if (case.case_id, case.corpus_tier) in witnessed:
                cases.append(case)
    if len(cases) != 260:
        raise SystemExit(f"bundle is missing witnessed cases: {len(cases)}")
    return sorted(cases, key=lambda case: (case.case_id, case.corpus_tier))


class TraceBuilder:
    def __init__(self) -> None:
        self.strings: list[str] = []
        self.string_index: dict[str, int] = {}
        self.cases = bytearray()
        self.decisions = bytearray()
        self.candidates = bytearray()
        self.queries = bytearray()

    def intern(self, value: str) -> int:
        index = self.string_index.get(value)
        if index is None:
            index = len(self.strings)
            self.strings.append(value)
            self.string_index[value] = index
        return index

    def add_case(self, case_id: str, tier: str, partition: str, expected_success: bool,
                 expected_operations: int, expected_failure: str | None,
                 decision_start: int, decision_count: int,
                 query_start: int, query_count: int) -> None:
        record = struct.pack(
            "<IBBBBIIIIIIII",
            self.intern(case_id),
            TIER_CODES[tier],
            PARTITION_CODES[partition],
            1 if expected_success else 0,
            FAILURE_CODES[expected_failure],
            decision_start,
            decision_count,
            query_start,
            query_count,
            expected_operations,
            0,
            0,
            0,
        )
        assert len(record) == 40
        self.cases += record

    def add_decision(self, case_idx: int, step: int, cand_start: int, cand_count: int,
                     chosen_op: int, chosen_args: str) -> None:
        record = struct.pack(
            "<IHIIIIH",
            case_idx,
            step,
            cand_start,
            cand_count,
            chosen_op,
            self.intern(chosen_args),
            0,
        )
        assert len(record) == 24
        self.decisions += record

    def add_candidate(self, row: int, op_id: int, args: str, expected_score: int,
                      features: tuple[int, ...]) -> None:
        if len(features) != FEATURES:
            raise ValueError("feature width changed")
        record = struct.pack("<HqII", row, expected_score, self.intern(args), op_id)
        record += struct.pack(f"<{FEATURES}h", *features)
        assert len(record) == 94
        self.candidates += record

    def add_query(self, surface: str, candidate_ids: tuple[int, ...],
                  entity_indexes: tuple[int, ...], expected_occurrences: int) -> None:
        record = struct.pack(
            "<IIQI QI I".replace(" ", ""),
            self.intern(surface),
            len(candidate_ids),
            _fnv1a(struct.pack(f"<{len(candidate_ids)}I", *candidate_ids)),
            len(entity_indexes),
            _fnv1a(struct.pack(f"<{len(entity_indexes)}I", *entity_indexes)),
            expected_occurrences,
            0,
        )
        assert len(record) == 36
        self.queries += record

    def blob(self) -> bytes:
        pool = bytearray(struct.pack("<I", len(self.strings)))
        for value in self.strings:
            encoded = value.encode("utf-8")
            if len(encoded) > 0xFFFF:
                raise ValueError("string too long")
            pool += struct.pack("<H", len(encoded)) + encoded

        def align(offset: int) -> int:
            return math.ceil(offset / PAGE) * PAGE

        order = (
            ("pool", bytes(pool)),
            ("cases", bytes(self.cases)),
            ("decisions", bytes(self.decisions)),
            ("candidates", bytes(self.candidates)),
            ("queries", bytes(self.queries)),
        )
        header = bytearray(PAGE)
        cursor = PAGE
        offsets: dict[str, tuple[int, int]] = {}
        blob = bytearray(header)
        for name, payload in order:
            offsets[name] = (cursor, len(payload))
            blob += payload
            blob += b"\x00" * (align(len(blob)) - len(blob))
            cursor = len(blob)
        counts = (
            len(self.cases) // 40,
            len(self.decisions) // 24,
            len(self.candidates) // 94,
            len(self.queries) // 36,
        )
        struct.pack_into(
            "<8sIIIIII" + "QQ" * 5,
            header,
            0,
            TRACE_MAGIC,
            TRACE_VERSION,
            PAGE,
            counts[0],
            counts[1],
            counts[2],
            counts[3],
            *(
                value
                for name, _payload in order
                for value in offsets[name]
            ),
        )
        blob[:PAGE] = header
        return bytes(blob)


def rollout_trace(
    case: ReplayCase,
    policy: QuantizedAdaptivePolicy,
    accepted: tuple[str, ...],
    builder: TraceBuilder,
    case_idx: int,
) -> dict:
    """Mirror of v14_controller_qualify._rollout with per-decision export."""

    state = state_from_replay(case)
    decision_start = len(builder.decisions) // 24
    operations = 0
    failure: str | None = None
    success = False
    for step in range(12):
        actions = legal_actions(state, argument_cap=64)
        if not actions:
            failure = "NO_LEGAL_ACTION"
            break
        rows = dict(zip(policy.operation_ids, policy.weights_int8, strict=True))
        cand_start = len(builder.candidates) // 94
        scores = []
        for action in actions:
            row = policy.operation_ids.index(action.operation_id)
            features = quantized_action_features(state, action)
            expected = sum(
                weight * feature
                for weight, feature in zip(rows[action.operation_id], features, strict=True)
            )
            builder.add_candidate(
                row, action.operation_id, _arguments_key(action), expected, features
            )
            scores.append(expected)
        choice_index = max(
            range(len(actions)),
            key=lambda index: (
                scores[index],
                -index,
                -actions[index].operation_id,
                _arguments_key(actions[index]),
            ),
        )
        chosen = actions[choice_index]
        assert chosen == policy.select(state, argument_cap=64)
        builder.add_decision(
            case_idx,
            step,
            cand_start,
            len(actions),
            chosen.operation_id,
            _arguments_key(chosen),
        )
        operations += 1
        state = execute_action(state, chosen)
        if state.terminal is not None:
            success = bool(
                state.terminal == "ANSWER"
                and state.verification_passed
                and canonical_answer_match(state.answer_values, accepted)
            )
            if not success:
                if state.terminal != "ANSWER":
                    failure = "PREMATURE_HALT"
                elif state.verification_passed:
                    failure = "WRONG_GROUNDED_ANSWER"
                else:
                    failure = "VERIFIER_REJECTION"
            break
    else:
        failure = "MAX_DEPTH"
    return {
        "success": success,
        "failure": failure,
        "operations": state.total_actions,
        "decision_count": len(builder.decisions) // 24 - decision_start,
    }


def export_trace(bundle: Path, benchmark: Path, mission5: Path, policy_path: Path,
                 pack_dir: Path, output_dir: Path) -> dict:
    hashes = {
        "benchmark": _sha256(benchmark),
        "mission5": _sha256(mission5),
    }
    bundle_manifest = verify_replay_bundle(bundle)
    hashes["replay_bundle"] = bundle_manifest.bundle_sha256
    if hashes != EXPECTED_HASHES:
        raise SystemExit(f"input hash mismatch: {hashes}")

    policy_payload = json.loads(policy_path.read_text(encoding="utf-8"))
    policy = QuantizedAdaptivePolicy.model_validate(policy_payload)
    benchmark_cases = {
        str(row["case_id"]): row for row in json.loads(benchmark.read_text())["cases"]
    }
    cases = load_witnessed_cases(bundle, mission5)
    reader = PackAddressReader(pack_dir)

    builder = TraceBuilder()
    successes = 0
    failures: Counter[str] = Counter()
    total_operations = 0
    surfaces_seen: set[str] = set()
    for case_idx, case in enumerate(cases):
        gold = benchmark_cases[case.case_id]
        accepted = tuple(str(item) for item in gold.get("accepted_answers", ()))
        outcome = rollout_trace(case, policy, accepted, builder, case_idx)
        successes += int(outcome["success"])
        total_operations += outcome["operations"]
        if outcome["failure"] is not None:
            failures[outcome["failure"]] += 1

        query_start = len(builder.queries) // 36
        state = state_from_replay(case)
        for mention in state.frame.get("entity_mentions") or ():
            surface = str(mention.get("surface") or "")
            if not surface:
                continue
            normalized = _norm(surface)
            if normalized in surfaces_seen:
                continue
            surfaces_seen.add(normalized)
            candidate_ids, overlaps = reader.query_candidates(normalized)
            # The bounded resolution contract: rank the union by gram overlap
            # descending with surface-id tie-break, cap at the controller's 64,
            # then resolve to distinct entity indexes and occurrence totals.
            top = sorted(overlaps, key=lambda sid: (-overlaps[sid], sid))[:64]
            entity_indexes: set[int] = set()
            for surface_id in top:
                entity_idx, _state_code = reader.surface_entity(surface_id)
                if entity_idx != NO_ENTITY:
                    entity_indexes.add(entity_idx)
            occurrence_total = 0
            for entity_idx in sorted(entity_indexes):
                occurrence_total += reader.evidence_occurrences(entity_idx)
            builder.add_query(
                normalized, candidate_ids, tuple(sorted(entity_indexes)), occurrence_total
            )
        query_count = len(builder.queries) // 36 - query_start
        decision_total = len(builder.decisions) // 24
        builder.add_case(
            case.case_id,
            case.corpus_tier,
            case.partition,
            outcome["success"],
            outcome["operations"],
            outcome["failure"] if outcome["failure"] in FAILURE_CODES else None,
            decision_total - outcome["decision_count"],
            outcome["decision_count"],
            query_start,
            query_count,
        )

    if successes != EXPECTED_SUCCESS or dict(failures) != EXPECTED_FAILURE_TAXONOMY:
        raise SystemExit(
            f"rollout reproduction failed: success={successes} failures={dict(failures)}"
        )
    if total_operations != EXPECTED_OPERATIONS_TOTAL:
        raise SystemExit(f"operation total mismatch: {total_operations}")

    blob = builder.blob()
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = output_dir / "phase6-trace.bin"
    trace_path.write_bytes(blob)
    manifest = {
        "schema_version": "aethersparse.v14-p4-trace.v1",
        "file": "phase6-trace.bin",
        "sha256": hashlib.sha256(blob).hexdigest(),
        "bytes": len(blob),
        "cases": len(cases),
        "decisions": len(builder.decisions) // 24,
        "candidates": len(builder.candidates) // 94,
        "queries": len(builder.queries) // 36,
        "strings": len(builder.strings),
        "inputs": hashes,
        "pack_id": json.loads((pack_dir / "manifest.json").read_text())["pack_id"],
        "rollout": {
            "success": successes,
            "failures": dict(failures),
            "operations": total_operations,
        },
        "policy_sha256": hashlib.sha256(policy_path.read_bytes()).hexdigest(),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument(
        "--benchmark",
        type=Path,
        default=ROOT / "data/v050/benchmark/INDEPENDENT_NATURAL_QUERY_SET_V050_R1.json",
    )
    parser.add_argument(
        "--mission5",
        type=Path,
        default=ROOT / "reports/droid/v10/mission5-real-reachability.json.gz",
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=ROOT / "reports/droid/v14/controller-selected-policy-int8.json",
    )
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = export_trace(
        args.bundle, args.benchmark, args.mission5, args.policy, args.pack, args.output
    )
    print(json.dumps(manifest, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
