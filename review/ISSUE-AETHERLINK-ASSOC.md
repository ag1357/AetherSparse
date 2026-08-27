# ISSUE: Device A never associates with Device B's softAP (AETHERCORE-V15)

Date: 2026-08-27 · Branch: `work/aethercore-v15-p4-physical-deployment` · Status: **OPEN — blocker for V15 physical acceptance**

## 30-second version

Device A (Tactility handheld, ESP32-P4 host + ESP32-C6 radio slave over ESP-Hosted/SDIO) cannot associate with Device B's softAP. Device A's own scan never lists `AETHERCORE-V15` (it sees 10-19 other APs fine and auto-connects to the home AP instantly). The association RPC reaches Device A's C6 slave, which fails it ~2.4 s later with a disconnect event whose **reason code is never logged**. A phone placed nearby saw `AETHERCORE-V15` only **briefly/intermittently**. Everything past association (TCP, protocol, cognition) is verified green on Device B via an on-device loopback self-test.

## Architecture (host vs slave — verified)

```
Device A                          Device B
ESP32-P4 (host)                   ESP32-P4 (host)
  Tactility OS                      ac_p4 firmware (this repo, firmware/p4_aethercore)
    AetherChat app                    softAP "AETHERCORE-V15" WPA2-PSK ch6
      AetherLinkTcp                   192.168.4.1, TCP :9000 (max_connection=1)
        WifiService                   Pack-v2 cognition service (READY)
        esp_wifi_remote ─┐          esp_hosted ─┐
ESP32-C6 (slave) ◄──SDIO RPC─┘   ESP32-C6 (slave) ◄──SDIO RPC─┘
  factory image (STA)      ══ 2.4 GHz RF ══   factory image (softAP)
```

- Both C6 slaves run **unmodified factory images** (Tactility hosted-mcu fork). Constraint: do NOT flash the C6s.
- Wi-Fi driver calls on each P4 host are RPC-forwarded to its C6 slave; the RF link is C6↔C6.
- DHCP for the AP runs on Device B's P4 (default AP netif). TCP endpoints are P4↔P4.

## Verified working (evidence: mission dir /media/cloud/2982-E16B/work/v15-p4-deployment/)

- **Device B boot chain**: radio phase → SD mount → pack verify PASS (817 s) → pack-v2 `direct_compact_resident` → service READY → `tcp_listen` bound 192.168.4.1:9000. Log: `phase-option-a-boot-loopback12.log`, `phase-acceptance-dual-console.log` (READY 02:09:15).
- **Device B on-device loopback green**: golden protocol-v2 SESSION_OPEN → HEALTH/CAPABILITIES round-trip through the real socket+dispatch path (`loopback:ok responses:2`).
- **Device A radio path healthy for known networks**: auto-connected to home AP `tesla-guest_EXT` immediately (02:47:19 in dual-console log); STA scans return 10-19 APs.
- **Phone saw `AETHERCORE-V15` briefly** → Device B's beacon radiates at least intermittently.
- Device B AP config is textbook (`link_tcp.cpp` radio_up; `sdkconfig` lines 762-764: SSID/pass/channel 6).

## Defect 1 — FIXED in this branch (flashed on Device A 03:19, awaiting retest)

