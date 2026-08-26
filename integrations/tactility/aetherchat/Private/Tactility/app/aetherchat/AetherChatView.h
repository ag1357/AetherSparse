#pragma once

#include <Tactility/app/aetherchat/AetherLinkProtocol.h>

#include <lvgl.h>

#include <string>

namespace tt::app::aetherchat {

struct Context;

enum class KeyboardMode : uint8_t { Auto, Show, Hide };

class AetherChatView {
    Context* context;
    lv_obj_t* messageList = nullptr;
    lv_obj_t* input = nullptr;
    lv_obj_t* status = nullptr;
    KeyboardMode keyboardMode = KeyboardMode::Auto;

    static void onSend(lv_event_t* event);
    static void onCancel(lv_event_t* event);
    static void onReset(lv_event_t* event);
    static void onKeyboardMode(lv_event_t* event);
    static void onInputFocus(lv_event_t* event);
    static void onClose(lv_event_t* event);

public:
    explicit AetherChatView(Context* contextValue) : context(contextValue) {}
    void init(lv_obj_t* parent);
    void append(const std::string& text, bool own = false);
    void setStatus(const std::string& text);
    void applyKeyboardMode();
};

} // namespace tt::app::aetherchat
