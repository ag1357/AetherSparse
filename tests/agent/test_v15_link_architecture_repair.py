from __future__ import annotations

import hashlib
import re
import socket
import subprocess
from pathlib import Path

from aethersparse.agent.protocol import (
    FramedJsonCodec,
    HealthPayload,
    MessageType,
    ProtocolMessage,
    SessionOpenPayload,
    SessionResumePayload,
    response,
)

ROOT = Path(__file__).resolve().parents[2]
FACTORY_PARENT = "aa0cebc98d09d390c27cd39a69d158842d8132cd"


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _sha256(path: str) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def _one(root: str, name: str) -> Path:
    matches = tuple((ROOT / root).rglob(name))
    assert len(matches) == 1, f"expected one {name} below {root}, found {matches}"
    return matches[0]


def _source_map(root: str) -> dict[str, bytes]:
    base = ROOT / root
    paths = tuple(base.rglob("*.cpp")) + tuple(base.rglob("*.h"))
    return {path.name: path.read_bytes() for path in paths}


def _recv_frame(peer: socket.socket) -> ProtocolMessage | None:
    header = bytearray()
    while len(header) < 4:
        chunk = peer.recv(4 - len(header))
        if not chunk:
            return None
        header.extend(chunk)
    size = int.from_bytes(header, "big")
    if size == 0 or size > FramedJsonCodec.MAX_FRAME_BYTES:
        return None
    body = bytearray()
    while len(body) < size:
        chunk = peer.recv(size - len(body))
        if not chunk:
            return None
        body.extend(chunk)
    return FramedJsonCodec.decode(bytes(header + body))


def test_frozen_protocol_v2_and_service_are_byte_unchanged() -> None:
    """A socket-orientation repair may not drift the qualified wire/runtime."""
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
        "firmware/p4_aethercore/main/service_runtime.cpp": (
            "eb148e56c8983c53b7670f12ef50f0f0a555dbe3b2389ad73e310342fa20eb73"
        ),
        "firmware/p4_aethercore/main/service_runtime.h": (
            "f10a3c6c017d28fd6b864bd7ccfc85589ba8daac10d05d42b1347dec5e280615"
        ),
        "firmware/p4_aethercore/host_test_protocol/vectors.txt": (
            "f5df619d6dbfe9a3b765db18c92a4f961d680c9615ee1246609be4354b403d09"
        ),
    }
    assert {path: _sha256(path) for path in expected} == expected


def test_protocol_v2_keeps_bounded_big_endian_framing_and_identity_fields() -> None:
    python = _text("src/aethersparse/agent/protocol.py")
    native = _text("firmware/p4_aethercore/main/protocol/protocol_v2.h")
    vectors = _text("firmware/p4_aethercore/host_test_protocol/vectors.txt").splitlines()

    assert "MAX_FRAME_BYTES = 16_384" in python
    assert 'struct.pack(">I", len(body))' in python
    assert 'struct.unpack(">I", frame[:4])' in python
    assert "kMaxFrameBytes = 16384" in native
    assert "request_id" in python and "session_id" in python
    assert "Str request_id" in native and "Str session_id" in native
    assert len(vectors) == 46
    assert any(line.startswith("E truncated_body TRUNCATED_FRAME ") for line in vectors)
    assert any(line.startswith("E length_oversize INVALID_LENGTH ") for line in vectors)


