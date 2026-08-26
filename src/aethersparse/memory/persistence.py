"""Versioned authoritative operational state with atomic integrity envelope."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from threading import RLock
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aethersparse.agent.contracts import SessionState
from aethersparse.cognitive.models import CognitiveObligationGraph

from .manager import MemoryTierManager
from .models import MemoryManagerState, PhysicalResidency


class SpecialistPersistence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    specialist_id: str
    parameter_family_id: str | None = None
    calibration_state: tuple[int, ...] = Field(default=(), max_length=64)
    residency: PhysicalResidency = PhysicalResidency.COLD


class PackBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    pack_id: str
    immutable_identity: str
    generation: int = Field(ge=0)


class SemanticAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    anchor_id: str
    event: str
    epoch: int = Field(ge=0)
    state_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    checkpoint_file: str = Field(pattern=r"^anchor-[0-9]{8}\.json$")


class StateDelta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    generation: int = Field(ge=2)
    operation: str
    payload: dict[str, Any]


class OperationalState(BaseModel):
    """Complete durable state. The native 180-byte COG blob remains a projection."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: str = "aethercore.operational-state.v1"
    service_generation: int = Field(default=1, ge=1)
    service_version: str = "15.0"
    sessions: tuple[SessionState, ...] = ()
    cogs: tuple[CognitiveObligationGraph, ...] = ()
    memory: MemoryManagerState = Field(default_factory=MemoryManagerState)
    specialists: tuple[SpecialistPersistence, ...] = ()
    pack_bindings: tuple[PackBinding, ...] = ()
    semantic_anchors: tuple[SemanticAnchor, ...] = Field(default=(), max_length=64)
    delta_journal_tail: tuple[StateDelta, ...] = Field(default=(), max_length=128)

    @model_validator(mode="after")
    def unique_authoritative_identities(self) -> OperationalState:
        collections = (
            ("session", [item.session_id for item in self.sessions]),
            ("COG", [item.cog_id for item in self.cogs]),
            ("specialist", [item.specialist_id for item in self.specialists]),
            ("pack", [item.pack_id for item in self.pack_bindings]),
            ("anchor", [item.anchor_id for item in self.semantic_anchors]),
        )
        for label, identifiers in collections:
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"duplicate {label} identity")
        return self

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()


