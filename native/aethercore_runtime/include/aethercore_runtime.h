#ifndef AETHERCORE_RUNTIME_H
#define AETHERCORE_RUNTIME_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#if defined(_WIN32) && defined(AETHERCORE_RUNTIME_BUILD)
#define AC_API __declspec(dllexport)
#elif defined(_WIN32)
#define AC_API __declspec(dllimport)
#else
#define AC_API __attribute__((visibility("default")))
#endif

#define AC_ABI_VERSION 1u
#define AC_MAX_CANDIDATES 32u
#define AC_MAX_SELECTED 8u
#define AC_POLICY_MAX_FEATURES 64u
#define AC_POLICY_MAX_ACTIONS 32u
#define AC_SESSION_ID_BYTES 40u
#define AC_SESSION_ENTITY_CAP 8u
#define AC_SESSION_CLARIFICATION_CAP 4u
#define AC_SESSION_RECENT_HASH_CAP 8u
#define AC_SESSION_SERIALIZED_BYTES 836u
#define AC_COG_RUNTIME_SERIALIZED_BYTES 180u
#define AC_COG_SCHEMA_VERSION 1u
#define AC_5C_SCHEMA_VERSION 1u
#define AC_POLICY_V2_MAX_FEATURES 64u
#define AC_POLICY_V2_MAX_ACTIONS 64u

typedef enum ac_status_v1 {
  AC_OK = 0,
  AC_INVALID_ARGUMENT = 1,
  AC_INVALID_STATE = 2,
  AC_BUFFER_TOO_SMALL = 3,
  AC_NO_LEGAL_ACTION = 4,
  AC_CHECKSUM_MISMATCH = 5,
  AC_ABI_MISMATCH = 6
} ac_status_v1;

typedef enum ac_action_v1 {
  AC_ACTION_SEARCH_KNOWLEDGE = 0,
  AC_ACTION_SELECT_EVIDENCE = 1,
  AC_ACTION_BUILD_PLAN = 2,
  AC_ACTION_VERIFY_PLAN = 3,
  AC_ACTION_ANSWER = 4,
  AC_ACTION_ASK_CLARIFICATION = 5,
  AC_ACTION_ABSTAIN = 6
} ac_action_v1;

typedef enum ac_terminal_v1 {
  AC_TERMINAL_NONE = 0,
  AC_TERMINAL_ANSWER = 1,
  AC_TERMINAL_CLARIFICATION = 2,
  AC_TERMINAL_ABSTAIN = 3
} ac_terminal_v1;

typedef enum ac_5c_constraint_kind_v1 {
  AC_5C_ROOT_INVARIANT = 0,
  AC_5C_CAPABILITY_BOUNDARY = 1,
  AC_5C_PERMISSION_RULE = 2,
  AC_5C_VERIFIER_INTEGRITY = 3,
  AC_5C_RESOURCE_LIMIT = 4,
  AC_5C_PHYSICAL_HARD_LIMIT = 5,
  AC_5C_SELF_MODIFICATION_BOUNDARY = 6,
  AC_5C_ROLLBACK_REQUIREMENT = 7,
  AC_5C_FAIL_CLOSED = 8
} ac_5c_constraint_kind_v1;

typedef enum ac_5c_effect_v1 {
  AC_5C_EFFECT_DENY = 0,
  AC_5C_EFFECT_REQUIRE_FLAGS = 1,
  AC_5C_EFFECT_LIMIT = 2,
  AC_5C_EFFECT_CAPABILITY_SUBSET = 3
} ac_5c_effect_v1;

typedef enum ac_specialist_kind_v1 {
  AC_SPECIALIST_DETERMINISTIC = 0,
  AC_SPECIALIST_LEARNED = 1,
  AC_SPECIALIST_SHARED_LEARNED = 2,
  AC_SPECIALIST_SENSOR = 3,
  AC_SPECIALIST_ACTUATOR = 4,
  AC_SPECIALIST_TOOL = 5,
  AC_SPECIALIST_HYBRID = 6
} ac_specialist_kind_v1;

typedef enum ac_activation_state_v1 {
  AC_ACTIVATION_COLD = 0,
  AC_ACTIVATION_WARM = 1,
  AC_ACTIVATION_HOT = 2
} ac_activation_state_v1;

enum {
  AC_WORKSPACE_PLAN_READY = 1u << 0,
  AC_WORKSPACE_VERIFIED = 1u << 1,
  AC_WORKSPACE_TERMINAL = 1u << 2
};

enum {
  AC_5C_STATE_FAIL_CLOSED = 1u << 0,
  AC_5C_STATE_VERIFIER_REQUIRED = 1u << 1,
  AC_5C_STATE_ROLLBACK_REQUIRED = 1u << 2,
  AC_5C_REQUEST_EXTERNAL_AUTHORIZED = 1u << 0,
  AC_5C_REQUEST_SIGNED_UPDATE = 1u << 1,
  AC_5C_REQUEST_SANDBOXED = 1u << 2,
  AC_5C_REQUEST_TESTED = 1u << 3,
  AC_5C_REQUEST_ROLLBACK_AVAILABLE = 1u << 4,
  AC_PROGRESS_STAGNATED = 1u << 0
};

