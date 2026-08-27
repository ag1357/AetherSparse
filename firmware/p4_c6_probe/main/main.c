/* AetherCore V15 mission — Device B C6 capability probe.
 *
 * Directive (mission doc, "C6 FIRMWARE / ESP-NOW — DO NOT FLASH BLINDLY",
 * 2026-08-26): before any C6 firmware decision, physically determine the
 * Device-B C6's factory firmware identity and capabilities over the existing
 * P4<->C6 SDIO path. This probe is strictly read-only toward the C6: no
 * flash writes, no OTA, no persistent state.
 *
 * Records, as MEAS JSONL on the console:
 *   1. ESP-Hosted link establishment + RPC readiness
 *   2. C6 firmware identity (fw version RPC + full app descriptor incl.
 *      project name, IDF version, compile date/time, ELF sha256)
 *   3. Wi-Fi operation via esp_wifi_remote (STA start, MAC, scan count)
 *   4. Hosted ESP-NOW capability: Tactility wire-contract REQ_INIT on the
 *      esp_hosted custom-data channel (msg 0xE500) — the RESP_INIT result
 *      is the decisive "esp_now_init() result" for the hosted path
 *   5. C6 STA MAC (settles the AetherLink peer-MAC open question)
 *
 * Bluetooth is intentionally not probed on Device B: no BLE consumer exists
 * there (CardKB2 pairs to Device A), and "where relevant" does not apply.
 *
 * Wire-contract constants are copied verbatim (Apache-2.0) from the
 * hand-synced contract header
 * Tactility/Private/Tactility/service/espnow/esp_hosted_espnow_bridge_proto.h
 * so this probe speaks exactly what Device A's working stack speaks.
 */

#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "esp_err.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h" /* routed to the C6 via esp_wifi_remote */
#include "nvs_flash.h"

#include "lwip/inet.h"
#include "lwip/sockets.h"

#include "esp_hosted.h"
#include "esp_hosted_api_types.h"
#include "esp_hosted_misc.h"

static const char *TAG = "p4_c6_probe";

/* ---- Tactility ESP-NOW bridge wire contract (verbatim subset) ---- */
#define ESPNOW_BRIDGE_REQ_INIT   0xE500U
#define ESPNOW_BRIDGE_RESP_INIT  0xE501U
#define ESPNOW_BRIDGE_KEY_LEN    16

typedef struct __attribute__((packed)) {
    uint32_t txn_id;
    uint8_t pmk[ESPNOW_BRIDGE_KEY_LEN];
    uint8_t channel;    /* 0 = use current STA/AP channel */
    uint8_t long_range; /* bool as uint8_t */
    uint8_t mode;       /* 0 = STATION */
} espnow_bridge_req_init_t;

typedef struct __attribute__((packed)) {
    uint32_t txn_id;
    int32_t esp_err;        /* slave-side esp_now_init() result */
    uint32_t espnow_version; /* slave esp_now_get_version(), 0 on error */
} espnow_bridge_resp_init_t;

static SemaphoreHandle_t s_resp_sem;
static volatile int s_resp_got = 0;
static volatile int32_t s_resp_err = ESP_FAIL;
static volatile uint32_t s_resp_version = 0;

static void on_bridge_resp_init(uint32_t msg_id, const uint8_t *data,
                                size_t len, void *ctx) {
    (void)msg_id;
    (void)ctx;
    if (len >= sizeof(espnow_bridge_resp_init_t)) {
        const espnow_bridge_resp_init_t *r = (const espnow_bridge_resp_init_t *)data;
        s_resp_err = r->esp_err;
        s_resp_version = r->espnow_version;
        s_resp_got = 1;
    } else {
        s_resp_got = 2; /* malformed */
    }
    xSemaphoreGive(s_resp_sem);
}

static void print_sha256_hex(const uint8_t *sha) {
    char hex[65];
    for (int i = 0; i < 32; i++) snprintf(hex + i * 2, 3, "%02x", sha[i]);
    hex[64] = 0;
    printf("\"elf_sha256\":\"%s\"", hex);
}

/* ---- Option A TCP framed-echo server (port 9000) ---- */

static int recv_all(int fd, uint8_t *buf, size_t want) {
    size_t got = 0;
    while (got < want) {
        int r = recv(fd, buf + got, want - got, 0);
        if (r <= 0) return -1;
        got += (size_t)r;
    }
    return 0;
}

static int send_all(int fd, const uint8_t *buf, size_t len) {
    size_t sent = 0;
    while (sent < len) {
        int r = send(fd, buf + sent, len - sent, 0);
        if (r <= 0) return -1;
        sent += (size_t)r;
    }
    return 0;
}

