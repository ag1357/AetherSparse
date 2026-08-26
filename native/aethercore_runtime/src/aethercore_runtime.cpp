#include "aethercore_runtime.h"

#include <algorithm>
#include <cstring>
#include <limits>

namespace {

constexpr uint8_t kSessionMagic[8] = {'A', 'E', 'S', 'S', 'V', '0', '1', '3'};
constexpr uint8_t kCogMagic[8] = {'A', 'C', 'O', 'G', 'V', '0', '1', '4'};

bool valid_workspace(const ac_workspace_v1 *workspace) {
  return workspace != nullptr && workspace->struct_size == sizeof(ac_workspace_v1) &&
         workspace->candidate_count <= AC_MAX_CANDIDATES &&
         workspace->selected_count <= AC_MAX_SELECTED;
}

bool selected_id(const ac_workspace_v1 *workspace, uint64_t entity_id) {
  for (uint32_t index = 0; index < workspace->selected_count; ++index) {
    if (workspace->selected_entity_ids[index] == entity_id) return true;
  }
  return false;
}

bool valid_session_state(const ac_session_v1 &session) {
  const ac_workspace_v1 &workspace = session.workspace;
  if (session.active_entity_count > AC_SESSION_ENTITY_CAP ||
      session.pending_clarification_count > AC_SESSION_CLARIFICATION_CAP ||
      !valid_workspace(&workspace) || session.session_id[0] == '\0' ||
      session.session_id[AC_SESSION_ID_BYTES - 1] != '\0') {
    return false;
  }
  bool found_nul = false;
  for (uint32_t index = 0; index < AC_SESSION_ID_BYTES; ++index) {
    if (session.session_id[index] == '\0') found_nul = true;
    if (found_nul && session.session_id[index] != '\0') return false;
  }
  for (uint32_t index = session.active_entity_count; index < AC_SESSION_ENTITY_CAP; ++index) {
    if (session.active_entity_ids[index] != 0u) return false;
  }
  for (uint32_t index = session.pending_clarification_count;
       index < AC_SESSION_CLARIFICATION_CAP; ++index) {
    if (session.pending_clarification_ids[index] != 0u) return false;
  }
  for (uint32_t index = 0; index < workspace.candidate_count; ++index) {
    const ac_candidate_v1 &candidate = workspace.candidates[index];
    if (candidate.entity_id == 0u) return false;
    for (uint32_t prior = 0; prior < index; ++prior) {
      if (workspace.candidates[prior].entity_id == candidate.entity_id) return false;
    }
  }
  for (uint32_t index = workspace.candidate_count; index < AC_MAX_CANDIDATES; ++index) {
    const ac_candidate_v1 &candidate = workspace.candidates[index];
    if (candidate.entity_id != 0u || candidate.score_q15 != 0 ||
        candidate.evidence_mask != 0u) {
      return false;
    }
  }
  for (uint32_t index = 0; index < workspace.selected_count; ++index) {
    const uint64_t selected = workspace.selected_entity_ids[index];
    if (selected == 0u) return false;
    for (uint32_t prior = 0; prior < index; ++prior) {
      if (workspace.selected_entity_ids[prior] == selected) return false;
    }
    bool supported = false;
    for (uint32_t candidate = 0; candidate < workspace.candidate_count; ++candidate) {
      if (workspace.candidates[candidate].entity_id == selected &&
          workspace.candidates[candidate].evidence_mask != 0u) {
        supported = true;
        break;
      }
    }
    if (!supported) return false;
  }
  for (uint32_t index = workspace.selected_count; index < AC_MAX_SELECTED; ++index) {
    if (workspace.selected_entity_ids[index] != 0u) return false;
  }
  constexpr uint32_t kKnownWorkspaceFlags =
      AC_WORKSPACE_PLAN_READY | AC_WORKSPACE_VERIFIED | AC_WORKSPACE_TERMINAL;
  if ((workspace.flags & ~kKnownWorkspaceFlags) != 0u || workspace.step_count > 64u ||
      workspace.terminal_disposition > AC_TERMINAL_ABSTAIN) {
    return false;
  }
  const bool plan_ready = (workspace.flags & AC_WORKSPACE_PLAN_READY) != 0u;
  const bool verified = (workspace.flags & AC_WORKSPACE_VERIFIED) != 0u;
  const bool terminal = (workspace.flags & AC_WORKSPACE_TERMINAL) != 0u;
  if ((verified && !plan_ready) || (plan_ready && workspace.selected_count == 0u) ||
      (verified && workspace.selected_count == 0u) ||
      (terminal != (workspace.terminal_disposition != AC_TERMINAL_NONE)) ||
      (workspace.terminal_disposition == AC_TERMINAL_ANSWER && !verified)) {
    return false;
  }
  if (workspace.step_count == 0u) {
    if (workspace.selected_count != 0u || workspace.last_action != 0u || plan_ready ||
        verified || terminal || workspace.terminal_disposition != AC_TERMINAL_NONE) {
      return false;
    }
  } else if (workspace.last_action > AC_ACTION_ABSTAIN) {
    return false;
  }
  return true;
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

void fnv_u8(uint64_t *state, uint8_t value) {
  *state ^= value;
  *state *= UINT64_C(1099511628211);
}

void fnv_u32(uint64_t *state, uint32_t value) {
  for (unsigned offset = 0; offset < 4; ++offset) {
    fnv_u8(state, static_cast<uint8_t>(value >> (offset * 8u)));
  }
}

void fnv_u64(uint64_t *state, uint64_t value) {
  for (unsigned offset = 0; offset < 8; ++offset) {
    fnv_u8(state, static_cast<uint8_t>(value >> (offset * 8u)));
  }
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

  bool u16(uint16_t value) {
    uint8_t encoded[2] = {
        static_cast<uint8_t>(value), static_cast<uint8_t>(value >> 8u)};
    return bytes(encoded, sizeof(encoded));
  }

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

  bool u16(uint16_t *value) {
    uint8_t encoded[2];
    if (!bytes(encoded, sizeof(encoded))) return false;
    *value = static_cast<uint16_t>(encoded[0]) |
             static_cast<uint16_t>(static_cast<uint16_t>(encoded[1]) << 8u);
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


  size_t cursor() const { return cursor_; }
  size_t size() const { return size_; }

 private:
  const uint8_t *input_;
  size_t size_;
  size_t cursor_ = 0;
};

}  // namespace

static_assert(sizeof(ac_candidate_v1) == 16, "candidate ABI drift");
static_assert(sizeof(ac_workspace_v1) == 648, "workspace ABI drift");
static_assert(sizeof(ac_session_v1) == 872, "session ABI drift");
static_assert(sizeof(ac_cog_summary_v1) == 48, "COG summary ABI drift");
static_assert(sizeof(ac_5c_constraint_v1) == 32, "5C constraint ABI drift");
static_assert(sizeof(ac_5c_state_v1) == 64, "5C state ABI drift");
static_assert(sizeof(ac_5c_request_v1) == 16, "5C request ABI drift");
static_assert(sizeof(ac_specialist_descriptor_v1) == 72, "specialist ABI drift");
static_assert(sizeof(ac_specialist_summary_v1) == 16, "specialist summary ABI drift");
static_assert(sizeof(ac_progress_v1) == 48, "progress ABI drift");

extern "C" {

uint32_t ac_abi_version(void) { return AC_ABI_VERSION; }
size_t ac_workspace_size_v1(void) { return sizeof(ac_workspace_v1); }
size_t ac_session_size_v1(void) { return sizeof(ac_session_v1); }
size_t ac_session_serialized_size_v1(void) { return AC_SESSION_SERIALIZED_BYTES; }
size_t ac_cog_summary_size_v1(void) { return sizeof(ac_cog_summary_v1); }
size_t ac_5c_constraint_size_v1(void) { return sizeof(ac_5c_constraint_v1); }
size_t ac_5c_state_size_v1(void) { return sizeof(ac_5c_state_v1); }
size_t ac_specialist_descriptor_size_v1(void) {
  return sizeof(ac_specialist_descriptor_v1);
}
size_t ac_progress_size_v1(void) { return sizeof(ac_progress_v1); }
size_t ac_cog_runtime_serialized_size_v1(void) {
  return AC_COG_RUNTIME_SERIALIZED_BYTES;
}

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
  if ((workspace->flags & (AC_WORKSPACE_VERIFIED | AC_WORKSPACE_TERMINAL)) != 0u) {
    return AC_INVALID_STATE;
  }
  for (size_t source = 0; source < incoming_count; ++source) {
    if (incoming[source].entity_id == 0u) return AC_INVALID_ARGUMENT;
  }
  for (size_t source = 0; source < incoming_count; ++source) {
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
        ac_candidate_v1 *worst = nullptr;
        for (uint32_t index = 0; index < workspace->candidate_count; ++index) {
          ac_candidate_v1 *candidate = &workspace->candidates[index];
          if (selected_id(workspace, candidate->entity_id)) continue;
          if (worst == nullptr || candidate_before(*worst, *candidate)) worst = candidate;
        }
        if (worst != nullptr && candidate_before(aggregate, *worst)) *worst = aggregate;
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

ac_status_v1 ac_policy_validate_i8_v2(const ac_int8_policy_v2 *policy) {
  if (policy == nullptr || policy->struct_size != sizeof(*policy) ||
      policy->weights == nullptr ||
      policy->feature_count == 0u || policy->feature_count > AC_POLICY_V2_MAX_FEATURES ||
      policy->action_count == 0u || policy->action_count > AC_POLICY_V2_MAX_ACTIONS ||
      policy->parameter_count !=
          static_cast<uint32_t>(policy->feature_count) * policy->action_count) {
    return AC_INVALID_ARGUMENT;
  }
  return AC_OK;
}

uint32_t ac_policy_macs_i8_v2(const ac_int8_policy_v2 *policy) {
  return ac_policy_validate_i8_v2(policy) == AC_OK ? policy->parameter_count : 0u;
}

ac_status_v1 ac_policy_score_candidate_i8_v2(const ac_int8_policy_v2 *policy,
                                              uint32_t action_index,
                                              const int16_t *features,
                                              int64_t *logit) {
  if (ac_policy_validate_i8_v2(policy) != AC_OK || features == nullptr ||
      logit == nullptr || action_index >= policy->action_count) {
    return AC_INVALID_ARGUMENT;
  }
  int64_t score = policy->bias == nullptr ? 0 : policy->bias[action_index];
  const size_t base = static_cast<size_t>(action_index) * policy->feature_count;
  for (uint32_t feature = 0; feature < policy->feature_count; ++feature) {
    score += static_cast<int64_t>(policy->weights[base + feature]) * features[feature];
  }
  *logit = score;
  return AC_OK;
}

ac_status_v1 ac_policy_select_i8_v2(const ac_int8_policy_v2 *policy,
                                    const int16_t *features,
                                    uint64_t legal_action_mask,
                                    uint32_t *selected_action,
                                    int64_t *selected_logit) {
  if (ac_policy_validate_i8_v2(policy) != AC_OK || features == nullptr ||
      selected_action == nullptr || selected_logit == nullptr) {
    return AC_INVALID_ARGUMENT;
  }
  bool found = false;
  int64_t best = std::numeric_limits<int64_t>::min();
  uint32_t best_action = 0;
  for (uint32_t action = 0; action < policy->action_count; ++action) {
    if ((legal_action_mask & (uint64_t{1} << action)) == 0u) continue;
    int64_t logit = 0;
    const ac_status_v1 scored =
        ac_policy_score_candidate_i8_v2(policy, action, features, &logit);
    if (scored != AC_OK) return scored;
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

ac_status_v1 ac_5c_digest_v1(const ac_5c_constraint_v1 *constraints,
                             size_t constraint_count,
                             uint64_t *digest_low,
                             uint64_t *digest_high) {
  if ((constraints == nullptr && constraint_count != 0u) || digest_low == nullptr ||
      digest_high == nullptr) {
    return AC_INVALID_ARGUMENT;
  }
  uint64_t low = UINT64_C(14695981039346656037);
  uint64_t high = UINT64_C(7809847782465536322);
  for (size_t index = 0; index < constraint_count; ++index) {
    const ac_5c_constraint_v1 &item = constraints[index];
    fnv_u32(&low, item.constraint_id);
    fnv_u8(&low, item.kind);
    fnv_u8(&low, item.effect);
    fnv_u8(&low, item.flags);
    fnv_u8(&low, item.reserved8);
    fnv_u64(&low, item.action_mask);
    fnv_u32(&low, item.capability_mask);
    fnv_u32(&low, item.required_flags);
    fnv_u32(&low, static_cast<uint32_t>(item.minimum_value));
    fnv_u32(&low, static_cast<uint32_t>(item.maximum_value));
    /* A different order/seed makes accidental paired collisions substantially harder. */
    fnv_u32(&high, static_cast<uint32_t>(item.maximum_value));
    fnv_u32(&high, static_cast<uint32_t>(item.minimum_value));
    fnv_u32(&high, item.required_flags);
    fnv_u32(&high, item.capability_mask);
    fnv_u64(&high, item.action_mask);
    fnv_u8(&high, item.reserved8);
    fnv_u8(&high, item.flags);
    fnv_u8(&high, item.effect);
    fnv_u8(&high, item.kind);
    fnv_u32(&high, item.constraint_id);
  }
  *digest_low = low;
  *digest_high = high;
  return AC_OK;
}

ac_status_v1 ac_5c_check_v1(const ac_5c_state_v1 *state,
                            const ac_5c_constraint_v1 *constraints,
                            size_t constraint_count,
                            const ac_5c_request_v1 *request,
                            uint32_t *allowed,
                            uint32_t *violated_constraint_id) {
  if (allowed != nullptr) *allowed = 0u;
  if (violated_constraint_id != nullptr) *violated_constraint_id = 0u;
  if (state == nullptr || request == nullptr || allowed == nullptr ||
      violated_constraint_id == nullptr || state->struct_size != sizeof(*state) ||
      state->schema_version != AC_5C_SCHEMA_VERSION || request->action >= 64u ||
      constraint_count != state->constraint_count ||
      (constraints == nullptr && constraint_count != 0u)) {
    return AC_INVALID_ARGUMENT;
  }
  if ((state->flags & AC_5C_STATE_FAIL_CLOSED) != 0u && constraint_count == 0u) {
    return AC_INVALID_STATE;
  }
  uint64_t digest_low = 0;
  uint64_t digest_high = 0;
  if (ac_5c_digest_v1(constraints, constraint_count, &digest_low, &digest_high) != AC_OK ||
      digest_low != state->immutable_digest_low ||
      digest_high != state->immutable_digest_high) {
    return AC_CHECKSUM_MISMATCH;
  }
  for (size_t index = 0; index < constraint_count; ++index) {
    const ac_5c_constraint_v1 &constraint = constraints[index];
    if (constraint.kind > AC_5C_FAIL_CLOSED || constraint.effect > AC_5C_EFFECT_CAPABILITY_SUBSET) {
      return AC_INVALID_ARGUMENT;
    }
    if ((constraint.action_mask & (uint64_t{1} << request->action)) == 0u) continue;
    bool violated = false;
    if (constraint.effect == AC_5C_EFFECT_DENY) {
      violated = true;
    } else if (constraint.effect == AC_5C_EFFECT_REQUIRE_FLAGS) {
      violated = (request->flags & constraint.required_flags) != constraint.required_flags;
    } else if (constraint.effect == AC_5C_EFFECT_LIMIT) {
      violated = request->metric_value < constraint.minimum_value ||
                 request->metric_value > constraint.maximum_value;
    } else if (constraint.effect == AC_5C_EFFECT_CAPABILITY_SUBSET) {
      violated = (request->capability_mask & ~constraint.capability_mask) != 0u;
    }
    if (violated) {
      *violated_constraint_id = constraint.constraint_id;
      return AC_OK;
    }
  }
  *allowed = 1u;
  return AC_OK;
}

ac_status_v1 ac_specialist_summarize_v1(
    const ac_specialist_descriptor_v1 *descriptors,
    size_t descriptor_count,
    uint32_t ram_budget_bytes,
    ac_specialist_summary_v1 *summary) {
  if (summary == nullptr || (descriptors == nullptr && descriptor_count != 0u)) {
    return AC_INVALID_ARGUMENT;
  }
  ac_specialist_summary_v1 result{};
  for (size_t index = 0; index < descriptor_count; ++index) {
    const ac_specialist_descriptor_v1 &descriptor = descriptors[index];
    if (descriptor.struct_size != sizeof(descriptor) ||
        descriptor.kind > AC_SPECIALIST_HYBRID ||
        descriptor.activation_state > AC_ACTIVATION_HOT || descriptor.specialist_id == 0u) {
      return AC_INVALID_ARGUMENT;
    }
    if (descriptor.activation_state == AC_ACTIVATION_COLD) {
      ++result.cold_count;
    } else {
      if (descriptor.ram_requirement_bytes >
          std::numeric_limits<uint32_t>::max() - result.resident_ram_bytes) {
        return AC_INVALID_STATE;
      }
      result.resident_ram_bytes += descriptor.ram_requirement_bytes;
      if (descriptor.activation_state == AC_ACTIVATION_WARM) {
        ++result.warm_count;
      } else {
        ++result.hot_count;
      }
    }
  }
  if (result.resident_ram_bytes > ram_budget_bytes) return AC_INVALID_STATE;
  *summary = result;
  return AC_OK;
}

ac_status_v1 ac_progress_record_v1(ac_progress_v1 *progress,
                                   uint32_t action,
                                   uint32_t error_signature,
                                   uint16_t open_obligations,
                                   uint16_t completed_obligations,
                                   uint16_t new_evidence,
                                   uint16_t new_hypothesis,
                                   uint16_t frontier_expansion,
                                   uint16_t verifier_state,
                                   uint16_t rollback_count) {
  if (progress == nullptr || progress->struct_size != sizeof(*progress)) {
    return AC_INVALID_ARGUMENT;
  }
  const bool prior = progress->reserved[0] != 0u;
  const bool repeated = prior && progress->last_action == action && error_signature != 0u &&
                        progress->repeated_error_signature == error_signature;
  const bool made_progress = completed_obligations > progress->completed_obligations ||
                             open_obligations < progress->open_obligations ||
                             new_evidence != 0u || new_hypothesis != 0u ||
                             frontier_expansion != 0u || rollback_count != 0u;
  if (repeated && progress->repeated_action_count != std::numeric_limits<uint16_t>::max()) {
    ++progress->repeated_action_count;
  }
  if (repeated && !made_progress) {
    if (progress->stagnation_cycles != std::numeric_limits<uint16_t>::max()) {
      ++progress->stagnation_cycles;
    }
    if (progress->stagnation_cycles >= 3u) progress->flags |= AC_PROGRESS_STAGNATED;
  } else {
    progress->stagnation_cycles = 0u;
    progress->flags &= static_cast<uint16_t>(~AC_PROGRESS_STAGNATED);
  }
  progress->open_obligations = open_obligations;
  progress->completed_obligations = completed_obligations;
  progress->new_evidence_count = static_cast<uint16_t>(
      std::min<uint32_t>(std::numeric_limits<uint16_t>::max(),
                         progress->new_evidence_count + new_evidence));
  progress->new_hypothesis_count = static_cast<uint16_t>(
      std::min<uint32_t>(std::numeric_limits<uint16_t>::max(),
                         progress->new_hypothesis_count + new_hypothesis));
  progress->frontier_expansion_count = static_cast<uint16_t>(
      std::min<uint32_t>(std::numeric_limits<uint16_t>::max(),
                         progress->frontier_expansion_count + frontier_expansion));
  progress->verifier_state = verifier_state;
  progress->rollback_count = static_cast<uint16_t>(
      std::min<uint32_t>(std::numeric_limits<uint16_t>::max(),
                         progress->rollback_count + rollback_count));
  progress->last_action = action;
  progress->repeated_error_signature = error_signature;
  if (progress->reserved[0] != std::numeric_limits<uint32_t>::max()) {
    ++progress->reserved[0];
  }
  return AC_OK;
}

ac_status_v1 ac_cog_runtime_serialize_v1(
    const ac_cog_summary_v1 *cog,
    const ac_5c_state_v1 *five_c,
    const ac_progress_v1 *progress,
    const ac_specialist_summary_v1 *specialists,
    uint8_t *output,
    size_t output_size,
    size_t *written) {
  if (written != nullptr) *written = AC_COG_RUNTIME_SERIALIZED_BYTES;
  if (cog == nullptr || five_c == nullptr || progress == nullptr || specialists == nullptr ||
      output == nullptr || cog->struct_size != sizeof(*cog) ||
      cog->schema_version != AC_COG_SCHEMA_VERSION ||
      five_c->struct_size != sizeof(*five_c) ||
      five_c->schema_version != AC_5C_SCHEMA_VERSION ||
      progress->struct_size != sizeof(*progress)) {
    return AC_INVALID_ARGUMENT;
  }
  if (output_size < AC_COG_RUNTIME_SERIALIZED_BYTES) return AC_BUFFER_TOO_SMALL;
  Writer writer(output, output_size);
  if (!writer.bytes(kCogMagic, sizeof(kCogMagic)) || !writer.u32(AC_ABI_VERSION)) {
    return AC_BUFFER_TOO_SMALL;
  }
#define AC_WRITE_U16(field) if (!writer.u16(field)) return AC_BUFFER_TOO_SMALL
#define AC_WRITE_U32(field) if (!writer.u32(field)) return AC_BUFFER_TOO_SMALL
#define AC_WRITE_U64(field) if (!writer.u64(field)) return AC_BUFFER_TOO_SMALL
  AC_WRITE_U16(cog->schema_version);
  AC_WRITE_U16(cog->open_goals);
  AC_WRITE_U16(cog->mandatory_open);
  AC_WRITE_U16(cog->mandatory_satisfied);
  AC_WRITE_U16(cog->blocked_or_failed);
  AC_WRITE_U16(cog->invariant_violations);
  AC_WRITE_U16(cog->active_hypotheses);
  AC_WRITE_U16(cog->competing_hypotheses);
  AC_WRITE_U16(cog->contradictions);
  AC_WRITE_U16(cog->evidence_count);
  AC_WRITE_U16(cog->unresolved_count);
  AC_WRITE_U16(cog->open_frontier);
  AC_WRITE_U16(cog->observed_state_count);
  AC_WRITE_U16(cog->completion_permille);
  AC_WRITE_U16(cog->stagnant_steps);
  AC_WRITE_U16(cog->repeated_error_count);
  AC_WRITE_U16(cog->repeated_action_count);
  AC_WRITE_U16(cog->verifier_state_code);
  AC_WRITE_U16(cog->halt_success_legal);
  for (uint16_t value : cog->reserved) AC_WRITE_U16(value);
  AC_WRITE_U16(five_c->schema_version);
  AC_WRITE_U16(five_c->constraint_count);
  AC_WRITE_U64(five_c->immutable_digest_low);
  AC_WRITE_U64(five_c->immutable_digest_high);
  AC_WRITE_U32(five_c->flags);
  AC_WRITE_U32(five_c->violation_count);
  AC_WRITE_U32(five_c->last_violation_id);
  for (uint32_t value : five_c->reserved) AC_WRITE_U32(value);
  AC_WRITE_U16(progress->open_obligations);
  AC_WRITE_U16(progress->completed_obligations);
  AC_WRITE_U16(progress->new_evidence_count);
  AC_WRITE_U16(progress->new_hypothesis_count);
  AC_WRITE_U16(progress->frontier_expansion_count);
  AC_WRITE_U16(progress->repeated_action_count);
  AC_WRITE_U16(progress->verifier_state);
  AC_WRITE_U16(progress->rollback_count);
  AC_WRITE_U32(progress->repeated_error_signature);
  AC_WRITE_U16(progress->stagnation_cycles);
  AC_WRITE_U16(progress->flags);
  AC_WRITE_U32(progress->last_action);
  for (uint32_t value : progress->reserved) AC_WRITE_U32(value);
  AC_WRITE_U32(specialists->cold_count);
  AC_WRITE_U32(specialists->warm_count);
  AC_WRITE_U32(specialists->hot_count);
  AC_WRITE_U32(specialists->resident_ram_bytes);
#undef AC_WRITE_U16
#undef AC_WRITE_U32
#undef AC_WRITE_U64
  if (writer.size() != AC_COG_RUNTIME_SERIALIZED_BYTES - sizeof(uint32_t)) {
    return AC_ABI_MISMATCH;
  }
  writer.u32(crc32(output, writer.size()));
  return writer.size() == AC_COG_RUNTIME_SERIALIZED_BYTES ? AC_OK : AC_ABI_MISMATCH;
}

ac_status_v1 ac_cog_runtime_deserialize_v1(
    const uint8_t *payload,
    size_t payload_size,
    ac_cog_summary_v1 *cog,
    ac_5c_state_v1 *five_c,
    ac_progress_v1 *progress,
    ac_specialist_summary_v1 *specialists) {
  if (payload == nullptr || cog == nullptr || five_c == nullptr || progress == nullptr ||
      specialists == nullptr || payload_size != AC_COG_RUNTIME_SERIALIZED_BYTES) {
    return AC_INVALID_ARGUMENT;
  }
  const uint32_t expected_crc = static_cast<uint32_t>(payload[payload_size - 4]) |
      (static_cast<uint32_t>(payload[payload_size - 3]) << 8u) |
      (static_cast<uint32_t>(payload[payload_size - 2]) << 16u) |
      (static_cast<uint32_t>(payload[payload_size - 1]) << 24u);
  if (crc32(payload, payload_size - 4) != expected_crc) return AC_CHECKSUM_MISMATCH;
  Reader reader(payload, payload_size - 4);
  uint8_t magic[8];
  uint32_t abi_version = 0;
  if (!reader.bytes(magic, sizeof(magic)) || std::memcmp(magic, kCogMagic, 8) != 0 ||
      !reader.u32(&abi_version)) {
    return AC_INVALID_ARGUMENT;
  }
  if (abi_version != AC_ABI_VERSION) return AC_ABI_MISMATCH;
  ac_cog_summary_v1 decoded_cog{};
  ac_5c_state_v1 decoded_five_c{};
  ac_progress_v1 decoded_progress{};
  ac_specialist_summary_v1 decoded_specialists{};
  decoded_cog.struct_size = sizeof(decoded_cog);
  decoded_five_c.struct_size = sizeof(decoded_five_c);
  decoded_progress.struct_size = sizeof(decoded_progress);
#define AC_READ_U16(field) if (!reader.u16(&(field))) return AC_INVALID_ARGUMENT
#define AC_READ_U32(field) if (!reader.u32(&(field))) return AC_INVALID_ARGUMENT
#define AC_READ_U64(field) if (!reader.u64(&(field))) return AC_INVALID_ARGUMENT
  AC_READ_U16(decoded_cog.schema_version);
  AC_READ_U16(decoded_cog.open_goals);
  AC_READ_U16(decoded_cog.mandatory_open);
  AC_READ_U16(decoded_cog.mandatory_satisfied);
  AC_READ_U16(decoded_cog.blocked_or_failed);
  AC_READ_U16(decoded_cog.invariant_violations);
  AC_READ_U16(decoded_cog.active_hypotheses);
  AC_READ_U16(decoded_cog.competing_hypotheses);
  AC_READ_U16(decoded_cog.contradictions);
  AC_READ_U16(decoded_cog.evidence_count);
  AC_READ_U16(decoded_cog.unresolved_count);
  AC_READ_U16(decoded_cog.open_frontier);
  AC_READ_U16(decoded_cog.observed_state_count);
  AC_READ_U16(decoded_cog.completion_permille);
  AC_READ_U16(decoded_cog.stagnant_steps);
  AC_READ_U16(decoded_cog.repeated_error_count);
  AC_READ_U16(decoded_cog.repeated_action_count);
  AC_READ_U16(decoded_cog.verifier_state_code);
  AC_READ_U16(decoded_cog.halt_success_legal);
  for (uint16_t &value : decoded_cog.reserved) AC_READ_U16(value);
  AC_READ_U16(decoded_five_c.schema_version);
  AC_READ_U16(decoded_five_c.constraint_count);
  AC_READ_U64(decoded_five_c.immutable_digest_low);
  AC_READ_U64(decoded_five_c.immutable_digest_high);
  AC_READ_U32(decoded_five_c.flags);
  AC_READ_U32(decoded_five_c.violation_count);
  AC_READ_U32(decoded_five_c.last_violation_id);
  for (uint32_t &value : decoded_five_c.reserved) AC_READ_U32(value);
  AC_READ_U16(decoded_progress.open_obligations);
  AC_READ_U16(decoded_progress.completed_obligations);
  AC_READ_U16(decoded_progress.new_evidence_count);
  AC_READ_U16(decoded_progress.new_hypothesis_count);
  AC_READ_U16(decoded_progress.frontier_expansion_count);
  AC_READ_U16(decoded_progress.repeated_action_count);
  AC_READ_U16(decoded_progress.verifier_state);
  AC_READ_U16(decoded_progress.rollback_count);
  AC_READ_U32(decoded_progress.repeated_error_signature);
  AC_READ_U16(decoded_progress.stagnation_cycles);
  AC_READ_U16(decoded_progress.flags);
  AC_READ_U32(decoded_progress.last_action);
  for (uint32_t &value : decoded_progress.reserved) AC_READ_U32(value);
  AC_READ_U32(decoded_specialists.cold_count);
  AC_READ_U32(decoded_specialists.warm_count);
  AC_READ_U32(decoded_specialists.hot_count);
  AC_READ_U32(decoded_specialists.resident_ram_bytes);
#undef AC_READ_U16
#undef AC_READ_U32
#undef AC_READ_U64
  constexpr uint32_t kKnownFiveCFlags = AC_5C_STATE_FAIL_CLOSED |
      AC_5C_STATE_VERIFIER_REQUIRED | AC_5C_STATE_ROLLBACK_REQUIRED;
  if (reader.cursor() != reader.size() ||
      decoded_cog.schema_version != AC_COG_SCHEMA_VERSION ||
      decoded_five_c.schema_version != AC_5C_SCHEMA_VERSION ||
      decoded_cog.completion_permille > 1000u || decoded_cog.halt_success_legal > 1u ||
      (decoded_progress.flags & ~AC_PROGRESS_STAGNATED) != 0u ||
      (decoded_five_c.flags & ~kKnownFiveCFlags) != 0u) {
    return AC_INVALID_STATE;
  }
  *cog = decoded_cog;
  *five_c = decoded_five_c;
  *progress = decoded_progress;
  *specialists = decoded_specialists;
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
      session->abi_version != AC_ABI_VERSION || !valid_session_state(*session)) {
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
  if (!valid_session_state(decoded)) return AC_INVALID_STATE;
  *session = decoded;
  return AC_OK;
}

}  // extern "C"
