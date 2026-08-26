// AetherCore protocol v2 native codec -- see protocol_v2.h for the wire
// profile. Byte-for-byte compatible with Python FramedJsonCodec
// (u32be length prefix + compact pydantic-style JSON body).
//
// Memory model: fully bounded. Decoding uses a single static parser context
// (fixed node + string pools) plus the caller-provided ProtocolMessage.
// Encoding writes directly into the caller buffer. No heap, no exceptions.
// Decode is single-threaded by design (one static parser arena).

#include "protocol_v2.h"

#include <cstdio>
#include <cstring>

namespace aethercore {
namespace protocol_v2 {

// ---------------------------------------------------------------------------
// Names
// ---------------------------------------------------------------------------
static const char* kTypeNames[] = {
    "SESSION_OPEN",        "SESSION_RESUME",   "USER_TEXT",
    "USER_CANCEL",         "RESET",            "ASSISTANT_TEXT_DELTA",
    "CLARIFICATION_REQUEST","TASK_STATUS",     "TOOL_ACTIVITY_SUMMARY",
    "EVIDENCE_SUMMARY",    "MEMORY_STATUS",    "ERROR",
    "HEALTH",              "CAPABILITIES",
};
static_assert(sizeof(kTypeNames) / sizeof(kTypeNames[0]) ==
                  static_cast<size_t>(MsgType::kCount),
              "type name table out of sync");

const char* ToString(MsgType t) {
  size_t i = static_cast<size_t>(t);
  return i < static_cast<size_t>(MsgType::kCount) ? kTypeNames[i] : "UNKNOWN";
}

bool MsgTypeFromString(const char* s, size_t len, MsgType& out) {
  for (size_t i = 0; i < static_cast<size_t>(MsgType::kCount); ++i) {
    if (std::strlen(kTypeNames[i]) == len &&
        std::memcmp(kTypeNames[i], s, len) == 0) {
      out = static_cast<MsgType>(i);
      return true;
    }
  }
  return false;
}

const char* ToString(DecodeError e) {
  switch (e) {
    case DecodeError::OK: return "OK";
    case DecodeError::TRUNCATED_FRAME: return "TRUNCATED_FRAME";
    case DecodeError::INVALID_LENGTH: return "INVALID_LENGTH";
    case DecodeError::INVALID_JSON: return "INVALID_JSON";
    case DecodeError::MISSING_FIELD: return "MISSING_FIELD";
    case DecodeError::UNKNOWN_TYPE: return "UNKNOWN_TYPE";
    case DecodeError::INVALID_FIELD_TYPE: return "INVALID_FIELD_TYPE";
    case DecodeError::VALIDATION_FAILED: return "VALIDATION_FAILED";
    case DecodeError::PAYLOAD_TYPE_MISMATCH: return "PAYLOAD_TYPE_MISMATCH";
  }
  return "UNKNOWN";
}

bool Str::equals(const char* s) const {
  return data != nullptr && std::strlen(s) == len &&
         std::memcmp(data, s, len) == 0;
}

bool ProtocolMessage::poolPut(const char* s, size_t len, Str& out) {
  if (len == 0) {
    out.data = pool;  // valid non-null pointer, len 0
    out.len = 0;
    return true;
  }
  if (pool_used + len > kStringPoolBytes) return false;
  std::memcpy(pool + pool_used, s, len);
  out.data = pool + pool_used;
  out.len = static_cast<uint32_t>(len);
  pool_used += static_cast<uint32_t>(len);
  return true;
}

// ---------------------------------------------------------------------------
// Minimal bounded JSON DOM parser (internal).
// ---------------------------------------------------------------------------
namespace {

constexpr uint32_t kMaxNodes = 1024;
constexpr uint32_t kParsePoolBytes = kStringPoolBytes;
constexpr uint32_t kMaxDepth = 8;

enum class Kind : uint8_t { OBJ, ARR, STR, INT, FLOAT, BOOL, NIL, MEMBER };

struct Node {
  Kind kind;
  int64_t i;        // INT value / BOOL value
  uint32_t off;     // STR/MEMBER: offset into string pool (unescaped)
  uint32_t len;     // STR/MEMBER: byte length
  int32_t child;    // OBJ: first MEMBER; ARR: first element; MEMBER: value
  int32_t next;     // next sibling (member or element)
};

struct Parser {
  const uint8_t* s;
  uint32_t n;
  uint32_t pos;
  uint32_t depth;
  Node nodes[kMaxNodes];
  uint32_t node_count;
  char pool[kParsePoolBytes];
  uint32_t pool_used;
  bool err;

  void skipWs() {
    while (pos < n && (s[pos] == ' ' || s[pos] == '\t' || s[pos] == '\n' ||
                       s[pos] == '\r'))
      ++pos;
  }

  Node* alloc(int32_t& idx) {
    if (node_count >= kMaxNodes) {
      err = true;
      return nullptr;
    }
    idx = static_cast<int32_t>(node_count++);
    return &nodes[idx];
  }

  bool poolAppend(char c) {
    if (pool_used >= kParsePoolBytes) {
      err = true;
      return false;
    }
    pool[pool_used++] = c;
    return true;
  }

