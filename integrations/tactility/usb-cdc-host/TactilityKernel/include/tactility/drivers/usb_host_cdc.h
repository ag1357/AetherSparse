// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

struct Device;
struct DeviceType;

/**
 * Byte-stream API of a USB CDC-ACM host class device.
 *
 * The platform driver owns enumeration, class handles and hardware
 * lifetime; consumers only move bytes. read() blocks up to timeout_ms and
 * returns the byte count (0 on timeout, -1 when no device is connected).
 * write() blocks up to timeout_ms and returns the byte count written
 * (0 or -1 on failure).
 */
struct UsbCdcApi {
    bool (*is_connected)(struct Device* device);
    int (*read)(struct Device* device, uint8_t* data, size_t capacity, uint32_t timeout_ms);
    int (*write)(struct Device* device, const uint8_t* data, size_t length, uint32_t timeout_ms);
};

extern const struct DeviceType USB_HOST_CDC_TYPE;

/**
 * Returns true while a CDC-ACM device is open and active.
 * @param device non-null ready USB CDC device.
 */
bool usb_host_cdc_is_connected(struct Device* device);

/**
 * Read up to capacity bytes, blocking up to timeout_ms.
 * @return byte count, 0 on timeout, -1 when disconnected.
 */
int usb_host_cdc_read(struct Device* device, uint8_t* data, size_t capacity, uint32_t timeout_ms);

/**
 * Write length bytes, blocking up to timeout_ms.
 * @return byte count written, 0 or -1 on failure.
 */
int usb_host_cdc_write(struct Device* device, const uint8_t* data, size_t length, uint32_t timeout_ms);

#ifdef __cplusplus
}
#endif
