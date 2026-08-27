# AetherChat Tactility overlay

This is the maintained Device-A overlay. `review/device-a-aetherchat/` is a
flat, byte-identical Factory mirror retained so the recovered custom Tactility
tree can be reviewed and patched without guessing its install paths. Tests
enforce that the corresponding source/header files remain identical.

The overlay follows Tactility `0.8.0-dev` application conventions and keeps
Device A limited to UI, keyboard/touch and transport. Cognition remains on the
accessory Device B.

## Selected offline link

Tactility's existing WebServer service is the sole owner of:

- the ESP32-C6 radio lifecycle;
- access-point mode and configured SSID/channel/password;
- the AP netif, `192.168.4.1`, DHCP and HTTP server.

AetherChat does not stop or start WebServer, change Wi-Fi state, pause scanning,
associate as a station, or call `esp_wifi` directly. It waits for the configured
Tactility WebServer/AP to be ready and opens a passive, bounded TCP listener on
port 9000. Device B joins the configured Tactility AP and initiates the one
full-duplex connection. Closing AetherChat closes only its accepted socket and
listener, leaving Tactility networking unchanged.

If the configured AP/WebServer is unavailable, AetherChat reports:

`Enable Tactility Web Server / Access Point`

It does not rewrite settings. Factory provisions Device B with the actual AP
credentials; the password is never emitted by AetherChat logs.

Protocol v2 remains four-byte big-endian length plus bounded JSON with a 16 KiB
frame cap. Partial frames are discarded on disconnect. A new Device-B
connection causes AetherChat to send `SESSION_OPEN` once or `SESSION_RESUME`
after a previous connection.

## Overlay installation

Copy `Source/` and `Private/` beneath the matching directories of the user's
exact Tactility port. The normal recursive Tactility source glob then includes
the app. The recovered official tree does not contain the user's custom
Waveshare P4/C6 3.5-inch BSP, so the physical build must use that exact Factory
tree and must not invent pin assignments.

CardKB2 continues to use factory BLE HID mode (`Fn+Sym+4`). No CardKB2 firmware
or GPIO change is part of this link repair.
