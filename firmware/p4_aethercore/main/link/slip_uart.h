/* ESP-IDF UART glue for the Device B link layer (compiled for the P4 target
 * only). Binds an ac::link::Link to a UART connected to the espnow-bridge-c6
 * (C6 TX GPIO5 -> P4 RX pin, C6 RX GPIO4 <- P4 TX pin; pins chosen with the
 * physical jumper wiring at bring-up).
 *
 * RX: FreeRTOS task drains uart_read_bytes into Link::rx_bytes.
 * TX: link_uart_send() emits one encode_message() buffer atomically.
 */
#pragma once

#include <cstddef>
#include <cstdint>

#include "slip_link.h"

namespace ac::link {

/* Install UART driver, start the RX task feeding `link`. Returns true on
 * success. uart_num default 1; baud 921600 (matches bridge default). */
bool link_uart_start(Link *link, int uart_num, int tx_pin, int rx_pin,
                     int baud);

/* Encode (via link) and write one protocol message to the UART. Returns
 * bytes written, 0 on error. */
size_t link_uart_send(Link *link, Ac20Type type, uint32_t request_id,
                      uint32_t session_id, const uint8_t *body,
                      size_t body_len);

}  // namespace ac::link