static void tcp_echo_task(void *arg) {
    (void)arg;
    static uint8_t s_buf[16400]; /* 16 KiB max frame + header slack */
    int ls = socket(AF_INET, SOCK_STREAM, IPPROTO_IP);
    if (ls < 0) { printf("MEAS {\"probe\":\"tcp.listen\",\"rc\":\"socket_fail\"}\n"); vTaskDelete(NULL); }
    int one = 1;
    setsockopt(ls, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    struct sockaddr_in addr = {0};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(9000);
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    if (bind(ls, (struct sockaddr *)&addr, sizeof(addr)) != 0 ||
        listen(ls, 1) != 0) {
        printf("MEAS {\"probe\":\"tcp.listen\",\"rc\":\"bind_fail\"}\n");
        vTaskDelete(NULL);
    }
    printf("MEAS {\"probe\":\"tcp.listen\",\"rc\":\"ESP_OK\",\"port\":9000}\n");
    for (;;) {
        struct sockaddr_in caddr;
        socklen_t clen = sizeof(caddr);
        int cs = accept(ls, (struct sockaddr *)&caddr, &clen);
        if (cs < 0) continue;
        printf("MEAS {\"probe\":\"tcp.client\",\"from\":\"" IPSTR "\"}\n",
               IP2STR((ip4_addr_t *)&caddr.sin_addr));
        uint64_t frames = 0, bytes = 0;
        for (;;) {
            uint8_t hdr[4];
            if (recv_all(cs, hdr, 4) != 0) break;
            uint32_t len = ((uint32_t)hdr[0] << 24) | ((uint32_t)hdr[1] << 16) |
                           ((uint32_t)hdr[2] << 8) | (uint32_t)hdr[3];
            if (len == 0 || len > 16384) break;
            if (recv_all(cs, s_buf, len) != 0) break;
            if (send_all(cs, hdr, 4) != 0 || send_all(cs, s_buf, len) != 0) break;
            frames++;
            bytes += len;
        }
        printf("MEAS {\"probe\":\"tcp.session\",\"frames\":%llu,\"bytes\":%llu}\n",
               (unsigned long long)frames, (unsigned long long)bytes);
        close(cs);
    }
}

/* All probe work runs from a task with a STATIC stack. Static arrays land
 * in internal SRAM (0x4FF...), never in TCM (0x301...): the flash-write
 * path asserts esp_task_stack_is_sane_cache_disabled(), which rejects TCM
 * stacks, and the heap-allocated main-task stack can land in TCM depending
 * on binary layout (observed boot-loop on stage 2). */
static StackType_t s_worker_stack[8192 / sizeof(StackType_t)];
static StaticTask_t s_worker_tcb;

static void probe_worker(void *arg) {
    (void)arg;
    esp_err_t err;
    printf("MEAS {\"probe\":\"boot\",\"target\":\"device-b-c6\",\"mode\":\"read-only\"}\n");

    err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    /* 1. ESP-Hosted link over SDIO (slot 1, pins per known-working build). */
    int hrc = esp_hosted_init();
    printf("MEAS {\"probe\":\"hosted.init\",\"rc\":%d}\n", hrc);

    /* 2. C6 firmware identity. Poll: the RPC layer becomes ready when the
     * slave announces itself; the fwversion RPC doubles as readiness check. */
    esp_hosted_coprocessor_fwver_t ver = {0};
    esp_err_t verr = ESP_FAIL;
    for (int i = 0; i < 15 && verr != ESP_OK; i++) {
        vTaskDelay(pdMS_TO_TICKS(1000));
        verr = esp_hosted_get_coprocessor_fwversion(&ver);
    }
    printf("MEAS {\"probe\":\"c6.fwversion\",\"rc\":\"%s\",\"major1\":%" PRIu32
           ",\"minor1\":%" PRIu32 ",\"patch1\":%" PRIu32 ",\"revision\":%" PRId32
           ",\"prerelease\":%" PRId32 ",\"build\":%" PRId32 "}\n",
           esp_err_to_name(verr), ver.major1, ver.minor1, ver.patch1,
           ver.revision, ver.prerelease, ver.build);

    esp_hosted_app_desc_t desc;
    memset(&desc, 0, sizeof(desc));
    esp_err_t derr = esp_hosted_get_coprocessor_app_desc(&desc);
    if (derr == ESP_OK) {
        printf("MEAS {\"probe\":\"c6.app_desc\",\"rc\":\"ESP_OK\","
               "\"project\":\"%s\",\"version\":\"%s\",\"idf_ver\":\"%s\","
               "\"date\":\"%s\",\"time\":\"%s\",\"secure_version\":%" PRIu32 ",",
               desc.project_name, desc.version, desc.idf_ver, desc.date,
               desc.time, desc.secure_version);
        print_sha256_hex(desc.app_elf_sha256);
        printf("}\n");
    } else {
        printf("MEAS {\"probe\":\"c6.app_desc\",\"rc\":\"%s\"}\n",
               esp_err_to_name(derr));
    }

    /* 3. Wi-Fi via esp_wifi_remote: STA up, MAC, blocking scan. */
    wifi_init_config_t wcfg = WIFI_INIT_CONFIG_DEFAULT();
    esp_err_t werr = esp_wifi_init(&wcfg);
    printf("MEAS {\"probe\":\"wifi.init\",\"rc\":\"%s\"}\n", esp_err_to_name(werr));
    if (werr == ESP_OK) {
        ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
        esp_err_t serr = esp_wifi_start();
        printf("MEAS {\"probe\":\"wifi.start\",\"rc\":\"%s\"}\n",
               esp_err_to_name(serr));
        if (serr == ESP_OK) {
            uint8_t mac[6] = {0};
            esp_wifi_get_mac(WIFI_IF_STA, mac);
            printf("MEAS {\"probe\":\"c6.sta_mac\",\"mac\":\"%02x:%02x:%02x:%02x:%02x:%02x\"}\n",
                   mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
            uint16_t ap_num = 0;
            esp_err_t cerr = esp_wifi_scan_start(NULL, true);
            if (cerr == ESP_OK) esp_wifi_scan_get_ap_num(&ap_num);
            printf("MEAS {\"probe\":\"wifi.scan\",\"rc\":\"%s\",\"ap_count\":%u}\n",
                   esp_err_to_name(cerr), (unsigned)ap_num);
        }
    }

    /* 4. Hosted ESP-NOW capability: the exact REQ_INIT Device A's working
     * Tactility stack sends. A RESP_INIT means the C6 firmware implements
     * the bridge (esp_now_init() ran slave-side); silence means the factory
     * firmware does not speak it. Read-only either way. */
    s_resp_sem = xSemaphoreCreateBinary();
    esp_err_t rerr = esp_hosted_register_custom_callback(
        ESPNOW_BRIDGE_RESP_INIT, on_bridge_resp_init, NULL);
    printf("MEAS {\"probe\":\"espnow_bridge.register\",\"rc\":\"%s\"}\n",
           esp_err_to_name(rerr));
    if (rerr == ESP_OK) {
        espnow_bridge_req_init_t req;
        memset(&req, 0, sizeof(req));
        req.txn_id = 0xA1CE0001;
        req.channel = 0;  /* current channel; capability probe only */
        req.mode = 0;     /* STATION */
        for (int attempt = 1; attempt <= 2 && !s_resp_got; attempt++) {
            req.txn_id = 0xA1CE0000 + attempt;
            esp_err_t xerr = esp_hosted_send_custom_data(
                ESPNOW_BRIDGE_REQ_INIT, (const uint8_t *)&req, sizeof(req));
            ESP_LOGI(TAG, "REQ_INIT attempt %d send rc=%s", attempt,
                     esp_err_to_name(xerr));
            if (xSemaphoreTake(s_resp_sem, pdMS_TO_TICKS(3000)) == pdTRUE) break;
        }
        printf("MEAS {\"probe\":\"espnow_bridge.req_init\",\"responded\":%s,"
               "\"slave_esp_now_init_err\":%" PRId32 ","
               "\"espnow_version\":%" PRIu32 "}\n",
               s_resp_got == 1 ? "true" : (s_resp_got == 2 ? "\"malformed\"" : "false"),
               s_resp_err, s_resp_version);
        esp_hosted_register_custom_callback(ESPNOW_BRIDGE_RESP_INIT, NULL, NULL);
    }

    /* 5. Option A physical test, bench topology: the bench machine hosts AP
     * "AC-BENCH" (hostapd on its spare AP interface); Device B joins as a
     * hosted STA through the FACTORY C6 firmware, takes DHCP, and serves a
     * TCP framed-echo (u32 BE length + JSON body, the Python FramedJsonCodec
     * byte format) so the client measures real over-the-air RTT through the
     * hosted Wi-Fi path. (Device-B softAP mode was separately proven in the
     * previous probe run: beacon + DHCP + listen all up on 192.168.4.1.) */
    esp_wifi_stop();
    esp_netif_t *sta_netif = esp_netif_create_default_wifi_sta();
    wifi_config_t sta_cfg = {0};
    strcpy((char *)sta_cfg.sta.ssid, "AC-BENCH");
    strcpy((char *)sta_cfg.sta.password, "aethercore15");
    sta_cfg.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &sta_cfg));
    esp_err_t aperr = esp_wifi_start();
    esp_err_t cerr = esp_wifi_connect();
    esp_netif_ip_info_t ip = {0};
    for (int i = 0; i < 20 && ip.ip.addr == 0; i++) {
        vTaskDelay(pdMS_TO_TICKS(500));
        esp_netif_get_ip_info(sta_netif, &ip);
    }
    printf("MEAS {\"probe\":\"sta.connect\",\"start_rc\":\"%s\",\"connect_rc\":\"%s\","
           "\"ip\":\"" IPSTR "\"}\n",
           esp_err_to_name(aperr), esp_err_to_name(cerr), IP2STR(&ip.ip));

    if (ip.ip.addr != 0) {
        xTaskCreate(tcp_echo_task, "tcp_echo", 6144, NULL, 5, NULL);
        printf("MEAS {\"probe\":\"stage2_ready\",\"port\":9000}\n");
    }
    printf("MEAS {\"probe\":\"done\"}\n");
    ESP_LOGI(TAG, "probe complete; idling");
    for (;;) vTaskDelay(pdMS_TO_TICKS(60000));
}

void app_main(void) {
    xTaskCreateStatic(probe_worker, "probe",
                      sizeof(s_worker_stack) / sizeof(StackType_t),
                      NULL, 5, s_worker_stack, &s_worker_tcb);
}
