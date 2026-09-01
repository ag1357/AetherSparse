#pragma once

#include <Tactility/service/accessorylink/AccessoryLinkService.h>

#include <cstddef>
#include <functional>
#include <string>

namespace tt::app::aetherchat {

class AetherLinkAccessory {
public:
    struct Callbacks {
        std::function<void()> onLinkUp;
        std::function<void()> onLinkDown;
        std::function<void(const char*, size_t)> onFrame;
        std::function<void(const char*)> onState;
    };

    bool start(const Callbacks& callbacks);
    void stop();
    bool isConnected() const;
    bool sendFrame(const std::string& json);

private:
    Callbacks callbacks{};
};

} // namespace tt::app::aetherchat
