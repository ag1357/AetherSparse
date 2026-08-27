#include <Tactility/app/aetherchat/AetherLinkProtocol.h>

#include <algorithm>
#include <cstdio>

namespace tt::app::aetherchat {

namespace {

// Wire type names are the UPPERCASE MessageType enum values, matching the
// Python protocol.py StrEnum and Device B's native codec (kTypeNames in
// firmware/p4_aethercore/main/protocol/protocol_v2.cpp) exactly. The strict
// Device B decoder rejects any other spelling as UNKNOWN_TYPE.
const char* typeToJsonName(MessageType type) {
    switch (type) {
        case MessageType::SessionOpen: return "SESSION_OPEN";
        case MessageType::SessionResume: return "SESSION_RESUME";
        case MessageType::UserText: return "USER_TEXT";
        case MessageType::UserCancel: return "USER_CANCEL";
        case MessageType::Reset: return "RESET";
        case MessageType::AssistantTextDelta: return "ASSISTANT_TEXT_DELTA";
        case MessageType::ClarificationRequest: return "CLARIFICATION_REQUEST";
        case MessageType::TaskStatus: return "TASK_STATUS";
        case MessageType::ToolActivitySummary: return "TOOL_ACTIVITY_SUMMARY";
        case MessageType::EvidenceSummary: return "EVIDENCE_SUMMARY";
        case MessageType::MemoryStatus: return "MEMORY_STATUS";
        case MessageType::Error: return "ERROR";
        case MessageType::Health: return "HEALTH";
        case MessageType::Capabilities: return "CAPABILITIES";
    }
    return "ERROR";
}

} // namespace

bool messageTypeFromJsonName(const std::string& name, MessageType& out) {
    for (uint16_t i = 0; i <= static_cast<uint16_t>(MessageType::Capabilities); ++i) {
        const auto candidate = static_cast<MessageType>(i);
        if (name == typeToJsonName(candidate)) {
            out = candidate;
            return true;
        }
    }
    return false;
}

std::string jsonEscape(const std::string& text) {
    std::string out;
    out.reserve(text.size() + 8);
    for (char c : text) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\b': out += "\\b"; break;
            case '\f': out += "\\f"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (static_cast<unsigned char>(c) < 0x20U) {
                    char buf[8];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", c);
                    out += buf;
                } else {
                    out += c;
                }
        }
    }
    return out;
}

std::string buildEnvelopeJson(
    MessageType type,
    uint32_t sessionId,
    uint32_t requestId,
    int64_t sequence,
    const std::string& payloadObjectJson
) {
    char ids[160];
    if (requestId != 0) {
        std::snprintf(
            ids, sizeof(ids),
            "\"message_id\":\"%lu-%lld\",\"request_id\":\"%lu\",\"session_id\":\"%lu\",\"sequence\":%lld,",
            (unsigned long)(requestId), (long long)sequence,
            (unsigned long)requestId, (unsigned long)sessionId, (long long)sequence
        );
    } else {
        std::snprintf(
            ids, sizeof(ids),
            "\"message_id\":\"s%lu-%lld\",\"request_id\":null,\"session_id\":\"%lu\",\"sequence\":%lld,",
            (unsigned long)sessionId, (long long)sequence,
            (unsigned long)sessionId, (long long)sequence
        );
    }
    std::string out = "{\"protocol_version\":\"aethercore-tactility.v2\",";
    out += ids;
    out += "\"type\":\"";
    out += typeToJsonName(type);
    out += "\",\"payload\":{";
    out += payloadObjectJson;
    out += "}}";
    return out;
}

