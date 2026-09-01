/* Universal raw-UART fallback. No SLIP, C6 or ESP-NOW semantics: this backend
 * carries the identical u32be-length + JSON stream used by USB and TCP. */
#include "link_uart_stream.h"

#include "driver/uart.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "link/aetherlink_stream.h"
#include "service_runtime.h"

namespace ac::linkuart {
namespace {
constexpr uart_port_t kPort = UART_NUM_1;
constexpr const char *TAG = "ac_uart";
bool g_started = false;
ac::aetherlink::FrameDecoder g_decoder;

bool open(void *) { return g_started; }
void close(void *) { g_decoder.reset(); }
int read(void *, uint8_t *data, size_t cap, uint32_t timeout) {
  return uart_read_bytes(kPort, data, cap, pdMS_TO_TICKS(timeout));
}
int write(void *, const uint8_t *data, size_t len, uint32_t) {
  return uart_write_bytes(kPort, data, len);
}
bool connected(void *) { return g_started; }
uint32_t capabilities(void *) {
  return ac::aetherlink::kCapabilityByteStream |
         ac::aetherlink::kCapabilityCancelIo |
         ac::aetherlink::kCapabilityUart;
}
void cancel(void *) { uart_flush_input(kPort); }
ac::aetherlink::Transport g_transport = {nullptr, open, close, read, write,
                                         connected, capabilities, cancel};

void task(void *) {
  uint8_t fragment[512];
  for (;;) {
    const int count = read(nullptr, fragment, sizeof(fragment), 250);
    if (count <= 0) continue;
    /* Physical-bring-up diagnostic: proves bytes arrive (vs wiring/baud) and
     * whether the first header bytes are plausible (u32be length <= 16 KiB). */
    ESP_LOGI(TAG, "rx %d bytes first=%02x %02x %02x %02x %02x %02x %02x %02x",
             count, count > 0 ? fragment[0] : 0, count > 1 ? fragment[1] : 0,
             count > 2 ? fragment[2] : 0, count > 3 ? fragment[3] : 0,
             count > 4 ? fragment[4] : 0, count > 5 ? fragment[5] : 0,
             count > 6 ? fragment[6] : 0, count > 7 ? fragment[7] : 0);
    size_t offset = 0;
    while (offset < static_cast<size_t>(count)) {
      size_t used = 0;
      const auto status = g_decoder.feed(fragment + offset,
          static_cast<size_t>(count) - offset, &used);
      offset += used;
      if (status == ac::aetherlink::DecodeStatus::kMalformedLength) {
        /* Boot-time line noise lands as stray 0x00 bytes before the peer's
         * UART driver starts driving TX; without a resync the length-prefixed
         * stream desyncs permanently (observed on first physical bring-up).
         * Drop the decoder state and the RX FIFO; the peer's next frame
         * starts on a clean boundary. */
        g_decoder.reset();
        uart_flush_input(kPort);
        ESP_LOGW(TAG, "malformed frame; decoder reset + RX flushed");
        break;
      }
      if (status == ac::aetherlink::DecodeStatus::kFrameReady) {
        ac::runtime::service_handle_message(ac::link::Ac20Type::UserText, 0, 0,
            g_decoder.payload(), g_decoder.payload_size());
        g_decoder.consume_frame();
      }
      if (used == 0) break;
    }
  }
}
}  // namespace

bool start(int baud, int tx_pin, int rx_pin) {
  if (baud < 115200 || tx_pin < 0 || rx_pin < 0) {
    ESP_LOGE(TAG, "UART fallback pins/baud not provisioned");
    return false;
  }
  uart_config_t config = {};
  config.baud_rate = baud;
  config.data_bits = UART_DATA_8_BITS;
  config.parity = UART_PARITY_DISABLE;
  config.stop_bits = UART_STOP_BITS_1;
  config.flow_ctrl = UART_HW_FLOWCTRL_DISABLE;
  config.rx_flow_ctrl_thresh = 0;
  config.source_clk = UART_SCLK_DEFAULT;
  if (uart_driver_install(kPort, 2048, 2048, 0, nullptr, 0) != ESP_OK ||
      uart_param_config(kPort, &config) != ESP_OK ||
      uart_set_pin(kPort, tx_pin, rx_pin, UART_PIN_NO_CHANGE,
                   UART_PIN_NO_CHANGE) != ESP_OK) return false;
  g_started = true;
  return true;
}

void serve() {
  static StackType_t stack[8192 / sizeof(StackType_t)];
  static StaticTask_t tcb;
  xTaskCreateStatic(task, "aetherlink_uart", sizeof(stack) / sizeof(stack[0]),
                    nullptr, 5, stack, &tcb);
}

void response_sink(void *, ac::link::Ac20Type, uint32_t, uint32_t,
                   const uint8_t *body, size_t length) {
  if (!ac::aetherlink::write_frame(g_transport, body, length, 1000))
    ESP_LOGW(TAG, "UART response short write");
}
}  // namespace ac::linkuart
