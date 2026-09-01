/* See service_runtime.h. Native port of agent/operational.py's request
 * handler semantics onto the Phase 9/10/11/12 modules. */
#include "service_runtime.h"

#include <ctype.h>
#include <stdio.h>
#include <string.h>

#include <new>
#include <string>
#include <vector>

#include "memory/memory_native.h"
#include "protocol/protocol_v2.h"
#include "service/service_core.h"

namespace ac::runtime {
namespace {

using ac::link::Ac20Type;
using aethercore::protocol_v2::DecodeError;
using aethercore::protocol_v2::MsgType;
using aethercore::protocol_v2::ProtocolMessage;
using aethercore::service::ServiceCore;
using aethercore::service::ServiceResponse;

ServiceCore g_core;
acmem::Manager *g_memory = nullptr; /* heap: holds up to wm bounds */
acmem::UserMemory *g_user_mem = nullptr;
ResponseSink g_sink = nullptr;
void *g_sink_ctx = nullptr;
uint32_t g_cur_request_id = 0; /* AC20 routing ids of the in-flight request */
uint32_t g_cur_session_id = 0;
RuntimeInfo g_info;
std::string g_state_path;
uint64_t g_requests = 0;
uint64_t g_errors = 0;
bool g_ready = false;

/* ------------------------- helpers --------------------------------------- */

std::string to_cpp(const aethercore::protocol_v2::Str &s) {
  return s.data ? std::string(s.data, s.len) : std::string();
}

/* Message + frame storage lives in static internal SRAM, never on a task
 * stack: ProtocolMessage is ~24 KB (20 KB string pool + bounded payload
 * union) and the encoded-frame buffer is 16 KB, far beyond any task stack.
 * The serial request loop (one frame in flight, single link caller)
 * guarantees these scratches are never concurrently live; make_response()
 * fully resets the response scratch per use. Two scratches are required
 * because the decoded request stays alive while its response is built. */
ProtocolMessage &decode_scratch() {
  static ProtocolMessage m;
  return m;
}

ProtocolMessage &resp_scratch() {
  static ProtocolMessage m;
  return m;
}

Ac20Type to_wire(MsgType t) { return static_cast<Ac20Type>(static_cast<int>(t)); }

void emit_meas(const char *kind, const ProtocolMessage &req, const char *detail) {
  printf("MEAS {\"phase\":\"service\",\"kind\":\"%s\",\"session\":\"%s\","
         "\"message\":\"%s\",\"detail\":\"%s\"}\n",
         kind, to_cpp(req.session_id).c_str(), to_cpp(req.message_id).c_str(),
         detail);
}

/* response() parity with agent/protocol.py: message_id "<id>-<suffix>",
 * request_id = request.request_id or message_id, session+sequence echo. */
void make_response(const ProtocolMessage &req, MsgType type, const char *suffix,
                   ProtocolMessage *out) {
  /* Construct in place: `*out = ProtocolMessage{}` would materialize the
   * ~24 KB message as a STACK temporary (observed stack-protection fault on
   * the 16 KiB link task). ProtocolMessage is trivially destructible. */
  new (out) ProtocolMessage();
  std::string mid = to_cpp(req.message_id) + "-" + suffix;
  std::string rid =
      req.has_request_id ? to_cpp(req.request_id) : to_cpp(req.message_id);
  std::string sid = to_cpp(req.session_id);
  out->poolPut("aethercore-tactility.v2", 22, out->protocol_version);
  out->poolPut(mid.data(), mid.size(), out->message_id);
  if (!rid.empty()) {
    out->poolPut(rid.data(), rid.size(), out->request_id);
    out->has_request_id = true;
  }
  out->poolPut(sid.data(), sid.size(), out->session_id);
  out->sequence = req.sequence;
  out->type = type;
}

void send_response(const ProtocolMessage &msg) {
  if (!g_sink) return;
  static uint8_t frame[aethercore::protocol_v2::kMaxEncodedFrame];
  size_t frame_len = 0;
  if (aethercore::protocol_v2::EncodeFrame(msg, frame, sizeof(frame),
                                           frame_len) !=
          aethercore::protocol_v2::EncodeError::OK ||
      frame_len < 4) {
    g_errors++;
    return;
  }
  g_sink(g_sink_ctx, to_wire(msg.type), g_cur_request_id, g_cur_session_id,
         frame + 4, frame_len - 4);
}

void send_error(const ProtocolMessage &req, const char *code,
                const char *message, bool recoverable) {
  ProtocolMessage &out = resp_scratch();
  make_response(req, MsgType::ERROR, "error", &out);
  out.p.error.code.data = out.pool;
  out.poolPut(code, strlen(code), out.p.error.code);
  out.poolPut(message, strlen(message), out.p.error.message);
  out.p.error.recoverable = recoverable;
  send_response(out);
}

/* ------------------------- memory interception ---------------------------- */
/* Exact ports of operational.py's four regexes + explicit_remember_payload
 * (the latter already lives in acmem). */

bool match_ci(const char *text, size_t *i, const char *word) {
  size_t n = strlen(word);
  for (size_t k = 0; k < n; k++) {
    if (tolower((unsigned char)text[*i + k]) != word[k]) return false;
  }
  *i += n;
  return true;
}

void skip_ws(const std::string &s, size_t *i) {
  while (*i < s.size() && isspace((unsigned char)s[*i])) (*i)++;
}

bool word_boundary_or_end(const std::string &s, size_t i) {
  return i >= s.size() || isspace((unsigned char)s[i]) || s[i] == '?' ||
         s[i] == '.' || s[i] == '!';
}

/* ^\s*(list|show)\s+(my\s+)?memor(y|ies)\s*[?.!]?\s*$ */
bool match_list_memory(const std::string &s) {
  size_t i = 0;
  skip_ws(s, &i);
  if (!match_ci(s.c_str(), &i, "list") && !match_ci(s.c_str(), &i, "show"))
    return false;
  if (!isspace((unsigned char)s[i])) return false;
  skip_ws(s, &i);
  size_t save = i;
  if (match_ci(s.c_str(), &i, "my")) {
    if (!isspace((unsigned char)s[i])) {
      i = save;
    } else {
      skip_ws(s, &i);
    }
  }
  if (!match_ci(s.c_str(), &i, "memor")) return false;
  if (!match_ci(s.c_str(), &i, "y") && !match_ci(s.c_str(), &i, "ies"))
    return false;
  skip_ws(s, &i);
  if (i < s.size() && (s[i] == '?' || s[i] == '.' || s[i] == '!')) i++;
  skip_ws(s, &i);
  return i == s.size();
}

/* ^\s*(what\s+do\s+you\s+remember\s+about|recall)\s+(.+?)\s*[?.!]?\s*$ */
bool match_recall_memory(const std::string &s, std::string *topic) {
  size_t i = 0;
  skip_ws(s, &i);
  size_t save = i;
  bool matched = false;
  if (match_ci(s.c_str(), &i, "what") && isspace((unsigned char)s[i])) {
    skip_ws(s, &i);
    if (match_ci(s.c_str(), &i, "do") && isspace((unsigned char)s[i])) {
      skip_ws(s, &i);
      if (match_ci(s.c_str(), &i, "you") && isspace((unsigned char)s[i])) {
        skip_ws(s, &i);
        if (match_ci(s.c_str(), &i, "remember") &&
            isspace((unsigned char)s[i])) {
          skip_ws(s, &i);
          if (match_ci(s.c_str(), &i, "about") &&
              isspace((unsigned char)s[i])) {
            matched = true;
          }
        }
      }
    }
  }
  if (!matched) {
    i = save;
    if (!match_ci(s.c_str(), &i, "recall")) return false;
    if (i >= s.size() || !isspace((unsigned char)s[i])) return false;
  }
  skip_ws(s, &i);
  size_t begin = i;
  size_t end = s.size();
  while (end > begin && isspace((unsigned char)s[end - 1])) end--;
  if (end > begin && (s[end - 1] == '?' || s[end - 1] == '.' || s[end - 1] == '!'))
    end--;
  while (end > begin && isspace((unsigned char)s[end - 1])) end--;
  if (end <= begin) return false;
  *topic = s.substr(begin, end - begin);
  return true;
}

/* ^\s*edit\s+memory\s+(mem-[0-9]{8})\s+to\s+(.+?)\s*$ */
bool match_edit_memory(const std::string &s, std::string *mem_id,
                       std::string *new_text) {
  size_t i = 0;
  skip_ws(s, &i);
  if (!match_ci(s.c_str(), &i, "edit")) return false;
  if (i >= s.size() || !isspace((unsigned char)s[i])) return false;
  skip_ws(s, &i);
  if (!match_ci(s.c_str(), &i, "memory")) return false;
  if (i >= s.size() || !isspace((unsigned char)s[i])) return false;
  skip_ws(s, &i);
  if (i + 12 > s.size()) return false;
  if (tolower((unsigned char)s[i]) != 'm' || tolower((unsigned char)s[i + 1]) != 'e' ||
      tolower((unsigned char)s[i + 2]) != 'm' || s[i + 3] != '-')
    return false;
  for (int k = 0; k < 8; k++)
    if (!isdigit((unsigned char)s[i + 4 + k])) return false;
  *mem_id = s.substr(i, 12);
  for (auto &c : *mem_id) c = (char)tolower((unsigned char)c); /* casefold */
  i += 12;
  if (i >= s.size() || !isspace((unsigned char)s[i])) return false;
  skip_ws(s, &i);
  if (!match_ci(s.c_str(), &i, "to")) return false;
  if (i >= s.size() || !isspace((unsigned char)s[i])) return false;
  skip_ws(s, &i);
  if (i >= s.size()) return false;
  *new_text = s.substr(i);
  return true;
}

/* ^\s*(delete|forget)\s+memory\s+(mem-[0-9]{8})\s*[.!]?\s*$ */
bool match_delete_memory(const std::string &s, std::string *mem_id) {
  size_t i = 0;
  skip_ws(s, &i);
  if (!match_ci(s.c_str(), &i, "delete") && !match_ci(s.c_str(), &i, "forget"))
    return false;
  if (i >= s.size() || !isspace((unsigned char)s[i])) return false;
  skip_ws(s, &i);
  if (!match_ci(s.c_str(), &i, "memory")) return false;
  if (i >= s.size() || !isspace((unsigned char)s[i])) return false;
  skip_ws(s, &i);
  if (i + 12 > s.size()) return false;
  if (tolower((unsigned char)s[i]) != 'm' || tolower((unsigned char)s[i + 1]) != 'e' ||
      tolower((unsigned char)s[i + 2]) != 'm' || s[i + 3] != '-')
    return false;
  for (int k = 0; k < 8; k++)
    if (!isdigit((unsigned char)s[i + 4 + k])) return false;
  *mem_id = s.substr(i, 12);
  for (auto &c : *mem_id) c = (char)tolower((unsigned char)c);
  i += 12;
  skip_ws(s, &i);
  if (i < s.size() && (s[i] == '.' || s[i] == '!')) i++;
  skip_ws(s, &i);
  return i == s.size();
}

void persist_memory() {
  if (!g_info.memory_persistent || g_state_path.empty()) return;
  if (!acmem::store_save(g_state_path.c_str(), *g_memory)) {
    printf("MEAS {\"phase\":\"memory\",\"event\":\"persist_failed\","
           "\"path\":\"%s\"}\n",
           g_state_path.c_str());
    g_errors++;
    return;
  }
  g_info.service_generation += 1;
}

void memory_status(const ProtocolMessage &req, const char *operation,
                   bool success, const std::vector<std::string> &ids,
                   const std::string &detail) {
  ProtocolMessage &out = resp_scratch();
  make_response(req, MsgType::MEMORY_STATUS, "memory", &out);
  out.poolPut(operation, strlen(operation), out.p.memory_status.operation);
  out.p.memory_status.success = success;
  out.p.memory_status.memory_ids.count = 0;
  for (const auto &id : ids) {
    if (out.p.memory_status.memory_ids.count >= 32) break;
    out.poolPut(id.data(), id.size(),
                out.p.memory_status.memory_ids
                    .items[out.p.memory_status.memory_ids.count++]);
  }
  out.poolPut(detail.data(), detail.size(), out.p.memory_status.detail);
  send_response(out);
}

/* Returns true when the text was handled as a user-memory command. */
bool user_memory_messages(const ProtocolMessage &req, const std::string &text) {
  const std::string user_id = to_cpp(req.session_id);
  const std::string authz =
      req.has_request_id ? to_cpp(req.request_id) : to_cpp(req.message_id);
  const std::string source_id =
      "session:" + user_id + ":" + to_cpp(req.message_id);

  std::string remembered = acmem::explicit_remember_payload(text);
  if (!remembered.empty()) {
    acmem::Record r;
    acmem::MemoryError e = g_user_mem->write(user_id, remembered, authz,
                                             source_id, 500, &r);
    std::vector<std::string> ids;
    std::string detail;
    if (!e) {
      ids.push_back(r.memory_id);
      detail = "explicit user assertion stored";
      persist_memory();
    } else {
      detail = e.detail;
    }
    memory_status(req, "WRITE_USER_MEMORY", !e, ids, detail);
    return true;
  }
  if (match_list_memory(text)) {
    std::vector<std::string> ids;
    std::string detail;
    for (const auto &r : g_user_mem->list(user_id)) {
      ids.push_back(r.memory_id);
      if (!detail.empty()) detail += "; ";
      detail += r.memory_id + ": " + r.payload.text;
    }
    if (detail.empty()) detail = "no user memories";
    memory_status(req, "LIST_USER_MEMORY", true, ids, detail);
    return true;
  }
  std::string topic;
  if (match_recall_memory(text, &topic)) {
    std::vector<std::string> ids;
    std::string detail;
    for (const auto &r : g_user_mem->search(user_id, topic)) {
      ids.push_back(r.memory_id);
      if (!detail.empty()) detail += "; ";
      detail += "USER_ASSERTED " + r.memory_id + ": " + r.payload.text;
    }
    if (detail.empty()) detail = "no matching user memory";
    memory_status(req, "SEARCH_USER_MEMORY", true, ids, detail);
    return true;
  }
  std::string mem_id, new_text;
  if (match_edit_memory(text, &mem_id, &new_text)) {
    acmem::Record r;
    acmem::MemoryError e =
        g_user_mem->edit(user_id, mem_id, new_text, authz, &r);
    std::vector<std::string> ids;
    if (!e) {
      ids.push_back(r.memory_id);
      persist_memory();
    }
    memory_status(req, "EDIT_USER_MEMORY", !e, ids, e ? e.detail : "");
    return true;
  }
  if (match_delete_memory(text, &mem_id)) {
    acmem::Record r;
    acmem::MemoryError e = g_user_mem->erase(user_id, mem_id, authz, &r);
    std::vector<std::string> ids;
    std::string detail;
    if (!e) {
      ids.push_back(r.memory_id);
      detail = "memory tombstoned";
      persist_memory();
    } else {
      detail = e.detail;
    }
    memory_status(req, "DELETE_USER_MEMORY", !e, ids, detail);
    return true;
  }
  return false;
}

/* ------------------------- health / capabilities -------------------------- */

void send_health(const ProtocolMessage &req) {
  ProtocolMessage &out = resp_scratch();
  make_response(req, MsgType::HEALTH, "health", &out);
  const char *status =
      g_ready ? (g_info.packv2_active ? "READY" : "DEGRADED_V14_LOOKUP")
              : "STARTING";
  out.poolPut(status, strlen(status), out.p.health.status);
  const char *ver = "15.0-p4-native";
  out.poolPut(ver, strlen(ver), out.p.health.runtime_version);
  out.p.health.service_generation = (int64_t)g_info.service_generation;
  send_response(out);
}

void send_capabilities(const ProtocolMessage &req) {
  ProtocolMessage &out = resp_scratch();
  make_response(req, MsgType::CAPABILITIES, "capabilities", &out);
  const char *pv = "aethercore-tactility.v2";
  const char *hw = "WAVESHARE_ESP32_P4_WIFI6_ACCESSORY_SKU_32020";
#if CONFIG_AC_LINK_USB_CDC_DEVICE
  const char *transport = "USB_CDC_ACM";
#elif CONFIG_AC_LINK_UART_FALLBACK
  const char *transport = "UART_STREAM";
#else
  const char *transport = "DEPRECATED_TCP_DIAGNOSTIC";
#endif
  out.poolPut(pv, strlen(pv), out.p.capabilities.protocol_version);
  out.poolPut(hw, strlen(hw), out.p.capabilities.hardware_class);
  const char *tools[] = {"SEARCH_KNOWLEDGE", "REPORT_RESULT"};
  out.p.capabilities.tools.count = 0;
  for (const char *t : tools)
    out.poolPut(t, strlen(t),
                out.p.capabilities.tools.items[out.p.capabilities.tools.count++]);
  const char *specs[] = {"semantic-address-v2", "cog-controller-v14"};
  out.p.capabilities.specialists.count = 0;
  for (const char *t : specs)
    out.poolPut(t, strlen(t),
                out.p.capabilities.specialists
                    .items[out.p.capabilities.specialists.count++]);
  const char *unavail[] = {"host-worktree", "host-build", "host-test-runner",
                           "automatic-integration"};
  out.p.capabilities.unavailable.count = 0;
  for (const char *t : unavail)
    out.poolPut(t, strlen(t),
                out.p.capabilities.unavailable
                    .items[out.p.capabilities.unavailable.count++]);
  out.poolPut(transport, strlen(transport), out.p.capabilities.transport);
  send_response(out);
}

/* ------------------------- vertical dispatch ------------------------------ */

void handle_query(const ProtocolMessage &req, const std::string &text) {
  ServiceResponse r = g_core.Query(to_cpp(req.session_id), text);
  if (r.disposition == "CLARIFY") {
    ProtocolMessage &out = resp_scratch();
    make_response(req, MsgType::CLARIFICATION_REQUEST, "response", &out);
    out.poolPut(r.clarify_question.data(), r.clarify_question.size(),
                out.p.clarification_request.question);
    out.p.clarification_request.choice_count = 0;
    for (const auto &c : r.clarify_choices) {
      if (out.p.clarification_request.choice_count >=
          aethercore::protocol_v2::kMaxChoices)
        break;
      out.poolPut(c.data(), c.size(),
                  out.p.clarification_request
                      .choices[out.p.clarification_request.choice_count++]);
    }
    send_response(out);
    emit_meas("clarify", req, "");
    return;
  }
  ProtocolMessage &delta = resp_scratch();
  make_response(req, MsgType::ASSISTANT_TEXT_DELTA, "response", &delta);
  delta.poolPut(r.text.data(), r.text.size(), delta.p.assistant_text_delta.text);
  delta.p.assistant_text_delta.final = true;
  send_response(delta);
  if (!r.evidence_handle_ids.empty()) {
    ProtocolMessage &ev = resp_scratch();
    make_response(req, MsgType::EVIDENCE_SUMMARY, "evidence", &ev);
    ev.p.evidence_summary.handle_ids.count = 0;
    for (const auto &h : r.evidence_handle_ids) {
      if (ev.p.evidence_summary.handle_ids.count >= 32) break;
      ev.poolPut(h.data(), h.size(),
                 ev.p.evidence_summary.handle_ids
                     .items[ev.p.evidence_summary.handle_ids.count++]);
    }
    const char *summary = "Exact evidence handles used by the accepted plan.";
    ev.poolPut(summary, strlen(summary), ev.p.evidence_summary.summary);
    send_response(ev);
  }
  emit_meas(r.disposition == "ANSWER" ? "answer" : "abstain", req,
            r.has_failure ? r.failure_reason.c_str() : "");
}

}  // namespace

/* ------------------------- public API ------------------------------------- */

bool service_init(const char *knowledge_path, const char *state_path,
                  const int8_t *policy_weights, size_t policy_weight_count,
                  const RuntimeInfo &info, char *err, size_t err_cap) {
  g_info = info;
  g_state_path = state_path ? state_path : "";
  /* Knowledge records (fail closed). */
  FILE *f = fopen(knowledge_path, "rb");
  if (!f) {
    snprintf(err, err_cap, "knowledge file missing: %s", knowledge_path);
    return false;
  }
  std::string json;
  char buf[4096];
  size_t n;
  while ((n = fread(buf, 1, sizeof(buf), f)) > 0) {
    json.append(buf, n);
    if (json.size() > (1u << 20)) {
      fclose(f);
      snprintf(err, err_cap, "knowledge file too large");
      return false;
    }
  }
  fclose(f);
  std::vector<aethercore::service::GroundedRecord> records;
  std::string perr;
  if (!aethercore::service::ParseGroundedRecordsJson(json.data(), json.size(),
                                                     &records, &perr)) {
    snprintf(err, err_cap, "knowledge parse: %s", perr.c_str());
    return false;
  }
  std::string cerr;
  if (!g_core.Init(std::move(records), policy_weights, policy_weight_count,
                   &cerr)) {
    snprintf(err, err_cap, "service core init: %s", cerr.c_str());
    return false;
  }
  /* Memory: load persisted state or start fresh (loud either way). */
  static acmem::Manager memory_static;
  static acmem::UserMemory user_static(&memory_static);
  g_memory = &memory_static;
  g_user_mem = &user_static;
  acmem::MemoryError le{};
  if (!g_state_path.empty() &&
      acmem::store_load(g_state_path.c_str(), g_memory, &le)) {
    g_info.memory_persistent = true;
    printf("MEAS {\"phase\":\"memory\",\"event\":\"loaded\",\"records\":%zu,"
           "\"epoch\":%llu}\n",
           g_memory->records(true).size(),
           (unsigned long long)g_memory->epoch());
  } else {
    g_info.memory_persistent = false;
    printf("MEAS {\"phase\":\"memory\",\"event\":\"session_only\","
           "\"detail\":\"%s\"}\n",
           le.detail.empty() ? "no state file" : le.detail.c_str());
  }
  g_ready = true;
  return true;
}

void service_set_response_sink(ResponseSink sink, void *ctx) {
  g_sink = sink;
  g_sink_ctx = ctx;
}

void service_handle_message(ac::link::Ac20Type type, uint32_t request_id,
                            uint32_t session_id, const uint8_t *body,
                            size_t body_len) {
  /* JSON body is authoritative for protocol ids; AC20 ids are echoed on
   * responses for transport-level correlation on Device A. */
  g_cur_request_id = request_id;
  g_cur_session_id = session_id;
  g_requests++;
  if (!g_ready) return;
  /* Wrap the body in the stream-codec length prefix for DecodeFrame. */
  if (body_len > aethercore::protocol_v2::kMaxFrameBytes) {
    g_errors++;
    return;
  }
  static uint8_t frame[aethercore::protocol_v2::kMaxEncodedFrame];
  frame[0] = (uint8_t)(body_len >> 24);
  frame[1] = (uint8_t)(body_len >> 16);
  frame[2] = (uint8_t)(body_len >> 8);
  frame[3] = (uint8_t)(body_len);
  memcpy(frame + 4, body, body_len);
  ProtocolMessage &msg = decode_scratch();
  DecodeError e = aethercore::protocol_v2::DecodeFrame(frame, body_len + 4, msg);
  if (e != DecodeError::OK) {
    g_errors++;
    /* Best-effort ERROR reply needs a session/message id; without a valid
     * envelope we cannot form one, so the malformed frame is dropped loudly
     * on the debug console instead (Python raises; the wire stays clean). */
    printf("MEAS {\"phase\":\"service\",\"event\":\"decode_reject\","
           "\"error\":\"%s\",\"bytes\":%zu}\n",
           aethercore::protocol_v2::ToString(e), body_len);
    return;
  }
  if (!msg.protocol_version.equals("aethercore-tactility.v2")) {
    send_error(msg, "UNSUPPORTED_PROTOCOL", "unsupported protocol version", false);
    return;
  }
  switch (msg.type) {
    case MsgType::SESSION_OPEN:
    case MsgType::SESSION_RESUME:
      send_health(msg);
      send_capabilities(msg);
      emit_meas("session_open", msg, "");
      break;
    case MsgType::HEALTH:
      send_health(msg);
      break;
    case MsgType::CAPABILITIES:
      send_capabilities(msg);
      break;
    case MsgType::USER_TEXT: {
      std::string text = to_cpp(msg.p.user_text.text);
      if (user_memory_messages(msg, text)) break;
      handle_query(msg, text);
      break;
    }
    case MsgType::USER_CANCEL:
      handle_query(msg, "cancel");
      break;
    case MsgType::RESET:
      handle_query(msg, "reset");
      break;
    default:
      send_error(msg, "UNSUPPORTED_MESSAGE",
                 "message is not a legal client request", false);
      break;
  }
}

uint64_t service_requests(void) { return g_requests; }
uint64_t service_errors(void) { return g_errors; }

}  // namespace ac::runtime
