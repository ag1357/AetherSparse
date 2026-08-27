// AetherLink TCP backend (Option A): minimal framed-JSON TCP client toward
// Device B's softAP, replacing the ESP-NOW/AC20 transport for AetherChat.
// Tactility's ESP-NOW service is untouched (the Chat app keeps using it);
// the AC20 wire shim (protocol_v2_wire.h) is retained in-tree as the ESP-NOW
// artifact but is not used on this path.
//
// Wire format (byte-parity with the Python FramedJsonCodec): each frame is
// u32 big-endian length + that many bytes of compact protocol-v2 envelope
// JSON. No AC20 header, no SLIP. Max frame 16 KiB.
//
// Wi-Fi association to the fixed bench AP is driven through Tactility's WiFi
// service API (see .cpp for the service-interaction notes). Reconnect uses
// bounded 1..5 s backoff. See
// work/v15-p4-deployment/phase-notes/phase-option-a-tcp-spec.md.
#pragma once

#ifdef ESP_PLATFORM
#include <sdkconfig.h>
#endif

#if defined(CONFIG_SOC_WIFI_SUPPORTED) || defined(CONFIG_SLAVE_SOC_WIFI_SUPPORTED)

#include <Tactility/PubSub.h>
#include <Tactility/service/wifi/Wifi.h>

#include <freertos/FreeRTOS.h>
#include <freertos/event_groups.h>
#include <freertos/semphr.h>
#include <freertos/task.h>

#include <cstddef>
#include <cstdint>
#include <functional>
#include <string>

namespace tt::app::aetherchat {

class AetherLinkTcp {

public:

    // Fixed deterministic link parameters (Option A spec; bench network).
    static constexpr const char* LINK_AP_SSID = "AETHERCORE-V15";
    static constexpr const char* LINK_AP_PASSWORD = "aethercore-v15-link";
    static constexpr const char* DEVICE_B_HOST = "192.168.4.1";
    static constexpr uint16_t DEVICE_B_PORT = 9000;
    static constexpr size_t MAX_FRAME_BYTES = 16384;

    struct Callbacks {
        /** TCP connected (Wi-Fi associated). Send SESSION_OPEN/RESUME here.
         * Called from the link task; sendFrame() is safe to call from it. */
        std::function<void()> onLinkUp;
        /** Link dropped (TCP or Wi-Fi). In-flight requests must be aborted. */
        std::function<void()> onLinkDown;
        /** One complete inbound frame (JSON envelope; bytes valid during the call).
         * Called from the link task; lock LVGL before touching the UI. */
        std::function<void(const char* json, size_t length)> onFrame;
        /** Human-readable link state for the UI status line (link task context). */
        std::function<void(const char* text)> onState;
    };

private:

    static constexpr EventBits_t EVENT_GOT_IP = 1U << 0U;
    static constexpr EventBits_t EVENT_STOP = 1U << 1U;
    static constexpr uint32_t BACKOFF_MIN_MS = 1000;
    static constexpr uint32_t BACKOFF_MAX_MS = 5000;

    Callbacks callbacks;
    TaskHandle_t task = nullptr;
    EventGroupHandle_t events = nullptr;
    SemaphoreHandle_t sendMutex = nullptr;
    SemaphoreHandle_t doneSemaphore = nullptr;
    PubSub<service::wifi::WifiEvent>::SubscriptionHandle wifiSubscription = nullptr;

    int socketFd = -1;
    volatile bool stopping = false;

    // What we changed at start() so stop() can restore it.
    bool stoppedWebServer = false;
    bool enabledWifi = false;
    bool pausedAutoScan = false;

    // Frame reassembly buffer (owned by the link task).
    uint8_t* rxBuffer = nullptr;
    size_t rxUsed = 0;

    static void taskEntry(void* context);
    void taskMain();
    void onWifiEvent(service::wifi::WifiEvent event);

    void setState(const char* text) const;
    bool waitForRadio(uint32_t timeoutMs);
    bool ensureAssociated();
    int tcpConnect();
    void readLoop(int fd);
    uint32_t sleepBackoff(uint32_t backoffMs) const;
    bool stopRequested(uint32_t delayMs) const;
    void closeSocket();

public:

    bool start(const Callbacks& callbacksValue);
    void stop();
    bool isConnected() const;

    /** Send one complete frame (u32be length prefix + body). Thread-safe.
     * @return false when not connected or the frame is out of bounds. */
    bool sendFrame(const uint8_t* data, size_t length);
    bool sendFrame(const std::string& json) {
        return sendFrame(reinterpret_cast<const uint8_t*>(json.data()), json.size());
    }
};

} // namespace tt::app::aetherchat

#endif // CONFIG_SOC_WIFI_SUPPORTED || CONFIG_SLAVE_SOC_WIFI_SUPPORTED
