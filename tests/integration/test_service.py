from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from aethersparse.service import create_app


def test_external_query_api() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/v1/query",
        json={
            "request_id": "api-test",
            "session_id": "browser",
            "text": "When did Apollo 11 launch?",
            "trace": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["disposition"] == "answer"
    assert "July 16, 1969" in body["sentence"]
    assert body["citations"]


def test_event_stream_has_ack_then_final() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/v1/query/events",
        json={
            "request_id": "stream-test",
            "session_id": "browser",
            "text": "Who landed on the Moon during Apollo 11?",
        },
    )

    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    assert lines[0] == {"event": "ack", "request_id": "stream-test"}
    assert lines[1]["event"] == "final"
    assert lines[1]["response"]["disposition"] == "answer"


def test_invalid_budget_is_rejected_at_boundary() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/v1/query",
        json={
            "request_id": "bad-budget",
            "session_id": "browser",
            "text": "When did Apollo 11 launch?",
            "budget": {"deadline_ms": 0, "energy_budget_mj": 1},
        },
    )

    assert response.status_code == 422


def test_terminal_contains_transport_only() -> None:
    terminal = Path("web/terminal_simulator/index.html").read_text(encoding="utf-8")
    forbidden = [
        "import aethersparse",
        "from aethersparse",
        "KnowledgeStore",
        "AetherSparseRuntime",
        "RETRIEVE_FACTS",
    ]

    assert "/v1/query" in terminal
    assert not any(token in terminal for token in forbidden)


def test_mobile_autonomy_endpoint_uses_external_service() -> None:
    response = TestClient(create_app()).post(
        "/v1/autonomy/query",
        json={
            "request_id": "mobile-test",
            "session_id": "android",
            "text": "When did Apollo 11 land on the Moon?",
            "trace": True,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["external_service_boundary"] is True
    assert payload["terminal_role"] == "P4/C6 terminal-only"
    assert payload["verification"]["status"] == "PASS"
    assert len(payload["variants"]) == 4
    assert payload["final"]["citations"]


def test_mobile_lab_is_directly_served() -> None:
    response = TestClient(create_app()).get("/lab")
    assert response.status_code == 200
    assert "Android qualification console" in response.text
