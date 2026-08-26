#!/usr/bin/env python3
"""Phase 9 Python/native parity driver for the AetherCore V15 service core.

Runs the canonical query script through:
  1. the Python reference vertical slice (src/aethersparse/agent/vertical.py)
     with the frozen V14 int8 policy artifact, and
  2. the native C++ harness binary (service_parity) over
     firmware/p4_aethercore/main/service/,

then diffs the normalized observable contract per query:
disposition, response text, grounded, verifier_accepted, failure_reason,
controller operation ids, semantic address candidate ids, open mandatory
obligations, the 19-field compact COG state, and evidence handle ids.

Exit code 0 iff every query matches (PARITY: PASS).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HOST_TEST = Path(__file__).resolve().parent
REPO = HOST_TEST.parents[2]
RECORDS = REPO / "tests/agent/fixtures/v13-grounded-records.json"
POLICY = REPO / "reports/droid/v14/controller-selected-policy-int8.json"

# Canonical parity script: (session_id, text).  Covers: direct grounded
# answer, pronoun follow-up across turns, ambiguity clarification and
# structured choice selection, choice re-ask on unrecognized reply,
# clarification-without-relation abstain, value-unavailable abstain,
# unsupported-entity abstain, cancel, reset, referent without antecedent.
SCRIPT = [
    ("s-turing", "Who was Alan Turing?"),
    ("s-turing", "Where was he born?"),
    ("s-turing", "What about Mercury?"),
    ("s-turing", "choice-2"),
    ("s-mercury", "Tell me about Mercury"),
    ("s-mercury", "the planet"),
    ("s-mercury", "choice-1"),
    ("s-element", "What is Mercury?"),
    ("s-element", "choice-1"),
    ("s-element", "Where was it born?"),
    ("s-direct", "Where was Alan Turing born?"),
    ("s-misc", "Who discovered unobtainium?"),
    ("s-misc", "cancel"),
    ("s-misc", "Who was Alan Turing?"),
    ("s-misc", "reset"),
    ("s-misc", "Where was he born?"),
]

FIELDS = (
    "disposition",
    "text",
    "grounded",
    "verifier_accepted",
    "failure_reason",
    "operations",
    "candidate_ids",
    "open_obligations",
    "cog_state",
    "evidence_handle_ids",
)


def run_python() -> list[dict]:
    sys.path.insert(0, str(REPO / "src"))
    from aethersparse.agent.session import InMemorySessionStore
    from aethersparse.agent.vertical import (
        AetherCoreRequest,
        AetherCoreVerticalSlice,
        GroundedKnowledgeRecord,
        load_selected_policy_json,
    )

    records = [
        GroundedKnowledgeRecord.model_validate(item)
        for item in json.loads(RECORDS.read_text(encoding="utf-8"))
    ]
    policy = load_selected_policy_json(POLICY.read_bytes())
    runtime = AetherCoreVerticalSlice(records, policy, InMemorySessionStore())
    results = []
    for session_id, text in SCRIPT:
        response = runtime.query(
            AetherCoreRequest(session_id=session_id, text=text)
        )
        results.append(
            {
                "session": session_id,
                "q": text,
                "disposition": response.disposition,
                "text": response.text,
                "grounded": response.grounded,
                "verifier_accepted": response.verifier_accepted,
                "failure_reason": response.failure_reason,
                "operations": list(response.controller_operations),
                "candidate_ids": list(response.semantic_address_candidate_ids),
                "open_obligations": list(response.open_mandatory_obligations),
                "cog_state": list(response.cog_compact_state),
                "evidence_handle_ids": list(response.evidence_handle_ids),
            }
        )
    return results


def run_native(binary: Path) -> list[dict]:
    with tempfile.NamedTemporaryFile(
        "w", suffix=".tsv", delete=False, encoding="utf-8"
    ) as script_file:
        for session_id, text in SCRIPT:
            script_file.write(f"{session_id}\t{text}\n")
        script_path = script_file.name
    completed = subprocess.run(
        [str(binary), str(RECORDS), script_path],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        sys.stderr.write(completed.stderr)
        raise SystemExit(f"native harness failed with code {completed.returncode}")
    results = []
    for line in completed.stdout.splitlines():
        if line.startswith("RESULT "):
            results.append(json.loads(line[len("RESULT ") :]))
    if len(results) != len(SCRIPT):
        raise SystemExit(
            f"native harness produced {len(results)} results for "
            f"{len(SCRIPT)} script queries"
        )
    return results


def main() -> int:
    binary = Path(sys.argv[1]) if len(sys.argv) > 1 else HOST_TEST / "build" / "service_parity"
    if not binary.exists():
        raise SystemExit(f"native binary missing: {binary} (build host_test first)")
    expected = run_python()
    observed = run_native(binary)
    failures = 0
    for index, (want, got) in enumerate(zip(expected, observed, strict=True)):
        bad = [field for field in FIELDS if want[field] != got.get(field)]
        status = "PASS" if not bad else "FAIL"
        if bad:
            failures += 1
        print(f"[{status}] #{index:02d} {want['session']}: {want['q']!r}"
              f" -> {want['disposition']}")
        for field in bad:
            print(f"    {field}:\n      python: {want[field]!r}\n      native: {got.get(field)!r}")
    total = len(expected)
    print(f"PARITY: {'PASS' if failures == 0 else 'FAIL'} "
          f"({total - failures}/{total} queries identical)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
