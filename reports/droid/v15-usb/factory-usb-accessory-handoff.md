# Factory handoff — V15 USB accessory link

Classification: `USB_ACCESSORY_LINK_HARDWARE_BLOCKED`

Branch: `work/aethercore-v15-usb-accessory-link`

Qualified software SHA/tree:
`54786d94255a3cca41dccbd70477a68d3b73b0b8` /
`28f493cb10aa52b52055ed1c028fea84021b25d9`.

## Stop condition

Do **not** perform a direct USB experiment with the two current boards.

- Device A's labelled OTG Type-C is wired as a sink/UFP and is not a verified host VBUS source.
- Device B's Type-C terminates at CH343 USB-UART; P4 native USB is on P1.
- Device A J6 and Device B P1 expose board 5 V rails, not a qualified current-limited,
  backfeed-protected accessory power link.
- Never join those 5 V rails while either board is independently powered.

No Wi-Fi, ESP-NOW, C6 flashing, AP association, TCP debugging, SDIO coexistence or SDSPI change is
authorized by this handoff.

## Required hardware repair before USB acceptance

Provide one reviewed interposer/carrier or Device-A revision with:

1. Device A P4 native USB D+/D- routed as a real host/DFP.
2. Controlled and current-limited host VBUS.
3. Backfeed isolation.
4. Correct CC/role behavior for any Type-C connector.
5. VBUS presence sensing for a self-powered Device B.
6. A declared host-powered current budget or an independently powered Device-B arrangement.
7. Continuity, polarity, idle-voltage, current-limit and backfeed bench checks before boards connect.

The exact schematic must be reviewed against the official Device-A and Device-B schematics. A cable
alone is not this repair.

## Bounded Factory sequence after hardware is green

1. Fetch the exact published branch SHA and verify its tree.
2. Build Device B with `AC_LINK_USB_CDC_DEVICE`; confirm ESP-Hosted/C6/Wi-Fi/TCP are absent from
   production boot.
3. Keep the Kingston card and native 4-bit SDMMC/Pack-v2 configuration unchanged.
4. Add Espressif CDC-ACM as another client of Tactility's existing shared USB-host service in the
   exact recovered custom tree; register it below `AccessoryLinkService`.
5. Build/install Device A without adding USB ownership to AetherChat.
6. Connect only through the reviewed interposer/power arrangement.
7. Observe USB enumeration and require protocol-v2 `HEALTH` plus `CAPABILITIES`; reject any ordinary
   serial adapter.
8. Exercise `SESSION_OPEN`, real arbitrary `USER_TEXT`, one follow-up, CardKB2 input and user-memory
   CRUD.
9. Unplug during a request; confirm bounded offline state and no Device-A crash.
10. Replug without reboot; confirm enumeration and `SESSION_RESUME`.

Stop at the first missing signature and return the Device-A host/class log plus Device-B USB/service
log. Do not reinterpret a link failure as a cognition or Pack-v2 failure.

## Immediate nonwireless fallback

If appliance progress is required before the interposer exists, use the implemented UART backend:

- both boards separately powered;
- 3.3 V P4 TX -> opposite RX and RX <- opposite TX;
- shared GND;
- no 5 V connection;
- initial target 921600 baud;
- pins provisioned only after checking the exact custom Tactility BSP for conflicts.

UART uses the identical AetherLink framing, messages, session and memory semantics. It is a legitimate
hardware-agnostic accessory fallback, not a return to wireless.
