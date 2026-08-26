#include <Tactility/app/aetherchat/AetherLinkProtocol.h>

#include <algorithm>

namespace tt::app::aetherchat {
namespace {

void put16(std::vector<uint8_t>& value, size_t offset, uint16_t item) {
    value[offset] = static_cast<uint8_t>(item & 0xFFU);
    value[offset + 1] = static_cast<uint8_t>((item >> 8U) & 0xFFU);
}

void put32(std::vector<uint8_t>& value, size_t offset, uint32_t item) {
    for (size_t index = 0; index < 4; ++index) {
        value[offset + index] = static_cast<uint8_t>((item >> (8U * index)) & 0xFFU);
    }
}

uint16_t get16(const uint8_t* value, size_t offset) {
    return static_cast<uint16_t>(value[offset]) |
        static_cast<uint16_t>(static_cast<uint16_t>(value[offset + 1]) << 8U);
}

uint32_t get32(const uint8_t* value, size_t offset) {
    uint32_t result = 0;
    for (size_t index = 0; index < 4; ++index) {
        result |= static_cast<uint32_t>(value[offset + index]) << (8U * index);
    }
    return result;
}

bool knownType(uint8_t value) {
    return value >= static_cast<uint8_t>(MessageType::SessionOpen) &&
        value <= static_cast<uint8_t>(MessageType::Capabilities);
}

} // namespace

bool serializeMessage(const Message& message, std::vector<uint8_t>& output) {
    if (message.sessionId == 0 || message.requestId == 0 || message.fragmentCount == 0 ||
        message.fragmentCount > 128 ||
        message.fragmentIndex >= message.fragmentCount ||
        message.payload.size() > AETHERLINK_V1_PAYLOAD_BYTES) {
        return false;
    }
    output.assign(AETHERLINK_HEADER_BYTES + message.payload.size(), 0);
    put32(output, 0, AETHERLINK_MAGIC);
    output[4] = AETHERLINK_VERSION;
    output[5] = static_cast<uint8_t>(message.type);
    output[6] = message.final ? 1U : 0U;
    output[7] = 0;
    put16(output, 8, static_cast<uint16_t>(message.payload.size()));
    put32(output, 10, message.sessionId);
    put32(output, 14, message.requestId);
    put16(output, 18, message.sequence);
    // Fragment metadata replaces the reserved/flags bytes for fragmented packets.
    output[7] = message.fragmentIndex;
    output[6] |= static_cast<uint8_t>((message.fragmentCount - 1U) << 1U);
    std::copy(message.payload.begin(), message.payload.end(), output.begin() + AETHERLINK_HEADER_BYTES);
    return true;
}

bool deserializeMessage(const uint8_t* data, size_t size, Message& output) {
    if (data == nullptr || size < AETHERLINK_HEADER_BYTES ||
        size > AETHERLINK_V1_PACKET_BYTES || get32(data, 0) != AETHERLINK_MAGIC ||
        data[4] != AETHERLINK_VERSION || !knownType(data[5])) {
        return false;
    }
    const auto payloadBytes = get16(data, 8);
    const auto fragmentCount = static_cast<uint8_t>((data[6] >> 1U) + 1U);
    const auto fragmentIndex = data[7];
    if (payloadBytes != size - AETHERLINK_HEADER_BYTES || get32(data, 10) == 0 ||
        get32(data, 14) == 0 || fragmentIndex >= fragmentCount) {
        return false;
    }
    output.type = static_cast<MessageType>(data[5]);
    output.final = (data[6] & 1U) != 0;
    output.sessionId = get32(data, 10);
    output.requestId = get32(data, 14);
    output.sequence = get16(data, 18);
    output.fragmentIndex = fragmentIndex;
    output.fragmentCount = fragmentCount;
    output.payload.assign(
        reinterpret_cast<const char*>(data + AETHERLINK_HEADER_BYTES), payloadBytes
    );
    return true;
}

} // namespace tt::app::aetherchat
