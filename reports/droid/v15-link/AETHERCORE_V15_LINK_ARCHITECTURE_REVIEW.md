# AetherCore V15 Device-A / Device-B Link Architecture Review

## Classification

**LINK_REPAIR_READY_FOR_FACTORY**

This classification qualifies the source-level topology repair for the bounded Factory physical
test. It does not claim that the repaired link has already been built or measured on either
device.

## Frozen checkpoint and scope

- Factory parent commit: `aa0cebc98d09d390c27cd39a69d158842d8132cd`
- Factory parent tree: `a5d84c7ed1aee35ba41c923b89bbb3fcfd431dba`
- Publication branch: `work/aethercore-v15-link-architecture-repair`
- Qualified remote source commit: `bef1943c4c9e50d42589089af69fb983335de1ae`
- Qualified remote source tree: `74498c026597c2a0d22c065103e6ed08ffb3a41f`
- Pinned Tactility reference: `0ee2415f3b5a063fadc2015d50d0d1c1c8b0b6e1`
- Scope: Device-A/Device-B network ownership and socket orientation only.

Controller, COG, Semantic Address, Pack-v2, memory, cognition, protocol-v2 semantics, both
factory C6 images, and the physical storage result remain unchanged.

## Finding

**Confirmed:** the primary integration mistake was making Device B the access point and forcing
Device A away from Tactility's existing AP/WebServer ownership.

Source evidence:

1. The live Factory `AetherLinkTcp` performed twelve invasive ownership operations: it stopped
   and restarted WebServer, paused and resumed Wi-Fi auto-scan, enabled/disabled the radio,
   commanded station association/disconnection, and retried that takeover from the app.
2. The Factory issue record shows that stopping WebServer could tear down the hosted-C6 radio
   while Tactility's Wi-Fi service still reported it on, producing `ESP_ERR_WIFI_NOT_STARTED`.
   The second loop then failed before TCP association.
3. Pinned Tactility `WebServerService` already owns AP configuration, AP netif, DHCP,
   `192.168.4.1`, radio lifecycle, configured SSID/password/channel, and HTTP service.
4. The pinned service constructs its HTTP route table privately and exposes no small external
   route/WebSocket registration API. Rebuilding AetherLink around HTTP would be larger and less
   direct than a passive TCP listener on a separate port.
5. Device B's real on-device loopback had already proven TCP framing, strict protocol dispatch,
   `HEALTH`/`CAPABILITIES`, and the cognition service. The unresolved failure was association
   topology, not AetherCore.

## Selected repair

### Device A

- Tactility remains the sole owner of its existing WebServer/AP network.
- AetherChat reads WebServer settings/readiness and passively binds `0.0.0.0:9000`.
- Listener backlog is one; only one Device-B client is authoritative.
- Disconnect discards partial input and returns to `accept()` for reconnect.
- Closing AetherChat closes only its client/listener sockets.
- When AP/WebServer is unavailable, the UI reports
  `Enable Tactility Web Server / Access Point`; it does not rewrite settings.
- The maintained `integrations/tactility/aetherchat` overlay is authoritative.
  `review/device-a-aetherchat` is a byte-identical Factory-layout mirror enforced by tests.

The live Device-A C++ source reduced from 1,257 to 1,152 lines: 105 lines removed (8.35%).

### Device B

- Production default: `AC_LINK_PRODUCTION_STA_CLIENT`.
- Factory hosted C6 joins the configured Device-A AP as a station and obtains DHCP before the
  SD slot-0 mount.
- After normal Pack-v2/service initialization, Device B initiates a persistent full-duplex TCP
  connection to configurable Device A, default `192.168.4.1:9000`.
- SSID/password Kconfig defaults are empty and fail closed. Factory must provision the actual
  Tactility settings; the password is never logged.
- Wi-Fi loss interrupts a blocked socket, reassociates, waits for DHCP, and reconnects without
  destroying AetherCore session state. Device A emits `SESSION_RESUME` on the next connection.
