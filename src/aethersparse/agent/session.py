"""Bounded session persistence independent of terminal transport."""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Protocol

from aethersparse.agent.contracts import SessionState


class SessionStore(Protocol):
    def load(self, session_id: str) -> SessionState: ...

    def save(self, state: SessionState) -> None: ...

    def reset(self, session_id: str) -> SessionState: ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._states: dict[str, SessionState] = {}
        self._lock = RLock()

    def load(self, session_id: str) -> SessionState:
        with self._lock:
            return self._states.get(session_id, SessionState(session_id=session_id))

    def save(self, state: SessionState) -> None:
        with self._lock:
            self._states[state.session_id] = state

    def reset(self, session_id: str) -> SessionState:
        state = SessionState(session_id=session_id)
        self.save(state)
        return state


class JsonSessionStore:
    """Atomic JSON persistence with one strictly validated file per session."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    @staticmethod
    def _safe_id(session_id: str) -> str:
        if not session_id or len(session_id) > 128:
            raise ValueError("invalid session ID")
        if any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for character in session_id
        ):
            raise ValueError("session ID must be URL/filesystem safe")
        return session_id

    def _path(self, session_id: str) -> Path:
        return self.root / f"{self._safe_id(session_id)}.json"

    def load(self, session_id: str) -> SessionState:
        path = self._path(session_id)
        with self._lock:
            if not path.exists():
                return SessionState(session_id=session_id)
            return SessionState.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, state: SessionState) -> None:
        path = self._path(state.session_id)
        temporary = path.with_suffix(".json.tmp")
        payload = json.dumps(state.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        with self._lock:
            temporary.write_text(payload + "\n", encoding="utf-8")
            os.replace(temporary, path)

    def reset(self, session_id: str) -> SessionState:
        state = SessionState(session_id=session_id)
        self.save(state)
        return state
