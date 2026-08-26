#pragma once

#include <Tactility/app/aetherchat/AetherChatView.h>

#include <cstdint>
#include <string>

namespace tt::app::aetherchat {

struct Context {
    uint32_t appInstanceId = 0;
    uint32_t sessionId = 0;
    uint32_t nextRequestId = 1;
    uint16_t sequence = 0;
    AetherChatView view = AetherChatView(this);
};

bool send(Context* context, MessageType type, const std::string& payload, bool final = true);

} // namespace tt::app::aetherchat
