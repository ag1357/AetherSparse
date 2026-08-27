/* Option A link: hosted softAP + single-client framed TCP server.
 *
 * Serial request loop = Python operational.py parity (one request at a
 * time; USER_CANCEL/RESET are ordinary follow-up queries). Backpressure is
 * structural: at most one client, one frame in dispatch, queued bytes
 * bounded by the 16 KiB frame cap + lwIP receive buffers; the TCP window
 * throttles a flooding peer.
 *
 * Telemetry (MEAS, esp_timer us): link.rx_complete -> cognition_ms ->
 * link.tx_first / link.tx_done, so transport time stays separated from
 * cognition time in the mission latency breakdown.
 */

#include "link_tcp.h"

#include <cstdio>
#include <cstring>

#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "esp_err.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_wifi.h" /* routed to the factory C6 via esp_wifi_remote */
#include "nvs_flash.h"

#include "esp_hosted.h"
#include "esp_hosted_event.h"

#include "lwip/inet.h"
#include "lwip/sockets.h"

#include "protocol/protocol_v2.h"
#include "service_runtime.h"

namespace ac::linktcp {
namespace {

constexpr const char *TAG = "ac_tcp";
constexpr size_t kMaxFrame = aethercore::protocol_v2::kMaxFrameBytes;

Config g_cfg;
int g_client = -1;
bool g_tx_first_pending = false;
int64_t g_rx_complete_us = 0;
uint32_t g_rx_index = 0;

/* Static task stacks: internal SRAM, never TCM (see header). */
StackType_t g_service_stack[16384 / sizeof(StackType_t)];
StaticTask_t g_service_tcb;
StackType_t g_loopback_stack[8192 / sizeof(StackType_t)];
StaticTask_t g_loopback_tcb;
SemaphoreHandle_t g_listener_ready = nullptr;

int send_all(int fd, const uint8_t *buf, size_t len) {
  size_t sent = 0;
  while (sent < len) {
    int r = send(fd, buf + sent, len - sent, 0);
    if (r <= 0) return -1;
    sent += (size_t)r;
  }
  return 0;
}

/* Returns 1 frame complete, 0 clean disconnect, -1 error/abort. */
int read_exact(int fd, uint8_t *buf, size_t want, bool *partial) {
  size_t got = 0;
  while (got < want) {
    int r = recv(fd, buf + got, want - got, 0);
    if (r <= 0) {
      if (got > 0) *partial = true;
      return (int)got > 0 ? -1 : (r == 0 ? 0 : -1);
    }
    got += (size_t)r;
  }
  return 1;
}

void service_task(void *arg);

}  // namespace

void response_sink(void *ctx, ac::link::Ac20Type type, uint32_t request_id,
                   uint32_t session_id, const uint8_t *json_body,
                   size_t body_len) {
  (void)ctx;
  (void)type;
  (void)request_id;
  (void)session_id; /* JSON envelope is authoritative (spec section 2). */
  int fd = g_client;
  if (fd < 0 || body_len == 0 || body_len > kMaxFrame) return;
  uint8_t hdr[4] = {(uint8_t)(body_len >> 24), (uint8_t)(body_len >> 16),
                    (uint8_t)(body_len >> 8), (uint8_t)body_len};
  if (g_tx_first_pending) {
    printf("MEAS {\"link\":\"tx_first\",\"rx\":%lu,\"dt_us\":%lld}\n",
           (unsigned long)g_rx_index,
           (long long)(esp_timer_get_time() - g_rx_complete_us));
    g_tx_first_pending = false;
  }
  if (send_all(fd, hdr, 4) != 0 || send_all(fd, json_body, body_len) != 0) {
    ESP_LOGW(TAG, "client write failed; dropping response");
  }
}

namespace {

void on_cp_init(void *, esp_event_base_t, int32_t, void *) {
  if (g_listener_ready) {
    /* Reuse the semaphore as the CP_INIT signal: given once per boot. */
    static bool signaled = false;
    if (!signaled) {
      signaled = true;
      xSemaphoreGive(g_listener_ready);
    }
  }
}

/* One-time loopback proof of the full server path (sockets + framing +
 * strict codec + dispatch) before any RF client is involved: connects to
 * the listener on the AP netif address, sends a golden SESSION_OPEN frame
 * produced by the Python FramedJsonCodec, and strictly decodes the
 * responses, expecting HEALTH then CAPABILITIES (Python order). */
const char kLoopbackBody[] =
    "{\"protocol_version\":\"aethercore-tactility.v2\",\"message_id\":\"1-0\","
    "\"request_id\":\"1\",\"session_id\":\"7\",\"sequence\":0,"
    "\"type\":\"SESSION_OPEN\",\"payload\":{\"client_version\":\"p4-loopback/1\","
    "\"supported_protocols\":[\"aethercore-tactility.v2\"],"
    "\"requested_capabilities\":[]}}";
static_assert(sizeof(kLoopbackBody) - 1 == 257, "golden frame length drift");

void loopback_task(void *) {
  xSemaphoreTake(g_listener_ready, portMAX_DELAY); /* listener up */
  vTaskDelay(pdMS_TO_TICKS(500));
  int64_t t0 = esp_timer_get_time();
  int fd = socket(AF_INET, SOCK_STREAM, IPPROTO_IP);
  bool ok = false;
  int responses = 0;
  if (fd >= 0) {
    struct sockaddr_in a = {0};
    a.sin_family = AF_INET;
    a.sin_port = htons(g_cfg.tcp_port);
    a.sin_addr.s_addr = htonl(0xC0A80401); /* 192.168.4.1 */
    if (connect(fd, (struct sockaddr *)&a, sizeof(a)) == 0) {
      uint32_t len = sizeof(kLoopbackBody) - 1;
      uint8_t hdr[4] = {(uint8_t)(len >> 24), (uint8_t)(len >> 16),
                        (uint8_t)(len >> 8), (uint8_t)len};
      if (send_all(fd, hdr, 4) == 0 &&
          send_all(fd, (const uint8_t *)kLoopbackBody, len) == 0) {
        static uint8_t buf[4 + 16640];
        bool saw_health = false, saw_caps = false;
        int64_t deadline = t0 + 8000000;
        while (esp_timer_get_time() < deadline && !(saw_health && saw_caps)) {
          bool partial = false;
          if (read_exact(fd, buf, 4, &partial) != 1) break;
          uint32_t flen = ((uint32_t)buf[0] << 24) | ((uint32_t)buf[1] << 16) |
                          ((uint32_t)buf[2] << 8) | buf[3];
          if (flen == 0 || flen > kMaxFrame) break;
          if (read_exact(fd, buf + 4, flen, &partial) != 1) break;
          aethercore::protocol_v2::ProtocolMessage msg;
          if (aethercore::protocol_v2::DecodeFrame(buf + 4, flen, msg) !=
              aethercore::protocol_v2::DecodeError::OK)
            break;
          responses++;
          if (msg.type == aethercore::protocol_v2::MsgType::HEALTH)
            saw_health = true;
          if (msg.type == aethercore::protocol_v2::MsgType::CAPABILITIES)
            saw_caps = true;
        }
        ok = saw_health && saw_caps;
      }
    }
    close(fd);
  }
  printf("MEAS {\"loopback\":\"%s\",\"responses\":%d,\"ms\":%lld}\n",
         ok ? "ok" : "fail", responses,
         (long long)(esp_timer_get_time() - t0) / 1000);
  vTaskDelete(NULL);
}

void serve_client(int fd) {
  static uint8_t frame[4 + 16640]; /* internal SRAM (static), off-stack */
  for (;;) {
    bool partial = false;
    int hr = read_exact(fd, frame, 4, &partial);
    if (hr != 1) {
      if (partial)
        printf("MEAS {\"link\":\"frame_partial_discard\",\"stage\":\"header\"}\n");
      return;
    }
    uint32_t len = ((uint32_t)frame[0] << 24) | ((uint32_t)frame[1] << 16) |
                   ((uint32_t)frame[2] << 8) | frame[3];
    if (len == 0 || len > kMaxFrame) {
      printf("MEAS {\"link\":\"frame_reject\",\"reason\":\"length\",\"len\":%lu}\n",
             (unsigned long)len);
      return; /* non-conformant peer framing: close without response */
    }
    if (read_exact(fd, frame + 4, len, &partial) != 1) {
      if (partial)
        printf("MEAS {\"link\":\"frame_partial_discard\",\"stage\":\"body\","
               "\"len\":%lu}\n", (unsigned long)len);
      return;
    }
    g_rx_index++;
    g_rx_complete_us = esp_timer_get_time();
    g_tx_first_pending = true;
    printf("MEAS {\"link\":\"rx_complete\",\"rx\":%lu,\"bytes\":%lu}\n",
           (unsigned long)g_rx_index, (unsigned long)len);
    int64_t t_dispatch = g_rx_complete_us;
    /* The runtime strictly decodes (schema errors -> bounded ERROR frame)
     * and dispatches; responses leave via response_sink on this same task.
     * The Ac20Type parameter is an ESP-NOW transport artifact, unused by
     * the runtime (JSON envelope is authoritative) — nominal value only. */
    ac::runtime::service_handle_message(ac::link::Ac20Type::UserText, 0, 0,
                                        frame + 4, len);
    printf("MEAS {\"cognition_ms\":%lld,\"rx\":%lu}\n",
           (long long)(esp_timer_get_time() - t_dispatch) / 1000,
           (unsigned long)g_rx_index);
  }
}

void service_task(void *) {
  esp_err_t err = nvs_flash_init();
  if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
    nvs_flash_erase();
    err = nvs_flash_init();
  }
  ESP_ERROR_CHECK(err);
  ESP_ERROR_CHECK(esp_netif_init());
  ESP_ERROR_CHECK(esp_event_loop_create_default());

