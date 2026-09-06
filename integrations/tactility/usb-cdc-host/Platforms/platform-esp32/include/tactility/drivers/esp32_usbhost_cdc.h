// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

struct Device;

/**
 * Devicetree config for the USB CDC-ACM host class client.
 *
 * vid/pid are discovery hints only: AetherLink protocol-v2 negotiation
 * (HEALTH/CAPABILITIES) above this driver is authoritative for accessory
 * identity. vid/pid of 0 match any CDC-ACM device.
 */
struct Esp32UsbHostCdcConfig {
    uint32_t vid;
    uint32_t pid;
};

#ifdef __cplusplus
}
#endif
