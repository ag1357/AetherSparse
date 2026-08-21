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

enum {
  AC_WORKSPACE_PLAN_READY = 1u << 0,
  AC_WORKSPACE_VERIFIED = 1u << 1,
  AC_WORKSPACE_TERMINAL = 1u << 2
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

AC_API uint32_t ac_abi_version(void);
AC_API size_t ac_workspace_size_v1(void);
AC_API size_t ac_session_size_v1(void);
AC_API size_t ac_session_serialized_size_v1(void);
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
