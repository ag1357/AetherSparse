/* Option A link: hosted softAP + single-client framed TCP server.
 *
 * Serial request loop = Python operational.py parity (one request at a
 * time; USER_CANCEL/RESET are ordinary follow-up queries). Backpressure is
 * structural: at most one client, one frame in dispatch, queued bytes
 * bounded by the 16 KiB frame cap + lwIP receive buffers; the TCP window
 * throttles a flooding peer.
 *
 * Boot ordering (hardware-proven): the radio phase MUST run before the SD
 * card (SDMMC slot 0) is mounted. The C6 link is SDIO slot 1 on the shared
 * SDMMC host peripheral; once slot 0 is mounted, slot-1 card init fails
 * (observed: repeated sdmmc_card_init failed). Tactility's working build
 * on this board uses the same order (hosted first, SD later).
 *
 * Shared-host constraint: the legacy SDMMC driver keeps per-slot clock
 * state but switches the host-wide clock divider per command with no
 * cross-slot mutex (sdmmc_host_set_card_clk writes host-wide registers;
 * see ESP-IDF issue 16233 and esp-hosted's host_sdcard_with_hosted
 * example). Measured behavior on this board: steady-state slot-0 traffic
 * (815 s pack verify) interleaves with slot-1 RX-streaming polls with
 * ZERO failures, but the slot-0 mount/card-init window (~1.5 s) glitches
 * concurrent slot-1 commands. Consequences, all applied here:
 *  1. the full radio bring-up (connect + AP config + AP start, retried)
 *     completes BEFORE the SD mount, while slot 0 is untouched;
 *  2. CONFIG_ESP_HOSTED_TRANSPORT_RESTART_ON_FAILURE is disabled, so the
 *     transient slot-1 failures inside the later SD mount window log +
 *     retry instead of resetting the CPU (observed boot loop otherwise);
 *  3. pack_io's SD mount overrides host.init/deinit with no-ops so the
 *     FAT mount helper never re-inits or de-inits the shared host.
 *
 * The hosted slave connection is lazy: esp_hosted_init() only starts the
 * framework; the SDIO transport connects from the first Wi-Fi call
 * (probe-verified; can block >100 s on the first esp_wifi_init).
 *
 * Telemetry (MEAS, esp_timer us): link.rx_complete -> cognition_ms ->
 * link.tx_first, so transport time stays separated from cognition time.
 */

#include "link_tcp.h"

#include <cstdio>
#include <cstring>

#include "freertos/FreeRTOS.h"
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
esp_netif_t *g_ap_netif = nullptr;

/* Static task stacks: internal SRAM, never TCM (see header). */
StackType_t g_service_stack[16384 / sizeof(StackType_t)];
StaticTask_t g_service_tcb;
StackType_t g_loopback_stack[8192 / sizeof(StackType_t)];
StaticTask_t g_loopback_tcb;

SemaphoreHandle_t g_radio_done = nullptr;   /* given after AP up/failed */
SemaphoreHandle_t g_serve_go = nullptr;     /* given when pack boot done */
SemaphoreHandle_t g_listener_ready = nullptr; /* given once listening */
bool g_radio_ok = false;

int send_all(int fd, const uint8_t *buf, size_t len) {
  size_t sent = 0;
  while (sent < len) {
    int r = send(fd, buf + sent, len - sent, 0);
    if (r <= 0) return -1;
    sent += (size_t)r;
  }
  return 0;
}

/* Returns 1 frame complete, 0 clean disconnect, -1 error/abort.
 * Sets *partial when bytes of an incomplete unit were read. */
