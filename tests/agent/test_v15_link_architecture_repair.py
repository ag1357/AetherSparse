"""Cross-cutting V15 AetherLink invariants retained through USB correction."""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "b5805d8deae14f884f979a2d2b7ac1c84bf8edb1"


def data(path: str) -> bytes:
    return (ROOT / path).read_bytes()


def text(path: str) -> str:
    return data(path).decode()


def sha(path: str) -> str:
    return hashlib.sha256(data(path)).hexdigest()


def test_protocol_v2_codec_and_golden_vectors_are_frozen() -> None:
    expected = {
        "src/aethersparse/agent/protocol.py": (
            "0c0f32f1fc76a25d6073d8e35fbd90bf84deec35da8d54b5f5c6861b72d9ce04"
        ),
        "firmware/p4_aethercore/main/protocol/protocol_v2.cpp": (
            "7cc174d58d988d90fa99fc93819db9615f786677dfed233610776ed5dd86a425"
        ),
        "firmware/p4_aethercore/main/protocol/protocol_v2.h": (
            "4cdc83167836507b3ca0b5ddea3dd7d671e26c787f15da2a2f7ce5db6fb01d9d"
        ),
        "firmware/p4_aethercore/host_test_protocol/vectors.txt": (
            "f5df619d6dbfe9a3b765db18c92a4f961d680c9615ee1246609be4354b403d09"
        ),
    }
    assert {path: sha(path) for path in expected} == expected


def test_cognition_pack_storage_and_memory_are_source_identical() -> None:
    frozen = (
        "firmware/p4_aethercore/main/pack_io.cpp",
        "firmware/p4_aethercore/main/pack_v2.cpp",
        "firmware/p4_aethercore/main/pack_v2.h",
        "firmware/p4_aethercore/main/policy_v14_selected.h",
        "firmware/p4_aethercore/main/memory/memory_native.cpp",
        "firmware/p4_aethercore/main/memory/memory_native.h",
        "firmware/p4_aethercore/main/service/service_core.cpp",
    )
    for path in frozen:
        parent = subprocess.run(
            ("git", "show", f"{SOURCE}:{path}"), cwd=ROOT, check=True,
            capture_output=True,
        ).stdout
        assert data(path) == parent, path


def test_aetherchat_uses_service_and_keeps_session_semantics() -> None:
    app = text("integrations/tactility/aetherchat/Source/app/aetherchat/AetherChatApp.cpp")
    wrapper = text(
        "integrations/tactility/aetherchat/Source/app/aetherchat/"
        "AetherLinkAccessory.cpp"
    )
    assert "AetherLinkAccessory::Callbacks" in app
    assert "SESSION_OPEN once" in app and "SESSION_RESUME" in app
    assert "one-in-flight" in app and "awaitingResponse" in app
    assert "usb_host_install" not in app + wrapper
    assert "esp_wifi" not in app + wrapper


def test_wireless_code_is_explicitly_deprecated() -> None:
    device_a = text("integrations/tactility/aetherchat/Deprecated/AetherLinkTcp.cpp")
    device_b = text("firmware/p4_aethercore/main/link_tcp.cpp")
    defaults = text("firmware/p4_aethercore/sdkconfig.defaults")
    assert "DEPRECATED_TRANSPORT" in device_a
    assert "WIFI_MODE_STA" in device_b and "WIFI_MODE_AP" in device_b
    assert "# CONFIG_AC_LINK_DEPRECATED_TCP_DIAGNOSTIC is not set" in defaults
