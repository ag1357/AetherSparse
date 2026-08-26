from __future__ import annotations

from pathlib import Path

from aethersparse.agent.knowledge import SourceType
from aethersparse.agent.source_adapters import FileSourceAdapter, build_python_impact_graph


def test_markdown_html_manual_and_source_adapters_preserve_provenance(tmp_path: Path) -> None:
    (tmp_path / "guide.md").write_text("# Guide\nUse the typed API.\n", encoding="utf-8")
    (tmp_path / "reference.html").write_text(
        "<h1>Reference</h1><script>discard()</script><p>Verified detail.</p>", encoding="utf-8"
    )
    adapter = FileSourceAdapter(
        tmp_path,
        source_namespace="project-docs",
        source_type=SourceType.SOFTWARE_DOCUMENTATION,
        source_version="git:test",
        license_id="Apache-2.0",
        patterns=("*.md", "*.html"),
    )
    objects = tuple(adapter.iter_objects())
    assert len(objects) == 2
    assert {item.title for item in objects} == {"Guide", "Reference"}
    assert all(item.provenance.content_digest.startswith("sha256:") for item in objects)
    assert "discard" not in next(item for item in objects if item.title == "Reference").body


def test_source_impact_graph_finds_definitions_references_dependencies_and_tests(
    tmp_path: Path,
) -> None:
    package = tmp_path / "demo"
    tests = tmp_path / "tests"
    package.mkdir()
    tests.mkdir()
    (package / "api.py").write_text("def public_api(x: int) -> int:\n    return x + 1\n")
    (package / "consumer.py").write_text(
        "from demo.api import public_api\n\ndef use() -> int:\n    return public_api(2)\n"
    )
    (tests / "test_api.py").write_text(
        "from demo.api import public_api\n\ndef test_api():\n    assert public_api(1) == 2\n"
    )
    graph = build_python_impact_graph(tmp_path)
    assert graph.affected_files("public_api") == (
        "demo/api.py",
        "demo/consumer.py",
        "tests/test_api.py",
    )
    assert ("demo/api.py", "tests/test_api.py") in graph.test_mapping