int read_exact(int fd, uint8_t *buf, size_t want, bool *partial) {
  size_t got = 0;
  while (got < want) {
    int r = recv(fd, buf + got, want - got, 0);
    if (r <= 0) {
      if (got > 0) *partial = true;
      return got > 0 ? -1 : (r == 0 ? 0 : -1);
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

/* One-time loopback proof of the full server path (sockets + framing +
 * strict codec + dispatch) before any RF client is involved: connects to a
 * dedicated self-test listener on 127.0.0.1:(port+1), sends a golden
 * SESSION_OPEN frame produced by the Python FramedJsonCodec, and strictly
 * decodes the responses, expecting HEALTH then CAPABILITIES (Python order).
 * The self-test uses the loopback interface because CONFIG_ESP_NETIF_LOOPBACK
 * is incompatible with esp_wifi_remote (link failure: missing netstack
 * symbols), so the AP netif cannot self-route; the production listener's
 * AP bind is separately proven by the tcp_listen line and the physical
 * acceptance exchange. The loopback listener is closed after the test. */
const char kLoopbackBody[] =
    "{\"protocol_version\":\"aethercore-tactility.v2\",\"message_id\":\"1-0\","
    "\"request_id\":\"1\",\"session_id\":\"7\",\"sequence\":0,"
    "\"type\":\"SESSION_OPEN\",\"payload\":{\"client_version\":\"p4-loopback/1\","
    "\"supported_protocols\":[\"aethercore-tactility.v2\"],"
    "\"requested_capabilities\":[]}}";
static_assert(sizeof(kLoopbackBody) - 1 == 257, "golden frame length drift");

void loopback_task(void *) {
  xSemaphoreTake(g_listener_ready, portMAX_DELAY);
  vTaskDelay(pdMS_TO_TICKS(500));
  int64_t t0 = esp_timer_get_time();
  int fd = socket(AF_INET, SOCK_STREAM, IPPROTO_IP);
  bool ok = false;
  int responses = 0;
  int err = 0;
  const char *stage = nullptr;
  if (fd >= 0) {
    struct sockaddr_in a = {0};
    a.sin_family = AF_INET;
    a.sin_port = htons(g_cfg.tcp_port + 1); /* self-test listener port */
    a.sin_addr.s_addr = htonl(0x7F000001); /* 127.0.0.1 self-test listener */
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
          int rr = read_exact(fd, buf, 4, &partial);
          if (rr != 1) { stage = "rx_hdr"; err = errno; break; }
          uint32_t flen = ((uint32_t)buf[0] << 24) | ((uint32_t)buf[1] << 16) |
                          ((uint32_t)buf[2] << 8) | buf[3];
          if (flen == 0 || flen > kMaxFrame) {
            stage = "rx_len"; err = (int)flen; break;
          }
          if (read_exact(fd, buf + 4, flen, &partial) != 1) {
            stage = "rx_body"; err = errno; break;
          }
          /* static: ProtocolMessage is ~24 KB — far beyond this task's
           * stack; single in-flight loopback exchange makes it safe. */
          static aethercore::protocol_v2::ProtocolMessage msg;
          /* DecodeFrame takes the FULL frame (4-byte length prefix + body);
           * buf holds exactly that after the two read_exact calls. */
          aethercore::protocol_v2::DecodeError de =
              aethercore::protocol_v2::DecodeFrame(buf, flen + 4, msg);
          if (de != aethercore::protocol_v2::DecodeError::OK) {
            stage = "decode"; err = (int)de; break;
          }
          responses++;
          if (msg.type == aethercore::protocol_v2::MsgType::HEALTH)
            saw_health = true;
          if (msg.type == aethercore::protocol_v2::MsgType::CAPABILITIES)
            saw_caps = true;
        }
        ok = saw_health && saw_caps;
      } else {
        stage = "send"; err = errno;
      }
    } else {
      stage = "connect"; err = errno;
    }
    close(fd);
  } else {
    stage = "socket"; err = errno;
  }
  printf("MEAS {\"loopback\":\"%s\",\"stage\":\"%s\",\"responses\":%d,"
         "\"ms\":%lld,\"err\":%d}\n",
         ok ? "ok" : "fail", stage ? stage : "rx_done", responses,
         (long long)(esp_timer_get_time() - t0) / 1000, err);
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
    printf("MEAS {\"cognition_ms\":%lld,\"rx\":%lu,\"link_stack_free\":%u}\n",
           (long long)(esp_timer_get_time() - t_dispatch) / 1000,
           (unsigned long)g_rx_index,
           (unsigned)(uxTaskGetStackHighWaterMark(NULL) * sizeof(StackType_t)));
  }
}

