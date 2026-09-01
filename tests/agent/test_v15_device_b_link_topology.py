"""Selected Device-B accessory topology regressions.

Earlier STA/client expectations are superseded. TCP remains historical source
evidence; production is the C6-free USB CDC-ACM device path.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FW = ROOT / "firmware" / "p4_aethercore"


def read(path: str) -> str:
    return (FW / path).read_text(encoding="utf-8")


def test_usb_device_is_selected_and_uart_is_fallback() -> None:
    kconfig = read("main/Kconfig.projbuild")
    defaults = read("sdkconfig.defaults")
    assert "default AC_LINK_USB_CDC_DEVICE" in kconfig
    assert "config AC_LINK_UART_FALLBACK" in kconfig
    assert "config AC_LINK_DEPRECATED_TCP_DIAGNOSTIC" in kconfig
    assert "CONFIG_AC_LINK_USB_CDC_DEVICE=y" in defaults
    assert "# CONFIG_AC_LINK_DEPRECATED_TCP_DIAGNOSTIC is not set" in defaults


def test_usb_starts_without_c6_and_pack_boot_remains() -> None:
    usb = read("main/link_usb_cdc.cpp")
    main = read("main/main.cpp")
    assert "tinyusb_driver_install" in usb and "tusb_cdc_acm_init" in usb
    assert "esp_hosted" not in usb and "esp_wifi" not in usb
    assert main.index("ac::linkusb::start()") < main.index("run_pack_boot()")
    assert "slave_reset_pulsed" not in main and "gpio_set_level" not in main


def test_hotplug_discards_partial_stream_state() -> None:
    usb = read("main/link_usb_cdc.cpp")
    assert "TINYUSB_EVENT_DETACHED" in usb
    assert "g_decoder.reset()" in usb
    assert "partial_discarded" in usb


def test_deprecated_tcp_source_is_preserved_but_not_linked_by_default() -> None:
    tcp = read("main/link_tcp.cpp")
    cmake = read("main/CMakeLists.txt")
    defaults = read("sdkconfig.defaults")
    assert "WIFI_MODE_AP" in tcp and "WIFI_MODE_STA" in tcp
    legacy = cmake.split("elseif(CONFIG_AC_LINK_DEPRECATED_TCP_DIAGNOSTIC)", 1)[1]
    assert "link_tcp.cpp" in legacy and "esp_hosted" in legacy
    assert "# CONFIG_AC_LINK_DEPRECATED_TCP_DIAGNOSTIC is not set" in defaults


def test_protocol_v2_frame_contract_is_shared_and_big_endian() -> None:
    stream = read("main/link/aetherlink_stream.cpp")
    protocol = read("main/protocol/protocol_v2.h")
    assert "constexpr uint32_t kMaxFrameBytes = 16384" in protocol
    assert "uint8_t header[kLengthBytes]" in stream
    assert "length >> 24" in stream
    assert "FrameDecoder::reset" in stream


def test_usb_dependency_replaces_hosted_in_production_manifest() -> None:
    component = read("main/idf_component.yml")
    defaults = read("sdkconfig.defaults")
    assert 'espressif/esp_tinyusb: "1.7.6~1"' in component
    assert "esp_hosted" not in component and "esp_wifi_remote" not in component
    assert "# CONFIG_ESP_HOSTED_ENABLED is not set" in defaults
