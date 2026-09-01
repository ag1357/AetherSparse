# Device-A Factory mirror

This directory is the flat Factory copy recovered from the user's customized
Tactility tree and reconciled during the V15 link-architecture repair.

The maintained overlay is `integrations/tactility/aetherchat/`. Corresponding
headers and sources in the two locations are intentionally byte-identical;
`tests/agent/test_v15_link_device_a_repair.py` enforces that invariant. Keep
this flat mirror when producing Factory patches because the custom Device-A
tree is not identical to pinned upstream Tactility.

The selected transport is the Tactility-owned AccessoryLink USB-host service.
AetherChat is only a client of that service and never owns USB, Wi-Fi, the
WebServer, ESP-Hosted, or a radio. Device B is a replaceable compute accessory;
CDC-ACM is current production and UART is the same-stream fallback. The former
TCP files remain only under `Deprecated/`, outside the production Factory
source/include layout.