typedef struct ac_candidate_v1 {
  uint64_t entity_id;
  int32_t score_q15;
  uint32_t evidence_mask;
} ac_candidate_v1;

typedef struct ac_workspace_v1 {
  uint32_t struct_size;
  uint32_t candidate_count;
  ac_candidate_v1 candidates[AC_MAX_CANDIDATES];
  uint32_t selected_count;
  uint32_t selected_padding;
  uint64_t selected_entity_ids[AC_MAX_SELECTED];
  uint32_t last_action;
  uint32_t step_count;
  uint32_t invalid_action_count;
  uint32_t flags;
  uint32_t terminal_disposition;
  uint32_t reserved[8];
} ac_workspace_v1;

typedef struct ac_session_v1 {
  uint32_t struct_size;
  uint32_t abi_version;
  char session_id[AC_SESSION_ID_BYTES];
  uint64_t turn_id;
  uint64_t active_entity_ids[AC_SESSION_ENTITY_CAP];
  uint32_t active_entity_count;
  uint32_t pending_clarification_count;
  uint64_t pending_clarification_ids[AC_SESSION_CLARIFICATION_CAP];
  uint64_t recent_utterance_hashes[AC_SESSION_RECENT_HASH_CAP];
  ac_workspace_v1 workspace;
} ac_session_v1;

typedef struct ac_linear_policy_v1 {
  uint32_t struct_size;
  uint32_t feature_count;
  uint32_t action_count;
  const int8_t *weights; /* row-major: action_count x feature_count */
  const int32_t *bias;
} ac_linear_policy_v1;

/* Compact view of the authoritative Python COG. It carries no evidence text. */
typedef struct ac_cog_summary_v1 {
  uint32_t struct_size;
  uint16_t schema_version;
  uint16_t open_goals;
  uint16_t mandatory_open;
  uint16_t mandatory_satisfied;
  uint16_t blocked_or_failed;
  uint16_t invariant_violations;
  uint16_t active_hypotheses;
  uint16_t competing_hypotheses;
  uint16_t contradictions;
  uint16_t evidence_count;
  uint16_t unresolved_count;
  uint16_t open_frontier;
  uint16_t observed_state_count;
  uint16_t completion_permille;
  uint16_t stagnant_steps;
  uint16_t repeated_error_count;
  uint16_t repeated_action_count;
  uint16_t verifier_state_code;
  uint16_t halt_success_legal;
  uint16_t reserved[3];
} ac_cog_summary_v1;

typedef struct ac_5c_constraint_v1 {
  uint32_t constraint_id;
  uint8_t kind;
  uint8_t effect;
  uint8_t flags;
  uint8_t reserved8;
  uint64_t action_mask;
  uint32_t capability_mask;
  uint32_t required_flags;
  int32_t minimum_value;
  int32_t maximum_value;
} ac_5c_constraint_v1;

typedef struct ac_5c_state_v1 {
  uint32_t struct_size;
  uint16_t schema_version;
  uint16_t constraint_count;
  uint64_t immutable_digest_low;
  uint64_t immutable_digest_high;
  uint32_t flags;
  uint32_t violation_count;
  uint32_t last_violation_id;
  uint32_t reserved[7];
} ac_5c_state_v1;

typedef struct ac_5c_request_v1 {
  uint32_t action;
  uint32_t capability_mask;
  uint32_t flags;
  int32_t metric_value;
} ac_5c_request_v1;

typedef struct ac_specialist_descriptor_v1 {
  uint32_t struct_size;
  uint16_t kind;
  uint16_t activation_state;
  uint64_t specialist_id;
  uint64_t parameter_family_id;
  uint32_t input_schema_id;
  uint32_t output_schema_id;
  uint32_t activation_cost_ops;
  uint32_t ram_requirement_bytes;
  uint32_t storage_requirement_bytes;
  uint32_t expected_latency_us;
  uint64_t allowed_action_mask;
  uint64_t constraint_mask;
  uint32_t calibration_state_id;
  uint32_t provenance_behavior;
} ac_specialist_descriptor_v1;

typedef struct ac_specialist_summary_v1 {
  uint32_t cold_count;
  uint32_t warm_count;
  uint32_t hot_count;
  uint32_t resident_ram_bytes;
} ac_specialist_summary_v1;

typedef struct ac_progress_v1 {
  uint32_t struct_size;
  uint16_t open_obligations;
  uint16_t completed_obligations;
  uint16_t new_evidence_count;
  uint16_t new_hypothesis_count;
  uint16_t frontier_expansion_count;
  uint16_t repeated_action_count;
  uint16_t verifier_state;
  uint16_t rollback_count;
  uint32_t repeated_error_signature;
  uint16_t stagnation_cycles;
  uint16_t flags;
  uint32_t last_action;
  uint32_t reserved[4];
} ac_progress_v1;

