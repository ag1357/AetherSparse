#include "aethercore_runtime.h"

#include <algorithm>
#include <cstring>
#include <limits>

namespace {

constexpr uint8_t kSessionMagic[8] = {'A', 'E', 'S', 'S', 'V', '0', '1', '3'};

bool valid_workspace(const ac_workspace_v1 *workspace) {
  return workspace != nullptr && workspace->struct_size == sizeof(ac_workspace_v1) &&
         workspace->candidate_count <= AC_MAX_CANDIDATES &&
         workspace->selected_count <= AC_MAX_SELECTED;
}

bool candidate_before(const ac_candidate_v1 &left, const ac_candidate_v1 &right) {
  if (left.score_q15 != right.score_q15) {
    return left.score_q15 > right.score_q15;
  }
  return left.entity_id < right.entity_id;
}

uint32_t crc32(const uint8_t *data, size_t size) {
  uint32_t crc = 0xffffffffu;
  for (size_t index = 0; index < size; ++index) {
    crc ^= data[index];
    for (unsigned bit = 0; bit < 8; ++bit) {
      const uint32_t mask = static_cast<uint32_t>(-(static_cast<int32_t>(crc & 1u)));
      crc = (crc >> 1u) ^ (0xedb88320u & mask);
    }
  }
  return ~crc;
}

class Writer {
 public:
  Writer(uint8_t *output, size_t capacity) : output_(output), capacity_(capacity) {}

  bool bytes(const void *value, size_t count) {
    if (cursor_ + count > capacity_) return false;
    std::memcpy(output_ + cursor_, value, count);
    cursor_ += count;
    return true;
  }

  bool u32(uint32_t value) {
    uint8_t encoded[4] = {
        static_cast<uint8_t>(value), static_cast<uint8_t>(value >> 8u),
        static_cast<uint8_t>(value >> 16u), static_cast<uint8_t>(value >> 24u)};
    return bytes(encoded, sizeof(encoded));
  }

  bool i32(int32_t value) { return u32(static_cast<uint32_t>(value)); }

  bool u64(uint64_t value) {
    uint8_t encoded[8];
    for (unsigned offset = 0; offset < 8; ++offset) {
      encoded[offset] = static_cast<uint8_t>(value >> (offset * 8u));
    }
    return bytes(encoded, sizeof(encoded));
  }

  size_t size() const { return cursor_; }

 private:
  uint8_t *output_;
  size_t capacity_;
  size_t cursor_ = 0;
};

class Reader {
 public:
  Reader(const uint8_t *input, size_t size) : input_(input), size_(size) {}

  bool bytes(void *value, size_t count) {
    if (cursor_ + count > size_) return false;
    std::memcpy(value, input_ + cursor_, count);
    cursor_ += count;
    return true;
  }

  bool u32(uint32_t *value) {
    uint8_t encoded[4];
    if (!bytes(encoded, sizeof(encoded))) return false;
    *value = static_cast<uint32_t>(encoded[0]) |
             (static_cast<uint32_t>(encoded[1]) << 8u) |
             (static_cast<uint32_t>(encoded[2]) << 16u) |
             (static_cast<uint32_t>(encoded[3]) << 24u);
    return true;
  }

  bool i32(int32_t *value) {
    uint32_t raw = 0;
    if (!u32(&raw)) return false;
    *value = static_cast<int32_t>(raw);
    return true;
  }

  bool u64(uint64_t *value) {
    uint8_t encoded[8];
    if (!bytes(encoded, sizeof(encoded))) return false;
    *value = 0;
    for (unsigned offset = 0; offset < 8; ++offset) {
      *value |= static_cast<uint64_t>(encoded[offset]) << (offset * 8u);
    }
    return true;
  }

 private:
  const uint8_t *input_;
  size_t size_;
  size_t cursor_ = 0;
};

}  // namespace

