from __future__ import annotations

import json
import struct
from pathlib import Path

from typer.testing import CliRunner

from aethersparse.cli import app
from aethersparse.edge_runtime.deployment_v15 import EvidenceLayout
from aethersparse.edge_runtime.production import (
    compile_native_runtime,
    compile_pack_v2_evidence,
    qualify_production_candidate,
)

ROOT = Path(__file__).parents[2]


def _evidence_fixture(path: Path) -> None:
    header = bytearray(4096)
    entries = struct.pack("<4I4I", 0, 10, 5, 1, 2, 20, 7, 3)
    struct.pack_into(
        "<8sI Q I QQQQQQ",
        header,
        0,
        b"ACP1EVD1",
        1,
        4,
        1,
        4096,
        len(entries),
        8192,
        0,
        8192,
        0,
    )
    path.write_bytes(header + entries)


def test_production_pack_emits_final_direct_image_and_descriptor(tmp_path: Path) -> None:
    source = tmp_path / "evidence.bin"
    output = tmp_path / "evidence-direct-v2.bin"
    _evidence_fixture(source)
    report = compile_pack_v2_evidence(
        source,
        output,
        entity_capacity=4,
        layout=EvidenceLayout.DIRECT_COMPACT_RESIDENT,
    )
    assert report["image_bytes"] == 48
    assert report["resident_bytes"] == 48
    assert report["device_time_repack_required"] is False
    assert output.is_file()
    assert json.loads(output.with_suffix(".bin.json").read_text(encoding="utf-8"))[
        "image_sha256"
    ] == report["image_sha256"]


def test_production_compile_and_qualification_are_callable(tmp_path: Path) -> None:
    compiled = compile_native_runtime(tmp_path / "libaethercore_runtime.so")
    assert compiled["cxx_standard"] == "C++17"
    assert compiled["bytes"] > 0
    qualified = qualify_production_candidate(ROOT / "reports/droid/v15")
    assert qualified["status"] == "PASS"
    assert qualified["pack_layout"] == "direct_compact_resident"


def test_aethercore_production_command_shape() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["aethercore", "--help"])
    assert result.exit_code == 0
    for command in ("compile", "pack", "qualify", "service"):
        assert command in result.stdout
    result = runner.invoke(
        app,
        ["aethercore", "qualify", "--report-root", str(ROOT / "reports/droid/v15")],
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["status"] == "PASS"