  g_listener_ready = xSemaphoreCreateBinary();
  ESP_ERROR_CHECK(esp_event_handler_register(ESP_HOSTED_EVENT,
                                             ESP_HOSTED_EVENT_CP_INIT,
                                             &on_cp_init, NULL));
  int hrc = esp_hosted_init();
  printf("MEAS {\"link\":\"hosted_init\",\"rc\":%d}\n", hrc);
  bool link_up =
      xSemaphoreTake(g_listener_ready, pdMS_TO_TICKS(45000)) == pdTRUE;
  /* Semaphore now repurposed for the listener-ready signal. */
  printf("MEAS {\"link\":\"hosted_link\",\"up\":%s}\n",
         link_up ? "true" : "false");
  if (!link_up) {
    /* Fail closed: without the hosted link there is no Wi-Fi path; do not
     * let esp_wifi_init abort the firmware below. */
    printf("MEAS {\"phase\":\"service\",\"status\":\"LINK_FAILED\","
           "\"detail\":\"hosted link timeout\"}\n");
    vTaskDelete(NULL);
  }

  esp_netif_t *ap = esp_netif_create_default_wifi_ap();
  wifi_init_config_t wcfg = WIFI_INIT_CONFIG_DEFAULT();
  ESP_ERROR_CHECK(esp_wifi_init(&wcfg));
  ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
  wifi_config_t ap_cfg = {0};
  strlcpy((char *)ap_cfg.ap.ssid, g_cfg.ap_ssid, sizeof(ap_cfg.ap.ssid));
  strlcpy((char *)ap_cfg.ap.password, g_cfg.ap_pass, sizeof(ap_cfg.ap.password));
  ap_cfg.ap.ssid_len = strlen(g_cfg.ap_ssid);
  ap_cfg.ap.channel = (uint8_t)g_cfg.ap_channel;
  ap_cfg.ap.authmode = WIFI_AUTH_WPA2_PSK;
  ap_cfg.ap.max_connection = 1; /* bounded client count (spec) */
  ap_cfg.ap.beacon_interval = 100;
  ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &ap_cfg));
  esp_err_t werr = esp_wifi_start();
  esp_netif_ip_info_t ip = {0};
  esp_netif_get_ip_info(ap, &ip);
  printf("MEAS {\"link\":\"ap\",\"rc\":\"%s\",\"ssid\":\"%s\",\"channel\":%d,"
         "\"ip\":\"" IPSTR "\"}\n", esp_err_to_name(werr), g_cfg.ap_ssid,
         g_cfg.ap_channel, IP2STR(&ip.ip));
  if (werr != ESP_OK) {
    printf("MEAS {\"phase\":\"service\",\"status\":\"LINK_FAILED\"}\n");
    vTaskDelete(NULL);
  }

  int ls = socket(AF_INET, SOCK_STREAM, IPPROTO_IP);
  int one = 1;
  setsockopt(ls, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
  struct sockaddr_in addr = {0};
  addr.sin_family = AF_INET;
  addr.sin_port = htons(g_cfg.tcp_port);
  addr.sin_addr.s_addr = ip.ip.addr; /* bind to the private AP netif only */
  if (bind(ls, (struct sockaddr *)&addr, sizeof(addr)) != 0 ||
      listen(ls, 1) != 0) {
    printf("MEAS {\"link\":\"tcp_listen\",\"rc\":\"bind_fail\"}\n");
    vTaskDelete(NULL);
  }
  printf("MEAS {\"link\":\"tcp_listen\",\"port\":%d,\"bound\":\"" IPSTR "\"}\n",
         g_cfg.tcp_port, IP2STR(&ip.ip));
  if (g_cfg.loopback_selftest)
    xTaskCreateStatic(loopback_task, "ac_loopback",
                      sizeof(g_loopback_stack) / sizeof(StackType_t), NULL, 4,
                      g_loopback_stack, &g_loopback_tcb);
  if (g_listener_ready) xSemaphoreGive(g_listener_ready); /* listener up */

  for (;;) {
    struct sockaddr_in ca;
    socklen_t cl = sizeof(ca);
    int cs = accept(ls, (struct sockaddr *)&ca, &cl);
    if (cs < 0) continue;
    g_client = cs;
    printf("MEAS {\"link\":\"client\",\"from\":\"" IPSTR "\"}\n",
           IP2STR((ip4_addr_t *)&ca.sin_addr));
    serve_client(cs);
    close(cs);
    g_client = -1;
    printf("MEAS {\"link\":\"client_closed\"}\n");
  }
}

}  // namespace

bool start(const Config &cfg) {
  g_cfg = cfg;
  return xTaskCreateStatic(service_task, "ac_link_tcp",
                           sizeof(g_service_stack) / sizeof(StackType_t), NULL,
                           5, g_service_stack, &g_service_tcb) != NULL;
}

}  // namespace ac::linktcp
