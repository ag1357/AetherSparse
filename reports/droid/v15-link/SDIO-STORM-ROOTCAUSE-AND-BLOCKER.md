# V15 Link Acceptance — SDIO Storm Root Cause and Blocker Report

Date: 2026-08-31 (~04:30 bench time)
Reporter: Factory (logs + tests role)
Branch: work/aethercore-v15-link-architecture-repair
Status protocol: **first missing acceptance signature = `tcp_connected`** (step 6 link formation
fails in every production-mode boot; bounded diagnostic-mode test passed all four signatures).

## TL;DR

The link architecture repair is sound (proven by the bounded diagnostic test: assoc + DHCP,
TCP connect, accept, protocol-v2 round trips). The **production** path is blocked by a hardware/
driver-level coupling on the Waveshare ESP32-P4-WIFI6 bench unit: **the SD card (SDMMC slot 0)
and the hosted C6 (SDIO slot 1) share one SDMMC host peripheral**, and slot-0 mount/activity
permanently kills the slot-1 data path. The 802.11 association (C6-autonomous) survives; all
host-driven traffic (TCP SYNs included) dies — `connect()` times out with **errno 113** and the
console floods with `sdmmc_send_cmd 0x107` / `Unrecoverable host sdio state` (err 263).

## Evidence chain (all timestamps in mission logs)

1. **Bounded diagnostic test (05-20, DIAGNOSTIC_ONLY, SD never mounted):** all four signatures
   green, link stable for the whole session (`phase-link-repair-bounded-test-dual-console.log`).
2. **Every production boot (radio-first order):** `sta_got_ip 192.168.4.2` at ~4-8 s (pre-mount),
   SD mount at ~10-14 s, hosted storm begins in the mount window, first TCP dial at READY gets
   no answer (errno 113 after 60 s), retries every 2-15 s, link never forms. Four full pack
   verifies PASS (818-821 s) because slot 0 (SD) stays healthy — the failure is slot-1 only.
3. **Pack-first order (local experiment):** pack boot completes (9 s, verify skipped via new
   default-off `AC_BOOT_VERIFY_SKIP`), then hosted init: `sdmmc_card_init failed` forever.
4. **Slave reset is ineffective:** hosted driver `Reset slave using GPIO[54]` x6 (its 34 s
   retry loop) plus a manual 300 ms GPIO54 pulse + 3 s settle before `radio_up()` — card_init
   fails after every attempt. Either GPIO54 does not reach C6-EN on this board, or the failure
   is host-side (shared peripheral state), not slave-side.
5. **Full power cycle does not save pack-first boots** (mount kills the C6 before init, every
   time); power cycle was required to recover the C6 after radio-first storms (P4-only resets
   leave the wedged C6 untouched — separate silicon).
6. Clock mitigations (40 -> 20 -> 10 MHz, RX streaming -> MAX_SIZE) do not prevent the wedge.
   40 MHz + streaming restored (config of the successful bounded test).

## Code state (this branch + local)

- `main/Kconfig.projbuild`: new `AC_BOOT_VERIFY_SKIP` (default **n**; loud
  `SKIPPED_INTERACTIVE_ACCEPTANCE` marker; used only for fast interactive cycles).
- `main/main.cpp`: production boot order changed to **pack first, radio second**, with the
  GPIO54 slave-reset pulse before `radio_up()` (comment documents the measured reason).
  Diagnostic path unchanged. NOTE: this order cannot work while the mount kills the C6;
  revert to radio-first if a host-level fix lands (radio-first at least associates, which is
  useful for debugging).
- `trace_runner.cpp`: verify gated by `AC_BOOT_VERIFY_SKIP`.
- Local `sdkconfig` (uncommitted): 40 MHz + RX streaming restored, `AC_LINK_RECONNECT_DELAY_MS=15000`,
  verify-skip on. Credentials never committed.
- Evidence excerpt: `work/v15-p4-deployment/phase-sdio-storm-rootcause-evidence.log`.

## Prior art pointers

- `pack_io.cpp` already carries the ESP-IDF issue 16233 coexistence workaround (no-op host
  init/deinit for the shared host, LDO chan 4 powers the TF slot). That made mount/deinit
  stop *crashing the driver*, but the shared-host disturbance on slot 1 remains fatal.
- esp-hosted example `host_sdcard_with_hosted` (issue 16233) is the vendor reference for this
  exact pairing.

## Options for the architecture owner (decision needed)

1. **Vendor/IDF escalation**: reproduce slot0-SD + slot1-hosted concurrent use on the vendor
   09_sdmmc + hosted example, file against esp-hosted/IDF (P4 shared-host SDIO). Ask vendor
   (Waveshare) whether GPIO54 is actually routed to C6-EN on the ESP32-P4-WIFI6.
2. **Transport change**: move the A<->B link off hosted-SDIO (e.g., UART to the C6, or A-side
   softAP already exists — B could use its USB netif? No — cleanest bounded option: keep C6
   for Wi-Fi but abandon concurrent SD+C6 on the shared host by moving the pack to a different
   medium (USB MSC? flash partition? smaller resident pack)).
3. **Hardware change on the bench**: a P4 board variant/revision where SD and C6 do not share
   the host, or external C6 module on SPI/UART.
4. **Sequencing hack (last resort)**: radio-first, dial + complete the whole interactive
   session in the ~6-10 s window before mounting the SD — not viable for real cognition
   (pack is required for answers); listed for completeness only.

## What remains green

- Host-side qualification (Phases 1-13) unchanged; V15-direct A/B numbers stand.
- Bounded link test artifact (four signatures) stands — the protocol/link design is validated.
- Device A side (Tactility + AetherChat overlay) is stable: AP, listener, auto-HEALTH/CAPS all
  behave as specified; both overlay fixes (radio re-init, readiness gate) are committed.