bool jsonExtractString(const std::string& json, const char* key, std::string& out) {
    // Find "key" then the opening quote of its string value.
    std::string needle = std::string("\"") + key + "\"";
    size_t pos = json.find(needle);
    if (pos == std::string::npos) {
        return false;
    }
    pos = json.find(':', pos + needle.size());
    if (pos == std::string::npos) {
        return false;
    }
    pos = json.find('"', pos + 1);
    if (pos == std::string::npos) {
        return false;
    }
    // Consume until the closing quote, handling JSON escapes.
    std::string value;
    for (size_t i = pos + 1; i < json.size(); ++i) {
        char c = json[i];
        if (c == '"') {
            out = value;
            return true;
        }
        if (c == '\\' && i + 1 < json.size()) {
            char esc = json[++i];
            switch (esc) {
                case '"': value += '"'; break;
                case '\\': value += '\\'; break;
                case '/': value += '/'; break;
                case 'b': value += '\b'; break;
                case 'f': value += '\f'; break;
                case 'n': value += '\n'; break;
                case 'r': value += '\r'; break;
                case 't': value += '\t'; break;
                case 'u':
                    // Only handle ASCII \u00XX; multi-byte UTF-8 passes through raw anyway.
                    if (i + 4 < json.size()) {
                        unsigned code = 0;
                        bool ok = true;
                        for (int n = 1; n <= 4; ++n) {
                            char h = json[i + n];
                            code <<= 4U;
                            if (h >= '0' && h <= '9') code |= (unsigned)(h - '0');
                            else if (h >= 'a' && h <= 'f') code |= (unsigned)(h - 'a' + 10);
                            else if (h >= 'A' && h <= 'F') code |= (unsigned)(h - 'A' + 10);
                            else { ok = false; break; }
                        }
                        if (ok) {
                            i += 4;
                            if (code < 0x80U) {
                                value += static_cast<char>(code);
                            } else if (code < 0x800U) {
                                value += static_cast<char>(0xC0U | (code >> 6U));
                                value += static_cast<char>(0x80U | (code & 0x3FU));
                            } else {
                                value += static_cast<char>(0xE0U | (code >> 12U));
                                value += static_cast<char>(0x80U | ((code >> 6U) & 0x3FU));
                                value += static_cast<char>(0x80U | (code & 0x3FU));
                            }
                            break;
                        }
                    }
                    value += esc;
                    break;
                default:
                    value += esc;
                    break;
            }
        } else {
            value += c;
        }
    }
    return false;
}

bool jsonExtractBool(const std::string& json, const char* key, bool& out) {
    std::string needle = std::string("\"") + key + "\"";
    size_t pos = json.find(needle);
    if (pos == std::string::npos) {
        return false;
    }
    pos = json.find(':', pos + needle.size());
    if (pos == std::string::npos) {
        return false;
    }
    ++pos;
    while (pos < json.size() && (json[pos] == ' ' || json[pos] == '\t')) {
        ++pos;
    }
    if (json.compare(pos, 4, "true") == 0) {
        out = true;
        return true;
    }
    if (json.compare(pos, 5, "false") == 0) {
        out = false;
        return true;
    }
    return false;
}

bool encodeMessage(const Message& message, std::vector<std::vector<uint8_t>>& frames) {
    frames.clear();
    if (message.sessionId == 0 || message.payload.size() > MAX_MESSAGE_BODY_BYTES) {
        return false;
    }

    const size_t bodySize = message.payload.size();
    const uint8_t* body = reinterpret_cast<const uint8_t*>(message.payload.data());

    if (bodySize <= wire::MAX_FRAME_PAYLOAD) {
        frames.emplace_back(wire::HEADER_BYTES + bodySize + wire::CRC_BYTES);
        const size_t written = wire::encodeFrame(
            frames.back().data(), frames.back().size(),
            message.type, 0, message.requestId, message.sessionId,
            body, bodySize
        );
        if (written == 0) {
            frames.clear();
            return false;
        }
        frames.back().resize(written);
        return true;
    }

    const size_t fragCount = (bodySize + wire::MAX_FRAGMENT_CHUNK - 1) / wire::MAX_FRAGMENT_CHUNK;
    if (fragCount > MAX_FRAGMENTS) {
        return false;
    }
    frames.reserve(fragCount);
    for (size_t index = 0; index < fragCount; ++index) {
        const size_t chunkOffset = index * wire::MAX_FRAGMENT_CHUNK;
        const size_t chunkSize = std::min(wire::MAX_FRAGMENT_CHUNK, bodySize - chunkOffset);

        uint8_t payload[wire::MAX_FRAME_PAYLOAD];
        wire::writeFragHeader(
            payload, message.sequence,
            static_cast<uint8_t>(index), static_cast<uint8_t>(fragCount)
        );
        std::copy(body + chunkOffset, body + chunkOffset + chunkSize,
            payload + wire::FRAG_HEADER_BYTES);

        std::vector<uint8_t> frame(wire::HEADER_BYTES + wire::FRAG_HEADER_BYTES + chunkSize + wire::CRC_BYTES);
        const size_t written = wire::encodeFrame(
            frame.data(), frame.size(),
            message.type, wire::FLAG_FRAGMENTED,
            message.requestId, message.sessionId,
            payload, wire::FRAG_HEADER_BYTES + chunkSize
        );
        if (written == 0) {
            frames.clear();
            return false;
        }
        frame.resize(written);
        frames.push_back(std::move(frame));
    }
    return true;
}