- The static internal-stack/TCM design, hosted-before-SD order, shared-SDMMC workaround, and
  hosted component versions remain unchanged.
- The prior AP/server implementation remains only under non-default
  `AC_LINK_LEGACY_B_AP_DIAGNOSTIC`.

### Fast diagnostic mode

`AC_LINK_DIAGNOSTIC_ONLY` performs hosted-C6 initialization, STA association, DHCP, TCP connect,
strict frame decode, and a bounded protocol-v2 `HEALTH` reply without mounting/verifying the
pack or starting cognition. It prints:

`LINK_DIAGNOSTIC_ONLY -- NOT AETHERCORE QUALIFICATION`

It can never satisfy production acceptance.

## Preserved Factory physical evidence

The repair retains, without requalification or reinterpretation:

- Kingston Canvas Go! Plus 128 GB A2: approximately 8.27 MB/s sequential read, 6.47 MB/s
  sequential write, 260 random 4 KiB IOPS, 3.9 ms p50 and 5.6 ms p95.
- Pack-v2 `direct_compact_resident`: 3,311,868-byte resident evidence directory and zero selected
  evidence-directory SD reads.
- Selected V15 query result: approximately 603 ms p50 and 972 ms p95, 2.31x faster than the
  V14-paged comparison on the same new card.
- Native memory, TCM/static-stack repair, factory C6 preservation, shared-host boot order, and
  Device-B production service.

No new physical measurement is claimed here.

## Frozen protocol evidence

Protocol-v2 remains byte-for-byte unchanged:

- four-byte big-endian body length;
- 16,384-byte JSON body limit;
- 46 Python/native golden vectors;
- request IDs, session IDs, sequences, one-in-flight backpressure;
- strict malformed-frame rejection and partial-frame discard;
- `HEALTH`, `CAPABILITIES`, `SESSION_OPEN`, `SESSION_RESUME`, user/memory/status/evidence,
  clarification, cancellation, and reset semantics.

Qualification freezes SHA-256 digests for both codecs, the native service, golden vectors, and
hosted dependencies. A real localhost test additionally proves the inverted server/client
orientation, full-duplex exchange, reconnect, `SESSION_RESUME`, and partial-prefix discard.

## Work qualification

| Gate | Result |
|---|---|
| Factory parent/tree | PASS exact |
| Primary ownership defect | CONFIRMED from source |
| Device-A passive/network-invariant gates | PASS |
| Device-A authoritative/mirror byte equality | PASS, 9 source/header files |
| Device-B STA/client default and configurable credentials | PASS |
| Hosted radio/DHCP before SD | PASS static/order gate |
| Persistent reconnect/session preservation | PASS source + localhost integration |
| Legacy AP diagnostic retained/non-default | PASS |
| Diagnostic-only pack/cognition bypass | PASS source gate |
| Protocol-v2 native golden/fuzz | PASS, 625 assertions; 40,000 fuzz cases |
| Legacy native link harness | PASS, 38/38 |
| Targeted Python repair/operational tests | PASS, 26/26 |
| Full Python suite | PASS, 567 passed / 1 skipped / 568 collected |
| Ruff | PASS |
| Strict mypy | PASS, 168 source files |
| Controller/COG/Pack-v2/memory drift | NONE |
| LICENSE/NOTICE drift | NONE |
| ESP-IDF/physical build | NOT RUN in Work; exact Factory tree/device required |

## Remaining acceptance

Factory should run one diagnostic image to prove the four transport signatures, then one normal
production image and one full pack verification. Physical association, DHCP, TCP acceptance,
`HEALTH`/`CAPABILITIES`, arbitrary `USER_TEXT`, follow-up/`SESSION_RESUME`, CardKB2 input, and
user-memory CRUD remain `PENDING_FACTORY`.

Failure of that bounded physical step should be reported at the first missing signature. It is
not evidence that AetherCore cognition or Pack-v2 failed.