  // Parse a JSON string (pos at '"'), unescape into pool.
  bool parseString(uint32_t& off, uint32_t& len) {
    if (pos >= n || s[pos] != '"') return false;
    ++pos;
    off = pool_used;
    while (true) {
      if (pos >= n) return false;
      uint8_t c = s[pos];
      if (c == '"') {
        ++pos;
        len = pool_used - off;
        return true;
      }
      if (c == '\\') {
        ++pos;
        if (pos >= n) return false;
        uint8_t e = s[pos++];
        switch (e) {
          case '"': if (!poolAppend('"')) return false; break;
          case '\\': if (!poolAppend('\\')) return false; break;
          case '/': if (!poolAppend('/')) return false; break;
          case 'b': if (!poolAppend('\b')) return false; break;
          case 'f': if (!poolAppend('\f')) return false; break;
          case 'n': if (!poolAppend('\n')) return false; break;
          case 'r': if (!poolAppend('\r')) return false; break;
          case 't': if (!poolAppend('\t')) return false; break;
          case 'u': {
            if (pos + 4 > n) return false;
            uint32_t cp = 0;
            for (int k = 0; k < 4; ++k) {
              uint8_t h = s[pos++];
              cp <<= 4;
              if (h >= '0' && h <= '9') cp |= h - '0';
              else if (h >= 'a' && h <= 'f') cp |= h - 'a' + 10;
              else if (h >= 'A' && h <= 'F') cp |= h - 'A' + 10;
              else return false;
            }
            // UTF-8 encode (surrogates encoded as-is, 3 bytes; good enough
            // for validation transport since we never collapse them).
            if (cp < 0x80) {
              if (!poolAppend(static_cast<char>(cp))) return false;
            } else if (cp < 0x800) {
              if (!poolAppend(static_cast<char>(0xC0 | (cp >> 6))) ||
                  !poolAppend(static_cast<char>(0x80 | (cp & 0x3F))))
                return false;
            } else {
              if (!poolAppend(static_cast<char>(0xE0 | (cp >> 12))) ||
                  !poolAppend(static_cast<char>(0x80 | ((cp >> 6) & 0x3F))) ||
                  !poolAppend(static_cast<char>(0x80 | (cp & 0x3F))))
                return false;
            }
            break;
          }
          default:
            return false;
        }
      } else if (c < 0x20) {
        return false;  // raw control char not allowed in JSON strings
      } else {
        ++pos;
        if (!poolAppend(static_cast<char>(c))) return false;
      }
    }
  }

  bool parseNumber(Node& node) {
    uint32_t start = pos;
    if (pos < n && s[pos] == '-') ++pos;
    if (pos >= n || s[pos] < '0' || s[pos] > '9') return false;
    if (s[pos] == '0') {
      ++pos;
    } else {
      while (pos < n && s[pos] >= '0' && s[pos] <= '9') ++pos;
    }
    bool is_float = false;
    if (pos < n && s[pos] == '.') {
      is_float = true;
      ++pos;
      if (pos >= n || s[pos] < '0' || s[pos] > '9') return false;
      while (pos < n && s[pos] >= '0' && s[pos] <= '9') ++pos;
    }
    if (pos < n && (s[pos] == 'e' || s[pos] == 'E')) {
      is_float = true;
      ++pos;
      if (pos < n && (s[pos] == '+' || s[pos] == '-')) ++pos;
      if (pos >= n || s[pos] < '0' || s[pos] > '9') return false;
      while (pos < n && s[pos] >= '0' && s[pos] <= '9') ++pos;
    }
    node.kind = is_float ? Kind::FLOAT : Kind::INT;
    node.i = 0;
    if (!is_float) {
      // Manual strtoll with overflow guard.
      bool neg = s[start] == '-';
      uint32_t p = neg ? start + 1 : start;
      uint64_t v = 0;
      for (; p < pos; ++p) {
        v = v * 10 + static_cast<uint64_t>(s[p] - '0');
        if (v > 0x7FFFFFFFFFFFFFFFull) return false;  // overflow
      }
      node.i = neg ? -static_cast<int64_t>(v) : static_cast<int64_t>(v);
    }
    return true;
  }

