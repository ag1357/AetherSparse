#ifdef ESP_PLATFORM
#include <sdkconfig.h>
#endif

#if defined(CONFIG_SOC_WIFI_SUPPORTED) || defined(CONFIG_SLAVE_SOC_WIFI_SUPPORTED)

#include <Tactility/app/aetherchat/AetherChatAppPrivate.h>

#include <Tactility/lvgl/Toolbar.h>

#include <lvgl/devices/keyboard.h>
#include <lvgl/widgets/toolbar.h>

#include <cstring>
#include <utility>

namespace tt::app::aetherchat {

void AetherChatView::append(const std::string& text, bool own) {
    if (messageList == nullptr) {
        return;
    }
    auto* label = lv_label_create(messageList);
    lv_label_set_text(label, text.c_str());
    lv_obj_set_width(label, lv_pct(100));
    lv_label_set_long_mode(label, LV_LABEL_LONG_WRAP);
    if (own && lv_display_get_color_format(lv_obj_get_display(label)) != LV_COLOR_FORMAT_L8) {
        lv_obj_set_style_text_color(label, lv_color_hex(0x80C0FF), 0);
    }
    lv_obj_scroll_to_y(messageList, LV_COORD_MAX, LV_ANIM_ON);
}

void AetherChatView::setStatus(const std::string& text) {
    if (status != nullptr) {
        lv_label_set_text(status, text.c_str());
    }
}

void AetherChatView::applyKeyboardMode() {
    auto* keyboard = lvgl_software_keyboard_get_last();
    if (keyboard == nullptr || keyboard->object == nullptr) {
        return;
    }
    if (keyboardMode == KeyboardMode::Hide ||
        (keyboardMode == KeyboardMode::Auto && lvgl_hardware_keyboard_is_available())) {
        lvgl_software_keyboard_hide(keyboard);
    } else if (keyboardMode == KeyboardMode::Show) {
        lvgl_software_keyboard_show(keyboard, input);
    }
}

void AetherChatView::onInputFocus(lv_event_t* event) {
    static_cast<AetherChatView*>(lv_event_get_user_data(event))->applyKeyboardMode();
}

void AetherChatView::onSend(lv_event_t* event) {
    auto* self = static_cast<AetherChatView*>(lv_event_get_user_data(event));
    const char* text = lv_textarea_get_text(self->input);
    if (text == nullptr || std::strlen(text) == 0) {
        return;
    }
    // One-in-flight backpressure (Option A spec): busy + ignore, no queue.
    if (self->app->isBusy()) {
        self->setStatus("Busy: awaiting response");
        return;
    }
    if (self->app->sendUserText(text)) {
        self->append(std::string("You: ") + text, true);
        lv_textarea_set_text(self->input, "");
        self->setStatus("Working...");
    } else {
        self->setStatus(self->app->isLinkUp() ? "Send failed" : "Not connected");
    }
}

void AetherChatView::onCancel(lv_event_t* event) {
    auto* self = static_cast<AetherChatView*>(lv_event_get_user_data(event));
    self->app->sendCancel();
    self->setStatus("Cancelling...");
}

void AetherChatView::onReset(lv_event_t* event) {
    auto* self = static_cast<AetherChatView*>(lv_event_get_user_data(event));
    self->app->sendReset();
    lv_obj_clean(self->messageList);
    self->setStatus("Reset requested");
}

void AetherChatView::onKeyboardMode(lv_event_t* event) {
    auto* self = static_cast<AetherChatView*>(lv_event_get_user_data(event));
    self->keyboardMode = static_cast<KeyboardMode>(
        (static_cast<uint8_t>(self->keyboardMode) + 1U) % 3U
    );
    const char* labels[] = {"Keyboard: AUTO", "Keyboard: SHOW", "Keyboard: HIDE"};
    self->setStatus(labels[static_cast<uint8_t>(self->keyboardMode)]);
    self->applyKeyboardMode();
}

void AetherChatView::init(AppContext& context, lv_obj_t* parent) {
    lv_obj_set_flex_flow(parent, LV_FLEX_FLOW_COLUMN);

    auto* toolbar = lvgl::toolbar_create(parent, context);
    lvgl_toolbar_add_text_button_action(toolbar, "KB", onKeyboardMode, this);

    messageList = lv_list_create(parent);
    lv_obj_set_flex_grow(messageList, 1);
    lv_obj_set_width(messageList, LV_PCT(100));

    status = lv_label_create(parent);
    lv_label_set_text(status, "Connecting...");

    auto* row = lv_obj_create(parent);
    lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
    lv_obj_set_size(row, LV_PCT(100), LV_SIZE_CONTENT);

    input = lv_textarea_create(row);
    lv_obj_set_flex_grow(input, 1);
    lv_textarea_set_one_line(input, true);
    // Protocol v2 bounds user text at MAX_USER_TEXT_BYTES; the TCP link
    // carries any allowed message in a single frame (no fragmentation).
    lv_textarea_set_max_length(input, MAX_USER_TEXT_BYTES);
    lv_textarea_set_placeholder_text(input, "Ask AetherCore...");
    lv_obj_add_event_cb(input, onInputFocus, LV_EVENT_FOCUSED, this);

    for (const auto& button : {
             std::pair<const char*, lv_event_cb_t>{"Send", onSend},
             {"Cancel", onCancel},
             {"Reset", onReset},
         }) {
        auto* object = lv_button_create(row);
        lv_obj_add_event_cb(object, button.second, LV_EVENT_CLICKED, this);
        auto* label = lv_label_create(object);
        lv_label_set_text(label, button.first);
    }
}

} // namespace tt::app::aetherchat

#endif // CONFIG_SOC_WIFI_SUPPORTED || CONFIG_SLAVE_SOC_WIFI_SUPPORTED
