#include <Tactility/service/accessorylink/AccessoryLinkService.h>

#include <algorithm>
#include <array>
#include <cstring>
#include <string_view>

namespace tt::service::accessorylink {
namespace {

Backend gBackend{};
Subscriber gSubscriber{};
void* gOwner = nullptr;
State gState = State::Unplugged;
std::array<uint8_t, 4> gHeader{};
size_t gHeaderSize = 0;
std::array<uint8_t, MAX_FRAME_BYTES> gBody{};
size_t gBodySize = 0;
size_t gBodyReceived = 0;
uint32_t gNegotiationRemainingMs = 0;
constexpr uint32_t NEGOTIATION_TIMEOUT_MS = 2500;

bool backendValid(const Backend& b) {
    return b.open && b.close && b.read && b.write && b.connected &&
        b.capabilities && b.cancel;
}

void resetFrame() {
    gHeaderSize = 0;
    gBodySize = 0;
    gBodyReceived = 0;
}

void setState(State state, const char* detail) {
    gState = state;
    if (gSubscriber.onState) gSubscriber.onState(state, detail);
}

void rejectCandidate(const char* detail) {
    resetFrame();
    gBackend.cancel(gBackend.context);
    gBackend.close(gBackend.context);
    setState(State::Rejected, detail);
    if (gSubscriber.onDisconnected) gSubscriber.onDisconnected();
}

bool writeAll(const uint8_t* data, size_t length, uint32_t timeoutMs) {
    size_t offset = 0;
    while (offset < length) {
        const int sent = gBackend.write(gBackend.context, data + offset,
            length - offset, timeoutMs);
        if (sent <= 0) return false;
        offset += static_cast<size_t>(sent);
    }
    return true;
}

void ingest(const uint8_t* data, size_t length) {
    while (length) {
        if (gHeaderSize < gHeader.size()) {
            const size_t take = std::min(length, gHeader.size() - gHeaderSize);
            std::memcpy(gHeader.data() + gHeaderSize, data, take);
            gHeaderSize += take;
            data += take;
            length -= take;
            if (gHeaderSize != gHeader.size()) continue;
            gBodySize = (static_cast<size_t>(gHeader[0]) << 24) |
                (static_cast<size_t>(gHeader[1]) << 16) |
                (static_cast<size_t>(gHeader[2]) << 8) | gHeader[3];
            if (gBodySize == 0 || gBodySize > MAX_FRAME_BYTES) {
                rejectCandidate("Malformed AetherLink frame");
                return;
            }
        }
        const size_t take = std::min(length, gBodySize - gBodyReceived);
        std::memcpy(gBody.data() + gBodyReceived, data, take);
        gBodyReceived += take;
        data += take;
        length -= take;
        if (gBodyReceived == gBodySize) {
            const std::string_view body(reinterpret_cast<const char*>(gBody.data()), gBodySize);
            if (gState == State::Negotiating &&
                body.find("\"protocol_version\":\"aethercore-tactility.v2\"") != std::string_view::npos &&
                body.find("\"type\":\"CAPABILITIES\"") != std::string_view::npos) {
                setState(State::Ready, "AetherCore accessory ready");
            }
            if (gSubscriber.onFrame) gSubscriber.onFrame(gBody.data(), gBodySize);
            resetFrame();
        }
    }
}

} // namespace

bool registerPlatformBackend(const Backend& backend) {
    if (!backendValid(backend) || backendValid(gBackend)) return false;
    gBackend = backend;
    setState(State::Discovering, "Accessory service available");
    return gBackend.open(gBackend.context);
}

void unregisterPlatformBackend(void* context) {
    if (!backendValid(gBackend) || gBackend.context != context) return;
    gBackend.cancel(gBackend.context);
    gBackend.close(gBackend.context);
    gBackend = {};
    resetFrame();
    setState(State::Unplugged, "AetherCore accessory unavailable");
    if (gSubscriber.onDisconnected) gSubscriber.onDisconnected();
}

bool subscribe(void* owner, const Subscriber& subscriber) {
    if (!owner || gOwner) return false;
    gOwner = owner;
    gSubscriber = subscriber;
    if (!backendValid(gBackend)) setState(State::Unplugged, "AetherCore accessory unavailable");
    return true;
}

void unsubscribe(void* owner) {
    if (owner != gOwner) return;
    gOwner = nullptr;
    gSubscriber = {};
}

bool sendFrame(const std::string& json) {
    if (!backendValid(gBackend) || !gBackend.connected(gBackend.context) ||
        json.empty() || json.size() > MAX_FRAME_BYTES) return false;
    const size_t n = json.size();
    const uint8_t header[4] = {static_cast<uint8_t>(n >> 24),
        static_cast<uint8_t>(n >> 16), static_cast<uint8_t>(n >> 8),
        static_cast<uint8_t>(n)};
    return writeAll(header, sizeof(header), 1000) &&
        writeAll(reinterpret_cast<const uint8_t*>(json.data()), n, 1000);
}

bool connected() {
    return backendValid(gBackend) && gBackend.connected(gBackend.context);
}

State state() { return gState; }

void poll(uint32_t timeoutMs) {
    if (!backendValid(gBackend)) return;
    if (!gBackend.connected(gBackend.context)) {
        if (gState != State::Unplugged) {
            resetFrame();
            setState(State::Unplugged, "AetherCore accessory unplugged");
            if (gSubscriber.onDisconnected) gSubscriber.onDisconnected();
        }
        return;
    }
    if (gState == State::Discovering || gState == State::Unplugged) {
        /* Strings/VID/PID are discovery hints. AetherChat's immediate session
         * negotiation and strict HEALTH/CAPABILITIES decode are authoritative. */
        setState(State::Negotiating, "Negotiating AetherLink v2");
        gNegotiationRemainingMs = NEGOTIATION_TIMEOUT_MS;
        if (gSubscriber.onConnected) gSubscriber.onConnected();
    }
    std::array<uint8_t, 1024> bytes{};
    const int count = gBackend.read(gBackend.context, bytes.data(), bytes.size(), timeoutMs);
    if (count > 0) ingest(bytes.data(), static_cast<size_t>(count));
    if (gState == State::Negotiating && timeoutMs > 0) {
        if (timeoutMs >= gNegotiationRemainingMs) {
            rejectCandidate("No valid AetherLink v2 CAPABILITIES response");
        } else {
            gNegotiationRemainingMs -= timeoutMs;
        }
    }
}

} // namespace tt::service::accessorylink