  bool parseValue(int32_t& out_idx) {
    if (++depth > kMaxDepth) return false;
    skipWs();
    if (pos >= n) { --depth; return false; }
    int32_t idx;
    Node* node = alloc(idx);
    if (!node) { --depth; return false; }
    node->child = -1;
    node->next = -1;
    bool ok = false;
    uint8_t c = s[pos];
    if (c == '{') {
      node->kind = Kind::OBJ;
      ++pos;
      skipWs();
      ok = true;
      int32_t* tail = &node->child;
      if (pos < n && s[pos] == '}') { ++pos; }
      else {
        while (ok) {
          skipWs();
          int32_t midx;
          Node* member = alloc(midx);
          if (!member) { ok = false; break; }
          member->kind = Kind::MEMBER;
          member->next = -1;
          if (!parseString(member->off, member->len)) { ok = false; break; }
          skipWs();
          if (pos >= n || s[pos] != ':') { ok = false; break; }
          ++pos;
          if (!parseValue(member->child)) { ok = false; break; }
          *tail = midx;
          tail = &nodes[midx].next;
          skipWs();
          if (pos < n && s[pos] == ',') { ++pos; continue; }
          if (pos < n && s[pos] == '}') { ++pos; break; }
          ok = false;
        }
      }
    } else if (c == '[') {
      node->kind = Kind::ARR;
      ++pos;
      skipWs();
      ok = true;
      int32_t* tail = &node->child;
      if (pos < n && s[pos] == ']') { ++pos; }
      else {
        while (ok) {
          int32_t eidx;
          if (!parseValue(eidx)) { ok = false; break; }
          *tail = eidx;
          tail = &nodes[eidx].next;
          skipWs();
          if (pos < n && s[pos] == ',') { ++pos; continue; }
          if (pos < n && s[pos] == ']') { ++pos; break; }
          ok = false;
        }
      }
    } else if (c == '"') {
      node->kind = Kind::STR;
      ok = parseString(node->off, node->len);
    } else if (c == 't') {
      node->kind = Kind::BOOL;
      node->i = 1;
      ok = pos + 4 <= n && std::memcmp(s + pos, "true", 4) == 0;
      if (ok) pos += 4;
    } else if (c == 'f') {
      node->kind = Kind::BOOL;
      node->i = 0;
      ok = pos + 5 <= n && std::memcmp(s + pos, "false", 5) == 0;
      if (ok) pos += 5;
    } else if (c == 'n') {
      node->kind = Kind::NIL;
      ok = pos + 4 <= n && std::memcmp(s + pos, "null", 4) == 0;
      if (ok) pos += 4;
    } else if (c == '-' || (c >= '0' && c <= '9')) {
      ok = parseNumber(*node);
    }
    --depth;
    if (!ok || err) return false;
    out_idx = idx;
    return true;
  }
};

// Static parser arena: single decode context, deterministic, zero heap.
static Parser g_parser;

// ---------------------------------------------------------------------------
// DOM helpers
// ---------------------------------------------------------------------------
const Node* objFind(const Parser& p, const Node& obj, const char* key) {
  size_t klen = std::strlen(key);
  for (int32_t m = obj.child; m >= 0; m = p.nodes[m].next) {
    const Node& member = p.nodes[m];
    if (member.len == klen &&
        std::memcmp(p.pool + member.off, key, klen) == 0) {
      return &p.nodes[member.child];
    }
  }
  return nullptr;
}

// Reject any keys not in `allowed` (extra="forbid").
bool objHasOnlyKeys(const Parser& p, const Node& obj, const char* const* allowed,
                    size_t allowed_count) {
  for (int32_t m = obj.child; m >= 0; m = p.nodes[m].next) {
    const Node& member = p.nodes[m];
    bool found = false;
    for (size_t k = 0; k < allowed_count; ++k) {
      size_t klen = std::strlen(allowed[k]);
      if (member.len == klen &&
          std::memcmp(p.pool + member.off, allowed[k], klen) == 0) {
        found = true;
        break;
      }
    }
    if (!found) return false;
  }
  return true;
}

bool sessionIdValid(const char* s, size_t len) {
  if (len < 1 || len > kMaxIdLen) return false;
  for (size_t i = 0; i < len; ++i) {
    char c = s[i];
    bool ok = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
              (c >= '0' && c <= '9') || c == '_' || c == '-';
    if (!ok) return false;
  }
  return true;
}

struct FieldResult {
  DecodeError err = DecodeError::OK;
  bool present = false;
};

// Copy a required/optional string field into the message pool with bounds.
DecodeError getStr(ProtocolMessage& msg, const Node& obj, const char* key,
                   bool required, size_t min_len, size_t max_len, Str& out,
                   bool* present = nullptr) {
  const Node* n = objFind(g_parser, obj, key);
  if (!n) {
    if (present) *present = false;
    return required ? DecodeError::MISSING_FIELD : DecodeError::OK;
  }
  if (n->kind != Kind::STR) return DecodeError::INVALID_FIELD_TYPE;
  if (n->len < min_len || n->len > max_len) return DecodeError::VALIDATION_FAILED;
  if (present) *present = true;
  if (!msg.poolPut(g_parser.pool + n->off, n->len, out))
    return DecodeError::INVALID_JSON;
  return DecodeError::OK;
}

// Defaulted string field.
DecodeError getStrDef(ProtocolMessage& msg, const Node& obj, const char* key,
                      const char* dflt, size_t max_len, Str& out) {
  bool present = false;
  DecodeError e = getStr(msg, obj, key, false, 0, max_len, out, &present);
  if (e != DecodeError::OK) return e;
  if (!present) {
    if (!msg.poolPut(dflt, std::strlen(dflt), out)) return DecodeError::INVALID_JSON;
  }
  return DecodeError::OK;
}

DecodeError getInt(const Node& obj, const char* key, bool required,
                   int64_t dflt, int64_t min, int64_t& out) {
  const Node* n = objFind(g_parser, obj, key);
  if (!n) {
    if (required) return DecodeError::MISSING_FIELD;
    out = dflt;
    return DecodeError::OK;
  }
  if (n->kind != Kind::INT) return DecodeError::INVALID_FIELD_TYPE;
  if (n->i < min) return DecodeError::VALIDATION_FAILED;
  out = n->i;
  return DecodeError::OK;
}

DecodeError getBool(const Node& obj, const char* key, bool required,
                    bool dflt, bool& out) {
  const Node* n = objFind(g_parser, obj, key);
  if (!n) {
    if (required) return DecodeError::MISSING_FIELD;
    out = dflt;
    return DecodeError::OK;
  }
  if (n->kind != Kind::BOOL) return DecodeError::INVALID_FIELD_TYPE;
  out = n->i != 0;
  return DecodeError::OK;
}

// Array-of-strings field with default and max items.
DecodeError getStrList(ProtocolMessage& msg, const Node& obj, const char* key,
                       uint32_t max_items, StrList& out, bool required = false,
                       uint32_t min_items = 0) {
  out.count = 0;
  const Node* n = objFind(g_parser, obj, key);
  if (!n) {
    if (required && min_items > 0) return DecodeError::MISSING_FIELD;
    if (min_items > 0) return DecodeError::VALIDATION_FAILED;  // default would violate min
    return required ? DecodeError::MISSING_FIELD : DecodeError::OK;
  }
  if (n->kind != Kind::ARR) return DecodeError::INVALID_FIELD_TYPE;
  uint32_t count = 0;
  for (int32_t e = n->child; e >= 0; e = g_parser.nodes[e].next) ++count;
  if (count < min_items || count > max_items) return DecodeError::VALIDATION_FAILED;
  uint32_t i = 0;
  for (int32_t e = n->child; e >= 0; e = g_parser.nodes[e].next, ++i) {
    const Node& item = g_parser.nodes[e];
    if (item.kind != Kind::STR) return DecodeError::INVALID_FIELD_TYPE;
    if (!msg.poolPut(g_parser.pool + item.off, item.len, out.items[i]))
      return DecodeError::INVALID_JSON;
  }
  out.count = count;
  return DecodeError::OK;
}

// ---------------------------------------------------------------------------
// Per-type payload validation
// ---------------------------------------------------------------------------
DecodeError decodePayload(ProtocolMessage& msg, const Node& payload) {
  if (payload.kind != Kind::OBJ) return DecodeError::INVALID_FIELD_TYPE;
  DecodeError e;
  switch (msg.type) {
    case MsgType::SESSION_OPEN: {
      static const char* keys[] = {"client_version", "supported_protocols",
                                   "requested_capabilities"};
      if (!objHasOnlyKeys(g_parser, payload, keys, 3))
        return DecodeError::VALIDATION_FAILED;
      e = getStr(msg, payload, "client_version", true, 0, kMaxUserText,
                 msg.p.session_open.client_version);
      if (e != DecodeError::OK) return e;
      e = getStrList(msg, payload, "supported_protocols", 4,
                     msg.p.session_open.supported_protocols);
      if (e != DecodeError::OK) return e;
      if (!objFind(g_parser, payload, "supported_protocols")) {
        // Python default: ("aethercore-tactility.v2",)
        const char* d = "aethercore-tactility.v2";
        if (!msg.poolPut(d, std::strlen(d),
                         msg.p.session_open.supported_protocols.items[0]))
          return DecodeError::INVALID_JSON;
        msg.p.session_open.supported_protocols.count = 1;
      }
      return getStrList(msg, payload, "requested_capabilities", 32,
                        msg.p.session_open.requested_capabilities);
    }
    case MsgType::SESSION_RESUME: {
      static const char* keys[] = {"client_version", "last_received_sequence"};
      if (!objHasOnlyKeys(g_parser, payload, keys, 2))
        return DecodeError::VALIDATION_FAILED;
      e = getStr(msg, payload, "client_version", true, 0, kMaxUserText,
                 msg.p.session_resume.client_version);
      if (e != DecodeError::OK) return e;
      return getInt(payload, "last_received_sequence", false, 0, 0,
                    msg.p.session_resume.last_received_sequence);
    }
    case MsgType::USER_TEXT: {
      static const char* keys[] = {"text"};
      if (!objHasOnlyKeys(g_parser, payload, keys, 1))
        return DecodeError::VALIDATION_FAILED;
      return getStr(msg, payload, "text", true, 1, kMaxUserText,
                    msg.p.user_text.text);
    }
    case MsgType::USER_CANCEL: {
      static const char* keys[] = {"reason"};
      if (!objHasOnlyKeys(g_parser, payload, keys, 1))
        return DecodeError::VALIDATION_FAILED;
      return getStrDef(msg, payload, "reason", "user", kMaxUserText,
                       msg.p.user_cancel.reason);
    }
    case MsgType::RESET: {
      static const char* keys[] = {"reason"};
      if (!objHasOnlyKeys(g_parser, payload, keys, 1))
        return DecodeError::VALIDATION_FAILED;
      return getStrDef(msg, payload, "reason", "user", kMaxUserText,
                       msg.p.reset.reason);
    }
    case MsgType::ASSISTANT_TEXT_DELTA: {
      static const char* keys[] = {"text", "final"};
      if (!objHasOnlyKeys(g_parser, payload, keys, 2))
        return DecodeError::VALIDATION_FAILED;
      e = getStr(msg, payload, "text", true, 1, kMaxAssistantDelta,
                 msg.p.assistant_text_delta.text);
      if (e != DecodeError::OK) return e;
      return getBool(payload, "final", false, false,
                     msg.p.assistant_text_delta.final);
    }
    case MsgType::CLARIFICATION_REQUEST: {
      static const char* keys[] = {"question", "choices"};
      if (!objHasOnlyKeys(g_parser, payload, keys, 2))
        return DecodeError::VALIDATION_FAILED;
      e = getStr(msg, payload, "question", true, 0, kMaxUserText,
                 msg.p.clarification_request.question);
      if (e != DecodeError::OK) return e;
      StrList list;
      e = getStrList(msg, payload, "choices", kMaxChoices, list, true, 2);
      if (e != DecodeError::OK) return e;
      msg.p.clarification_request.choice_count = list.count;
      for (uint32_t i = 0; i < list.count; ++i)
        msg.p.clarification_request.choices[i] = list.items[i];
      return DecodeError::OK;
    }
    case MsgType::TASK_STATUS: {
      static const char* keys[] = {"status", "detail"};
      if (!objHasOnlyKeys(g_parser, payload, keys, 2))
        return DecodeError::VALIDATION_FAILED;
      e = getStr(msg, payload, "status", true, 0, kMaxUserText,
                 msg.p.task_status.status);
      if (e != DecodeError::OK) return e;
      return getStrDef(msg, payload, "detail", "", kMaxUserText,
                       msg.p.task_status.detail);
    }
    case MsgType::TOOL_ACTIVITY_SUMMARY: {
      static const char* keys[] = {"tool", "success", "summary"};
      if (!objHasOnlyKeys(g_parser, payload, keys, 3))
        return DecodeError::VALIDATION_FAILED;
      e = getStr(msg, payload, "tool", true, 0, kMaxUserText,
                 msg.p.tool_activity_summary.tool);
      if (e != DecodeError::OK) return e;
      e = getBool(payload, "success", true, false,
                  msg.p.tool_activity_summary.success);
      if (e != DecodeError::OK) return e;
      return getStr(msg, payload, "summary", true, 0, kMaxUserText,
                    msg.p.tool_activity_summary.summary);
    }
    case MsgType::EVIDENCE_SUMMARY: {
      static const char* keys[] = {"handle_ids", "summary"};
      if (!objHasOnlyKeys(g_parser, payload, keys, 2))
        return DecodeError::VALIDATION_FAILED;
      e = getStrList(msg, payload, "handle_ids", 32,
                     msg.p.evidence_summary.handle_ids, true);
      if (e != DecodeError::OK) return e;
      return getStr(msg, payload, "summary", true, 0, kMaxUserText,
                    msg.p.evidence_summary.summary);
    }
    case MsgType::MEMORY_STATUS: {
      static const char* keys[] = {"operation", "success", "memory_ids",
                                   "detail"};
      if (!objHasOnlyKeys(g_parser, payload, keys, 4))
        return DecodeError::VALIDATION_FAILED;
      e = getStr(msg, payload, "operation", true, 0, kMaxUserText,
                 msg.p.memory_status.operation);
      if (e != DecodeError::OK) return e;
      e = getBool(payload, "success", true, false, msg.p.memory_status.success);
      if (e != DecodeError::OK) return e;
      e = getStrList(msg, payload, "memory_ids", 32,
                     msg.p.memory_status.memory_ids);
      if (e != DecodeError::OK) return e;
      return getStrDef(msg, payload, "detail", "", kMaxMemoryDetail,
                       msg.p.memory_status.detail);
    }
    case MsgType::ERROR: {
      static const char* keys[] = {"code", "message", "recoverable"};
      if (!objHasOnlyKeys(g_parser, payload, keys, 3))
        return DecodeError::VALIDATION_FAILED;
      e = getStr(msg, payload, "code", true, 0, kMaxUserText, msg.p.error.code);
      if (e != DecodeError::OK) return e;
      e = getStr(msg, payload, "message", true, 0, kMaxUserText,
                 msg.p.error.message);
      if (e != DecodeError::OK) return e;
      return getBool(payload, "recoverable", true, false,
                     msg.p.error.recoverable);
    }
    case MsgType::HEALTH: {
      static const char* keys[] = {"status", "runtime_version",
                                   "service_generation"};
      if (!objHasOnlyKeys(g_parser, payload, keys, 3))
        return DecodeError::VALIDATION_FAILED;
      e = getStr(msg, payload, "status", true, 0, kMaxUserText,
                 msg.p.health.status);
      if (e != DecodeError::OK) return e;
      e = getStr(msg, payload, "runtime_version", true, 0, kMaxIdLen,
                 msg.p.health.runtime_version);
      if (e != DecodeError::OK) return e;
      return getInt(payload, "service_generation", false, 1, 1,
                    msg.p.health.service_generation);
    }
    case MsgType::CAPABILITIES: {
      static const char* keys[] = {"protocol_version", "hardware_class",
                                   "tools", "specialists", "unavailable",
                                   "transport"};
      if (!objHasOnlyKeys(g_parser, payload, keys, 6))
        return DecodeError::VALIDATION_FAILED;
      e = getStr(msg, payload, "protocol_version", true, 0, kMaxIdLen,
                 msg.p.capabilities.protocol_version);
      if (e != DecodeError::OK) return e;
      e = getStr(msg, payload, "hardware_class", true, 0, kMaxIdLen,
                 msg.p.capabilities.hardware_class);
      if (e != DecodeError::OK) return e;
      e = getStrList(msg, payload, "tools", 64, msg.p.capabilities.tools);
      if (e != DecodeError::OK) return e;
      e = getStrList(msg, payload, "specialists", 64,
                     msg.p.capabilities.specialists);
      if (e != DecodeError::OK) return e;
      e = getStrList(msg, payload, "unavailable", 64,
                     msg.p.capabilities.unavailable);
      if (e != DecodeError::OK) return e;
      return getStr(msg, payload, "transport", true, 0, kMaxIdLen,
                    msg.p.capabilities.transport);
    }
    default:
      return DecodeError::UNKNOWN_TYPE;
  }
}

}  // namespace

// ---------------------------------------------------------------------------
// Decode
// ---------------------------------------------------------------------------
DecodeError DecodeFrame(const uint8_t* frame, size_t len, ProtocolMessage& out) {
  if (len < 4) return DecodeError::TRUNCATED_FRAME;
  uint32_t size = (static_cast<uint32_t>(frame[0]) << 24) |
                  (static_cast<uint32_t>(frame[1]) << 16) |
                  (static_cast<uint32_t>(frame[2]) << 8) |
                  static_cast<uint32_t>(frame[3]);
  if (size > kMaxFrameBytes || len != static_cast<size_t>(size) + 4)
    return size > kMaxFrameBytes ? DecodeError::INVALID_LENGTH
           : (len < static_cast<size_t>(size) + 4
                  ? DecodeError::TRUNCATED_FRAME
                  : DecodeError::INVALID_LENGTH);

  // Reset parser arena and message.
  g_parser.s = frame + 4;
  g_parser.n = size;
  g_parser.pos = 0;
  g_parser.depth = 0;
  g_parser.node_count = 0;
  g_parser.pool_used = 0;
  g_parser.err = false;
  out.pool_used = 0;

  int32_t root;
  if (!g_parser.parseValue(root)) return DecodeError::INVALID_JSON;
  g_parser.skipWs();
  if (g_parser.pos != g_parser.n) return DecodeError::INVALID_JSON;
  const Node& envelope = g_parser.nodes[root];
  if (envelope.kind != Kind::OBJ) return DecodeError::INVALID_FIELD_TYPE;

  static const char* env_keys[] = {"protocol_version", "message_id",
                                   "request_id",       "session_id",
                                   "sequence",         "type",
                                   "payload"};
  if (!objHasOnlyKeys(g_parser, envelope, env_keys, 7))
    return DecodeError::VALIDATION_FAILED;

  DecodeError e;
  e = getStrDef(out, envelope, "protocol_version", "aethercore-tactility.v2",
                kMaxIdLen, out.protocol_version);
  if (e != DecodeError::OK) return e;
  e = getStr(out, envelope, "message_id", true, 1, kMaxIdLen, out.message_id);
  if (e != DecodeError::OK) return e;

  // request_id: string or null, default null.
  const Node* rid = objFind(g_parser, envelope, "request_id");
  out.has_request_id = false;
  if (rid) {
    if (rid->kind == Kind::NIL) {
      // explicit null: same as absent
    } else if (rid->kind == Kind::STR) {
      if (rid->len < 1 || rid->len > kMaxIdLen)
        return DecodeError::VALIDATION_FAILED;
      if (!out.poolPut(g_parser.pool + rid->off, rid->len, out.request_id))
        return DecodeError::INVALID_JSON;
      out.has_request_id = true;
    } else {
      return DecodeError::INVALID_FIELD_TYPE;
    }
  }

  e = getStr(out, envelope, "session_id", true, 1, kMaxIdLen, out.session_id);
  if (e != DecodeError::OK) return e;
  if (!sessionIdValid(out.session_id.data, out.session_id.len))
    return DecodeError::VALIDATION_FAILED;

  e = getInt(envelope, "sequence", true, 0, 0, out.sequence);
  if (e != DecodeError::OK) return e;

  const Node* type_node = objFind(g_parser, envelope, "type");
  if (!type_node) return DecodeError::MISSING_FIELD;
  if (type_node->kind != Kind::STR) return DecodeError::INVALID_FIELD_TYPE;
  if (!MsgTypeFromString(g_parser.pool + type_node->off, type_node->len,
                         out.type))
    return DecodeError::UNKNOWN_TYPE;

  const Node* payload = objFind(g_parser, envelope, "payload");
  if (!payload) return DecodeError::MISSING_FIELD;
  return decodePayload(out, *payload);
}

// ---------------------------------------------------------------------------
// Encode (byte-for-byte compatible with pydantic model_dump_json + >I prefix)
// ---------------------------------------------------------------------------
namespace {

struct Writer {
  uint8_t* buf;
  size_t cap;
  size_t len;
  bool overflow;

