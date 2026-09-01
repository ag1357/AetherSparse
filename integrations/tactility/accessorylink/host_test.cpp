#include <Tactility/service/accessorylink/AccessoryLinkService.h>

#include <cassert>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

using namespace tt::service::accessorylink;

struct Fake {
    bool up = true;
    std::vector<uint8_t> rx;
    std::vector<uint8_t> tx;
};

static bool open(void*) { return true; }
static void close(void*) {}
static int read(void* context, uint8_t* out, size_t cap, uint32_t) {
    auto& f = *static_cast<Fake*>(context);
    const size_t count = f.rx.size() < cap ? f.rx.size() : cap;
    if (count == 0) return 0;
    std::memcpy(out, f.rx.data(), count);
    f.rx.erase(f.rx.begin(), f.rx.begin() + count);
    return static_cast<int>(count);
}
static int write(void* context, const uint8_t* data, size_t len, uint32_t) {
    auto& out = static_cast<Fake*>(context)->tx;
    out.insert(out.end(), data, data + len);
    return static_cast<int>(len);
}
static bool connected(void* context) { return static_cast<Fake*>(context)->up; }
static Capabilities capabilities(void*) { return {"FAKE", 0}; }
static void cancel(void*) {}

static std::vector<uint8_t> frame(const std::string& body) {
    const size_t n = body.size();
    std::vector<uint8_t> result = {static_cast<uint8_t>(n >> 24),
        static_cast<uint8_t>(n >> 16), static_cast<uint8_t>(n >> 8),
        static_cast<uint8_t>(n)};
    result.insert(result.end(), body.begin(), body.end());
    return result;
}

int main() {
    Fake fake;
    Backend backend = {&fake, open, close, read, write, connected, capabilities, cancel};
    int up = 0, down = 0, frames = 0;
    int owner = 1;
    Subscriber subscriber = {
        .onConnected = [&] { up++; },
        .onDisconnected = [&] { down++; },
        .onFrame = [&](const uint8_t*, size_t) { frames++; },
        .onState = [](State, const char*) {},
    };
    assert(subscribe(&owner, subscriber));
    assert(registerPlatformBackend(backend));
    poll(0);
    assert(up == 1 && state() == State::Negotiating);

    // Fragmented CAPABILITIES promotes a CDC candidate only after v2 proof.
    const std::string caps =
        "{\"protocol_version\":\"aethercore-tactility.v2\","
        "\"type\":\"CAPABILITIES\",\"payload\":{}}";
    const auto bytes = frame(caps);
    fake.rx.insert(fake.rx.end(), bytes.begin(), bytes.begin() + 2);
    poll(0);
    assert(frames == 0);
    fake.rx.insert(fake.rx.end(), bytes.begin() + 2, bytes.end());
    poll(0);
    assert(frames == 1 && state() == State::Ready);

    assert(sendFrame("{\"type\":\"HEALTH\"}"));
    assert(fake.tx.size() == 4 + std::strlen("{\"type\":\"HEALTH\"}"));

    // Unplug drops partial state; replug negotiates a fresh stream.
    const auto partial = frame("{\"type\":\"ERROR\"}");
    fake.rx.insert(fake.rx.end(), partial.begin(), partial.begin() + 7);
    poll(0);
    fake.up = false;
    poll(0);
    assert(down == 1 && state() == State::Unplugged);
    fake.rx.clear();
    fake.up = true;
    poll(0);
    assert(up == 2 && state() == State::Negotiating);

    unregisterPlatformBackend(&fake);
    unsubscribe(&owner);

    // An ordinary serial adapter that never proves protocol-v2 authority is
    // bounded and rejected rather than treated as an AetherCore accessory.
    Fake unrelated;
    int unrelatedOwner = 2;
    assert(subscribe(&unrelatedOwner, subscriber));
    assert(registerPlatformBackend({&unrelated, open, close, read, write,
        connected, capabilities, cancel}));
    poll(1000);
    poll(1000);
    poll(500);
    assert(state() == State::Rejected);
    unregisterPlatformBackend(&unrelated);
    unsubscribe(&unrelatedOwner);
    return 0;
}
