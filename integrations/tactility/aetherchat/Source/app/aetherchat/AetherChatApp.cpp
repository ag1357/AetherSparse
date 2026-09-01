// AetherChat: Device A front-end for AetherCore V15.
//
// User types here (Tactility on the Waveshare ESP32-P4-WIFI6-Touch-LCD-3.5);
// Tactility owns the AccessoryLink/USB-host service and hardware lifetime.
// AetherChat consumes framed protocol-v2 messages only; cognition, Pack-v2,
// memory and knowledge remain on the removable Device-B compute accessory.
//
// See work/v15-p4-deployment/phase-notes/phase-option-a-tcp-spec.md.
#ifdef ESP_PLATFORM
#include <sdkconfig.h>
#endif

#include <Tactility/app/aetherchat/AetherChatAppPrivate.h>

#include <Tactility/app/AppManifest.h>

#include <esp_random.h>
#include <esp_timer.h>
#include <lvgl/lvgl.h>
#include <tactility/log.h>

#include <cstdlib>

namespace tt::app::aetherchat {

constexpr auto* TAG = "AetherChat";

constexpr auto* CLIENT_VERSION_PAYLOAD = "\"client_version\":\"tactility-0.8.0-dev\"";

void AetherChatApp::onCreate(AppContext& appContext) {
    sessionId = esp_random();
    if (sessionId == 0) {
        sessionId = 1;
    }

    AetherLinkAccessory::Callbacks callbacks = {
        .onLinkUp = [this] { onLinkUp(); },
        .onLinkDown = [this] { onLinkDown(); },
        .onFrame = [this](const char* json, size_t length) { onFrame(json, length); },
        .onState = [this](const char* text) { onLinkState(text); },
    };
    if (!link.start(callbacks)) {
        LOG_E(TAG, "Failed to subscribe to Tactility AccessoryLink service");
    }
}

void AetherChatApp::onDestroy(AppContext& appContext) {
    link.stop();
}

void AetherChatApp::onShow(AppContext& context, lv_obj_t* parent) {
    view.init(context, parent);
}

void AetherChatApp::onLinkUp() {
    // A dead connection can leave a request stranded; the new link starts clean.
    awaitingResponse = false;
    inFlightRequestId = 0;

    // Device B session state is session_id-keyed and survives disconnects:
    // SESSION_OPEN once per app lifetime, SESSION_RESUME on every later link.
    // Both payloads require client_version (SessionResumePayload in protocol.py).
    const MessageType type = everConnected ? MessageType::SessionResume : MessageType::SessionOpen;
    if (sendMessage(type, CLIENT_VERSION_PAYLOAD, /*withRequestId=*/false)) {
        everConnected = true;
    }
}

void AetherChatApp::onLinkDown() {
    awaitingResponse = false;
    inFlightRequestId = 0;
    lvgl_lock();
    view.setStatus("Link: down");
    lvgl_unlock();
}

void AetherChatApp::onLinkState(const char* text) {
    lvgl_lock();
    view.setStatus(text);
    lvgl_unlock();
}

bool AetherChatApp::sendMessage(MessageType type, const std::string& payloadObjectJson, bool withRequestId) {
    if (!link.isConnected()) {
        return false;
    }

    const uint32_t requestId = withRequestId ? nextRequestId++ : 0;
    const std::string json = buildEnvelopeJson(
        type, sessionId, requestId, nextJsonSequence++, payloadObjectJson
    );

    if (withRequestId) {
        // Arm before sending so a fast response can never race the busy flag.
        inFlightRequestId = requestId;
        firstResponseSeen = false;
        tSendUs = esp_timer_get_time();
        awaitingResponse = true;
        LOG_I(TAG, "MEAS {\"link\":\"req\",\"id\":%lu,\"t_send_us\":%lld}",
            (unsigned long)requestId, (long long)tSendUs.load());
    }

    if (!link.sendFrame(json)) {
        if (withRequestId) {
            awaitingResponse = false;
            inFlightRequestId = 0;
        }
        return false;
    }
    return true;
}

bool AetherChatApp::sendUserText(const std::string& text) {
    if (text.empty() || text.size() > MAX_USER_TEXT_BYTES) {
        return false;
    }
    if (awaitingResponse.load()) {
        return false; // one-in-flight backpressure: busy + ignore, no queue
    }
    return sendMessage(
        MessageType::UserText,
        std::string("\"text\":\"") + jsonEscape(text) + "\"",
        /*withRequestId=*/true
    );
}

void AetherChatApp::sendCancel() {
    // USER_CANCEL is an ordinary serial query on Device B (Python parity), so
    // it obeys the same one-in-flight rule.
    if (awaitingResponse.load()) {
        lvgl_lock();
        view.setStatus("Busy: awaiting response");
        lvgl_unlock();
        return;
    }
    sendMessage(MessageType::UserCancel, "\"reason\":\"user\"", /*withRequestId=*/true);
}

void AetherChatApp::sendReset() {
    if (awaitingResponse.load()) {
        lvgl_lock();
        view.setStatus("Busy: awaiting response");
        lvgl_unlock();
        return;
    }
    sendMessage(MessageType::Reset, "\"reason\":\"user\"", /*withRequestId=*/true);
    // Restart protocol state for the next conversation.
    nextRequestId = 1;
    nextJsonSequence = 0;
}

void AetherChatApp::onFrame(const char* json, size_t length) {
    Message message;
    message.payload.assign(json, length);

    std::string typeName;
    if (!jsonExtractString(message.payload, "type", typeName) ||
        !messageTypeFromJsonName(typeName, message.type)) {
        LOG_W(TAG, "Frame with unknown/missing type dropped");
        return;
    }
    std::string idString;
    if (jsonExtractString(message.payload, "session_id", idString)) {
        message.sessionId = std::strtoul(idString.c_str(), nullptr, 10);
    }
    if (jsonExtractString(message.payload, "request_id", idString)) {
        // Absent/null or non-numeric (session-scoped) parses as 0.
        message.requestId = std::strtoul(idString.c_str(), nullptr, 10);
    }
    handleMessage(message);
}

void AetherChatApp::handleMessage(const Message& message) {
    if (message.sessionId != sessionId) {
        return; // not our session (e.g. stale traffic from a previous boot)
    }

    // Telemetry + one-in-flight release. Terminal responses per the Option A
    // spec are ASSISTANT_TEXT_DELTA final=true and CLARIFICATION_REQUEST;
    // MEMORY_STATUS terminates memory-intercepted requests and ERROR
    // terminates failures (Device B emits no delta for those).
    if (inFlightRequestId.load() != 0 && message.requestId == inFlightRequestId.load()) {
        const int64_t now = esp_timer_get_time();
        if (!firstResponseSeen.exchange(true)) {
            LOG_I(TAG, "MEAS {\"link\":\"first_response\",\"id\":%lu,\"dt_us\":%lld}",
                (unsigned long)message.requestId, (long long)(now - tSendUs.load()));
        }
        bool final = false;
        if (message.type == MessageType::AssistantTextDelta) {
            jsonExtractBool(message.payload, "final", final);
        }
        const bool terminal =
            (message.type == MessageType::AssistantTextDelta && final) ||
            message.type == MessageType::ClarificationRequest ||
            message.type == MessageType::MemoryStatus ||
            message.type == MessageType::Error;
        if (terminal) {
            LOG_I(TAG, "MEAS {\"link\":\"final\",\"id\":%lu,\"e2e_us\":%lld}",
                (unsigned long)message.requestId, (long long)(now - tSendUs.load()));
            awaitingResponse = false;
            inFlightRequestId = 0;
        }
    }

    std::string text;
    bool final = false;

    lvgl_lock();
    switch (message.type) {
        case MessageType::AssistantTextDelta:
            if (jsonExtractString(message.payload, "text", text)) {
                view.append(text);
            }
            jsonExtractBool(message.payload, "final", final);
            view.setStatus(final ? "Ready" : "Receiving...");
            break;
        case MessageType::ClarificationRequest:
            if (jsonExtractString(message.payload, "question", text)) {
                view.append(std::string("Clarification: ") + text);
            }
            view.setStatus("Needs input");
            break;
        case MessageType::EvidenceSummary:
            if (jsonExtractString(message.payload, "summary", text)) {
                view.append(std::string("Evidence: ") + text);
            }
            break;
        case MessageType::MemoryStatus:
            if (jsonExtractString(message.payload, "detail", text) && !text.empty()) {
                view.append(std::string("Memory: ") + text);
            } else if (jsonExtractString(message.payload, "operation", text)) {
                view.setStatus(std::string("Memory: ") + text);
            }
            break;
        case MessageType::TaskStatus:
            if (jsonExtractString(message.payload, "status", text)) {
                view.setStatus(text);
            }
            break;
        case MessageType::ToolActivitySummary:
            if (jsonExtractString(message.payload, "summary", text)) {
                view.setStatus(text);
            }
            break;
        case MessageType::Health:
            if (jsonExtractString(message.payload, "status", text)) {
                std::string version;
                if (jsonExtractString(message.payload, "runtime_version", version)) {
                    text += " (" + version + ")";
                }
                view.setStatus(std::string("Health: ") + text);
            }
            break;
        case MessageType::Capabilities:
            if (jsonExtractString(message.payload, "hardware_class", text)) {
                view.setStatus(std::string("Peer: ") + text);
            }
            break;
        case MessageType::Error: {
            std::string detail;
            if (!jsonExtractString(message.payload, "message", detail)) {
                detail = "unknown";
            }
            view.append(std::string("Error: ") + detail);
            view.setStatus("Error");
            break;
        }
        default:
            break;
    }
    lvgl_unlock();
}

extern const AppManifest manifest = {
    .appId = "AetherChat",
    .appName = "AetherChat",
    .appCategory = Category::User,
    .createApp = create<AetherChatApp>
};

} // namespace tt::app::aetherchat
