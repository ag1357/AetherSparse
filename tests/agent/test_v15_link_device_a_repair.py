from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REVIEW = ROOT / "review" / "device-a-aetherchat"
OVERLAY = ROOT / "integrations" / "tactility" / "aetherchat"
REVIEW_TCP = REVIEW / "Source" / "AetherLinkTcp.cpp"
OVERLAY_TCP = OVERLAY / "Source" / "app" / "aetherchat" / "AetherLinkTcp.cpp"


def test_device_a_overlay_and_factory_review_copy_are_synchronized() -> None:
    private_names = (
        "AetherChatAppPrivate.h",
        "AetherChatView.h",
        "AetherLinkProtocol.h",
        "AetherLinkTcp.h",
        "protocol_v2_wire.h",
    )
    source_names = (
        "AetherChatApp.cpp",
        "AetherChatView.cpp",
        "AetherLinkProtocol.cpp",
        "AetherLinkTcp.cpp",
    )
    for name in private_names:
        assert (REVIEW / "Private" / name).read_bytes() == (
            OVERLAY / "Private" / "Tactility" / "app" / "aetherchat" / name
        ).read_bytes()
    for name in source_names:
        assert (REVIEW / "Source" / name).read_bytes() == (
            OVERLAY / "Source" / "app" / "aetherchat" / name
        ).read_bytes()


def test_device_a_transport_never_owns_tactility_networking() -> None:
    source = REVIEW_TCP.read_text(encoding="utf-8")
    forbidden = (
        'stopService("WebServer")',
        'startService("WebServer")',
        "service::wifi::setAutoScanPaused",
        "service::wifi::setEnabled",
        "service::wifi::connect",
        "service::wifi::disconnect",
        "esp_wifi_",
    )
    for token in forbidden:
        assert token not in source

    assert "settings::webserver::loadOrGetDefault()" in source
    assert "settings::webserver::WiFiMode::AccessPoint" in source
    assert "service::webserver::isWebServerEnabled()" in source
    assert 'setState("Enable Tactility Web Server / Access Point")' in source
    assert "webSettings.apPassword" not in source


def test_device_a_is_a_bounded_reconnecting_tcp_server() -> None:
    source = REVIEW_TCP.read_text(encoding="utf-8")
    header = (REVIEW / "Private" / "AetherLinkTcp.h").read_text(encoding="utf-8")

    assert "static constexpr uint16_t LISTEN_PORT = 9000;" in header
    assert "static constexpr size_t MAX_FRAME_BYTES = 16384;" in header
    assert "address.sin_addr.s_addr = htonl(INADDR_ANY);" in source
    assert "setsockopt(fd, SOL_SOCKET, SO_REUSEADDR" in source
    assert "listen(fd, 1)" in source
    assert "accept(fd," in source
    assert source.count("while (!stopping && isTactilityApReady())") >= 3
    assert 'setState("Link: waiting for Device B reconnect")' in source


def test_device_a_disconnect_discards_partial_frame_and_keeps_wire_exact() -> None:
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
