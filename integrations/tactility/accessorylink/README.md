# Tactility AccessoryLink service overlay

This is the authoritative Device-A platform boundary for AetherLink. The USB
host stack and CDC-ACM class driver live below this service; AetherChat only
subscribes, sends framed JSON, and receives framed JSON.

The pinned upstream Tactility commit `0ee2415f3b5a063fadc2015d50d0d1c1c8b0b6e1`
already has one shared ESP-IDF USB host controller and concurrent HID/MSC/MIDI
class clients. A CDC host client should be added beside those clients using
`espressif/usb_host_cdc_acm`; it must not call `usb_host_install()` or own VBUS
from AetherChat. The user's recovered custom board tree must add this overlay
to its component source/include lists and wire one platform backend during the
USB-host device lifecycle.

The service deliberately treats VID/PID/product strings only as discovery
hints. A candidate becomes AetherCore only after strict protocol-v2 negotiation
and a valid `CAPABILITIES` response.
