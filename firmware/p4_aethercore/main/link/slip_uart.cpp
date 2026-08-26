/* See slip_uart.h. ESP-IDF-only glue (not compiled in host tests). */
#include "slip_uart.h"

#include "driver/uart.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

namespace ac::link {

namespace {
uart_port_t g_uart_num = UART_NUM_MAX;
SemaphoreHandle_t g_tx_mutex = nullptr;

/* Static (single link per device). */
Link *g_rx_link = nullptr;

void rx_task(void *) {
  uint8_t buf[256];
  for (;;) {
    int n = uart_read_bytes(g_uart_num, buf, sizeof(buf), pdMS_TO_TICKS(1000));
    if (n > 0 && g_rx_link) g_rx_link->rx_bytes(buf, (size_t)n);
  }
}
}  // namespace

bool link_uart_start(Link *link, int uart_num, int tx_pin, int rx_pin,
                     int baud) {
  if (!link) return false;
  if (!g_tx_mutex) g_tx_mutex = xSemaphoreCreateMutex();
  if (!g_tx_mutex) return false;
  uart_config_t cfg = {};
  cfg.baud_rate = baud;
  cfg.data_bits = UART_DATA_8_BITS;
  cfg.parity = UART_PARITY_DISABLE;
  cfg.stop_bits = UART_STOP_BITS_1;
  cfg.flow_ctrl = UART_HW_FLOWCTRL_DISABLE;
  cfg.source_clk = UART_SCLK_DEFAULT;
  uart_port_t port = static_cast<uart_port_t>(uart_num);
  if (uart_driver_install(port, 4096, 4096, 0, nullptr, 0) != ESP_OK)
    return false;
  if (uart_param_config(port, &cfg) != ESP_OK) return false;
  if (uart_set_pin(port, tx_pin, rx_pin, UART_PIN_NO_CHANGE,
                   UART_PIN_NO_CHANGE) != ESP_OK)
    return false;
  g_uart_num = port;
  g_rx_link = link;
  if (xTaskCreate(rx_task, "ac_link_rx", 4096, nullptr, 10, nullptr) !=
      pdPASS)
    return false;
  ESP_LOGI("ac_link", "uart%d up @%d tx=%d rx=%d", uart_num, baud, tx_pin,
           rx_pin);
  return true;
}

size_t link_uart_send(Link *link, Ac20Type type, uint32_t request_id,
                      uint32_t session_id, const uint8_t *body,
                      size_t body_len) {
  if (!link || g_uart_num == UART_NUM_MAX) return 0;
  /* Bounded TX staging: 12 KiB covers messages up to ~5.5 KiB even when every
   * byte SLIP-escapes (typical JSON bodies: far larger). The service layer
   * chunks ASSISTANT_TEXT_DELTA to <= 1500 B, so this is never the limit in
   * practice; encode_message() fails safe (returns 0) if a body would not
   * fit, rather than overflowing. */
  static uint8_t out[12288];
  if (xSemaphoreTake(g_tx_mutex, pdMS_TO_TICKS(2000)) != pdTRUE) return 0;
  size_t n = link->encode_message(out, sizeof(out), type, request_id,
                                  session_id, body, body_len);
  int wrote = 0;
  if (n > 0)
    wrote = uart_write_bytes(g_uart_num, reinterpret_cast<const char *>(out),
                             n);
  xSemaphoreGive(g_tx_mutex);
  return wrote > 0 ? (size_t)wrote : 0;
}

}  // namespace ac::link
