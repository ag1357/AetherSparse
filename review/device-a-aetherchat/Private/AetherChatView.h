#pragma once

#ifdef ESP_PLATFORM
#include <sdkconfig.h>
#endif

#if defined(CONFIG_SOC_WIFI_SUPPORTED) || defined(CONFIG_SLAVE_SOC_WIFI_SUPPORTED)

#include <Tactility/app/AppContext.h>

#include <lvgl.h>

#include <string>

namespace tt::app::aetherchat {

class AetherChatApp;

enum class KeyboardMode : uint8_t { Auto, Show, Hide };

class AetherChatView {

    AetherChatApp* app;

    lv_obj_t* messageList = nullptr;
    lv_obj_t* input = nullptr;
    lv_obj_t* status = nullptr;
    KeyboardMode keyboardMode = KeyboardMode::Auto;

    static void onSend(lv_event_t* event);
    static void onCancel(lv_event_t* event);
    static void onReset(lv_event_t* event);
    static void onKeyboardMode(lv_event_t* event);
    static void onInputFocus(lv_event_t* event);

public:
    explicit AetherChatView(AetherChatApp* appValue) : app(appValue) {}
    void init(AppContext& context, lv_obj_t* parent);
    void append(const std::string& text, bool own = false);
    void setStatus(const std::string& text);
    void applyKeyboardMode();
};

} // namespace tt::app::aetherchat

#endif // CONFIG_SOC_WIFI_SUPPORTED || CONFIG_SLAVE_SOC_WIFI_SUPPORTED
