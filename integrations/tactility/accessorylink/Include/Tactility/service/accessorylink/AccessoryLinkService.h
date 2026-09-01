// Tactility-owned, transport-independent removable-accessory service.
#pragma once

#include <cstddef>
#include <cstdint>
#include <functional>
#include <string>

namespace tt::service::accessorylink {

constexpr size_t MAX_FRAME_BYTES = 16u * 1024u;

enum class State {
    Unplugged,
    Discovering,
    Negotiating,
    Ready,
    Rejected,
    Error,
};

struct Capabilities {
    const char* transport;
    uint32_t flags;
};

struct Subscriber {
    std::function<void()> onConnected;
    std::function<void()> onDisconnected;
    std::function<void(const uint8_t*, size_t)> onFrame;
    std::function<void(State, const char*)> onState;
};

/* Platform backend contract. The platform USB-host driver owns enumeration,
 * class handles and hardware lifetime. This service owns framing, accessory
 * negotiation, single-subscriber dispatch and hotplug semantics. */
struct Backend {
    void* context;
    bool (*open)(void*);
    void (*close)(void*);
    int (*read)(void*, uint8_t*, size_t, uint32_t timeoutMs);
    int (*write)(void*, const uint8_t*, size_t, uint32_t timeoutMs);
    bool (*connected)(void*);
    Capabilities (*capabilities)(void*);
    void (*cancel)(void*);
};

bool registerPlatformBackend(const Backend& backend);
void unregisterPlatformBackend(void* context);
bool subscribe(void* owner, const Subscriber& subscriber);
void unsubscribe(void* owner);
bool sendFrame(const std::string& json);
bool connected();
State state();
void poll(uint32_t timeoutMs);

} // namespace tt::service::accessorylink
