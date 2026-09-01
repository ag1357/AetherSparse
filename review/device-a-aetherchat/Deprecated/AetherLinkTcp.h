// DEPRECATED_TRANSPORT: archived outside production includes; not production.
//
// Tactility's WebServer service remains the sole owner of the ESP32-C6 radio,
// AP netif, DHCP service and 192.168.4.1 address. AetherChat only listens on a
// separate TCP port on that existing netif. Device B joins the Tactility AP
// and initiates the full-duplex connection.
//
// Wire format is unchanged: u32 big-endian length followed by one bounded
// protocol-v2 JSON envelope. A disconnect discards any partial frame.
#pragma once

#ifdef ESP_PLATFORM
#include <sdkconfig.h>
#endif

#if defined(CONFIG_SOC_WIFI_SUPPORTED) || defined(CONFIG_SLAVE_SOC_WIFI_SUPPORTED)

#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include <freertos/task.h>

#include <cstddef>
#include <cstdint>
#include <functional>
#include <string>

namespace tt::app::aetherchat {

class AetherLinkTcp {

public:

    static constexpr uint16_t LISTEN_PORT = 9000;
    static constexpr size_t MAX_FRAME_BYTES = 16384;

    struct Callbacks {
        /** Device B connected. Send SESSION_OPEN/RESUME from this callback. */
        std::function<void()> onLinkUp;
        /** The accepted Device-B connection closed or failed. */
        std::function<void()> onLinkDown;
        /** One complete protocol-v2 JSON body; bytes live through the call. */
        std::function<void(const char* json, size_t length)> onFrame;
        /** Bounded human-readable transport state for the UI. */
        std::function<void(const char* text)> onState;
    };

private:

    static constexpr uint32_t RETRY_MS = 1000;
    static constexpr uint32_t IO_TICK_MS = 500;

    Callbacks callbacks;
    TaskHandle_t task = nullptr;
    SemaphoreHandle_t sendMutex = nullptr;
    SemaphoreHandle_t doneSemaphore = nullptr;

    int listenerFd = -1;
    int socketFd = -1;
    volatile bool stopping = false;

    uint8_t* rxBuffer = nullptr;
    size_t rxUsed = 0;

    static void taskEntry(void* context);
    void taskMain();

    void setState(const char* text) const;
    bool isTactilityApReady() const;
    int openListener() const;
    int acceptClient(int fd) const;
    void readLoop(int fd);
    bool stopRequested(uint32_t delayMs) const;
    void wakeSockets();

public:

    bool start(const Callbacks& callbacksValue);
    void stop();
    bool isConnected() const;

    /** Send one framed body. Thread-safe; exactly one request may be in flight. */
    bool sendFrame(const uint8_t* data, size_t length);
    bool sendFrame(const std::string& json) {
        return sendFrame(reinterpret_cast<const uint8_t*>(json.data()), json.size());
    }
};

} // namespace tt::app::aetherchat

#endif // CONFIG_SOC_WIFI_SUPPORTED || CONFIG_SLAVE_SOC_WIFI_SUPPORTED
