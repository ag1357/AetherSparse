# AetherCore P4 accessory firmware

The production link selection is Device-B USB CDC-ACM device mode. It carries
the unchanged AetherLink protocol-v2 stream and does not initialize ESP-Hosted,
the Device-B C6, Wi-Fi, or TCP. Raw UART is the hardware-universal fallback.
The old wireless/TCP implementation is compiled only when the explicit
deprecated diagnostic option is selected.

Build with ESP-IDF 5.5.x:

```sh
idf.py set-target esp32p4
idf.py reconfigure
idf.py build
```

`idf.py reconfigure` resolves the exact manifest-pinned
`espressif/esp_tinyusb` `1.7.6~1` component and creates a fresh dependency lock.
The stale Factory lock that selected `esp_hosted`/`esp_wifi_remote` was removed
with the production wireless dependency.

Relevant Kconfig selections:

- `AC_LINK_USB_CDC_DEVICE`: production default.
- `AC_LINK_UART_FALLBACK`: raw u32be-length + JSON stream, initially 921600 baud.
- `AC_LINK_DEPRECATED_TCP_DIAGNOSTIC`: historical C6/TCP source only.

The current Pack-v2, Kingston 4-bit SDMMC path, resident evidence directory,
cache, cognition, protocol codec, memory and session implementation are frozen.
USB starts independently; storage boot follows unchanged.

The exact current board connectors are not a safe direct USB cable pair. Follow
the qualification report's VBUS/interposer gate; never join the two boards'
5 V rails while both are independently powered.