bool Reassembler::feed(const uint8_t* data, size_t size, Message& out) {
    wire::Frame frame;
    if (!wire::decodeFrame(data, size, frame)) {
        return false;
    }

    if ((frame.flags & wire::FLAG_FRAGMENTED) == 0) {
        out.type = frame.type;
        out.sessionId = frame.sessionId;
        out.requestId = frame.requestId;
        out.sequence = 0;
        out.payload.assign(reinterpret_cast<const char*>(frame.payload), frame.payloadLen);
        return true;
    }

    if (frame.fragCount > MAX_FRAGMENTS) {
        return false;
    }

    // Find the matching partial, or claim a slot (prefer free, else evict the
    // entry with the fewest fragments received).
    Partial* slot = nullptr;
    Partial* evict = nullptr;
    for (auto& partial : partials) {
        if (partial.inUse &&
            partial.sessionId == frame.sessionId &&
            partial.requestId == frame.requestId &&
            partial.msgSeq == frame.msgSeq &&
            partial.fragCount == frame.fragCount) {
            slot = &partial;
            break;
        }
        if (!partial.inUse) {
            slot = slot == nullptr ? &partial : slot;
        } else if (evict == nullptr || partial.received < evict->received) {
            evict = &partial;
        }
    }
    if (slot == nullptr) {
        slot = evict;
    }
    if (slot == nullptr) {
        return false;
    }

    if (!slot->inUse || slot->msgSeq != frame.msgSeq ||
        slot->sessionId != frame.sessionId || slot->requestId != frame.requestId ||
        slot->fragCount != frame.fragCount) {
        // (Re)initialize the slot for this message.
        slot->inUse = true;
        slot->sessionId = frame.sessionId;
        slot->requestId = frame.requestId;
        slot->msgSeq = frame.msgSeq;
        slot->type = frame.type;
        slot->fragCount = frame.fragCount;
        slot->received = 0;
        slot->receivedMask = 0;
        slot->body.assign(frame.fragCount * wire::MAX_FRAGMENT_CHUNK, '\0');
    }

    const uint32_t bit = 1U << frame.fragIndex;
    if ((slot->receivedMask & bit) == 0) {
        const size_t offset = frame.fragIndex * wire::MAX_FRAGMENT_CHUNK;
        if (offset + frame.payloadLen > slot->body.size()) {
            slot->inUse = false;
            return false;
        }
        std::copy(frame.payload, frame.payload + frame.payloadLen, slot->body.begin() + offset);
        slot->receivedMask |= bit;
        slot->received++;
    }

    if (slot->received != slot->fragCount) {
        return false;
    }

    // Complete: trim trailing padding of the final fragment.
    size_t totalSize = slot->body.size();
    while (totalSize > 0 && slot->body[totalSize - 1] == '\0') {
        --totalSize;
    }
    out.type = slot->type;
    out.sessionId = slot->sessionId;
    out.requestId = slot->requestId;
    out.sequence = slot->msgSeq;
    out.payload.assign(slot->body.data(), totalSize);
    slot->inUse = false;
    return true;
}

} // namespace tt::app::aetherchat
