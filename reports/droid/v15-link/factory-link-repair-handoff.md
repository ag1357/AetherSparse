# Factory V15 Link Repair Handoff

Qualified repair source SHA: `bef1943c4c9e50d42589089af69fb983335de1ae`

Qualified repair source tree: `74498c026597c2a0d22c065103e6ed08ffb3a41f`

Classification: `LINK_REPAIR_READY_FOR_FACTORY`

Required parent: `aa0cebc98d09d390c27cd39a69d158842d8132cd` / tree
`a5d84c7ed1aee35ba41c923b89bbb3fcfd431dba`.

This is a bounded link test. Do not retrain, rebuild the corpus, redesign Pack-v2, alter either C6
image, or repeat V15 storage qualification.

1. Fetch the exact published Work repair SHA on
   `work/aethercore-v15-link-architecture-repair` and verify its tree.
2. Rebuild Device-A AetherChat against the already-recovered custom Tactility tree. Use the
   synchronized `integrations/tactility/aetherchat` overlay; `review/device-a-aetherchat` is its
   review mirror.
3. Configure and enable Device A's existing Tactility WebServer/AP. Do not let AetherChat rewrite
   those settings or stop the service.
4. Provision Device B with the actual Device-A AP SSID/password. Do not log the password.
5. Select `CONFIG_AC_LINK_PRODUCTION_STA_CLIENT=y` and
   `CONFIG_AC_LINK_DIAGNOSTIC_ONLY=y`; flash Device B.
6. Confirm all four signatures: Device B station associated, DHCP address obtained, Device B TCP
   connected to `192.168.4.1:9000`, and Device A accepted the client. Save both serial logs.
7. Stop using the diagnostic build. Its required banner is
   `LINK_DIAGNOSTIC_ONLY — NOT AETHERCORE QUALIFICATION`; it is never an acceptance result.
8. Build/flash normal production Device-B firmware with diagnostic-only disabled. Keep the
   station/client topology and hosted-C6-before-SD ordering.
9. Perform exactly one full Pack-v2 verification.
10. Open AetherChat without stopping the Tactility WebServer/AP.
11. Confirm physical protocol-v2 `HEALTH` and `CAPABILITIES` exchange.
12. Send one arbitrary real `USER_TEXT` request and record its grounded response.
13. Send one follow-up after disconnect/reconnect and confirm `SESSION_RESUME` preserves state.
14. Confirm CardKB2 text entry through Device A.
15. Exercise authorized user-memory create, list/recall, edit, and delete; confirm status and
    restart persistence.

If step 6 fails, report the first missing signature and both bounded logs. Do not return to the
legacy Device-B AP topology except by explicitly selecting `LEGACY_B_AP_DIAGNOSTIC` for comparison.
