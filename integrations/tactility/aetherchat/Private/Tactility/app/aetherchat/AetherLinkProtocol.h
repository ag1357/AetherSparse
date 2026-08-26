#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace tt::app::aetherchat {

constexpr uint32_t AETHERLINK_MAGIC = 0x41455432; // "AET2"
constexpr uint8_t AETHERLINK_VERSION = 2;
constexpr size_t AETHERLINK_HEADER_BYTES = 20;
constexpr size_t AETHERLINK_V1_PACKET_BYTES = 250;
constexpr size_t AETHERLINK_V1_PAYLOAD_BYTES =
    AETHERLINK_V1_PACKET_BYTES - AETHERLINK_HEADER_BYTES;

enum class MessageType : uint8_t {
    SessionOpen = 1,
    SessionResume = 2,
    UserText = 3,
    UserCancel = 4,
    Reset = 5,
    AssistantTextDelta = 6,
    ClarificationRequest = 7,
    TaskStatus = 8,
    ToolActivitySummary = 9,
    EvidenceSummary = 10,
    MemoryStatus = 11,
    Error = 12,
    Health = 13,
    Capabilities = 14,
};

struct Message {
    MessageType type = MessageType::Error;
    bool final = false;
    uint32_t sessionId = 0;
    uint32_t requestId = 0;
    uint16_t sequence = 0;
    uint8_t fragmentIndex = 0;
    uint8_t fragmentCount = 1;
    std::string payload;
};

bool serializeMessage(const Message& message, std::vector<uint8_t>& output);
bool deserializeMessage(const uint8_t* data, size_t size, Message& output);

} // namespace tt::app::aetherchat
