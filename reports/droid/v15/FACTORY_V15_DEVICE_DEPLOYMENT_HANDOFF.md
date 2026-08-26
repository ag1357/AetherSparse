
# Factory V15 device-deployment handoff

Fetch `work/aethercore-v15-operational-system`, resolve and record its exact remote SHA/tree, and verify it descends
from `c3aa2ef61e6ae77a12063e47221c6e4decae3762`. Do not move `main` and do not create a release tag.

## Physical identities

- **Device A:** Waveshare ESP32-P4-WIFI6-Touch-LCD-3.5 with integrated C6; preserve
  the user's working Tactility installation and configuration. Overlay
  `integrations/tactility/aetherchat` only after obtaining that exact custom BSP source.
- **Device B:** separate Waveshare ESP32-P4-WIFI6 SKU 32020 accessory, ESP32-P4
  rev v1.3, 32 MiB PSRAM. This is the cognition target.
- **CardKB2:** power by USB-C, press `Fn+Sym+4`, and pair as BLE HID to Device A.
  Do not flash it. There are no CardKB2 GPIO connections.
- **Compute link:** configure the existing C6-hosted ESP-NOW service. There is no
  Device-A-to-Device-B GPIO wiring; both devices require power and pairing/configuration.

## Required sequence

1. Back up Device-A configuration and retain V14 accessory image/pack.
2. Test the Kingston Canvas Go! Plus 128 GB A2 card with the unchanged V14 binary.
3. Record A2 results separately from the earlier poor 128 GB medium.
4. Build V15 using `aethersparse aethercore compile`, then build the prepacked
   PERFORMANCE image with `aethersparse aethercore pack`.
5. Flash Device B, verify pack hashes, and run qualification.
6. Overlay/build AetherChat against the exact user BSP; pair ESP-NOW and CardKB2.
7. Ask the user to type a real query; test follow-up, memory CRUD, cancel, and reset.
8. Capture address p50/p95, pages/misses, SDMMC/DMA counters, CPU, PSRAM/SRAM/stack,
   transport, and reconnect behavior.

The 128 GB card is test media; the long-term pack contract remains 256 GB class.
If parity fails, restore V14 and retain the V15 state export for diagnosis.