  void put(char c) {
    if (len >= cap) {
      overflow = true;
      return;
    }
    buf[len++] = static_cast<uint8_t>(c);
  }
  void putBytes(const char* s, size_t n) {
    if (len + n > cap) {
      overflow = true;
      return;
    }
    std::memcpy(buf + len, s, n);
    len += n;
  }
  void putCStr(const char* s) { putBytes(s, std::strlen(s)); }

  // JSON string escaping identical to serde/pydantic: ", \, and control
  // chars (<0x20) via short escapes or \u00XX; non-ASCII emitted raw.
  void putJsonStr(const char* s, size_t n) {
    put('"');
    for (size_t i = 0; i < n; ++i) {
      uint8_t c = static_cast<uint8_t>(s[i]);
      switch (c) {
        case '"': putCStr("\\\""); break;
        case '\\': putCStr("\\\\"); break;
        case '\b': putCStr("\\b"); break;
        case '\f': putCStr("\\f"); break;
        case '\n': putCStr("\\n"); break;
        case '\r': putCStr("\\r"); break;
        case '\t': putCStr("\\t"); break;
        default:
          if (c < 0x20) {
            char esc[7];
            std::snprintf(esc, sizeof(esc), "\\u%04x", c);
            putCStr(esc);
          } else {
            put(static_cast<char>(c));
          }
      }
    }
    put('"');
  }
  void putJsonStr(const Str& s) { putJsonStr(s.data ? s.data : "", s.len); }

