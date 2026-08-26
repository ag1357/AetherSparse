# AetherChat Tactility overlay

This overlay follows the upstream Tactility `0.8.0-dev` in-memory Chat app conventions:
thread-per-app lifecycle, `window_manager`, LVGL widgets, app events, and the existing
`tt::service::espnow` abstraction. Device A performs UI/input/transport only.

Copy `Source/` and `Private/` beneath the matching directories of the user's exact Tactility
port. The normal recursive Tactility source glob then includes the app. The recovered official
tree does not contain the user's custom Waveshare P4/C6 3.5-inch BSP, so a device build must wait
for that exact port; this overlay is not evidence that the custom target itself compiles.

CardKB2 uses its factory BLE HID mode (`Fn+Sym+4`). Tactility `0.8.0-dev` already supplies the BLE
HID host and dynamic `KeyboardDeviceListener`; no CardKB2 flashing or GPIO connection is required.
The application setting cycles `AUTO`, `SHOW`, and `HIDE`. `AUTO` follows Tactility's verified
`lvgl_hardware_keyboard_is_available()` behavior and retains touchscreen keyboard fallback.

The selected compute transport is the existing C6-hosted ESP-NOW service. There is no
Device-A-to-Device-B GPIO data link: each device needs power and ESP-NOW pairing/configuration.
Factory must obtain the exact custom BSP/port before flashing and must not invent pin numbers.