void service_task(void *) {
  /* Phase 1 (radio): must complete BEFORE the SD card is mounted (shared
   * SDMMC host; see file header). All flash-writing init (NVS) happens
   * here on this static-stack task — never from a heap-stacked task. */
  esp_err_t err = nvs_flash_init();
  if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
    nvs_flash_erase();
    err = nvs_flash_init();
  }
  ESP_ERROR_CHECK(err);
  ESP_ERROR_CHECK(esp_netif_init());
  ESP_ERROR_CHECK(esp_event_loop_create_default());

  int hrc = esp_hosted_init();
  printf("MEAS {\"link\":\"hosted_init\",\"rc\":%d}\n", hrc);

  esp_netif_t *ap = esp_netif_create_default_wifi_ap();
  g_ap_netif = ap;
  wifi_init_config_t wcfg = WIFI_INIT_CONFIG_DEFAULT();
  esp_err_t winit = ESP_FAIL;
  for (int i = 0; i < 8 && winit != ESP_OK; i++) {
    /* First call performs the lazy SDIO connect to the C6 and can block
     * for >100 s (slave reset + boot). */
    winit = esp_wifi_init(&wcfg);
    if (winit != ESP_OK) vTaskDelay(pdMS_TO_TICKS(1000));
  }
  printf("MEAS {\"link\":\"wifi_init\",\"rc\":\"%s\"}\n",
         esp_err_to_name(winit));

  esp_err_t werr = ESP_FAIL;
  if (winit == ESP_OK && esp_wifi_set_mode(WIFI_MODE_AP) == ESP_OK) {
    wifi_config_t ap_cfg = {0};
    strlcpy((char *)ap_cfg.ap.ssid, g_cfg.ap_ssid, sizeof(ap_cfg.ap.ssid));
    strlcpy((char *)ap_cfg.ap.password, g_cfg.ap_pass,
            sizeof(ap_cfg.ap.password));
    ap_cfg.ap.ssid_len = strlen(g_cfg.ap_ssid);
    ap_cfg.ap.channel = (uint8_t)g_cfg.ap_channel;
    ap_cfg.ap.authmode = WIFI_AUTH_WPA2_PSK;
    ap_cfg.ap.max_connection = 1; /* bounded client count (spec) */
    ap_cfg.ap.beacon_interval = 100;
    werr = esp_wifi_set_config(WIFI_IF_AP, &ap_cfg);
  }
  /* Start the AP NOW, before the SD mount: with the link freshly connected
   * and slot 0 untouched, the start RPC + softap-started event exchange
   * runs on an undisturbed host. Slot-1 RX streaming polls that collide
   * with the later SD mount window fail transiently and are retried by
   * the hosted RX thread (restart-on-failure is disabled) — verified
   * survivable, zero failures during the 815 s pack verify. A start
   * deferred past pack boot was observed to fail its RPC (ESP_FAIL). */
  esp_err_t serr = ESP_FAIL;
  if (werr == ESP_OK) {
    for (int i = 0; i < 5 && serr != ESP_OK; i++) {
      serr = esp_wifi_start();
      if (serr != ESP_OK) vTaskDelay(pdMS_TO_TICKS(1000));
    }
  }
  esp_netif_ip_info_t ip = {0};
  esp_netif_get_ip_info(g_ap_netif, &ip);
  printf("MEAS {\"link\":\"ap\",\"rc\":\"%s\",\"ssid\":\"%s\",\"channel\":%d,"
         "\"ip\":\"" IPSTR "\"}\n", esp_err_to_name(serr), g_cfg.ap_ssid,
         g_cfg.ap_channel, IP2STR(&ip.ip));
  if (werr != ESP_OK || serr != ESP_OK) {
    printf("MEAS {\"link\":\"radio_failed\",\"detail\":\"cfg %s start %s\"}\n",
           esp_err_to_name(werr), esp_err_to_name(serr));
    g_radio_ok = false;
    xSemaphoreGive(g_radio_done);
    vTaskDelete(NULL);
  }
  g_radio_ok = true;
  xSemaphoreGive(g_radio_done);

  /* Phase 2 (service): wait until the pack is verified + Pack-v2 resident,
   * then run the one-time loopback self-test on a dedicated loopback
   * listener and finally open the production listener. AP is up (phase 1). */
  xSemaphoreTake(g_serve_go, portMAX_DELAY);

  if (g_cfg.loopback_selftest) {
    int lb = socket(AF_INET, SOCK_STREAM, IPPROTO_IP);
    int one = 1;
    setsockopt(lb, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    struct sockaddr_in la = {0};
    la.sin_family = AF_INET;
    la.sin_port = htons(g_cfg.tcp_port + 1);
    la.sin_addr.s_addr = htonl(0x7F000001); /* loopback interface */
    if (lb >= 0 && bind(lb, (struct sockaddr *)&la, sizeof(la)) == 0 &&
        listen(lb, 1) == 0) {
      xTaskCreateStatic(loopback_task, "ac_loopback",
                        sizeof(g_loopback_stack) / sizeof(StackType_t), NULL, 4,
                        g_loopback_stack, &g_loopback_tcb);
      xSemaphoreGive(g_listener_ready);
      int cs = accept(lb, NULL, NULL); /* the self-test client */
      if (cs >= 0) {
        g_client = cs;
        serve_client(cs); /* identical dispatch path to production */
        close(cs);
        g_client = -1;
      }
    } else {
      printf("MEAS {\"loopback\":\"fail\",\"detail\":\"selftest_listen\"}\n");
    }
    if (lb >= 0) close(lb);
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

bool radio_up(const Config &cfg) {
  g_cfg = cfg;
  g_radio_done = xSemaphoreCreateBinary();
  g_serve_go = xSemaphoreCreateBinary();
  g_listener_ready = xSemaphoreCreateBinary();
  if (xTaskCreateStatic(service_task, "ac_link_tcp",
                        sizeof(g_service_stack) / sizeof(StackType_t), NULL, 5,
                        g_service_stack, &g_service_tcb) == NULL)
    return false;
  /* Block the caller (main task) until the AP is up or has failed: the SD
   * mount must not start while slot-1 bring-up is in flight. */
  xSemaphoreTake(g_radio_done, portMAX_DELAY);
  return g_radio_ok;
}

void serve(void) { xSemaphoreGive(g_serve_go); }

}  // namespace ac::linktcp
