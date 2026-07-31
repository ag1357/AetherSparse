from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import aethersparse.service as service
from aethersparse.service import create_app

from .test_topology import _store


def test_cell_retrieval_is_available_only_through_external_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    monkeypatch.setattr(service, "DEFAULT_CORPUS", store.path)
    response = TestClient(create_app()).post(
        "/v3/cells/retrieve",
        json={"text": "How are the Moon and tides related?", "kind": "hybrid"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["external_service_boundary"] is True
    assert body["exact_evidence_graph_is_authoritative"] is True
    assert body["answer_emission_enabled"] is False
    assert body["broad_frontier_expansion"] is False
    assert body["selected_evidence"]