class AuthoritativeStateStore:
    """SessionStore-compatible owner of a single crash-safe authoritative state."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.anchor_root = self.path.parent / f"{self.path.stem}.anchors"
        self.anchor_root.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._state = self._load_file() if self.path.exists() else OperationalState()

    @staticmethod
    def _digest(payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()

    def _load_file(self) -> OperationalState:
        envelope = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(envelope, dict) or set(envelope) != {"sha256", "state"}:
            raise ValueError("authoritative state envelope is malformed")
        payload = json.dumps(
            envelope["state"], sort_keys=True, separators=(",", ":")
        ).encode()
        if envelope["sha256"] != self._digest(payload):
            raise ValueError("authoritative state integrity check failed")
        return OperationalState.model_validate(envelope["state"])

    def _commit(self, state: OperationalState) -> None:
        payload = state.canonical_bytes()
        envelope = {
            "sha256": self._digest(payload),
            "state": state.model_dump(mode="json"),
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
        self._state = state

    @property
    def state(self) -> OperationalState:
        return self._state

    def load(self, session_id: str) -> SessionState:
        with self._lock:
            return next(
                (item for item in self._state.sessions if item.session_id == session_id),
                SessionState(session_id=session_id),
            )

    def save(self, state: SessionState) -> None:
        with self._lock:
            sessions = tuple(
                item for item in self._state.sessions if item.session_id != state.session_id
            )
            sessions = tuple(sorted((*sessions, state), key=lambda item: item.session_id))
            generation = self._state.service_generation + 1
            delta = StateDelta(
                generation=generation,
                operation="SESSION_UPSERT",
                payload={"session": state.model_dump(mode="json")},
            )
            self._commit(
                self._state.model_copy(
                    update={
                        "sessions": sessions,
                        "service_generation": generation,
                        "delta_journal_tail": (*self._state.delta_journal_tail, delta)[-128:],
                    }
                )
            )

    def reset(self, session_id: str) -> SessionState:
        """Reset one session while preserving long-term memory and every other session."""

        state = SessionState(session_id=session_id)
        self.save(state)
        return state

    def save_complete(
        self,
        *,
        memory: MemoryTierManager,
        cogs: tuple[CognitiveObligationGraph, ...] | None = None,
        specialists: tuple[SpecialistPersistence, ...] | None = None,
        pack_bindings: tuple[PackBinding, ...] | None = None,
    ) -> OperationalState:
        with self._lock:
            generation = self._state.service_generation + 1
            components: dict[str, object] = {
                "memory": memory.export_state(),
            }
            if cogs is not None:
                components["cogs"] = cogs
            if specialists is not None:
                components["specialists"] = specialists
            if pack_bindings is not None:
                components["pack_bindings"] = pack_bindings
            delta = StateDelta(
                generation=generation,
                operation="COMPONENTS_REPLACE",
                payload={
                    key: (
                        value.model_dump(mode="json")
                        if isinstance(value, BaseModel)
                        else [item.model_dump(mode="json") for item in value]
                        if isinstance(value, tuple)
                        else value
                    )
                    for key, value in components.items()
                },
            )
            updates: dict[str, object] = {
                **components,
                "service_generation": generation,
                "delta_journal_tail": (*self._state.delta_journal_tail, delta)[-128:],
            }
            updated = self._state.model_copy(update=updates)
            self._commit(updated)
            return updated

    def restore_memory(self) -> MemoryTierManager:
        return MemoryTierManager(self._state.memory)

    def add_anchor(self, event: str, epoch: int) -> SemanticAnchor:
        if event not in {
            "USER_TURN_COMMITTED",
            "CLARIFICATION_RESOLVED",
            "ENTITY_BINDING_RESOLVED",
            "EVIDENCE_VERIFIED",
            "OBLIGATION_COMPLETED",
            "TOOL_RESULT_ACCEPTED",
            "USER_MEMORY_MUTATED",
            "TASK_CHECKPOINT",
            "ANSWER_COMMITTED",
        }:
            raise ValueError("unknown semantic anchor event")
        snapshot = self._state
        digest = f"sha256:{self._digest(snapshot.canonical_bytes())}"
        anchor_number = 1 + max(
            (
                int(item.anchor_id.removeprefix("anchor-"))
                for item in self._state.semantic_anchors
            ),
            default=0,
        )
        checkpoint_file = f"anchor-{anchor_number:08d}.json"
        checkpoint_path = self.anchor_root / checkpoint_file
        temporary = checkpoint_path.with_suffix(".json.tmp")
        temporary.write_bytes(snapshot.canonical_bytes() + b"\n")
        os.replace(temporary, checkpoint_path)
        anchor = SemanticAnchor(
            anchor_id=checkpoint_file.removesuffix(".json"),
            event=event,
            epoch=epoch,
            state_digest=digest,
            checkpoint_file=checkpoint_file,
        )
        anchors = (*self._state.semantic_anchors, anchor)[-64:]
        self._commit(self._state.model_copy(update={"semantic_anchors": anchors}))
        return anchor

    @staticmethod
    def _apply_delta(state: OperationalState, delta: StateDelta) -> OperationalState:
        if delta.operation == "SESSION_UPSERT":
            session = SessionState.model_validate(delta.payload["session"])
            sessions = tuple(
                item for item in state.sessions if item.session_id != session.session_id
            )
            sessions = tuple(sorted((*sessions, session), key=lambda item: item.session_id))
            return state.model_copy(
                update={"sessions": sessions, "service_generation": delta.generation}
            )
        if delta.operation == "COMPONENTS_REPLACE":
            updates: dict[str, object] = {"service_generation": delta.generation}
            if "memory" in delta.payload:
                updates["memory"] = MemoryManagerState.model_validate(delta.payload["memory"])
            if "cogs" in delta.payload:
                updates["cogs"] = tuple(
                    CognitiveObligationGraph.model_validate(item)
                    for item in delta.payload["cogs"]
                )
            if "specialists" in delta.payload:
                updates["specialists"] = tuple(
                    SpecialistPersistence.model_validate(item)
                    for item in delta.payload["specialists"]
                )
            if "pack_bindings" in delta.payload:
                updates["pack_bindings"] = tuple(
                    PackBinding.model_validate(item) for item in delta.payload["pack_bindings"]
                )
            return state.model_copy(update=updates)
        raise ValueError("unknown authoritative state delta")

    def restore_anchor_and_replay(self, anchor_id: str) -> OperationalState:
        """Restore a digest-bound checkpoint then replay its deterministic journal suffix."""

        with self._lock:
            anchor = next(
                (item for item in self._state.semantic_anchors if item.anchor_id == anchor_id),
                None,
            )
            if anchor is None:
                raise KeyError(anchor_id)
            path = self.anchor_root / anchor.checkpoint_file
            payload = path.read_bytes().rstrip(b"\n")
            if f"sha256:{self._digest(payload)}" != anchor.state_digest:
                raise ValueError("semantic anchor integrity check failed")
            restored = OperationalState.model_validate_json(payload)
            deltas = tuple(
                delta
                for delta in self._state.delta_journal_tail
                if delta.generation > restored.service_generation
            )
            expected = restored.service_generation + 1
            for delta in deltas:
                if delta.generation != expected:
                    raise ValueError("semantic anchor delta journal is not contiguous")
                restored = self._apply_delta(restored, delta)
                expected += 1
            restored = restored.model_copy(
                update={
                    "semantic_anchors": self._state.semantic_anchors,
                    "delta_journal_tail": self._state.delta_journal_tail,
                }
            )
            self._commit(restored)
            return restored
