"""Canonical workspace and route signatures for observer records."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence

from aethersparse.observer.models import CycleTelemetry


def canonical_state_signature(state: Mapping[str, object]) -> str:
    """Hash a symbolic workspace view without retaining its source surfaces."""

    payload = json.dumps(state, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(payload).hexdigest()


def route_signature(cycles: Sequence[CycleTelemetry]) -> str:
    """Return a compact, deterministic and human-readable cognitive route."""

    lines = []
    for cycle in cycles:
        experts = ",".join(sorted(cycle.active_experts)) or "NONE"
        lines.append(f"C{cycle.cycle_number}:{experts}")
    final = cycles[-1]
    lines.append(f"HALT:{final.depth_decision.value}:{final.verifier_status.value}")
    return "\n".join(lines)


def signature_sha256(signature: str) -> str:
    return hashlib.sha256(signature.encode("utf-8")).hexdigest()


def verify_route_signature(record_signature: str, record_hash: str) -> bool:
    return signature_sha256(record_signature) == record_hash
