#!/usr/bin/env python3
"""Python side of the Phase 10 native memory parity harness.

Mirrors host_test_memory/main.cpp op-for-op using the authoritative
src/aethersparse/memory implementation and prints the identical normalized
trace. Byte-identical stdout = behavioral parity.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

import aethersparse.agent  # noqa: E402,F401  # import order resolves the memory/agent circular import
from aethersparse.memory.manager import MemoryTierManager  # noqa: E402
from aethersparse.memory.models import (  # noqa: E402
    MemoryAuthority,
    MemoryManagerState,
    MemoryPayload,
    MemoryProvenance,
    MemoryType,
    MemoryWatermarks,
    PhysicalResidency,
    SemanticTier,
)
from aethersparse.memory.user import UserMemoryService, explicit_remember_payload  # noqa: E402


def emit_record(r) -> None:
    print(
        json.dumps(
            {
                "record": r.memory_id,
                "tier": r.semantic_tier.value,
                "residency": r.residency.value,
                "hash": r.content_hash,
                "epoch": r.modified_epoch,
                "access": r.access_count,
                "deletion": r.deletion_state.value,
                "pinned": r.pinned,
                "vb": r.verification_bound,
            },
            separators=(",", ":"),
        )
    )


def emit_err(op: str, code: int, detail: str) -> None:
    print(json.dumps({"op": op, "error": code, "detail": detail}, separators=(",", ":")))


def main() -> int:
    store_path = sys.argv[1] if len(sys.argv) > 1 else None
    m = MemoryTierManager(
        MemoryManagerState(
            watermarks=MemoryWatermarks(
                ephemeral_limit=4, short_term_limit=3, working_limit=4,
                long_term_limit=8,
            )
        )
    )
    um = UserMemoryService(m)

    print(json.dumps({"remember": explicit_remember_payload(
        "Remember that my favorite color is green.") or ""}, separators=(",", ":")))
    print(json.dumps({"remember": explicit_remember_payload(
        "remember that  Mercury is interesting ") or ""}, separators=(",", ":")))
    print(json.dumps({"remember": explicit_remember_payload(
        "please remember this") or ""}, separators=(",", ":")))
    print(json.dumps({"remember": explicit_remember_payload(
        "REMEMBER THAT Case Matters") or ""}, separators=(",", ":")))

    m.authorize_user_mutation("authz-1")
    res = um.write("operator", "my favorite color is green",
                   authorization_id="authz-1", source_id="uart-user",
                   salience_milli=700)
    emit_record(res.records[0])
    mem_id = res.records[0].memory_id
    try:
        um.write("operator", "unauthorized attempt", authorization_id="",
                 source_id="uart-user")
        emit_err("write-noauth", 0, "")
    except Exception as exc:
        emit_err("write-noauth", 1, str(exc))
    hits = um.search("operator", "favorite color").records
    if hits:
        emit_record(hits[0])
    res = um.edit("operator", mem_id, "my favorite color is blue",
                  authorization_id="authz-2")
    emit_record(res.records[0])
    emit_record(um.read("operator", mem_id).records[0])
    res = um.read("other-user", mem_id)
    emit_err("read-wrong-user", 2 if not res.success else 0, res.detail)

    sys_prov = MemoryProvenance(authority=MemoryAuthority.SYSTEM, source_id="trace")
    for i in range(3):
        rec = m.create(
            memory_type=MemoryType.CONVERSATION_TURN,
            semantic_tier=SemanticTier.SHORT_TERM,
            residency=PhysicalResidency.HOT,
            payload=MemoryPayload(text=f"user turn {i}"),
            provenance=sys_prov,
            session_scope="sess-1",
            salience_milli=100 + i,
            novelty_milli=50,
        )
        emit_record(rec)
    emit_record(m.promote("mem-00000003", reason="recall"))
    emit_record(m.demote("mem-00000003", SemanticTier.SHORT_TERM, reason="stale"))
    try:
        m.demote("mem-00000003", SemanticTier.LONG_TERM, reason="bad")
        emit_err("demote-longer", 0, "")
    except ValueError as exc:
        emit_err("demote-longer", 3, str(exc))

    emit_record(
        m.create(
            memory_type=MemoryType.CONVERSATION_TURN,
            semantic_tier=SemanticTier.SHORT_TERM,
            residency=PhysicalResidency.HOT,
            payload=MemoryPayload(text="overflow turn"),
            provenance=sys_prov,
            session_scope="sess-1",
            salience_milli=5,
            novelty_milli=50,
        )
    )

    emit_record(
        m.create(
            memory_type=MemoryType.SCRATCH,
            semantic_tier=SemanticTier.SHORT_TERM,
            residency=PhysicalResidency.HOT,
            payload=MemoryPayload(text="temporary probe"),
            provenance=sys_prov,
            ttl_epochs=2,
            session_scope="sess-1",
            salience_milli=10,
            novelty_milli=10,
        )
    )
    emit_record(
        m.create(
            memory_type=MemoryType.SCRATCH,
            semantic_tier=SemanticTier.SHORT_TERM,
            residency=PhysicalResidency.HOT,
            payload=MemoryPayload(text="pinned probe"),
            provenance=sys_prov,
            ttl_epochs=2,
            session_scope="sess-1",
            salience_milli=10,
            novelty_milli=10,
            pinned=True,
        )
    )
    print(json.dumps({"reclaimed": m.advance(3)}, separators=(",", ":")))

    for c in m.promotion_candidates(minimum_score=0):
        print(json.dumps({"candidate": c.memory_id,
                          "score": m.promotion_score(c)}, separators=(",", ":")))

    rec = m.bind_selected_evidence(
        payload=MemoryPayload(text="evidence blob head"),
        provenance=MemoryProvenance(
            authority=MemoryAuthority.EXTERNAL_GROUNDED,
            source_id="pack",
            evidence_handle="evh-1",
        ),
        evidence_handle="evh-1",
        cog_bindings=("cog-1",),
        session_scope="sess-1",
    )
    emit_record(rec)
    emit_record(m.mark_verification_bound(rec.memory_id))

    res = um.delete("operator", mem_id, authorization_id="authz-3")
    emit_record(res.records[0])
    print(json.dumps({"compacted": m.compact_tombstones(before_epoch=m.epoch)},
                     separators=(",", ":")))

    state = m.export_state()
    exported = json.dumps(state.model_dump(mode="json"), sort_keys=True,
                          separators=(",", ":"))
    import hashlib

    digest = hashlib.sha256(exported.encode()).hexdigest()
    print(json.dumps({"export_sha256": f"sha256:{digest}",
                      "export_bytes": len(exported)}, separators=(",", ":")))

    if store_path:
        envelope = {"sha256": digest, "state": state.model_dump(mode="json")}
        Path(store_path).write_text(
            json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        # Reload through the authoritative store semantics.
        raw = json.loads(Path(store_path).read_text(encoding="utf-8"))
        payload = json.dumps(raw["state"], sort_keys=True, separators=(",", ":")).encode()
        assert hashlib.sha256(payload).hexdigest() == raw["sha256"]
        restored = MemoryTierManager(MemoryManagerState.model_validate(raw["state"]))
        reexported = json.dumps(
            restored.export_state().model_dump(mode="json"),
            sort_keys=True, separators=(",", ":"),
        )
        print(json.dumps(
            {"roundtrip": "identical" if reexported == exported else "MISMATCH"},
            separators=(",", ":")))

    print("STATE_BEGIN")
    print(exported)
    print("STATE_END")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
