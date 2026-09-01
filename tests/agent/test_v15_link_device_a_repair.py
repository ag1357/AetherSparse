from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REVIEW = ROOT / "review" / "device-a-aetherchat"
OVERLAY = ROOT / "integrations" / "tactility" / "aetherchat"
REVIEW_TCP = REVIEW / "Deprecated" / "AetherLinkTcp.cpp"
OVERLAY_TCP = OVERLAY / "Deprecated" / "AetherLinkTcp.cpp"


def test_device_a_overlay_and_factory_review_copy_are_synchronized() -> None:
    private_names = (
        "AetherChatAppPrivate.h",
        "AetherChatView.h",
        "AetherLinkAccessory.h",
        "AetherLinkProtocol.h",
        "protocol_v2_wire.h",
    )
    source_names = (
        "AetherChatApp.cpp",
        "AetherChatView.cpp",
        "AetherLinkAccessory.cpp",
        "AetherLinkProtocol.cpp",
    )
    for name in private_names:
        assert (REVIEW / "Private" / name).read_bytes() == (
            OVERLAY / "Private" / "Tactility" / "app" / "aetherchat" / name
        ).read_bytes()
    for name in source_names:
        assert (REVIEW / "Source" / name).read_bytes() == (
            OVERLAY / "Source" / "app" / "aetherchat" / name
        ).read_bytes()
    for name in ("AetherLinkTcp.h", "AetherLinkTcp.cpp"):
        assert (REVIEW / "Deprecated" / name).read_bytes() == (
            OVERLAY / "Deprecated" / name
        ).read_bytes()


def test_selected_device_a_transport_never_owns_network_or_usb_hardware() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (REVIEW / "Source").glob("*.cpp")
    )
    forbidden = (
        'stopService("WebServer")',
        'startService("WebServer")',
        "service::wifi::setAutoScanPaused",
        "service::wifi::setEnabled",
        "service::wifi::connect",
        "service::wifi::disconnect",
        "esp_wifi_",
        "usb_host_install",
        "tinyusb",
        "listen(fd, 1)",
        "service::webserver::isWebServerEnabled()",
    )
    for token in forbidden:
        assert token not in source

    assert "service::accessorylink::subscribe" in source
    assert "service::accessorylink::sendFrame" in source


def test_deprecated_tcp_diagnostic_is_preserved_outside_production() -> None:
    source = REVIEW_TCP.read_text(encoding="utf-8")
    header = (REVIEW / "Deprecated" / "AetherLinkTcp.h").read_text(encoding="utf-8")

    assert "static constexpr uint16_t LISTEN_PORT = 9000;" in header
    assert "static constexpr size_t MAX_FRAME_BYTES = 16384;" in header
    assert "address.sin_addr.s_addr = htonl(INADDR_ANY);" in source
    assert "setsockopt(fd, SOL_SOCKET, SO_REUSEADDR" in source
    assert "listen(fd, 1)" in source
    assert "accept(fd," in source
    assert source.count("while (!stopping && isTactilityApReady())") >= 3
    assert 'setState("Link: waiting for Device B reconnect")' in source


def test_deprecated_tcp_keeps_historical_wire_evidence() -> None:
    source = REVIEW_TCP.read_text(encoding="utf-8")

    assert "putLengthHeader" in source
    assert "getLengthHeader" in source
    assert "length == 0 || length > MAX_FRAME_BYTES" in source
    assert "rxUsed = 0;" in source
    assert "shutdown(socketFd, SHUT_RDWR);" in source
    assert "shutdown(listenerFd, SHUT_RDWR);" in source

    stop_body = source.split("void AetherLinkTcp::stop()", maxsplit=1)[1].split(
        "bool AetherLinkTcp::isConnected()", maxsplit=1
    )[0]
    assert "wakeSockets();" in stop_body
    assert "service::" not in stop_body
    assert "settings::" not in stop_body
