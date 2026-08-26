/* AetherCore V15 native memory subsystem (Phase 10), ESP32-P4 bounded port of
 * src/aethersparse/memory/{models,manager,user,persistence}.py.
 *
 * Faithful to the Python semantics with these documented device bounds:
 *  - All strings ASCII printable (0x20..0x7E); other input rejected. The
 *    Python canonical-JSON content hashes match byte-for-byte for ASCII.
 *  - Watermarks are configurable; device default long_term=256 (Python
 *    default 4096). Parity tests run with identical watermarks.
 *  - Persistence: canonical JSON + sha256 envelope + atomic rename, same
 *    "aethercore.memory.v1" schema as Python MemoryManagerState export.
 *
 * Semantic tiers (EPHEMERAL/SHORT_TERM/WORKING/LONG_TERM) are independent of
 * physical residency (COLD/WARM/HOT), exactly as in Python. User-memory CRUD
 * requires explicit one-shot authorization; cancel/reset of a session never
 * deletes long-term memory.
 */
#pragma once

#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace acmem {

enum class SemanticTier { EPHEMERAL = 0, SHORT_TERM, WORKING, LONG_TERM };
enum class Residency { COLD = 0, WARM, HOT };
enum class Authority {
  EXTERNAL_GROUNDED = 0,
  USER_ASSERTED,
  OBSERVATION,
  INFERENCE,
  SYSTEM,
  PROJECT_APPROVED,
  LEARNED_STATE,
};
enum class MemoryType {
  SCRATCH = 0,
  CANDIDATE_FEATURE,
  UNCOMMITTED_TOOL_RESULT,
  HYPOTHESIS,
  CONVERSATION_TURN,
  ACTIVE_REFERENCE,
  TASK_STATE,
  COG_ITEM,
  SELECTED_EVIDENCE,
  EXTERNAL_KNOWLEDGE,
  USER_MEMORY,
  LEARNED_SPECIALIST_STATE,
  PROJECT_KNOWLEDGE,
};
enum class Deletion { ACTIVE = 0, TOMBSTONED };

const char *to_str(SemanticTier v);
const char *to_str(Residency v);
const char *to_str(Authority v);
const char *to_str(MemoryType v);
const char *to_str(Deletion v);
bool tier_from_str(const char *s, SemanticTier *out);

struct Payload {
  std::string text; /* 1..4096 chars */
  bool negated = false;
  std::string quantity;    /* empty = null */
  std::string unit;        /* empty = null */
  uint32_t uncertainty_milli = 0;
  std::string perspective; /* empty = null */
};

struct Provenance {
  Authority authority = Authority::SYSTEM;
  std::string source_id; /* 1..256 */
  std::string evidence_handle; /* empty = null */
  std::vector<std::string> derivation_ids; /* <= 16 */
};

struct Record {
  std::string memory_id; /* 1..96 */
  MemoryType type = MemoryType::SCRATCH;
  SemanticTier tier = SemanticTier::EPHEMERAL;
  Residency residency = Residency::COLD;
  Provenance provenance;
  Payload payload;
  std::string source_evidence_handle; /* empty = null */
  uint64_t created_epoch = 0;
  uint64_t modified_epoch = 0;
  uint64_t last_access_epoch = 0;
  uint64_t access_count = 0;
  uint32_t confidence_milli = 1000;
  uint32_t salience_milli = 0;
  uint32_t novelty_milli = 0;
  int64_t expires_epoch = -1; /* -1 = null */
  std::string content_hash;   /* "sha256:<64 hex>" */
  std::vector<std::string> cog_bindings; /* <= 16 */
  std::string session_scope;             /* empty = null */
  std::string user_scope;                /* empty = null */
  Deletion deletion = Deletion::ACTIVE;
  bool pinned = false;
  bool verification_bound = false;
};

struct Watermarks {
  uint32_t ephemeral = 128;
  uint32_t short_term = 64;
  uint32_t working = 128;
  uint32_t long_term = 256; /* device bound; Python default 4096 */
};

struct JournalEntry {
  uint64_t epoch = 0;
  std::string operation; /* <= 32 */
  std::string memory_id;
  int from_tier = -1; /* SemanticTier or -1 */
  int to_tier = -1;
  std::string reason; /* <= 192 */
};

/* Canonical JSON (Python json.dumps sort_keys/separators compatible). */
std::string payload_canonical_json(const Payload &p);
std::string record_canonical_json(const Record &r);
std::string sha256_hex(const void *data, size_t len);
std::string sha256_hex(const std::string &s);

