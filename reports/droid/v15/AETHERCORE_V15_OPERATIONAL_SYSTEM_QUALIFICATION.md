
# AetherCore V15 operational-system qualification

## Classification

**V15_READY_WITH_STORAGE_EXPERIMENT_PENDING**

V15 converts the physically qualified V14 architecture into a persistent operational
system without changing Semantic Address or weakening exact verification. Source main
is `c3aa2ef61e6ae77a12063e47221c6e4decae3762` (tree `09888952949745677b6a1b4939b90f14ccfe83d8`); qualification source is `93b22ff27a0304f41f323b32832bee667c937583`.

## Qualified result

| Area | Result |
|---|---|
| V14 frozen parity | 51/51 ABI; 260/260 cases; 1,329/1,329 decisions; 107/107 queries |
| Native hardening | selected pinning; post-VERIFY and post-TERMINAL freeze PASS |
| Malformed wire | 18 session + 4 COG semantic forgeries rejected |
| COG deserialize | exact 180-byte Python/native roundtrip |
| Memory | four tiers, independent residency, authorized user CRUD, restart/resume PASS |
| Controller | selected V14 1,292 int8; 242/260 total; 138/150 tuning |
| Optional specialist | 54 int8 rejected: 239/260 total; 129/150 tuning |
| Natural input / observer | 21/21 phenomena; 11-event, 40-byte exact observer |
| Agent | 5/5 sandbox tasks, 55 operations, zero unauthorized integration |
| AetherChat | 3 C++ files / 344 LOC; framing compile and malformed rejection PASS |

## Deployment selection

PERFORMANCE uses the prepacked direct compact evidence directory (3,311,868 bytes)
and a 2 MiB cache. Projected total residency is 6,421,665 bytes, leaving 27,132,767
bytes of the 32 MiB PSRAM envelope. Against the unchanged poor-card profile, modeled
mean address latency falls from 1,217.25 ms to 463.31 ms by eliminating evidence-
directory media misses. This is a host model, not a physical claim.

## Remaining gate

Factory must first run the Kingston A2 medium with the unchanged V14 binary, then
measure Pack-v2 physically. It must also obtain the exact user custom Waveshare
Tactility BSP source for the Device-A build. CardKB2 uses factory BLE HID and the
compute link uses existing C6-hosted ESP-NOW, so no GPIO values are required or invented.

The final self/manual knowledge pack remains intentionally deferred until both devices
are physically validated.
