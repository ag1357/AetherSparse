/* Selected Device-B production link: protocol-v2 over USB CDC-ACM device. */
#pragma once

#include <cstddef>
#include <cstdint>

#include "link/ac20_wire.h"

namespace ac::linkusb {

/* Installs the native ESP32-P4 USB device stack.  This path has no C6,
 * ESP-Hosted, Wi-Fi, TCP or SDMMC-host dependency. */
bool start();
void serve();

void response_sink(void *ctx, ac::link::Ac20Type type, uint32_t request_id,
                   uint32_t session_id, const uint8_t *json_body,
                   size_t body_len);

}  // namespace ac::linkusb