class MemoryError {
 public:
  enum Code {
    OK = 0,
    AUTH_REQUIRED,
    NOT_FOUND,
    INVALID,
    BOUND_EXHAUSTED,
    IMMUTABLE_TYPE,
  } code = OK;
  std::string detail;
  explicit operator bool() const { return code != OK; }
};

class Manager {
 public:
  explicit Manager(Watermarks wm = Watermarks{});

  /* Authorize exactly one user-memory mutation (one-shot token). */
  bool authorize_user_mutation(const std::string &authorization_id);

  MemoryError create(MemoryType type, SemanticTier tier, Residency residency,
                     const Payload &payload, const Provenance &provenance,
                     const std::string &source_evidence_handle,
                     int64_t ttl_epochs, uint32_t confidence_milli,
                     uint32_t salience_milli, uint32_t novelty_milli,
                     const std::vector<std::string> &cog_bindings,
                     const std::string &session_scope,
                     const std::string &user_scope, bool pinned,
                     bool verification_bound,
                     const std::string &authorization_id, Record *out);

  MemoryError get(const std::string &memory_id, bool include_deleted, Record *out);
  std::vector<Record> records(bool include_deleted) const; /* id-sorted */
  MemoryError promote(const std::string &memory_id, const std::string &reason,
                      Record *out);
  MemoryError demote(const std::string &memory_id, SemanticTier target,
                     const std::string &reason, Record *out);
  MemoryError set_residency(const std::string &memory_id, Residency r, Record *out);
  static uint64_t promotion_score(const Record &r);
  std::vector<Record> promotion_candidates(uint64_t minimum_score) const;
  MemoryError mark_verification_bound(const std::string &memory_id, Record *out);
  uint32_t reclaim_expired();
  uint32_t advance(uint64_t epochs);
  std::vector<Record> search_user(const std::string &user_scope,
                                  const std::string &query) const;
  MemoryError edit_user(const std::string &memory_id, const Payload &payload,
                        const std::string &authorization_id, Record *out);
  MemoryError delete_user(const std::string &memory_id,
                          const std::string &authorization_id, Record *out);
  uint32_t compact_tombstones(uint64_t before_epoch);

  uint64_t epoch() const { return epoch_; }
  uint64_t next_id() const { return next_id_; }
  const Watermarks &watermarks() const { return wm_; }
  const std::vector<JournalEntry> &journal() const { return journal_; }

  /* Canonical "aethercore.memory.v1" export (EPHEMERAL excluded, as Python). */
  std::string export_state_json() const;
  bool import_state_json(const std::string &json, MemoryError *err);

 private:
  uint64_t tick();
  void journal(const char *op, const std::string &id, int from_tier, int to_tier,
               const std::string &reason);
  MemoryError enforce_bound(SemanticTier tier);
  bool consume_authorization(const std::string &authorization_id);

  Watermarks wm_;
  uint64_t epoch_ = 0;
  uint64_t next_id_ = 1;
  std::map<std::string, Record> records_;
  std::vector<JournalEntry> journal_; /* tail, <= 128 */
  std::vector<std::string> authorizations_;
};

/* Authorized user-memory CRUD (UserMemoryService parity). */
class UserMemory {
 public:
  explicit UserMemory(Manager *m) : m_(m) {}
  std::vector<Record> list(const std::string &user_id) const;
  MemoryError read(const std::string &user_id, const std::string &memory_id,
                   Record *out);
  MemoryError write(const std::string &user_id, const std::string &text,
                    const std::string &authorization_id,
                    const std::string &source_id, uint32_t salience_milli,
                    Record *out);
  MemoryError edit(const std::string &user_id, const std::string &memory_id,
                   const std::string &text, const std::string &authorization_id,
                   Record *out);
  MemoryError erase(const std::string &user_id, const std::string &memory_id,
                    const std::string &authorization_id, Record *out);
  std::vector<Record> search(const std::string &user_id,
                             const std::string &query) const;

 private:
  Manager *m_;
};

/* Explicit "remember that X" matcher (case-insensitive, trailing period ok).
 * Returns the payload text, or empty when not an explicit remember request. */
std::string explicit_remember_payload(const std::string &text);

/* Bounded persistence: canonical JSON + sha256 envelope, atomic rename.
 * load() tolerates a missing file (fresh state); a malformed envelope or
 * integrity mismatch fails closed. */
bool store_save(const char *path, const Manager &m);
bool store_load(const char *path, Manager *m, MemoryError *err);

}  // namespace acmem
