
# Current AetherCore architecture — V15 candidate

Source main is `c3aa2ef61e6ae77a12063e47221c6e4decae3762` (tree `09888952949745677b6a1b4939b90f14ccfe83d8`). The Work candidate is
`work/aethercore-v15-operational-system` at qualification source `93b22ff27a0304f41f323b32832bee667c937583`. It is classified
`V15_READY_WITH_STORAGE_EXPERIMENT_PENDING`.

## Device boundary

- Device A is the Waveshare ESP32-P4/C6 3.5-inch Tactility appliance. It owns UI,
  touch/CardKB2 input, media, and transport only.
- Device B is the separate Waveshare ESP32-P4-WIFI6 SKU 32020 accessory. It owns
  Semantic Address, COG, policy, evidence, memory, tools, and knowledge packs.
- The selected link is the existing C6-hosted ESP-NOW service; there is no
  Device-A-to-Device-B cognition GPIO link.

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
bounded frames, cancellation, and explicit failures. AetherChat is a 3-file,
344-line overlay against Tactility 0.8.0-dev, below the existing Chat app's 5 files
and 992 lines. CardKB2 uses factory BLE-HID mode (`Fn+Sym+4`), USB-C power, and no
GPIO or firmware replacement.

The exact user custom Waveshare Tactility BSP source was not present in Work, so
the physical Device-A build remains a Factory gate and no GPIO values are invented.
