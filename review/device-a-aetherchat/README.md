# Device-A Factory mirror

This directory is the flat Factory copy recovered from the user's customized
Tactility tree and reconciled during the V15 link-architecture repair.

The maintained overlay is `integrations/tactility/aetherchat/`. Corresponding
headers and sources in the two locations are intentionally byte-identical;
`tests/agent/test_v15_link_device_a_repair.py` enforces that invariant. Keep
this flat mirror when producing Factory patches because the custom Device-A
tree is not identical to pinned upstream Tactility.

The selected transport is a passive one-client listener on TCP port 9000. It
uses Tactility's configured WebServer/AP only as an existing network and never
owns the radio, AP, DHCP, HTTP server or station association lifecycle.
