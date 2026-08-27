from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FW = ROOT / "firmware" / "p4_aethercore"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_device_b_sta_client_is_selected_and_credentials_fail_closed() -> None:
    kconfig = _read(FW / "main" / "Kconfig.projbuild")
    defaults = _read(FW / "sdkconfig.defaults")

    assert "default AC_LINK_PRODUCTION_STA_CLIENT" in kconfig
    assert "config AC_LINK_LEGACY_B_AP_DIAGNOSTIC" in kconfig
    assert 'CONFIG_AC_LINK_DEVICE_A_SSID=""' in defaults
    assert 'CONFIG_AC_LINK_DEVICE_A_PASS=""' in defaults
    assert 'CONFIG_AC_LINK_DEVICE_A_IPV4="192.168.4.1"' in defaults
    assert "CONFIG_AC_LINK_PRODUCTION_STA_CLIENT=y" in defaults
    assert "# CONFIG_AC_LINK_LEGACY_B_AP_DIAGNOSTIC is not set" in defaults


def test_production_radio_is_sta_dhcp_before_sd_pack_boot() -> None:
    link = _read(FW / "main" / "link_tcp.cpp")
    main = _read(FW / "main" / "main.cpp")

    production = link.split("#if CONFIG_AC_LINK_PRODUCTION_STA_CLIENT", 2)[2].split("#else", 1)[0]
    assert "esp_netif_create_default_wifi_sta()" in production
    assert "esp_wifi_set_mode(WIFI_MODE_STA)" in production
    assert "esp_wifi_connect()" in production
    assert "IP_EVENT_STA_GOT_IP" in link
    assert "password" not in link[link.index('MEAS {\\"link\\":\\"sta\\"') :][:500]
    assert main.index("ac::linktcp::radio_up(lcfg)") < main.index("run_pack_boot()")


def test_production_socket_orientation_and_reconnect_are_client_side() -> None:
    link = _read(FW / "main" / "link_tcp.cpp")
    phase_two = link.split("/* Phase 2:", 1)[1]
    client = (
        phase_two.split("#if CONFIG_AC_LINK_LEGACY_B_AP_DIAGNOSTIC", 1)[1]
        .split("#else", 1)[1]
        .split("#endif", 1)[0]
    )

    assert "inet_pton(AF_INET, g_cfg.device_a_ipv4" in client
    assert "connect(fd," in client
    assert '\\"mode\\":\\"sta_client\\"' in client
    assert "xEventGroupWaitBits(g_wifi_events, kStaHasIp" in client
    assert '\\"session_state\\":\\"preserved\\"' in client
    assert "shutdown(g_client, SHUT_RDWR)" in link
    assert "listen(" not in client
    assert "accept(" not in client


def test_legacy_ap_server_is_guarded_and_not_default() -> None:
    link = _read(FW / "main" / "link_tcp.cpp")
    legacy_radio = link.split("#else", 1)[1].split("#endif", 1)[0]

    assert "esp_netif_create_default_wifi_ap()" in legacy_radio
    assert "esp_wifi_set_mode(WIFI_MODE_AP)" in legacy_radio
    assert "WIFI_IF_AP" in legacy_radio
    assert "CONFIG_AC_LINK_LEGACY_B_AP_DIAGNOSTIC" in link


def test_link_diagnostic_mode_skips_pack_and_cognition() -> None:
    main = _read(FW / "main" / "main.cpp")
    diagnostic = main.rsplit("#if CONFIG_AC_LINK_DIAGNOSTIC_ONLY", 1)[1].split("#else", 1)[0]

    assert "LINK_DIAGNOSTIC_ONLY -- NOT AETHERCORE QUALIFICATION" in diagnostic
    assert '\\"pack_mounted\\":false' in diagnostic
    assert '\\"cognition_started\\":false' in diagnostic
    assert "run_pack_boot" not in diagnostic
    assert "service_init" not in diagnostic
    assert "ac::linktcp::serve()" in diagnostic


def test_protocol_v2_frame_contract_is_still_bounded_and_big_endian() -> None:
    link = _read(FW / "main" / "link_tcp.cpp")
    protocol = _read(FW / "main" / "protocol" / "protocol_v2.h")

    assert "constexpr uint32_t kMaxFrameBytes = 16384" in protocol
    assert "uint8_t hdr[4]" in link
    assert "body_len >> 24" in link
    assert "frame_partial_discard" in link
    assert "DecodeFrame(frame, frame_len, req)" in link
    assert "sizeof(kProtocolVersion) - 1" in link


def test_factory_hosted_dependencies_and_static_worker_are_preserved() -> None:
    link = _read(FW / "main" / "link_tcp.cpp")
    component = _read(FW / "main" / "idf_component.yml")
    defaults = _read(FW / "sdkconfig.defaults")

    assert 'espressif/esp_hosted: "2.12.11"' in component
    assert 'espressif/esp_wifi_remote: "1.2.3"' in component
    assert "xTaskCreateStatic(service_task" in link
    assert "g_service_stack[16384 / sizeof(StackType_t)]" in link
    assert "# CONFIG_ESP_HOSTED_TRANSPORT_RESTART_ON_FAILURE is not set" in defaults
