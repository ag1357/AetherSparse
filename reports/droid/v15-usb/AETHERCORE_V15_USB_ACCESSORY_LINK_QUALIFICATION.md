# AetherCore V15 USB Accessory-Link Qualification

## Classification

**USB_ACCESSORY_LINK_HARDWARE_BLOCKED**

The USB-first, hardware-agnostic software boundary is implemented and host-qualified. The exact
current Waveshare boards do not, however, provide a safe standards-compliant direct USB host/device
cable path. Factory must not connect their exposed 5 V rails or improvise a Type-C cable. This is a
physical connector/VBUS-role blocker, not an AetherCore, protocol, Pack-v2, SDMMC, or cognition
failure.

## Frozen checkpoint

- Required source commit: `b5805d8deae14f884f979a2d2b7ac1c84bf8edb1`
- Required source tree: `a6e0e8d92783c2f7b7c061f594979b95051620d5`
- Branch: `work/aethercore-v15-usb-accessory-link`
- Published qualified-content commit: `60751b6de4ebe20cf85ee03a7a490d32bf307dbe`
- Published qualified-content tree: `8e56fad63e002eca3045de8d177bd6a96accd582`
- Pinned Tactility reference: `0ee2415f3b5a063fadc2015d50d0d1c1c8b0b6e1`

The qualified parent and tree were verified before branching. Main was not modified.

## Corrected architecture

Device A remains the permanent Tactility terminal. Device B is now explicitly a replaceable compute
accessory rather than a second wireless appliance. AetherLink exposes `open`, `close`, `read`,
`write`, `connected`, `capabilities`, and `cancel`; protocol v2 remains above that interface.

| Backend | Status | Protocol framing | Device-B C6 |
|---|---|---|---|
| USB CDC-ACM | Selected production | shared u32be + JSON | unused |
| Raw UART | Universal fallback | identical shared stream | unused |
| TCP/wireless | Deprecated diagnostic source | unchanged historical stream | diagnostic only |

The shared allocation-free decoder accepts split headers, split bodies and multiple frames per read,
rejects zero/oversize lengths, caps JSON at 16 KiB, and discards partial state on disconnect.
Protocol message types, request/session IDs, one-in-flight behavior and golden vectors are unchanged.

## Device B implementation

- ESP32-P4 TinyUSB CDC-ACM device backend around the existing production service.
- USB initialization is independent of and precedes the unchanged Pack-v2/storage boot.
- Production defaults remove ESP-Hosted, C6, Wi-Fi and TCP dependencies.
- Existing Kingston A2, 4-bit SDMMC, Pack-v2, cache and resident evidence directory are unchanged.
- Hot unplug wakes the service task and resets only incomplete framing state.
- Replug permits a fresh protocol negotiation and existing `SESSION_RESUME` semantics.
- Raw UART fallback carries the same frames at a default target of 921600 baud and fails closed until
  carrier-specific TX/RX pins are provisioned.

The stale generated dependency lock selecting `esp_hosted` and `esp_wifi_remote` was removed.
ESP-IDF `reconfigure` now resolves the manifest-pinned `esp_tinyusb` `1.7.6~1` and creates a fresh
lock.

## Device A implementation

A Tactility-owned `AccessoryLinkService` now owns the platform backend, framing, hotplug state and
single-subscriber dispatch. AetherChat only subscribes and retains conversation/session semantics.
It never calls TinyUSB, `usb_host_install`, ESP Wi-Fi, WebServer control or ESP-Hosted.

The service state is `UNPLUGGED -> DISCOVERING -> NEGOTIATING -> READY`. VID/PID/product strings are
only discovery hints. A candidate CDC endpoint becomes authoritative only after an exact
`aethercore-tactility.v2` `CAPABILITIES` response; malformed or silent unrelated serial devices are
bounded and rejected. Unplug resets an incomplete frame, fails the pending connection visibly, and
replug triggers a new `SESSION_OPEN` or `SESSION_RESUME` from AetherChat.

Pinned Tactility already has one shared ESP-IDF USB-host installation and concurrent class clients
for HID/MSC/MIDI. It does not contain a CDC-ACM host client. The correct integration is one additional
platform class client using Espressif's CDC-ACM host component; AetherChat must not install or own the
host stack. The exact user-customized Tactility BSP was not present, so that final backend wiring is
left to its authoritative Factory tree.

## Exact source/hardware validation

| Required fact | Result | Evidence/consequence |
|---|---|---|
| Device A exposes native P4 USB | PARTIAL PASS | Schematic J6 exposes `USB1P1_N/P`; the labelled Type-C OTG connector has CC pull-downs and is a sink/UFP, not a standards-compliant host source. |
| Tactility permits shared USB host integration | PASS | Pinned platform owns one host library and multiple class clients; CDC host is additive. |
| CDC host can coexist with Tactility classes | SOURCE-READY | Class-client architecture supports it; exact custom BSP build/physical measurement remains pending. |
| Device B exposes native P4 USB device | PARTIAL PASS | Native `USBD_N/P` is on P1. Its Type-C connector terminates at CH343 USB-UART, not the P4 USB device peripheral. |
| Device-B USB device and SDMMC are independent | PASS FROM SOC/SOURCE | Production USB path has no C6/hosted dependency and leaves the qualified slot-0 SDMMC implementation unchanged. Physical coexistence is still a Factory measurement after safe hardware exists. |
| Device-B C6 is required | PASS: NO | No C6, hosted or Wi-Fi code/dependency is selected in production. |
| Direct VBUS/power topology is safe | **FAIL / BLOCKER** | Device-A connector does not source host VBUS; both exposed 5 V nets are board power rails without a verified current-limited host switch/backfeed barrier. |