/* V2 lifts the legal-action mask to 64 actions while retaining integer-only inference. */
typedef struct ac_int8_policy_v2 {
  uint32_t struct_size;
  uint16_t feature_count;
  uint16_t action_count;
  uint32_t parameter_count;
  uint32_t state_schema_id;
  uint64_t model_id;
  const int8_t *weights;
  const int32_t *bias; /* optional; NULL means exact zero bias */
} ac_int8_policy_v2;

AC_API uint32_t ac_abi_version(void);
AC_API size_t ac_workspace_size_v1(void);
AC_API size_t ac_session_size_v1(void);
AC_API size_t ac_session_serialized_size_v1(void);
AC_API size_t ac_cog_summary_size_v1(void);
AC_API size_t ac_5c_constraint_size_v1(void);
AC_API size_t ac_5c_state_size_v1(void);
AC_API size_t ac_specialist_descriptor_size_v1(void);
AC_API size_t ac_progress_size_v1(void);
AC_API size_t ac_cog_runtime_serialized_size_v1(void);
AC_API ac_status_v1 ac_workspace_init_v1(ac_workspace_v1 *workspace);
AC_API ac_status_v1 ac_session_init_v1(ac_session_v1 *session, const char *session_id);

/* Monotone canonical-ID union followed by one deterministic global K=32 cap. */
AC_API ac_status_v1 ac_union_candidates_v1(
    ac_workspace_v1 *workspace,
    const ac_candidate_v1 *incoming,
    size_t incoming_count);

/* Returns a bit mask over ac_action_v1. */
AC_API uint32_t ac_legal_action_mask_v1(const ac_workspace_v1 *workspace);

/* Integer linear policy; illegal actions are masked before deterministic argmax. */
AC_API ac_status_v1 ac_policy_select_v1(
    const ac_linear_policy_v1 *policy,
    const int16_t *features,
    uint32_t legal_action_mask,
    uint32_t *selected_action,
    int64_t *selected_logit);

/* Validate and execute the bound V14 int8 controller representation. */
AC_API ac_status_v1 ac_policy_validate_i8_v2(const ac_int8_policy_v2 *policy);
AC_API uint32_t ac_policy_macs_i8_v2(const ac_int8_policy_v2 *policy);
/* Score one action/argument candidate with its own feature vector.  The
 * controller uses this for argument-aware COG claim contrast before a stable
 * deterministic argmax over legal candidates. */
AC_API ac_status_v1 ac_policy_score_candidate_i8_v2(
    const ac_int8_policy_v2 *policy,
    uint32_t action_index,
    const int16_t *features,
    int64_t *logit);
AC_API ac_status_v1 ac_policy_select_i8_v2(
    const ac_int8_policy_v2 *policy,
    const int16_t *features,
    uint64_t legal_action_mask,
    uint32_t *selected_action,
    int64_t *selected_logit);

/* Const-only 5C evaluation; root state and constraints cannot be rewritten here. */
AC_API ac_status_v1 ac_5c_digest_v1(
    const ac_5c_constraint_v1 *constraints,
    size_t constraint_count,
    uint64_t *digest_low,
    uint64_t *digest_high);
AC_API ac_status_v1 ac_5c_check_v1(
    const ac_5c_state_v1 *state,
    const ac_5c_constraint_v1 *constraints,
    size_t constraint_count,
    const ac_5c_request_v1 *request,
    uint32_t *allowed,
    uint32_t *violated_constraint_id);

AC_API ac_status_v1 ac_specialist_summarize_v1(
    const ac_specialist_descriptor_v1 *descriptors,
    size_t descriptor_count,
    uint32_t ram_budget_bytes,
    ac_specialist_summary_v1 *summary);

/* Deterministic accounting; stagnation is asserted after three no-progress repeats. */
AC_API ac_status_v1 ac_progress_record_v1(
    ac_progress_v1 *progress,
    uint32_t action,
    uint32_t error_signature,
    uint16_t open_obligations,
    uint16_t completed_obligations,
    uint16_t new_evidence,
    uint16_t new_hypothesis,
    uint16_t frontier_expansion,
    uint16_t verifier_state,
    uint16_t rollback_count);

/* Canonical little-endian compact COG/5C/progress/specialist snapshot plus CRC-32. */
AC_API ac_status_v1 ac_cog_runtime_serialize_v1(
    const ac_cog_summary_v1 *cog,
    const ac_5c_state_v1 *five_c,
    const ac_progress_v1 *progress,
    const ac_specialist_summary_v1 *specialists,
    uint8_t *output,
    size_t output_size,
    size_t *written);

/* Exact bounded transition. argument_id is required only for SELECT_EVIDENCE. */
AC_API ac_status_v1 ac_execute_action_v1(
    ac_workspace_v1 *workspace,
    uint32_t action,
    uint64_t argument_id);

/* Canonical little-endian encoding with trailing CRC-32; never raw struct bytes. */
AC_API ac_status_v1 ac_session_serialize_v1(
    const ac_session_v1 *session,
    uint8_t *output,
    size_t output_size,
    size_t *written);
AC_API ac_status_v1 ac_session_deserialize_v1(
    const uint8_t *payload,
    size_t payload_size,
    ac_session_v1 *session);

#ifdef __cplusplus
}
#endif

#endif
