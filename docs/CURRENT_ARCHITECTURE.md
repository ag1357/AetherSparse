
# Current AetherCore architecture — V15 USB accessory correction

The accessory-link correction branches from
`b5805d8deae14f884f979a2d2b7ac1c84bf8edb1` (tree
`a6e0e8d92783c2f7b7c061f594979b95051620d5`) on
`work/aethercore-v15-usb-accessory-link`. The cognition and storage results at
that parent remain frozen; this revision corrects only the replaceable-accessory
boundary.

## Device boundary

- Device A is the Waveshare ESP32-P4/C6 3.5-inch Tactility appliance. It owns UI,
  touch/CardKB2 input, media, and transport only.
- Device B is a **replaceable compute accessory**. The current implementation is
  the Waveshare ESP32-P4-WIFI6 SKU 32020, but future Device B hardware may be an
  MCU, Linux module, accelerator, FPGA/bridge, or another controller. It owns
  Semantic Address, COG, policy, evidence, memory, tools, and knowledge packs.
- AetherLink is a transport-independent byte stream. Protocol v2 remains above
  it unchanged. USB CDC-ACM is the selected production backend, UART is the
  universal fallback, and the old C6/TCP implementation is a non-default
  historical diagnostic only.
- Production Device B does not initialize ESP-Hosted, its C6, Wi-Fi, or TCP.
- The exact current boards need a role-correct, current-limited and
  backfeed-protected interposer/carrier before a safe direct USB physical test.
  Until then, provisioned P4-to-P4 UART can exercise the same AetherLink framing
  without returning to wireless.

## Operational cognition

Semantic Address v2 feeds the authoritative COG and the selected 1,292-parameter
int8 legal-mask controller. Exact operations, immutable 5C boundaries, evidence
pinning, and the verifier remain mandatory. Autonomous control remains 242/260
overall and 138/150 unseen tuning. A tested 54-parameter passage-context head
reduced performance to 239/260 and is archived inactive.

## Memory and persistence

EPHEMERAL, SHORT_TERM, WORKING, and LONG_TERM are semantic lifetimes. COLD, WARM,
and HOT are independent physical residency states. Persistent user memory requires
explicit authorization and supports list/read/write/edit/delete/search, tombstones,
and compaction. The authoritative state persists sessions, complete COGs, memory,
specialists, pack generations, semantic checkpoints, and deterministic deltas.

## Runtime and deployment

The hot path remains allocation-free C++17 behind a stable C ABI. V15 hardens
selected evidence and the wire trust boundary. PERFORMANCE uses a 3,311,868-byte
resident direct evidence table and 2 MiB cache, projecting 6,421,665 resident bytes
and 27,132,767 bytes PSRAM headroom. COMPACT uses an 8,632-byte top directory and
at most one paged leaf read per lookup.

## Service and terminal

`aethercore-server` exposes protocol v2 with resume, capabilities, memory status,
bounded frames, cancellation, and explicit failures. AetherChat consumes a
Tactility-owned AccessoryLink service; the app never owns USB-host hardware or a
radio. Candidate CDC devices are only discovery hints and must prove protocol
version plus `CAPABILITIES` before the service enters READY. CardKB2 remains a
Device-A input concern.

The exact user custom Waveshare Tactility BSP source was not present in Work, so
the physical Device-A build remains a Factory gate and no conflicting GPIO values
are invented. The pinned Tactility source proves a shared USB-host controller and
multi-class client model but does not yet include the required CDC-ACM host client.