Official architecture references: [ESP32-P4 USB Host](https://docs.espressif.com/projects/esp-usb/en/latest/esp32p4/usb_host.html),
[Espressif CDC-ACM host component](https://components.espressif.com/component/espressif/usb_host_cdc_acm),
[pinned Tactility source](https://github.com/TactilityProject/Tactility/tree/0ee2415f3b5a063fadc2015d50d0d1c1c8b0b6e1),
[Device A board source](https://github.com/waveshareteam/ESP32-P4-WIFI6-Touch-LCD-3.5), and
[Device B official resources](https://docs.waveshare.com/ESP32-P4-WIFI6/Resources-And-Documents).

## Power and cable decision

There is **no approved direct USB cable for the exact current pair**.

Do not:

- use Device A's labelled OTG Type-C as a host port;
- use Device B's CH343 Type-C as P4 native CDC;
- tie Device A J6 `USB0_5V` to Device B P1 `VCC_5V` while either board is separately powered;
- make a data-only D+/D-/GND lead and assume VBUS detection/role behavior is valid.

The USB path needs a small role-correct interposer/carrier (or Device-A board revision) providing a
real DFP/host connection, controlled/current-limited VBUS, backfeed protection, correct ground/data
routing, and VBUS presence sensing for any self-powered Device B. It must state whether the accessory
is host-powered within a measured budget or self-powered with VBUS isolated. Only after schematic
review and continuity/current-limit checks is USB Factory qualification authorized.

The bounded non-wireless fallback is 3.3 V P4 UART: both boards separately powered, TX crossed to RX,
and shared GND, with no 5 V connection. GPIO choice remains configurable until checked against the
user's exact Tactility BSP; no pin assignment is invented here.

## Portability matrix

| Future Device B | AetherLink backend | Qualification note |
|---|---|---|
| ESP32-P4 / P4-Pico | USB CDC device | Native USB device is suitable; storage remains independent. Do not assume P4-Pico storage pins without its actual schematic. |
| Luckfox Pico/RV1103 family | Linux/SDK USB gadget, CDC initially | Official SDK material documents default USB peripheral/RNDIS operation and selectable host mode. |
| Alif Ensemble E-series | USB 2.0 device | Device role is sufficient. B2 host-mode erratum is irrelevant to Device B; revision-specific device support still requires board validation. |
| VoCore2 | UART or external USB-device bridge | Official product specification calls its native USB host-only. |
| Onion Omega2+/MT7688 | UART or external USB-device bridge | Official documentation describes USB as a host; native gadget/device capability is not assumed. |
| FPGA module | USB controller/PHY, bridge MCU/FIFO, UART or SPI | AetherLink messages and session semantics stay fixed; only the backend/capabilities change. |

Primary portability references: [Luckfox USB peripheral mode](https://wiki.luckfox.com/Luckfox-Pico-Plus-Mini/Device-Tree/),
[Alif B2 USB erratum](https://alifsemi.com/download/AERR0008),
[VoCore2 specification](https://vocore.io/v2.html), and
[Onion Omega2 USB host documentation](https://docs.onion.io/omega2-project-book-vol1/omega2-intro.html).

## Qualification results

| Gate | Result |
|---|---|
| Required parent SHA/tree | PASS exact |
| Shared transport framing host test | PASS |
| Fragmentation/multiple-frame/disconnect regressions | PASS |
| Tactility AccessoryLink fake-backend/hotplug/identity test | PASS |
| Focused Python architecture regressions | PASS, 21/21 |
| Full Python suite | PASS, 569 passed / 1 skipped / 570 collected |
| Ruff on modified Python paths | PASS |
| Strict mypy | PASS, 168 source files |
| Native protocol-v2 golden/fuzz | PASS, 625 assertions; 40,000 fuzz cases |
| Frozen Python/native protocol vectors | unchanged, SHA-256 `f5df619d...403d09` |
| Pack-v2/storage/controller/memory frozen paths | byte-identical to parent |
| LICENSE/NOTICE | unchanged |
| ESP-IDF target build | NOT RUN; toolchain and exact custom Tactility BSP unavailable in Work |
| Physical USB test | BLOCKED by exact connector/VBUS topology |

## Preserved Factory result

The Kingston A2 measurements, Pack-v2 direct resident evidence table, native memory/session,
protocol-v2 service, selected cache, approximately 603 ms p50 / 972 ms p95 query result and 2.31x
V14-paged speedup remain authoritative. They were not rerun or reinterpreted.

## Exact next justified action

Freeze the software boundary. Produce and review the minimal USB host/device interposer/carrier
schematic (or revised Device-A connector/power stage) against both official board schematics. Verify
role signaling, current limit, VBUS sensing and backfeed isolation on the bench. Then integrate the
CDC host backend into the exact custom Tactility tree and run the bounded Factory acceptance once.
If immediate system progress is required before that hardware exists, qualify the same protocol over
the provisioned direct P4 UART fallback—never Device-B wireless.