**Symptom** (after any boot where Tactility's WebServer app auto-started its own softAP `Tactility-0000`):
opening AetherChat stops the WebServer service (by design — it squats on 192.168.4.1 + AP mode),
which tears down the C6 slave's Wi-Fi entirely. The host-side WiFi service state still reads "On",
so `setEnabled(true)` is skipped and every connect RPC is rejected instantly:

```
WifiService: Connecting to AETHERCORE-V15
H_API: esp_wifi_remote_connect
rpc_rsp: Hosted RPC_Resp [0x21a], uid [106], resp code [12290]      ← ESP_ERR_WIFI_NOT_STARTED (0x3002)
E WifiService: Failed to connect to AETHERCORE-V15 (undefined)
```

**Fix** (in `review/device-a-aetherchat/Source/AetherLinkTcp.cpp`, `start()`): after stopping WebServer,
force a radio re-init (`setEnabled(false)` + `setEnabled(true)`, dispatched in order on the service thread).

## Defect 2 — OPEN, the blocker: association never completes

**Symptom** (boots where WebServer never started, and expected post-fix):
connect reaches the slave, fails after ~2.4 s, **no reason code logged**:

```
02:46:06 WifiService: Connecting to AETHERCORE-V15
02:46:06 H_API: esp_wifi_remote_connect
02:46:09 RPC_WRAP: ESP Event: Station mode: Disconnected     ← ×2 (duplicate handlers), no reason
```

- ~2.4 s-to-fail matches a full-channel-scan miss (reason 201 NO_AP_FOUND profile), consistent with
  `AETHERCORE-V15` never appearing in Device A's scan list. Unconfirmed — reason is not logged.
- AetherLinkTcp retries on a 6 s window (`ASSOCIATE_TIMEOUT_MS`) with 1-5 s backoff → user sees
  "associating → retry" forever.

**Ordered suspects:**
1. **RF/antenna on Device B's C6** (Waveshare accessory board): beacon marginal (phone saw it only
   briefly). Check antenna seating; run phone-associate test (WPA2 pass in `sdkconfig` CONFIG_AC_TCP_AP_PASS)
   — if the phone connects reliably with good RSSI near the bench, B's AP is healthy and the fault is A-side.
2. **A-side hosted scan/connect quirks** (known issue: hosted scan sometimes returns stale/0 results).
3. B-side slave handling of hosted `set_config(AP)` (channel/auth/beacon fields) — host side verified correct.
4. Channel/country mismatch (ch 6 pinned; unlikely).

**Eliminated:** Tactility host-side connect gating (`dispatchConnect` has no pause gate — verified in
`device-a-tactility-ref/Wifi.cpp.REFERENCE`); SSID/pass literals on A (verified vs spec); B's TCP/pack path
(loopback green); WPA2 AP config on B.

## What the reviewing agent should do

1. **Add disconnect-reason logging** in `device-a-tactility-ref/esp32_wifi.cpp.REFERENCE`
   (`on_wifi_or_ip_event`, `WIFI_EVENT_STA_DISCONNECTED`, ~line 100): log
   `((wifi_event_sta_disconnected_t*)event_data)->reason`. This turns every failure into a precise diagnosis
   (201 = scan miss / 15 = 4-way timeout / 205 = connection fail).
2. **Re-check `AetherLinkTcp.cpp`** (`ensureAssociated`, `waitForRadio`, start/stop lifecycle) against
   `Wifi.cpp.REFERENCE` — this is the user's customized Tactility tree, upstream will not match.
3. Consider a pre-connect scan probe: `service::wifi::scan()` and check results for the SSID before
   attempting connect, surfacing "AP not visible" as a distinct UI state from "association failed".
4. Device B side entry point: `firmware/p4_aethercore/main/link_tcp.cpp` (`radio_up`) — optionally add an
   `AP_STACONNECTED/DISCONNECTED` esp_event log to see association arrivals on B.

## Hard constraints (mission)

- Do NOT flash or modify either C6 slave image (factory images are pristine; recovery programmer unavailable).
- Keep the 4-byte big-endian length-prefix framing (protocol-v2), fail-closed semantics.
- Device B costs ~17 min to reboot (pack verify) — avoid reflashing/resetting it unless necessary.
- Device A reflashes are cheap (esptool, app partition 0x10000, ~6 s boot).

## Success signature (when fixed)

```
[A] AetherLinkTcp: Link: associating → Link: associated (192.168.4.x) → Link: up
[A] SESSION_OPEN sent
[B] MEAS {"link":"rx_complete",...} {"phase":"service","kind":"session_open",...} {"cognition_ms":N,...}
```
Then the acceptance sequence continues (HEALTH/CAPABILITIES, real USER_TEXT, SESSION_RESUME, memory CRUD).
