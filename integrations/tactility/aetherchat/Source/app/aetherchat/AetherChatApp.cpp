#ifdef ESP_PLATFORM
#include <sdkconfig.h>
#endif

#if defined(CONFIG_SOC_WIFI_SUPPORTED) || defined(CONFIG_SLAVE_SOC_WIFI_SUPPORTED)

#include <Tactility/app/aetherchat/AetherChatAppPrivate.h>
#include <Tactility/service/espnow/EspNow.h>

#include <app/event.h>
#include <app/manager.h>
#include <app/manifest.h>
#include <app/scheduler.h>
#include <lvgl_window_manager/window_manager.h>
#include <tactility/check.h>
#include <tactility/log.h>

#include <array>

namespace tt::app::aetherchat {
namespace {

constexpr std::array<uint8_t, ESP_NOW_ETH_ALEN> BROADCAST = {
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF
};

void enableTransport() {
    static uint8_t key[ESP_NOW_KEY_LEN] = {};
    service::espnow::enable(service::espnow::EspNowConfig(
        key, service::espnow::Mode::Station, 1, false, false
    ));
}

void receive(Context* context, const uint8_t* data, int length) {
    Message message;
    if (length <= 0 || !deserializeMessage(data, static_cast<size_t>(length), message) ||
        message.sessionId != context->sessionId) {
        return;
    }
    lvgl_lock();
    switch (message.type) {
        case MessageType::AssistantTextDelta:
        case MessageType::ClarificationRequest:
            context->view.append(message.payload);
            context->view.setStatus(message.final ? "Ready" : "Receiving...");
            break;
        case MessageType::TaskStatus:
        case MessageType::ToolActivitySummary:
        case MessageType::MemoryStatus:
        case MessageType::Health:
        case MessageType::Capabilities:
            context->view.setStatus(message.payload);
            break;
        case MessageType::EvidenceSummary:
            context->view.append(std::string("Evidence: ") + message.payload);
            break;
        case MessageType::Error:
            context->view.append(std::string("Error: ") + message.payload);
            context->view.setStatus("Error");
            break;
        default:
            break;
    }
    lvgl_unlock();
}

void createWidgets(lv_obj_t* parent, void* userData) {
    static_cast<Context*>(userData)->view.init(parent);
}

int32_t appMain(int, char*[]) {
    Context context;
    context.appInstanceId = app_scheduler_current_app_id();
    context.sessionId = context.appInstanceId == 0 ? 1 : context.appInstanceId;
    enableTransport();

    TaskEventGroup eventGroup {};
    task_event_group_construct(&eventGroup);
    AppEventSubscription subscription {};
    check(app_event_subscribe(&subscription, &eventGroup) == ERROR_NONE);
    WindowId window = window_manager_create(context.appInstanceId, createWidgets, &context);
    auto receiver = service::espnow::subscribeReceiver(
        [&context](const esp_now_recv_info_t*, const uint8_t* data, int length) {
            receive(&context, data, length);
        }
    );
    send(&context, MessageType::SessionOpen, "tactility-0.8.0-dev");

    bool close = false;
    while (!close) {
        task_event_group_wait_any(&eventGroup, nullptr, portMAX_DELAY);
        AppEvent event {};
        while (app_event_poll(&subscription, &event) == ERROR_NONE) {
            close = event.type == APP_EVENT_CLOSE;
            if (close) break;
        }
    }
    service::espnow::unsubscribeReceiver(receiver);
    service::espnow::disable();
    window_manager_remove(window);
    check(app_event_unsubscribe(&subscription) == ERROR_NONE);
    task_event_group_destruct(&eventGroup);
    return 0;
}

} // namespace

bool send(Context* context, MessageType type, const std::string& payload, bool final) {
    Message message;
    message.type = type;
    message.final = final;
    message.sessionId = context->sessionId;
    message.requestId = context->nextRequestId++;
    message.sequence = context->sequence++;
    message.payload = payload;
    std::vector<uint8_t> wire;
    return serializeMessage(message, wire) &&
        service::espnow::send(BROADCAST.data(), wire.data(), wire.size());
}

extern const ::AppManifest manifest = {
    .id = "tactility.aetherchat",
    .name = "AetherChat",
    .category = APP_CATEGORY_USER,
    .location = {APP_LOCATION_MEMORY, reinterpret_cast<void*>(appMain)}
};

} // namespace tt::app::aetherchat

#endif
