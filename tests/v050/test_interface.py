from pathlib import Path

from fastapi.testclient import TestClient

from aethersparse.service import create_app


def test_structured_controller_terminal_is_android_accessible() -> None:
    response = TestClient(create_app()).get("/controller")

    assert response.status_code == 200
    assert 'name="viewport"' in response.text
    assert "/v5/controller/query" in response.text
    assert "Bounded evidence graph" in response.text
    assert "Exact bindings" in response.text


def test_structured_controller_terminal_is_packaged_as_static_html() -> None:
    root = Path(__file__).resolve().parents[2]
    interface = root / "web" / "structured_controller" / "index.html"

    assert interface.is_file()
    assert interface.stat().st_size < 64 * 1024