  void putInt(int64_t v) {
    char tmp[24];
    int n = std::snprintf(tmp, sizeof(tmp), "%lld",
                          static_cast<long long>(v));
    putBytes(tmp, static_cast<size_t>(n));
  }
  void putBool(bool b) { putCStr(b ? "true" : "false"); }

  void putStrList(const Str* items, uint32_t count) {
    put('[');
    for (uint32_t i = 0; i < count; ++i) {
      if (i) put(',');
      putJsonStr(items[i]);
    }
    put(']');
  }
  void putStrList(const StrList& l) { putStrList(l.items, l.count); }
};

// Validate encoder-side bounds (mirror decoder checks).
bool validForEncode(const ProtocolMessage& m) {
  if (m.message_id.len < 1 || m.message_id.len > kMaxIdLen) return false;
  if (!m.message_id.data) return false;
  if (m.has_request_id &&
      (m.request_id.len < 1 || m.request_id.len > kMaxIdLen))
    return false;
  if (!m.session_id.data ||
      !sessionIdValid(m.session_id.data, m.session_id.len))
    return false;
  if (m.sequence < 0) return false;
  switch (m.type) {
    case MsgType::USER_TEXT:
      return m.p.user_text.text.len >= 1 &&
             m.p.user_text.text.len <= kMaxUserText;
    case MsgType::ASSISTANT_TEXT_DELTA:
      return m.p.assistant_text_delta.text.len >= 1 &&
             m.p.assistant_text_delta.text.len <= kMaxAssistantDelta;
    case MsgType::CLARIFICATION_REQUEST:
      return m.p.clarification_request.choice_count >= 2 &&
             m.p.clarification_request.choice_count <= kMaxChoices;
    case MsgType::HEALTH:
      return m.p.health.service_generation >= 1;
    case MsgType::SESSION_RESUME:
      return m.p.session_resume.last_received_sequence >= 0;
    default:
      return true;
  }
}

}  // namespace

EncodeError EncodeFrame(const ProtocolMessage& msg, uint8_t* out, size_t cap,
                        size_t& out_len) {
  out_len = 0;
  if (!validForEncode(msg)) return EncodeError::INVALID_MESSAGE;
  if (cap < 4) return EncodeError::BUFFER_TOO_SMALL;

  Writer w{out + 4, cap - 4, 0, false};
  w.put('{');
  w.putCStr("\"protocol_version\":");
  if (msg.protocol_version.data) {
    w.putJsonStr(msg.protocol_version);
  } else {
    const char* dflt = "aethercore-tactility.v2";
    w.putJsonStr(dflt, std::strlen(dflt));
  }
  w.putCStr(",\"message_id\":");
  w.putJsonStr(msg.message_id);
  w.putCStr(",\"request_id\":");
  if (msg.has_request_id) w.putJsonStr(msg.request_id);
  else w.putCStr("null");
  w.putCStr(",\"session_id\":");
  w.putJsonStr(msg.session_id);
  w.putCStr(",\"sequence\":");
  w.putInt(msg.sequence);
  w.putCStr(",\"type\":");
  w.putJsonStr(ToString(msg.type), std::strlen(ToString(msg.type)));
  w.putCStr(",\"payload\":");
  w.put('{');
  switch (msg.type) {
    case MsgType::SESSION_OPEN:
      w.putCStr("\"client_version\":");
      w.putJsonStr(msg.p.session_open.client_version);
      w.putCStr(",\"supported_protocols\":");
      w.putStrList(msg.p.session_open.supported_protocols);
      w.putCStr(",\"requested_capabilities\":");
      w.putStrList(msg.p.session_open.requested_capabilities);
      break;
    case MsgType::SESSION_RESUME:
      w.putCStr("\"client_version\":");
      w.putJsonStr(msg.p.session_resume.client_version);
      w.putCStr(",\"last_received_sequence\":");
      w.putInt(msg.p.session_resume.last_received_sequence);
      break;
    case MsgType::USER_TEXT:
      w.putCStr("\"text\":");
      w.putJsonStr(msg.p.user_text.text);
      break;
    case MsgType::USER_CANCEL:
      w.putCStr("\"reason\":");
      w.putJsonStr(msg.p.user_cancel.reason);
      break;
    case MsgType::RESET:
      w.putCStr("\"reason\":");
      w.putJsonStr(msg.p.reset.reason);
      break;
    case MsgType::ASSISTANT_TEXT_DELTA:
      w.putCStr("\"text\":");
      w.putJsonStr(msg.p.assistant_text_delta.text);
      w.putCStr(",\"final\":");
      w.putBool(msg.p.assistant_text_delta.final);
      break;
    case MsgType::CLARIFICATION_REQUEST:
      w.putCStr("\"question\":");
      w.putJsonStr(msg.p.clarification_request.question);
      w.putCStr(",\"choices\":");
      w.putStrList(msg.p.clarification_request.choices,
                   msg.p.clarification_request.choice_count);
      break;
    case MsgType::TASK_STATUS:
      w.putCStr("\"status\":");
      w.putJsonStr(msg.p.task_status.status);
      w.putCStr(",\"detail\":");
      w.putJsonStr(msg.p.task_status.detail);
      break;
    case MsgType::TOOL_ACTIVITY_SUMMARY:
      w.putCStr("\"tool\":");
      w.putJsonStr(msg.p.tool_activity_summary.tool);
      w.putCStr(",\"success\":");
      w.putBool(msg.p.tool_activity_summary.success);
      w.putCStr(",\"summary\":");
      w.putJsonStr(msg.p.tool_activity_summary.summary);
      break;
    case MsgType::EVIDENCE_SUMMARY:
      w.putCStr("\"handle_ids\":");
      w.putStrList(msg.p.evidence_summary.handle_ids);
      w.putCStr(",\"summary\":");
      w.putJsonStr(msg.p.evidence_summary.summary);
      break;
    case MsgType::MEMORY_STATUS:
      w.putCStr("\"operation\":");
      w.putJsonStr(msg.p.memory_status.operation);
      w.putCStr(",\"success\":");
      w.putBool(msg.p.memory_status.success);
      w.putCStr(",\"memory_ids\":");
      w.putStrList(msg.p.memory_status.memory_ids);
      w.putCStr(",\"detail\":");
      w.putJsonStr(msg.p.memory_status.detail);
      break;
    case MsgType::ERROR:
      w.putCStr("\"code\":");
      w.putJsonStr(msg.p.error.code);
      w.putCStr(",\"message\":");
      w.putJsonStr(msg.p.error.message);
      w.putCStr(",\"recoverable\":");
      w.putBool(msg.p.error.recoverable);
      break;
    case MsgType::HEALTH:
      w.putCStr("\"status\":");
      w.putJsonStr(msg.p.health.status);
      w.putCStr(",\"runtime_version\":");
      w.putJsonStr(msg.p.health.runtime_version);
      w.putCStr(",\"service_generation\":");
      w.putInt(msg.p.health.service_generation);
      break;
    case MsgType::CAPABILITIES:
      w.putCStr("\"protocol_version\":");
      w.putJsonStr(msg.p.capabilities.protocol_version);
      w.putCStr(",\"hardware_class\":");
      w.putJsonStr(msg.p.capabilities.hardware_class);
      w.putCStr(",\"tools\":");
      w.putStrList(msg.p.capabilities.tools);
      w.putCStr(",\"specialists\":");
      w.putStrList(msg.p.capabilities.specialists);
      w.putCStr(",\"unavailable\":");
      w.putStrList(msg.p.capabilities.unavailable);
      w.putCStr(",\"transport\":");
      w.putJsonStr(msg.p.capabilities.transport);
      break;
    default:
      return EncodeError::INVALID_MESSAGE;
  }
  w.putCStr("}}");
  if (w.overflow) return EncodeError::BUFFER_TOO_SMALL;
  if (w.len > kMaxFrameBytes) return EncodeError::BUFFER_TOO_SMALL;

  uint32_t size = static_cast<uint32_t>(w.len);
  out[0] = static_cast<uint8_t>(size >> 24);
  out[1] = static_cast<uint8_t>(size >> 16);
  out[2] = static_cast<uint8_t>(size >> 8);
  out[3] = static_cast<uint8_t>(size);
  out_len = 4 + size;
  return EncodeError::OK;
}

}  // namespace protocol_v2
}  // namespace aethercore
