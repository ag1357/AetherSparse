/* ESP32-P4 USB CDC-ACM device backend for AetherLink.
 *
 * Only byte movement is implemented here.  The shared FrameDecoder owns the
 * four-byte big-endian framing and resets partial state on detach.  Device B
 * is identified authoritatively by the protocol-v2 SESSION_OPEN -> HEALTH +
 * CAPABILITIES exchange; VID/PID/product strings are discovery hints only.
 */
#include "link_usb_cdc.h"

#include <cstdio>
#include <cstring>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "esp_log.h"
#include "esp_timer.h"
#include "tinyusb.h"
#include "tinyusb_cdc_acm.h"
#include "tusb_cdc_acm.h"

#include "link/aetherlink_stream.h"
#include "protocol/protocol_v2.h"
#include "service_runtime.h"

namespace ac::linkusb {
namespace {

constexpr const char *TAG = "ac_usb_cdc";
constexpr uint32_t kIoTimeoutMs = 1000;

TaskHandle_t g_service_task = nullptr;
bool g_started = false;
bool g_attached = false;
ac::aetherlink::FrameDecoder g_decoder;
ac::aetherlink::Transport g_transport{};

void device_event(tinyusb_event_t *event, void *) {
  if (event == nullptr) return;
  if (event->id == TINYUSB_EVENT_ATTACHED) {
    g_attached = true;
    printf("MEAS {\"link\":\"usb_attached\"}\n");
  } else if (event->id == TINYUSB_EVENT_DETACHED) {
    g_attached = false;
    g_decoder.reset();
    if (g_service_task != nullptr) xTaskNotifyGive(g_service_task);
    printf("MEAS {\"link\":\"usb_detached\",\"partial_discarded\":true}\n");
  }
}

void cdc_rx(int, cdcacm_event_t *) {
  if (g_service_task != nullptr) xTaskNotifyGive(g_service_task);
}

bool transport_open(void *) { return g_started; }
void transport_close(void *) { g_decoder.reset(); }

int transport_read(void *, uint8_t *bytes, size_t capacity,
                   uint32_t timeout_ms) {
  if (bytes == nullptr || capacity == 0) return -1;
  size_t received = 0;
  esp_err_t rc = tinyusb_cdcacm_read(TINYUSB_CDC_ACM_0, bytes, capacity,
                                     &received);
  if (rc != ESP_OK) return -1;
  if (received != 0) return static_cast<int>(received);
  ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(timeout_ms));
  if (!g_attached) return 0;
  rc = tinyusb_cdcacm_read(TINYUSB_CDC_ACM_0, bytes, capacity, &received);
  return rc == ESP_OK ? static_cast<int>(received) : -1;
}

int transport_write(void *, const uint8_t *bytes, size_t length,
                    uint32_t timeout_ms) {
  if (bytes == nullptr || length == 0 || !tud_cdc_n_connected(0)) return -1;
  size_t sent = 0;
  const int64_t deadline = esp_timer_get_time() +
                           static_cast<int64_t>(timeout_ms) * 1000;
  while (sent < length && esp_timer_get_time() < deadline) {
    sent += tinyusb_cdcacm_write_queue(
        TINYUSB_CDC_ACM_0, bytes + sent, length - sent);
    tinyusb_cdcacm_write_flush(TINYUSB_CDC_ACM_0, 10);
    if (sent < length) vTaskDelay(1);
  }
  return static_cast<int>(sent);
}

bool transport_connected(void *) {
  return g_attached && tud_cdc_n_connected(0);
}
uint32_t transport_capabilities(void *) {
  return ac::aetherlink::kCapabilityByteStream |
         ac::aetherlink::kCapabilityHotplug |
         ac::aetherlink::kCapabilityCancelIo |
         ac::aetherlink::kCapabilityUsbCdc;
}
void transport_cancel(void *) {
  if (g_service_task != nullptr) xTaskNotifyGive(g_service_task);
}

void service_task(void *) {
  g_service_task = xTaskGetCurrentTaskHandle();
  static uint8_t fragment[1024];
  for (;;) {
    if (!g_transport.connected(g_transport.context)) {
      g_decoder.reset();
      vTaskDelay(pdMS_TO_TICKS(100));
      continue;
    }
    const int n = g_transport.read(g_transport.context, fragment,
                                   sizeof(fragment), 250);
    if (n <= 0) continue;
    size_t offset = 0;
    while (offset < static_cast<size_t>(n)) {
      size_t consumed = 0;
      const auto status = g_decoder.feed(fragment + offset,
                                         static_cast<size_t>(n) - offset,
                                         &consumed);
      offset += consumed;
      if (status == ac::aetherlink::DecodeStatus::kMalformedLength) {
        ESP_LOGW(TAG, "malformed protocol-v2 frame length; stream reset");
        break;
      }
      if (status == ac::aetherlink::DecodeStatus::kFrameReady) {
        /* JSON is authoritative for type/request/session on stream links. */
        ac::runtime::service_handle_message(
            ac::link::Ac20Type::UserText, 0, 0, g_decoder.payload(),
            g_decoder.payload_size());
        g_decoder.consume_frame();
      }
      if (consumed == 0 &&
          status == ac::aetherlink::DecodeStatus::kNeedMore) break;
    }
  }
}

}  // namespace

bool start() {
  if (g_started) return true;
  tinyusb_config_t tusb_cfg = {};
  tusb_cfg.external_phy = false;
  tusb_cfg.self_powered = false;
  tusb_cfg.vbus_monitor_io = 0;
  tusb_cfg.event_cb = device_event;
  if (tinyusb_driver_install(&tusb_cfg) != ESP_OK) {
    ESP_LOGE(TAG, "tinyusb_driver_install failed");
    return false;
  }
  const tinyusb_config_cdcacm_t acm_cfg = {
      .usb_dev = TINYUSB_USBDEV_0,
      .cdc_port = TINYUSB_CDC_ACM_0,
      .callback_rx = cdc_rx,
      .callback_rx_wanted_char = nullptr,
      .callback_line_state_changed = nullptr,
      .callback_line_coding_changed = nullptr,
  };
  if (tusb_cdc_acm_init(&acm_cfg) != ESP_OK) {
    ESP_LOGE(TAG, "tusb_cdc_acm_init failed");
    tinyusb_driver_uninstall();
    return false;
  }
  g_transport = {nullptr, transport_open, transport_close, transport_read,
                 transport_write, transport_connected,
                 transport_capabilities, transport_cancel};
  g_started = ac::aetherlink::valid(g_transport);
  printf("MEAS {\"phase\":\"link\",\"transport\":\"USB_CDC_ACM\","
         "\"c6_initialized\":false}\n");
  return g_started;
}

void serve() {
  static StackType_t stack[16384 / sizeof(StackType_t)];
  static StaticTask_t tcb;
  if (!g_started || g_service_task != nullptr) return;
  xTaskCreateStatic(service_task, "aetherlink_usb", sizeof(stack) / sizeof(stack[0]),
                    nullptr, 5, stack, &tcb);
}

void response_sink(void *, ac::link::Ac20Type, uint32_t, uint32_t,
                   const uint8_t *json_body, size_t body_len) {
  if (!ac::aetherlink::write_frame(g_transport, json_body, body_len,
                                   kIoTimeoutMs)) {
    ESP_LOGW(TAG, "response dropped: USB CDC disconnected or short write");
  }
}

}  // namespace ac::linkusb
