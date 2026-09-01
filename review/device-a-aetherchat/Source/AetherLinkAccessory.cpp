#include <Tactility/app/aetherchat/AetherLinkAccessory.h>

namespace tt::app::aetherchat {

bool AetherLinkAccessory::start(const Callbacks& nextCallbacks) {
    callbacks = nextCallbacks;
    service::accessorylink::Subscriber subscriber = {
        .onConnected = [this] { if (callbacks.onLinkUp) callbacks.onLinkUp(); },
        .onDisconnected = [this] { if (callbacks.onLinkDown) callbacks.onLinkDown(); },
        .onFrame = [this](const uint8_t* data, size_t length) {
            if (callbacks.onFrame) callbacks.onFrame(reinterpret_cast<const char*>(data), length);
        },
        .onState = [this](service::accessorylink::State, const char* detail) {
            if (callbacks.onState) callbacks.onState(detail);
        },
    };
    return service::accessorylink::subscribe(this, subscriber);
}

void AetherLinkAccessory::stop() {
    service::accessorylink::unsubscribe(this);
    callbacks = {};
}

bool AetherLinkAccessory::isConnected() const {
    return service::accessorylink::connected();
}

bool AetherLinkAccessory::sendFrame(const std::string& json) {
    return service::accessorylink::sendFrame(json);
}

} // namespace tt::app::aetherchat