static_assert(sizeof(ac_candidate_v1) == 16, "candidate ABI drift");
static_assert(sizeof(ac_workspace_v1) == 648, "workspace ABI drift");
static_assert(sizeof(ac_session_v1) == 872, "session ABI drift");

extern "C" {

uint32_t ac_abi_version(void) { return AC_ABI_VERSION; }
size_t ac_workspace_size_v1(void) { return sizeof(ac_workspace_v1); }
size_t ac_session_size_v1(void) { return sizeof(ac_session_v1); }
size_t ac_session_serialized_size_v1(void) { return AC_SESSION_SERIALIZED_BYTES; }

ac_status_v1 ac_workspace_init_v1(ac_workspace_v1 *workspace) {
  if (workspace == nullptr) return AC_INVALID_ARGUMENT;
  std::memset(workspace, 0, sizeof(*workspace));
  workspace->struct_size = sizeof(*workspace);
  return AC_OK;
}

ac_status_v1 ac_session_init_v1(ac_session_v1 *session, const char *session_id) {
  if (session == nullptr || session_id == nullptr) return AC_INVALID_ARGUMENT;
  const size_t length = std::strlen(session_id);
  if (length == 0 || length >= AC_SESSION_ID_BYTES) return AC_INVALID_ARGUMENT;
  std::memset(session, 0, sizeof(*session));
  session->struct_size = sizeof(*session);
  session->abi_version = AC_ABI_VERSION;
  std::memcpy(session->session_id, session_id, length);
  return ac_workspace_init_v1(&session->workspace);
}

ac_status_v1 ac_union_candidates_v1(ac_workspace_v1 *workspace,
                                     const ac_candidate_v1 *incoming,
                                     size_t incoming_count) {
  if (!valid_workspace(workspace) || (incoming == nullptr && incoming_count != 0)) {
    return AC_INVALID_ARGUMENT;
  }
  for (size_t source = 0; source < incoming_count; ++source) {
    if (incoming[source].entity_id == 0) return AC_INVALID_ARGUMENT;
    bool already_aggregated = false;
    for (size_t earlier = 0; earlier < source; ++earlier) {
      if (incoming[earlier].entity_id == incoming[source].entity_id) {
        already_aggregated = true;
        break;
      }
    }
    if (already_aggregated) continue;
    ac_candidate_v1 aggregate = incoming[source];
    for (size_t later = source + 1; later < incoming_count; ++later) {
      if (incoming[later].entity_id == aggregate.entity_id) {
        aggregate.score_q15 = std::max(aggregate.score_q15, incoming[later].score_q15);
        aggregate.evidence_mask |= incoming[later].evidence_mask;
      }
    }
    bool merged = false;
    for (uint32_t target = 0; target < workspace->candidate_count; ++target) {
      if (workspace->candidates[target].entity_id == aggregate.entity_id) {
        workspace->candidates[target].score_q15 =
            std::max(workspace->candidates[target].score_q15, aggregate.score_q15);
        workspace->candidates[target].evidence_mask |= aggregate.evidence_mask;
        merged = true;
        break;
      }
    }
    if (!merged) {
      if (workspace->candidate_count < AC_MAX_CANDIDATES) {
        workspace->candidates[workspace->candidate_count++] = aggregate;
      } else {
        const auto worst = std::max_element(
            workspace->candidates, workspace->candidates + workspace->candidate_count,
            candidate_before);
        if (candidate_before(aggregate, *worst)) *worst = aggregate;
      }
    }
  }
  std::sort(workspace->candidates,
            workspace->candidates + workspace->candidate_count,
            candidate_before);
  return AC_OK;
}

uint32_t ac_legal_action_mask_v1(const ac_workspace_v1 *workspace) {
  if (!valid_workspace(workspace) || (workspace->flags & AC_WORKSPACE_TERMINAL) != 0u ||
      workspace->step_count >= 64u) {
    return 0;
  }
  uint32_t mask = (1u << AC_ACTION_SEARCH_KNOWLEDGE) | (1u << AC_ACTION_ABSTAIN);
  if (workspace->candidate_count > 0u && workspace->selected_count < AC_MAX_SELECTED) {
    mask |= 1u << AC_ACTION_SELECT_EVIDENCE;
  }
  if (workspace->selected_count > 0u) mask |= 1u << AC_ACTION_BUILD_PLAN;
  if ((workspace->flags & AC_WORKSPACE_PLAN_READY) != 0u) {
    mask |= 1u << AC_ACTION_VERIFY_PLAN;
  }
  if ((workspace->flags & AC_WORKSPACE_VERIFIED) != 0u) mask |= 1u << AC_ACTION_ANSWER;
  if (workspace->candidate_count > 1u && workspace->selected_count == 0u) {
    mask |= 1u << AC_ACTION_ASK_CLARIFICATION;
  }
  return mask;
}

ac_status_v1 ac_policy_select_v1(const ac_linear_policy_v1 *policy,
                                 const int16_t *features,
                                 uint32_t legal_action_mask,
                                 uint32_t *selected_action,
                                 int64_t *selected_logit) {
  if (policy == nullptr || features == nullptr || selected_action == nullptr ||
      selected_logit == nullptr || policy->struct_size != sizeof(*policy) ||
      policy->weights == nullptr || policy->bias == nullptr || policy->feature_count == 0u ||
      policy->feature_count > AC_POLICY_MAX_FEATURES || policy->action_count == 0u ||
      policy->action_count > AC_POLICY_MAX_ACTIONS) {
    return AC_INVALID_ARGUMENT;
  }
  bool found = false;
  int64_t best = std::numeric_limits<int64_t>::min();
  uint32_t best_action = 0;
  for (uint32_t action = 0; action < policy->action_count; ++action) {
    if ((legal_action_mask & (1u << action)) == 0u) continue;
    int64_t logit = policy->bias[action];
    const size_t base = static_cast<size_t>(action) * policy->feature_count;
    for (uint32_t feature = 0; feature < policy->feature_count; ++feature) {
      logit += static_cast<int64_t>(policy->weights[base + feature]) * features[feature];
    }
    if (!found || logit > best) {
      found = true;
      best = logit;
      best_action = action;
    }
  }
  if (!found) return AC_NO_LEGAL_ACTION;
  *selected_action = best_action;
  *selected_logit = best;
  return AC_OK;
}

ac_status_v1 ac_execute_action_v1(ac_workspace_v1 *workspace,
                                  uint32_t action,
                                  uint64_t argument_id) {
  if (!valid_workspace(workspace) || action >= AC_POLICY_MAX_ACTIONS) {
    return AC_INVALID_ARGUMENT;
  }
  const uint32_t legal = ac_legal_action_mask_v1(workspace);
  if ((legal & (1u << action)) == 0u) {
    ++workspace->invalid_action_count;
    return AC_INVALID_STATE;
  }
  if (action == AC_ACTION_SELECT_EVIDENCE) {
    const ac_candidate_v1 *candidate = nullptr;
    for (uint32_t index = 0; index < workspace->candidate_count; ++index) {
      if (workspace->candidates[index].entity_id == argument_id) {
        candidate = &workspace->candidates[index];
        break;
      }
    }
    if (candidate == nullptr || candidate->evidence_mask == 0u) {
      ++workspace->invalid_action_count;
      return AC_INVALID_ARGUMENT;
    }
    for (uint32_t index = 0; index < workspace->selected_count; ++index) {
      if (workspace->selected_entity_ids[index] == argument_id) {
        ++workspace->invalid_action_count;
        return AC_INVALID_STATE;
      }
    }
    workspace->selected_entity_ids[workspace->selected_count++] = argument_id;
  } else if (action == AC_ACTION_BUILD_PLAN) {
    workspace->flags |= AC_WORKSPACE_PLAN_READY;
    workspace->flags &= ~AC_WORKSPACE_VERIFIED;
  } else if (action == AC_ACTION_VERIFY_PLAN) {
    bool supported = workspace->selected_count > 0u;
    for (uint32_t selected = 0; selected < workspace->selected_count; ++selected) {
      bool found = false;
      for (uint32_t candidate = 0; candidate < workspace->candidate_count; ++candidate) {
        if (workspace->selected_entity_ids[selected] ==
                workspace->candidates[candidate].entity_id &&
            workspace->candidates[candidate].evidence_mask != 0u) {
          found = true;
          break;
        }
      }
      supported = supported && found;
    }
    if (!supported) {
      ++workspace->invalid_action_count;
      return AC_INVALID_STATE;
    }
    workspace->flags |= AC_WORKSPACE_VERIFIED;
  } else if (action == AC_ACTION_ANSWER) {
    workspace->flags |= AC_WORKSPACE_TERMINAL;
    workspace->terminal_disposition = AC_TERMINAL_ANSWER;
  } else if (action == AC_ACTION_ASK_CLARIFICATION) {
    workspace->flags |= AC_WORKSPACE_TERMINAL;
    workspace->terminal_disposition = AC_TERMINAL_CLARIFICATION;
  } else if (action == AC_ACTION_ABSTAIN) {
    workspace->flags |= AC_WORKSPACE_TERMINAL;
    workspace->terminal_disposition = AC_TERMINAL_ABSTAIN;
  }
  workspace->last_action = action;
  ++workspace->step_count;
  return AC_OK;
}

ac_status_v1 ac_session_serialize_v1(const ac_session_v1 *session,
                                     uint8_t *output,
                                     size_t output_size,
                                     size_t *written) {
  if (written != nullptr) *written = AC_SESSION_SERIALIZED_BYTES;
  if (session == nullptr || output == nullptr || session->struct_size != sizeof(*session) ||
      session->abi_version != AC_ABI_VERSION || !valid_workspace(&session->workspace) ||
      session->active_entity_count > AC_SESSION_ENTITY_CAP ||
      session->pending_clarification_count > AC_SESSION_CLARIFICATION_CAP) {
    return AC_INVALID_ARGUMENT;
  }
  if (output_size < AC_SESSION_SERIALIZED_BYTES) return AC_BUFFER_TOO_SMALL;
  Writer writer(output, output_size);
  if (!writer.bytes(kSessionMagic, sizeof(kSessionMagic)) || !writer.u32(AC_ABI_VERSION) ||
      !writer.bytes(session->session_id, AC_SESSION_ID_BYTES) || !writer.u64(session->turn_id) ||
      !writer.u32(session->active_entity_count)) {
    return AC_BUFFER_TOO_SMALL;
  }
  for (uint64_t value : session->active_entity_ids) writer.u64(value);
  writer.u32(session->pending_clarification_count);
  for (uint64_t value : session->pending_clarification_ids) writer.u64(value);
  for (uint64_t value : session->recent_utterance_hashes) writer.u64(value);
  writer.u32(session->workspace.candidate_count);
  for (const ac_candidate_v1 &candidate : session->workspace.candidates) {
    writer.u64(candidate.entity_id);
    writer.i32(candidate.score_q15);
    writer.u32(candidate.evidence_mask);
  }
  writer.u32(session->workspace.selected_count);
  for (uint64_t value : session->workspace.selected_entity_ids) writer.u64(value);
  writer.u32(session->workspace.last_action);
  writer.u32(session->workspace.step_count);
  writer.u32(session->workspace.invalid_action_count);
  writer.u32(session->workspace.flags);
  writer.u32(session->workspace.terminal_disposition);
  if (writer.size() != AC_SESSION_SERIALIZED_BYTES - sizeof(uint32_t)) return AC_ABI_MISMATCH;
  writer.u32(crc32(output, writer.size()));
  return writer.size() == AC_SESSION_SERIALIZED_BYTES ? AC_OK : AC_ABI_MISMATCH;
}

ac_status_v1 ac_session_deserialize_v1(const uint8_t *payload,
                                       size_t payload_size,
                                       ac_session_v1 *session) {
  if (payload == nullptr || session == nullptr) return AC_INVALID_ARGUMENT;
  if (payload_size != AC_SESSION_SERIALIZED_BYTES) return AC_INVALID_ARGUMENT;
  const uint32_t expected_crc = static_cast<uint32_t>(payload[payload_size - 4]) |
      (static_cast<uint32_t>(payload[payload_size - 3]) << 8u) |
      (static_cast<uint32_t>(payload[payload_size - 2]) << 16u) |
      (static_cast<uint32_t>(payload[payload_size - 1]) << 24u);
  if (crc32(payload, payload_size - 4) != expected_crc) return AC_CHECKSUM_MISMATCH;
  Reader reader(payload, payload_size - 4);
  uint8_t magic[8];
  uint32_t version = 0;
  if (!reader.bytes(magic, sizeof(magic)) || std::memcmp(magic, kSessionMagic, 8) != 0 ||
      !reader.u32(&version)) {
    return AC_INVALID_ARGUMENT;
  }
  if (version != AC_ABI_VERSION) return AC_ABI_MISMATCH;
  ac_session_v1 decoded{};
  decoded.struct_size = sizeof(decoded);
  decoded.abi_version = version;
  decoded.workspace.struct_size = sizeof(decoded.workspace);
  if (!reader.bytes(decoded.session_id, AC_SESSION_ID_BYTES) || !reader.u64(&decoded.turn_id) ||
      !reader.u32(&decoded.active_entity_count)) {
    return AC_INVALID_ARGUMENT;
  }
  for (uint64_t &value : decoded.active_entity_ids) if (!reader.u64(&value)) return AC_INVALID_ARGUMENT;
  if (!reader.u32(&decoded.pending_clarification_count)) return AC_INVALID_ARGUMENT;
  for (uint64_t &value : decoded.pending_clarification_ids) if (!reader.u64(&value)) return AC_INVALID_ARGUMENT;
  for (uint64_t &value : decoded.recent_utterance_hashes) if (!reader.u64(&value)) return AC_INVALID_ARGUMENT;
  if (!reader.u32(&decoded.workspace.candidate_count)) return AC_INVALID_ARGUMENT;
  for (ac_candidate_v1 &candidate : decoded.workspace.candidates) {
    if (!reader.u64(&candidate.entity_id) || !reader.i32(&candidate.score_q15) ||
        !reader.u32(&candidate.evidence_mask)) return AC_INVALID_ARGUMENT;
  }
  if (!reader.u32(&decoded.workspace.selected_count)) return AC_INVALID_ARGUMENT;
  for (uint64_t &value : decoded.workspace.selected_entity_ids) if (!reader.u64(&value)) return AC_INVALID_ARGUMENT;
  if (!reader.u32(&decoded.workspace.last_action) ||
      !reader.u32(&decoded.workspace.step_count) ||
      !reader.u32(&decoded.workspace.invalid_action_count) ||
      !reader.u32(&decoded.workspace.flags) ||
      !reader.u32(&decoded.workspace.terminal_disposition)) return AC_INVALID_ARGUMENT;
  if (!valid_workspace(&decoded.workspace) ||
      decoded.active_entity_count > AC_SESSION_ENTITY_CAP ||
      decoded.pending_clarification_count > AC_SESSION_CLARIFICATION_CAP ||
      decoded.session_id[AC_SESSION_ID_BYTES - 1] != '\0') return AC_INVALID_STATE;
  *session = decoded;
  return AC_OK;
}

}  // extern "C"