def test_inverted_topology_round_trip_reconnect_and_partial_discard() -> None:
    """Device A passively accepts; Device B actively connects; v2 stays full duplex."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    address = listener.getsockname()

    device_b = socket.create_connection(address)
    device_a, _ = listener.accept()
    opened = ProtocolMessage(
        message_id="terminal-0",
        request_id="request-0",
        session_id="link-test",
        sequence=0,
        type=MessageType.SESSION_OPEN,
        payload=SessionOpenPayload(client_version="tactility-link-test"),
    )
    device_a.sendall(FramedJsonCodec.encode(opened))
    received_open = _recv_frame(device_b)
    assert received_open == opened
    assert received_open is not None
    health = response(
        received_open,
        MessageType.HEALTH,
        HealthPayload(status="ready", runtime_version="v15"),
    )
    device_b.sendall(FramedJsonCodec.encode(health))
    received_health = _recv_frame(device_a)
    assert received_health == health
    assert received_health.request_id == "request-0"
    assert received_health.session_id == "link-test"

    # An incomplete prefix belongs only to this connection and is discarded at EOF.
    device_b.sendall(b"\x00\x00")
    device_b.close()
    assert _recv_frame(device_a) is None
    device_a.close()

    reconnected_b = socket.create_connection(address)
    reconnected_a, _ = listener.accept()
    resumed = ProtocolMessage(
        message_id="terminal-1",
        request_id=None,
        session_id="link-test",
        sequence=1,
        type=MessageType.SESSION_RESUME,
        payload=SessionResumePayload(
            client_version="tactility-link-test", last_received_sequence=0
        ),
    )
    reconnected_a.sendall(FramedJsonCodec.encode(resumed))
    assert _recv_frame(reconnected_b) == resumed

    reconnected_b.close()
    reconnected_a.close()
    listener.close()


def test_device_a_is_passive_single_client_endpoint() -> None:
    review_map = _source_map("review/device-a-aetherchat")
    maintained_map = _source_map("integrations/tactility/aetherchat")
    assert review_map == maintained_map

    live = _one("review/device-a-aetherchat", "AetherLinkTcp.cpp").read_text(encoding="utf-8")
    synced = _one("integrations/tactility/aetherchat", "AetherLinkTcp.cpp").read_text(
        encoding="utf-8"
    )
    assert live == synced

    forbidden = (
        'stopService("WebServer")',
        'startService("WebServer")',
        "setAutoScanPaused",
        "service::wifi::setEnabled",
        "service::wifi::connect",
        "service::wifi::disconnect",
        "esp_wifi_",
    )
    assert not {token for token in forbidden if token in live}
    assert "listen(" in live and "accept(" in live
    assert re.search(r"listen\s*\([^,]+,\s*1\s*\)", live)
    assert "SO_REUSEADDR" in live
    assert "MAX_FRAME_BYTES = 16384" in _one(
        "review/device-a-aetherchat", "AetherLinkTcp.h"
    ).read_text(encoding="utf-8")


def test_device_a_shutdown_preserves_network_and_reconnect_discards_partials() -> None:
    transport = _one("review/device-a-aetherchat", "AetherLinkTcp.cpp").read_text(
        encoding="utf-8"
    )
    app = _one("review/device-a-aetherchat", "AetherChatApp.cpp").read_text(encoding="utf-8")

    # The accepted client may disappear, but the listener survives for the next Device-B client.
    assert re.search(r"while\s*\([^)]*!?stopping[^)]*\)", transport)
    assert "rxUsed = 0" in transport
    assert "SESSION_OPEN once" in app
    assert "SESSION_RESUME" in app
    assert "one-in-flight" in app
    assert "awaitingResponse" in app
    assert "request_id" in app and "session_id" in app


def test_device_b_production_mode_is_configurable_sta_tcp_client() -> None:
    kconfig = _text("firmware/p4_aethercore/main/Kconfig.projbuild")
    link = _text("firmware/p4_aethercore/main/link_tcp.cpp")
    header = _text("firmware/p4_aethercore/main/link_tcp.h")

    required_config = (
        "AC_LINK_PRODUCTION_STA_CLIENT",
        "AC_LINK_LEGACY_B_AP_DIAGNOSTIC",
        "AC_LINK_DEVICE_A_SSID",
        "AC_LINK_DEVICE_A_PASS",
        "AC_LINK_DEVICE_A_IPV4",
        "AC_TCP_PORT",
        "AC_LINK_RECONNECT_DELAY_MS",
        "AC_LINK_STA_CONNECT_TIMEOUT_MS",
        "AC_LINK_DIAGNOSTIC_ONLY",
    )
    assert not {name for name in required_config if name not in kconfig}
    assert re.search(r"config AC_LINK_PRODUCTION_STA_CLIENT\b[\s\S]*?default y", kconfig)
    assert 'default "192.168.4.1"' in kconfig
    assert re.search(r"config AC_TCP_PORT\b[\s\S]*?default 9000", kconfig)

    assert "WIFI_MODE_STA" in link and "WIFI_IF_STA" in link
    assert "esp_netif_create_default_wifi_sta" in link
    assert "connect(" in link
    assert ("sta_ssid" in header and "sta_pass" in header) or (
        "network_ssid" in header and "network_pass" in header
    )
    assert "device_a_ipv4" in header and "tcp_port" in header
    assert "reconnect_delay_ms" in header and "connect_timeout_ms" in header
    assert "diagnostic_only" in header


def test_device_b_preserves_legacy_diagnostic_and_radio_before_sd() -> None:
    kconfig = _text("firmware/p4_aethercore/main/Kconfig.projbuild")
    link = _text("firmware/p4_aethercore/main/link_tcp.cpp")
    main = _text("firmware/p4_aethercore/main/main.cpp")

    assert "LEGACY_B_AP_DIAGNOSTIC" in kconfig
    assert "WIFI_MODE_AP" in link  # retained only inside the non-default legacy branch
    assert "LINK_DIAGNOSTIC_ONLY" in main
    assert (
        "LINK_DIAGNOSTIC_ONLY — NOT AETHERCORE QUALIFICATION" in main
        or "LINK_DIAGNOSTIC_ONLY -- NOT AETHERCORE QUALIFICATION" in main
    )
    assert main.index("ac::linktcp::radio_up") < main.index("run_pack_boot()")
    assert main.index("CONFIG_AC_LINK_DIAGNOSTIC_ONLY") < main.index("run_pack_boot()")
    assert "static" in link and "g_service_stack" in link


def test_no_c6_firmware_or_cognition_dependency_drift() -> None:
    """Only Device-A/B host link code and its tests/reports may move in this mission."""
    assert _sha256("firmware/p4_aethercore/dependencies.lock") == (
        "d85bd35802937522c37a688e6bc3721104580cfc2011836c337b093389fb5f46"
    )
    assert _sha256("firmware/p4_aethercore/main/idf_component.yml") == (
        "34ca7ab39bcb94b88d926002e50afbbeec16f5ea35741f248e7e9c57ae35d8a3"
    )

    changed = subprocess.run(
        ("git", "diff", "--name-only", f"{FACTORY_PARENT}..HEAD"),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    frozen_prefixes = ("src/aethersparse/controller/", "src/aethersparse/cognitive/")
    frozen_files = {
        "firmware/p4_aethercore/main/pack_v2.cpp",
        "firmware/p4_aethercore/main/pack_v2.h",
        "firmware/p4_aethercore/main/policy_v14_selected.h",
        "firmware/p4_aethercore/main/memory/memory_native.cpp",
        "firmware/p4_aethercore/main/memory/memory_native.h",
    }
    assert not [path for path in changed if path.startswith(frozen_prefixes)]
    assert not frozen_files.intersection(changed)
