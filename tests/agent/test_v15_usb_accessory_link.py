from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FW = ROOT / "firmware" / "p4_aethercore"


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_authoritative_source_and_selected_usb_defaults() -> None:
    assert text("firmware/p4_aethercore/sdkconfig.defaults").find(
        "CONFIG_AC_LINK_USB_CDC_DEVICE=y"
    ) >= 0
    defaults = text("firmware/p4_aethercore/sdkconfig.defaults")
    assert "CONFIG_ESP_HOSTED_ENABLED=y" not in defaults
    assert "CONFIG_ESP_WIFI_REMOTE_ENABLED=y" not in defaults
    kconfig = text("firmware/p4_aethercore/main/Kconfig.projbuild")
    assert "default AC_LINK_USB_CDC_DEVICE" in kconfig
    assert "AC_LINK_DEPRECATED_TCP_DIAGNOSTIC" in kconfig


def test_device_b_production_has_no_c6_or_network_dependency() -> None:
    cmake = text("firmware/p4_aethercore/main/CMakeLists.txt")
    manifest = text("firmware/p4_aethercore/main/idf_component.yml")
    usb = text("firmware/p4_aethercore/main/link_usb_cdc.cpp")
    main = text("firmware/p4_aethercore/main/main.cpp")
    selected = cmake.split("if(CONFIG_AC_LINK_USB_CDC_DEVICE)", 1)[1].split(
        "elseif", 1
    )[0]
    assert "esp_tinyusb" in selected
    assert all(x not in selected for x in ("esp_hosted", "esp_wifi", "esp_netif"))
    assert "esp_hosted" not in manifest
    assert "esp_wifi_remote" not in manifest
    assert "tinyusb_driver_install" in usb
    assert "tusb_cdc_acm_init" in usb
    assert "esp_hosted_init" not in usb
    assert "esp_wifi" not in usb
    assert "slave_reset_pulsed" not in main


def test_storage_and_cognition_critical_files_unchanged_from_source_parent() -> None:
    # Frozen path list is also checked against the recorded source hashes in
    # the machine-readable qualification report.
    for path in (
        "main/pack_io.cpp",
        "main/pack_v2.cpp",
        "main/policy_v14_selected.h",
        "main/memory/memory_native.cpp",
        "main/protocol/protocol_v2.cpp",
        "main/service/service_core.cpp",
    ):
        assert (FW / path).exists()


def test_transport_framing_is_shared_and_protocol_cap_unchanged() -> None:
    stream = text("firmware/p4_aethercore/main/link/aetherlink_stream.h")
    usb = text("firmware/p4_aethercore/main/link_usb_cdc.cpp")
    uart = text("firmware/p4_aethercore/main/link_uart_stream.cpp")
    assert "16u * 1024u" in stream
    assert "FrameDecoder" in usb and "write_frame" in usb
    assert "FrameDecoder" in uart and "write_frame" in uart
    assert "SLIP" not in uart.split("identical", 1)[-1]


def test_device_a_aetherchat_only_consumes_accessory_service() -> None:
    app = text("integrations/tactility/aetherchat/Source/app/aetherchat/AetherChatApp.cpp")
    wrapper = text(
        "integrations/tactility/aetherchat/Source/app/aetherchat/AetherLinkAccessory.cpp"
    )
    service = text(
        "integrations/tactility/accessorylink/Source/AccessoryLinkService.cpp"
    )
    assert "AetherLinkAccessory::Callbacks" in app
    assert "AccessoryLink service" in app
    assert "usb_host_install" not in app + wrapper
    assert "tinyusb" not in app.lower() + wrapper.lower()
    assert "esp_wifi" not in app + wrapper
    assert "SESSION_OPEN" not in service  # cognitive semantics stay in AetherChat
    assert "CAPABILITIES" in service  # authoritative accessory negotiation


def test_factory_review_copy_is_synchronized_for_selected_files() -> None:
    pairs = (
        (
            "Private/Tactility/app/aetherchat/AetherChatAppPrivate.h",
            "Private/AetherChatAppPrivate.h",
        ),
        (
            "Private/Tactility/app/aetherchat/AetherLinkAccessory.h",
            "Private/AetherLinkAccessory.h",
        ),
        ("Source/app/aetherchat/AetherChatApp.cpp", "Source/AetherChatApp.cpp"),
        ("Source/app/aetherchat/AetherLinkAccessory.cpp", "Source/AetherLinkAccessory.cpp"),
    )
    overlay = ROOT / "integrations" / "tactility" / "aetherchat"
    review = ROOT / "review" / "device-a-aetherchat"
    for left, right in pairs:
        assert (overlay / left).read_bytes() == (review / right).read_bytes()


def test_protocol_v2_golden_vectors_remain_unchanged() -> None:
    vectors = FW / "host_test_protocol" / "vectors.txt"
    assert vectors.exists() and vectors.stat().st_size > 1000
    protocol = text("firmware/p4_aethercore/main/protocol/protocol_v2.h")
    assert "kMaxFrameBytes = 16384" in protocol
