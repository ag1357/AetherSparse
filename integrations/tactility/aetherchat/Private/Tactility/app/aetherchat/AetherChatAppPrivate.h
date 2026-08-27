#pragma once

#ifdef ESP_PLATFORM
#include <sdkconfig.h>
#endif

#if defined(CONFIG_SOC_WIFI_SUPPORTED) || defined(CONFIG_SLAVE_SOC_WIFI_SUPPORTED)

#include "AetherChatView.h"
#include "AetherLinkProtocol.h"
#include "AetherLinkTcp.h"

#include <Tactility/app/App.h>

#include <atomic>

namespace tt::app::aetherchat {

class AetherChatApp final : public App {

    AetherChatView view = AetherChatView(this);
    AetherLinkTcp link;

    uint32_t sessionId = 0;         // generated in onCreate, retained across reconnects
    uint32_t nextRequestId = 1;
    int64_t nextJsonSequence = 0;
    bool everConnected = false;     // SESSION_OPEN on first link, SESSION_RESUME after

    // One-in-flight backpressure (Option A spec): no queue, busy + ignore.
    // Written from the LVGL task (send) and the link task (response/link down).
    std::atomic<bool> awaitingResponse{false};
    std::atomic<uint32_t> inFlightRequestId{0};

    // Per-request telemetry (esp_timer microseconds).
    std::atomic<int64_t> tSendUs{0};
    std::atomic<bool> firstResponseSeen{false};

    void onLinkUp();
    void onLinkDown();
    void onFrame(const char* json, size_t length);
    void onLinkState(const char* text);
    void handleMessage(const Message& message);

public:
    void onCreate(AppContext& appContext) override;
    void onDestroy(AppContext& appContext) override;
    void onShow(AppContext& context, lv_obj_t* parent) override;

    // Send a protocol v2 message with the given JSON payload object body
    // (fields only, no braces) as one framed TCP message.
    bool sendMessage(MessageType type, const std::string& payloadObjectJson, bool withRequestId);

    bool isBusy() const { return awaitingResponse.load(); }
    bool isLinkUp() const { return link.isConnected(); }

    bool sendUserText(const std::string& text);
    void sendCancel();
    void sendReset();

    ~AetherChatApp() override = default;
};

} // namespace tt::app::aetherchat

#endif // CONFIG_SOC_WIFI_SUPPORTED || CONFIG_SLAVE_SOC_WIFI_SUPPORTED
