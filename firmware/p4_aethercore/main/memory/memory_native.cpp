/* See memory_native.h. Ports src/aethersparse/memory Python semantics. */
#include "memory_native.h"

#include <algorithm>
#include <cctype>
#include <cstdio>
#include <cstring>

namespace acmem {

/* ------------------------- enums ------------------------- */

const char *to_str(SemanticTier v) {
  static const char *k[] = {"EPHEMERAL", "SHORT_TERM", "WORKING", "LONG_TERM"};
  return k[static_cast<int>(v)];
}
const char *to_str(Residency v) {
  static const char *k[] = {"COLD", "WARM", "HOT"};
  return k[static_cast<int>(v)];
}
const char *to_str(Authority v) {
  static const char *k[] = {"EXTERNAL_GROUNDED", "USER_ASSERTED", "OBSERVATION",
                            "INFERENCE",         "SYSTEM",        "PROJECT_APPROVED",
                            "LEARNED_STATE"};
  return k[static_cast<int>(v)];
}
const char *to_str(MemoryType v) {
  static const char *k[] = {"SCRATCH",
                            "CANDIDATE_FEATURE",
                            "UNCOMMITTED_TOOL_RESULT",
                            "HYPOTHESIS",
                            "CONVERSATION_TURN",
                            "ACTIVE_REFERENCE",
                            "TASK_STATE",
                            "COG_ITEM",
                            "SELECTED_EVIDENCE",
                            "EXTERNAL_KNOWLEDGE",
                            "USER_MEMORY",
                            "LEARNED_SPECIALIST_STATE",
                            "PROJECT_KNOWLEDGE"};
  return k[static_cast<int>(v)];
}
const char *to_str(Deletion v) { return v == Deletion::ACTIVE ? "ACTIVE" : "TOMBSTONED"; }

bool tier_from_str(const char *s, SemanticTier *out) {
  for (int i = 0; i < 4; i++)
    if (!strcmp(s, to_str(static_cast<SemanticTier>(i)))) {
      *out = static_cast<SemanticTier>(i);
      return true;
    }
  return false;
}

/* ------------------------- sha256 (portable, self-contained) ------------- */

namespace {
struct Sha256 {
  uint32_t h[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                   0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
  uint8_t buf[64];
  uint64_t len = 0;
  size_t used = 0;
  static uint32_t rotr(uint32_t x, int n) { return (x >> n) | (x << (32 - n)); }
  void block(const uint8_t *p) {
    static const uint32_t K[64] = {
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
        0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
        0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
        0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
        0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
        0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
        0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
        0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
        0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
        0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
        0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2};
    uint32_t w[64];
    for (int i = 0; i < 16; i++)
      w[i] = (uint32_t)p[i * 4] << 24 | (uint32_t)p[i * 4 + 1] << 16 |
             (uint32_t)p[i * 4 + 2] << 8 | p[i * 4 + 3];
    for (int i = 16; i < 64; i++) {
      uint32_t s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3);
      uint32_t s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10);
      w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    uint32_t a = h[0], b = h[1], c = h[2], d = h[3], e = h[4], f = h[5],
             g = h[6], hh = h[7];
    for (int i = 0; i < 64; i++) {
      uint32_t S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      uint32_t ch = (e & f) ^ (~e & g);
      uint32_t t1 = hh + S1 + ch + K[i] + w[i];
      uint32_t S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      uint32_t mj = (a & b) ^ (a & c) ^ (b & c);
      uint32_t t2 = S0 + mj;
      hh = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
    }
    h[0] += a; h[1] += b; h[2] += c; h[3] += d;
    h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
  }
  void update(const uint8_t *p, size_t n) {
    len += n;
    while (n) {
      size_t take = 64 - used;
      if (take > n) take = n;
      memcpy(buf + used, p, take);
      used += take;
      p += take;
      n -= take;
      if (used == 64) {
        block(buf);
        used = 0;
      }
    }
  }
  void final(uint8_t out[32]) {
    uint64_t bits = len * 8;
    uint8_t one = 0x80;
    update(&one, 1);
    uint8_t zero = 0;
    while (used != 56) update(&zero, 1);
    uint8_t lenb[8];
    for (int i = 0; i < 8; i++) lenb[i] = (uint8_t)(bits >> (56 - 8 * i));
    update(lenb, 8);
    for (int i = 0; i < 8; i++) {
      out[i * 4] = (uint8_t)(h[i] >> 24);
      out[i * 4 + 1] = (uint8_t)(h[i] >> 16);
      out[i * 4 + 2] = (uint8_t)(h[i] >> 8);
      out[i * 4 + 3] = (uint8_t)h[i];
    }
  }
};
}  // namespace

std::string sha256_hex(const void *data, size_t len) {
  Sha256 s;
  s.update((const uint8_t *)data, len);
  uint8_t d[32];
  s.final(d);
  char hex[65];
  for (int i = 0; i < 32; i++) snprintf(hex + i * 2, 3, "%02x", d[i]);
  return std::string(hex, 64);
}
std::string sha256_hex(const std::string &s) { return sha256_hex(s.data(), s.size()); }

/* ------------------------- canonical JSON -------------------------------- */
/* Python json.dumps(obj, sort_keys=True, separators=(",",":")) with
 * ensure_ascii=True. Input is bounded to printable ASCII at the API edges, so
 * escaping only needs the C0 set + quote + backslash. */

namespace {
void json_escape(std::string *out, const std::string &s) {
  out->push_back('"');
  for (char c : s) {
    switch (c) {
      case '"': out->append("\\\""); break;
      case '\\': out->append("\\\\"); break;
      case '\b': out->append("\\b"); break;
      case '\f': out->append("\\f"); break;
      case '\n': out->append("\\n"); break;
      case '\r': out->append("\\r"); break;
      case '\t': out->append("\\t"); break;
      default:
        if ((unsigned char)c < 0x20) {
          char tmp[8];
          snprintf(tmp, sizeof(tmp), "\\u%04x", c);
          out->append(tmp);
        } else {
          out->push_back(c);
        }
    }
  }
  out->push_back('"');
}
void json_opt_str(std::string *out, const std::string &s) {
  if (s.empty()) {
    out->append("null");
  } else {
    json_escape(out, s);
  }
}
void json_str_array(std::string *out, const std::vector<std::string> &v) {
  out->push_back('[');
  for (size_t i = 0; i < v.size(); i++) {
    if (i) out->push_back(',');
    json_escape(out, v[i]);
  }
  out->push_back(']');
}
}  // namespace

std::string payload_canonical_json(const Payload &p) {
  std::string o = "{";
  o += "\"negated\":";
  o += p.negated ? "true" : "false";
  o += ",\"perspective\":";
  json_opt_str(&o, p.perspective);
  o += ",\"quantity\":";
  json_opt_str(&o, p.quantity);
  o += ",\"text\":";
  json_escape(&o, p.text);
  o += ",\"uncertainty_milli\":" + std::to_string(p.uncertainty_milli);
  o += ",\"unit\":";
  json_opt_str(&o, p.unit);
  o += "}";
  return o;
}

std::string record_canonical_json(const Record &r) {
  std::string o = "{";
  o += "\"access_count\":" + std::to_string(r.access_count);
  o += ",\"cog_bindings\":";
  json_str_array(&o, r.cog_bindings);
  o += ",\"confidence_milli\":" + std::to_string(r.confidence_milli);
  o += ",\"content_hash\":";
  json_escape(&o, r.content_hash);
  o += ",\"created_epoch\":" + std::to_string(r.created_epoch);
  o += ",\"deletion_state\":";
  json_escape(&o, to_str(r.deletion));
  if (r.expires_epoch >= 0) {
    o += ",\"expires_epoch\":" + std::to_string((uint64_t)r.expires_epoch);
  } else {
    o += ",\"expires_epoch\":null";
  }
  o += ",\"last_access_epoch\":" + std::to_string(r.last_access_epoch);
  o += ",\"memory_id\":";
  json_escape(&o, r.memory_id);
  o += ",\"memory_type\":";
  json_escape(&o, to_str(r.type));
  o += ",\"modified_epoch\":" + std::to_string(r.modified_epoch);
  o += ",\"novelty_milli\":" + std::to_string(r.novelty_milli);
  o += ",\"payload\":";
  o += payload_canonical_json(r.payload);
  o += ",\"pinned\":";
  o += r.pinned ? "true" : "false";
  o += ",\"provenance\":{\"authority\":";
  json_escape(&o, to_str(r.provenance.authority));
  o += ",\"derivation_ids\":";
  json_str_array(&o, r.provenance.derivation_ids);
  o += ",\"evidence_handle\":";
  json_opt_str(&o, r.provenance.evidence_handle);
  o += ",\"source_id\":";
  json_escape(&o, r.provenance.source_id);
  o += "}";
  o += ",\"residency\":";
  json_escape(&o, to_str(r.residency));
  o += ",\"salience_milli\":" + std::to_string(r.salience_milli);
  o += ",\"semantic_tier\":";
  json_escape(&o, to_str(r.tier));
  o += ",\"session_scope\":";
  json_opt_str(&o, r.session_scope);
  o += ",\"source_evidence_handle\":";
  json_opt_str(&o, r.source_evidence_handle);
  o += ",\"user_scope\":";
  json_opt_str(&o, r.user_scope);
  o += ",\"verification_bound\":";
  o += r.verification_bound ? "true" : "false";
  o += "}";
  return o;
}

/* ------------------------- manager --------------------------------------- */

Manager::Manager(Watermarks wm) : wm_(wm) {}

bool Manager::authorize_user_mutation(const std::string &authorization_id) {
  if (authorization_id.empty() || authorization_id.size() > 128) return false;
  authorizations_.push_back(authorization_id);
  return true;
}

bool Manager::consume_authorization(const std::string &authorization_id) {
  for (size_t i = 0; i < authorizations_.size(); i++) {
    if (authorizations_[i] == authorization_id) {
      authorizations_.erase(authorizations_.begin() + (long)i);
      return true;
    }
  }
  return false;
}

uint64_t Manager::tick() {
  epoch_ += 1;
  reclaim_expired();
  return epoch_;
}

void Manager::journal(const char *op, const std::string &id, int from_tier,
                      int to_tier, const std::string &reason) {
  JournalEntry e;
  e.epoch = epoch_;
  e.operation = op;
  e.memory_id = id;
  e.from_tier = from_tier;
  e.to_tier = to_tier;
  e.reason = reason.substr(0, 192);
  journal_.push_back(e);
  if (journal_.size() > 128) journal_.erase(journal_.begin());
}

static bool valid_text(const std::string &s, size_t lo, size_t hi) {
  if (s.size() < lo || s.size() > hi) return false;
  for (unsigned char c : s)
    if (c < 0x20 || c > 0x7e) return false;
  return true;
}

MemoryError Manager::create(MemoryType type, SemanticTier tier, Residency residency,
                            const Payload &payload, const Provenance &provenance,
                            const std::string &source_evidence_handle,
                            int64_t ttl_epochs, uint32_t confidence_milli,
                            uint32_t salience_milli, uint32_t novelty_milli,
                            const std::vector<std::string> &cog_bindings,
                            const std::string &session_scope,
                            const std::string &user_scope, bool pinned,
                            bool verification_bound,
                            const std::string &authorization_id, Record *out) {
  if (type == MemoryType::USER_MEMORY &&
      !consume_authorization(authorization_id)) {
    return {MemoryError::AUTH_REQUIRED, "explicit user authorization is required"};
  }
  if (!valid_text(payload.text, 1, 4096))
    return {MemoryError::INVALID, "payload text bounds/charset"};
  if (payload.uncertainty_milli > 1000 || confidence_milli > 1000 ||
      salience_milli > 1000 || novelty_milli > 1000)
    return {MemoryError::INVALID, "milli fields must be 0..1000"};
  if (provenance.authority == Authority::EXTERNAL_GROUNDED &&
      provenance.evidence_handle.empty())
    return {MemoryError::INVALID, "external grounded memory requires an evidence handle"};
  if (type == MemoryType::USER_MEMORY) {
    if (tier != SemanticTier::LONG_TERM)
      return {MemoryError::INVALID, "user memory is long-term"};
    if (provenance.authority != Authority::USER_ASSERTED)
      return {MemoryError::INVALID, "user memory requires USER_ASSERTED provenance"};
    if (user_scope.empty())
      return {MemoryError::INVALID, "user memory requires a user scope"};
  }
  if (type == MemoryType::SELECTED_EVIDENCE &&
      (tier != SemanticTier::WORKING || !pinned))
    return {MemoryError::INVALID, "selected evidence must be pinned working memory"};
  if (verification_bound && !pinned)
    return {MemoryError::INVALID, "verification-bound memory must be pinned"};

  uint64_t epoch = tick();
  Record r;
  {
    char id[24];
    snprintf(id, sizeof(id), "mem-%08llu", (unsigned long long)next_id_++);
    r.memory_id = id;
  }
  r.type = type;
  r.tier = tier;
  r.residency = residency;
  r.provenance = provenance;
  r.payload = payload;
  r.source_evidence_handle = source_evidence_handle;
  r.created_epoch = epoch;
  r.modified_epoch = epoch;
  r.last_access_epoch = epoch;
  r.access_count = 0;
  r.confidence_milli = confidence_milli;
  r.salience_milli = salience_milli;
  r.novelty_milli = novelty_milli;
  r.expires_epoch = ttl_epochs >= 0 ? (int64_t)(epoch + (uint64_t)ttl_epochs) : -1;
  r.content_hash = "sha256:" + sha256_hex(payload_canonical_json(payload));
  r.cog_bindings = cog_bindings;
  if (r.cog_bindings.size() > 16) r.cog_bindings.resize(16);
  r.session_scope = session_scope;
  r.user_scope = user_scope;
  r.deletion = Deletion::ACTIVE;
  r.pinned = pinned;
  r.verification_bound = verification_bound;
  records_[r.memory_id] = r;
  journal("CREATE", r.memory_id, -1, static_cast<int>(tier), "");
  MemoryError bound = enforce_bound(tier);
  if (bound) return bound;
  if (out) *out = r;
  return {};
}

MemoryError Manager::get(const std::string &memory_id, bool include_deleted,
                         Record *out) {
  uint64_t epoch = tick();
  auto it = records_.find(memory_id);
  if (it == records_.end() ||
      (it->second.deletion == Deletion::TOMBSTONED && !include_deleted))
    return {MemoryError::NOT_FOUND, "memory not found"};
  it->second.last_access_epoch = epoch;
  it->second.access_count += 1;
  if (out) *out = it->second;
  return {};
}

std::vector<Record> Manager::records(bool include_deleted) const {
  std::vector<Record> out;
  for (const auto &kv : records_)
    if (include_deleted || kv.second.deletion == Deletion::ACTIVE)
      out.push_back(kv.second);
  return out; /* std::map iteration is memory_id-sorted, as Python */
}

MemoryError Manager::promote(const std::string &memory_id, const std::string &reason,
                             Record *out) {
  auto it = records_.find(memory_id);
  if (it == records_.end()) return {MemoryError::NOT_FOUND, "memory not found"};
  Record &r = it->second;
  static const int next_tier[4] = {1, 2, 3, -1};
  int target = next_tier[static_cast<int>(r.tier)];
  if (target < 0) {
    if (out) *out = r;
    return {};
  }
  if (target == static_cast<int>(SemanticTier::LONG_TERM) &&
      r.type != MemoryType::EXTERNAL_KNOWLEDGE && r.type != MemoryType::USER_MEMORY &&
      r.type != MemoryType::LEARNED_SPECIALIST_STATE &&
      r.type != MemoryType::PROJECT_KNOWLEDGE)
    return {MemoryError::INVALID,
            "ordinary working state cannot be promoted into long-term authority"};
  uint64_t epoch = tick();
  SemanticTier from = r.tier;
  r.tier = static_cast<SemanticTier>(target);
  r.modified_epoch = epoch;
  journal("PROMOTE", memory_id, static_cast<int>(from), target, reason);
  MemoryError bound = enforce_bound(r.tier);
  if (bound) return bound;
  if (out) *out = r;
  return {};
}

uint64_t Manager::promotion_score(const Record &r) {
  uint64_t authority_bonus;
  switch (r.provenance.authority) {
    case Authority::EXTERNAL_GROUNDED:
    case Authority::USER_ASSERTED:
    case Authority::PROJECT_APPROVED:
      authority_bonus = 200;
      break;
    case Authority::OBSERVATION:
    case Authority::SYSTEM:
      authority_bonus = 100;
      break;
    case Authority::LEARNED_STATE:
      authority_bonus = 50;
      break;
    default:
      authority_bonus = 0;
  }
  uint64_t reuse = (r.access_count > 20 ? 20 : r.access_count) * 50;
  return r.salience_milli + r.novelty_milli + r.confidence_milli + reuse +
         authority_bonus;
}

std::vector<Record> Manager::promotion_candidates(uint64_t minimum_score) const {
  std::vector<Record> out;
  for (const auto &kv : records_) {
    const Record &r = kv.second;
    if (r.deletion == Deletion::ACTIVE && r.tier != SemanticTier::LONG_TERM &&
        promotion_score(r) >= minimum_score)
      out.push_back(r);
  }
  std::stable_sort(out.begin(), out.end(), [](const Record &a, const Record &b) {
    uint64_t sa = promotion_score(a), sb = promotion_score(b);
    if (sa != sb) return sa > sb;
    return a.memory_id < b.memory_id;
  });
  return out;
}

MemoryError Manager::demote(const std::string &memory_id, SemanticTier target,
                            const std::string &reason, Record *out) {
  auto it = records_.find(memory_id);
  if (it == records_.end()) return {MemoryError::NOT_FOUND, "memory not found"};
  Record &r = it->second;
  if (static_cast<int>(target) >= static_cast<int>(r.tier))
    return {MemoryError::INVALID, "demotion target must have a shorter semantic lifetime"};
  if (r.pinned || r.verification_bound)
    return {MemoryError::INVALID,
            "pinned or verification-bound memory cannot be demoted"};
  uint64_t epoch = tick();
  SemanticTier from = r.tier;
  r.tier = target;
  r.modified_epoch = epoch;
  journal("DEMOTE", memory_id, static_cast<int>(from), static_cast<int>(target),
          reason);
  MemoryError bound = enforce_bound(target);
  if (bound) return bound;
  if (out) *out = r;
  return {};
}

MemoryError Manager::set_residency(const std::string &memory_id, Residency residency,
                                   Record *out) {
  auto it = records_.find(memory_id);
  if (it == records_.end()) return {MemoryError::NOT_FOUND, "memory not found"};
  it->second.residency = residency;
  journal("RESIDENCY", memory_id, -1, -1, to_str(residency));
  if (out) *out = it->second;
  return {};
}

MemoryError Manager::mark_verification_bound(const std::string &memory_id,
                                             Record *out) {
  auto it = records_.find(memory_id);
  if (it == records_.end()) return {MemoryError::NOT_FOUND, "memory not found"};
  Record &r = it->second;
  if (r.type != MemoryType::SELECTED_EVIDENCE)
    return {MemoryError::INVALID, "only selected evidence can become verification-bound"};
  r.pinned = true;
  r.verification_bound = true;
  r.modified_epoch = tick();
  journal("VERIFY_PIN", memory_id, -1, -1, "");
  if (out) *out = r;
  return {};
}

uint32_t Manager::reclaim_expired() {
  std::vector<std::string> expired;
  for (const auto &kv : records_) {
    const Record &r = kv.second;
    if (r.expires_epoch >= 0 && (uint64_t)r.expires_epoch <= epoch_ && !r.pinned &&
        (r.tier == SemanticTier::EPHEMERAL || r.tier == SemanticTier::SHORT_TERM))
      expired.push_back(kv.first);
  }
  for (const auto &id : expired) {
    records_.erase(id);
    journal("EXPIRE", id, -1, -1, "");
  }
  return (uint32_t)expired.size();
}

uint32_t Manager::advance(uint64_t epochs) {
  epoch_ += epochs;
  return reclaim_expired();
}

MemoryError Manager::enforce_bound(SemanticTier tier) {
  uint32_t limit = wm_.ephemeral;
  if (tier == SemanticTier::SHORT_TERM) limit = wm_.short_term;
  if (tier == SemanticTier::WORKING) limit = wm_.working;
  if (tier == SemanticTier::LONG_TERM) limit = wm_.long_term;
  for (;;) {
    size_t active = 0;
    for (const auto &kv : records_)
      if (kv.second.tier == tier && kv.second.deletion == Deletion::ACTIVE) active++;
    if (active <= limit) return {};
    /* victim: min(last_access, salience, created, memory_id) among unpinned */
    std::string victim;
    bool any = false;
    for (const auto &kv : records_) {
      const Record &r = kv.second;
      if (r.tier != tier || r.deletion != Deletion::ACTIVE || r.pinned) continue;
      if (!any) {
        victim = kv.first;
        any = true;
        continue;
      }
      const Record &v = records_[victim];
      if (r.last_access_epoch < v.last_access_epoch ||
          (r.last_access_epoch == v.last_access_epoch &&
           (r.salience_milli < v.salience_milli ||
            (r.salience_milli == v.salience_milli &&
             (r.created_epoch < v.created_epoch ||
              (r.created_epoch == v.created_epoch && r.memory_id < v.memory_id))))))
        victim = kv.first;
    }
    if (!any)
      return {MemoryError::BOUND_EXHAUSTED,
              std::string(to_str(tier)) + " bound exhausted by pinned records"};
    records_.erase(victim);
    journal("EVICT", victim, static_cast<int>(tier), -1, "BOUND");
  }
}

std::vector<Record> Manager::search_user(const std::string &user_scope,
                                         const std::string &query) const {
  std::vector<std::string> terms;
  std::string cur;
  for (char c : query) {
    if (isspace((unsigned char)c)) {
      if (!cur.empty()) {
        terms.push_back(cur);
        cur.clear();
      }
    } else {
      cur.push_back((char)tolower((unsigned char)c));
    }
  }
  if (!cur.empty()) terms.push_back(cur);
  std::vector<Record> out;
  for (const auto &kv : records_) {
    const Record &r = kv.second;
    if (r.type != MemoryType::USER_MEMORY || r.user_scope != user_scope ||
        r.deletion != Deletion::ACTIVE)
      continue;
    std::string hay;
    hay.reserve(r.payload.text.size());
    for (char c : r.payload.text) hay.push_back((char)tolower((unsigned char)c));
    bool all = true;
    for (const auto &t : terms)
      if (hay.find(t) == std::string::npos) {
        all = false;
        break;
      }
    if (all) out.push_back(r);
  }
  std::stable_sort(out.begin(), out.end(), [](const Record &a, const Record &b) {
    if (a.salience_milli != b.salience_milli)
      return a.salience_milli > b.salience_milli;
    return a.memory_id < b.memory_id;
  });
  return out;
}

MemoryError Manager::edit_user(const std::string &memory_id, const Payload &payload,
                               const std::string &authorization_id, Record *out) {
  if (!consume_authorization(authorization_id))
    return {MemoryError::AUTH_REQUIRED, "explicit user authorization is required"};
  auto it = records_.find(memory_id);
  if (it == records_.end()) return {MemoryError::NOT_FOUND, "memory not found"};
  Record &r = it->second;
  if (r.type != MemoryType::USER_MEMORY)
    return {MemoryError::IMMUTABLE_TYPE, "only user memory may be edited through this API"};
  if (r.deletion == Deletion::TOMBSTONED)
    return {MemoryError::NOT_FOUND, "memory not found"};
  if (!valid_text(payload.text, 1, 4096))
    return {MemoryError::INVALID, "payload text bounds/charset"};
  r.payload = payload;
  r.content_hash = "sha256:" + sha256_hex(payload_canonical_json(payload));
  r.modified_epoch = tick();
  journal("EDIT", memory_id, -1, -1, "");
  if (out) *out = r;
  return {};
}

MemoryError Manager::delete_user(const std::string &memory_id,
                                 const std::string &authorization_id, Record *out) {
  if (!consume_authorization(authorization_id))
    return {MemoryError::AUTH_REQUIRED, "explicit user authorization is required"};
  auto it = records_.find(memory_id);
  if (it == records_.end()) return {MemoryError::NOT_FOUND, "memory not found"};
  Record &r = it->second;
  if (r.type != MemoryType::USER_MEMORY)
    return {MemoryError::IMMUTABLE_TYPE,
            "immutable external knowledge cannot be deleted through user memory"};
  r.deletion = Deletion::TOMBSTONED;
  r.modified_epoch = tick();
  journal("TOMBSTONE", memory_id, -1, -1, "");
  if (out) *out = r;
  return {};
}

uint32_t Manager::compact_tombstones(uint64_t before_epoch) {
  std::vector<std::string> victims;
  for (const auto &kv : records_)
    if (kv.second.deletion == Deletion::TOMBSTONED &&
        kv.second.modified_epoch <= before_epoch)
      victims.push_back(kv.first);
  for (const auto &id : victims) {
    records_.erase(id);
    journal("COMPACT", id, -1, -1, "");
  }
  return (uint32_t)victims.size();
}

/* ------------------------- user-memory service ---------------------------- */

std::vector<Record> UserMemory::list(const std::string &user_id) const {
  std::vector<Record> out;
  for (const auto &r : m_->records(false))
    if (r.type == MemoryType::USER_MEMORY && r.user_scope == user_id)
      out.push_back(r);
  return out;
}

MemoryError UserMemory::read(const std::string &user_id,
                             const std::string &memory_id, Record *out) {
  MemoryError e = m_->get(memory_id, false, out);
  if (e) return e;
  if (out->type != MemoryType::USER_MEMORY || out->user_scope != user_id)
    return {MemoryError::NOT_FOUND, "memory not found"};
  return {};
}

MemoryError UserMemory::write(const std::string &user_id, const std::string &text,
                              const std::string &authorization_id,
                              const std::string &source_id, uint32_t salience_milli,
                              Record *out) {
  if (!m_->authorize_user_mutation(authorization_id))
    return {MemoryError::AUTH_REQUIRED, "invalid user-memory authorization"};
  Payload p;
  p.text = text;
  Provenance prov;
  prov.authority = Authority::USER_ASSERTED;
  prov.source_id = source_id.empty() ? "user" : source_id;
  return m_->create(MemoryType::USER_MEMORY, SemanticTier::LONG_TERM,
                    Residency::COLD, p, prov, "", -1, 1000, salience_milli, 1000,
                    {}, "", user_id, false, false, authorization_id, out);
}

MemoryError UserMemory::edit(const std::string &user_id,
                             const std::string &memory_id, const std::string &text,
                             const std::string &authorization_id, Record *out) {
  Record cur;
  MemoryError e = read(user_id, memory_id, &cur);
  if (e) return e;
  if (!m_->authorize_user_mutation(authorization_id))
    return {MemoryError::AUTH_REQUIRED, "invalid user-memory authorization"};
  Payload p = cur.payload;
  p.text = text;
  return m_->edit_user(memory_id, p, authorization_id, out);
}

MemoryError UserMemory::erase(const std::string &user_id,
                              const std::string &memory_id,
                              const std::string &authorization_id, Record *out) {
  Record cur;
  MemoryError e = read(user_id, memory_id, &cur);
  if (e) return e;
  if (!m_->authorize_user_mutation(authorization_id))
    return {MemoryError::AUTH_REQUIRED, "invalid user-memory authorization"};
  return m_->delete_user(memory_id, authorization_id, out);
}

std::vector<Record> UserMemory::search(const std::string &user_id,
                                       const std::string &query) const {
  return m_->search_user(user_id, query);
}

std::string explicit_remember_payload(const std::string &text) {
  /* ^\s*remember\s+that\s+(.+?)\s*[.]?\s*$ case-insensitive */
  size_t i = 0;
  auto skip_ws = [&]() {
    while (i < text.size() && isspace((unsigned char)text[i])) i++;
  };
  auto match_word = [&](const char *w) {
    size_t n = strlen(w);
    if (i + n > text.size()) return false;
    for (size_t k = 0; k < n; k++)
      if (tolower((unsigned char)text[i + k]) != w[k]) return false;
    i += n;
    return true;
  };
  skip_ws();
  if (!match_word("remember")) return "";
  if (i < text.size() && !isspace((unsigned char)text[i])) return "";
  skip_ws();
  if (!match_word("that")) return "";
  if (i < text.size() && !isspace((unsigned char)text[i])) return "";
  skip_ws();
  size_t begin = i;
  size_t end = text.size();
  while (end > begin && isspace((unsigned char)text[end - 1])) end--;
  if (end > begin && text[end - 1] == '.') end--;
  while (end > begin && isspace((unsigned char)text[end - 1])) end--;
  if (end <= begin) return "";
  return text.substr(begin, end - begin);
}

/* ------------------------- export / import / store ------------------------ */

std::string Manager::export_state_json() const {
  std::string o = "{\"epoch\":" + std::to_string(epoch_);
  o += ",\"journal_tail\":[";
  bool first = true;
  for (const auto &e : journal_) {
    if (!first) o.push_back(',');
    first = false;
    o += "{\"epoch\":" + std::to_string(e.epoch);
    o += ",\"from_tier\":";
    if (e.from_tier >= 0)
      json_escape(&o, to_str(static_cast<SemanticTier>(e.from_tier)));
    else
      o += "null";
    o += ",\"memory_id\":";
    json_escape(&o, e.memory_id);
    o += ",\"operation\":";
    json_escape(&o, e.operation);
    o += ",\"reason\":";
    json_escape(&o, e.reason);
    o += ",\"to_tier\":";
    if (e.to_tier >= 0)
      json_escape(&o, to_str(static_cast<SemanticTier>(e.to_tier)));
    else
      o += "null";
    o += "}";
  }
  o += "],\"next_id\":" + std::to_string(next_id_);
  o += ",\"records\":[";
  first = true;
  for (const auto &kv : records_) {
    if (kv.second.tier == SemanticTier::EPHEMERAL) continue; /* as Python */
    if (!first) o.push_back(',');
    first = false;
    o += record_canonical_json(kv.second);
  }
  o += "],\"schema_version\":\"aethercore.memory.v1\"";
  o += ",\"watermarks\":{\"ephemeral_limit\":" + std::to_string(wm_.ephemeral) +
       ",\"long_term_limit\":" + std::to_string(wm_.long_term) +
       ",\"short_term_limit\":" + std::to_string(wm_.short_term) +
       ",\"working_limit\":" + std::to_string(wm_.working) + "}}";
  return o;
}

/* Minimal bounded JSON reader for the export shape above (keys may arrive in
 * any order; values are the types this module writes). Not a general parser. */
namespace {
struct JR {
  const char *p;
  const char *end;
  bool ok = true;
  void ws() {
    while (p < end && (*p == ' ' || *p == '\n' || *p == '\t' || *p == '\r')) p++;
  }
  bool lit(const char *s) {
    size_t n = strlen(s);
    if ((size_t)(end - p) < n || memcmp(p, s, n)) {
      ok = false;
      return false;
    }
    p += n;
    return true;
  }
  bool str(std::string *out) {
    ws();
    if (p >= end || *p != '"') {
      ok = false;
      return false;
    }
    p++;
    out->clear();
    while (p < end && *p != '"') {
      if (*p == '\\') {
        p++;
        if (p >= end) break;
        switch (*p) {
          case 'n': out->push_back('\n'); break;
          case 't': out->push_back('\t'); break;
          case 'r': out->push_back('\r'); break;
          case 'b': out->push_back('\b'); break;
          case 'f': out->push_back('\f'); break;
          case 'u':
            if (end - p < 5) {
              ok = false;
              return false;
            }
            out->push_back('?'); /* ASCII-only inputs never produce this */
            p += 4;
            break;
          default: out->push_back(*p);
        }
        p++;
      } else {
        out->push_back(*p++);
      }
    }
    if (p >= end || *p != '"') {
      ok = false;
      return false;
    }
    p++;
    return true;
  }
  bool opt_str(std::string *out) {
    ws();
    if ((size_t)(end - p) >= 4 && !memcmp(p, "null", 4)) {
      p += 4;
      out->clear();
      return true;
    }
    return str(out);
  }
  bool u64(uint64_t *v) {
    ws();
    uint64_t x = 0;
    bool any = false;
    while (p < end && isdigit((unsigned char)*p)) {
      x = x * 10 + (uint64_t)(*p - '0');
      p++;
      any = true;
    }
    if (!any) ok = false;
    *v = x;
    return any;
  }
  bool i64(int64_t *v) {
    ws();
    if (p < end && *p == '-') {
      p++;
      uint64_t x;
      if (!u64(&x)) return false;
      *v = -(int64_t)x;
      return true;
    }
    uint64_t x;
    if (!u64(&x)) return false;
    *v = (int64_t)x;
    return true;
  }
  bool boolean(bool *v) {
    ws();
    if ((size_t)(end - p) >= 4 && !memcmp(p, "true", 4)) {
      p += 4;
      *v = true;
      return true;
    }
    if ((size_t)(end - p) >= 5 && !memcmp(p, "false", 5)) {
      p += 5;
      *v = false;
      return true;
    }
    ok = false;
    return false;
  }
  bool key(const char *want) {
    std::string k;
    if (!str(&k)) return false;
    ws();
    if (p >= end || *p != ':') {
      ok = false;
      return false;
    }
    p++;
    return k == want;
  }
  /* Skip any value (used for unknown keys). */
  bool skip() {
    ws();
    if (p >= end) {
      ok = false;
      return false;
    }
    if (*p == '"') {
      std::string s;
      return str(&s);
    }
    if (*p == '{') {
      p++;
      ws();
      if (p < end && *p == '}') {
        p++;
        return true;
      }
      for (;;) {
        std::string k;
        if (!str(&k)) return false;
        ws();
        if (p >= end || *p != ':') {
          ok = false;
          return false;
        }
        p++;
        if (!skip()) return false;
        ws();
        if (p < end && *p == ',') {
          p++;
          continue;
        }
        if (p < end && *p == '}') {
          p++;
          return true;
        }
        ok = false;
        return false;
      }
    }
    if (*p == '[') {
      p++;
      ws();
      if (p < end && *p == ']') {
        p++;
        return true;
      }
      for (;;) {
        if (!skip()) return false;
        ws();
        if (p < end && *p == ',') {
          p++;
          continue;
        }
        if (p < end && *p == ']') {
          p++;
          return true;
        }
        ok = false;
        return false;
      }
    }
    /* number / true / false / null */
    while (p < end && *p != ',' && *p != '}' && *p != ']' && *p != ' ' &&
           *p != '\n')
      p++;
    return true;
  }
};

bool enum_from(const std::string &s, const char *const *names, int n, int *out) {
  for (int i = 0; i < n; i++)
    if (s == names[i]) {
      *out = i;
      return true;
    }
  return false;
}

bool parse_record(JR *jr, Record *r) {
  jr->ws();
  if (!jr->lit("{")) return false;
  bool first = true;
  while (jr->ok) {
    jr->ws();
    if (jr->p < jr->end && *jr->p == '}') {
      jr->p++;
      return true;
    }
    if (!first && !jr->lit(",")) return false;
    first = false;
    std::string k;
    if (!jr->str(&k)) return false;
    jr->ws();
    if (!jr->lit(":")) return false;
    std::string s;
    uint64_t u;
    bool b;
    if (k == "memory_id") {
      if (!jr->str(&r->memory_id)) return false;
    } else if (k == "memory_type") {
      if (!jr->str(&s)) return false;
      static const char *names[] = {"SCRATCH",
                                    "CANDIDATE_FEATURE",
                                    "UNCOMMITTED_TOOL_RESULT",
                                    "HYPOTHESIS",
                                    "CONVERSATION_TURN",
                                    "ACTIVE_REFERENCE",
                                    "TASK_STATE",
                                    "COG_ITEM",
                                    "SELECTED_EVIDENCE",
                                    "EXTERNAL_KNOWLEDGE",
                                    "USER_MEMORY",
                                    "LEARNED_SPECIALIST_STATE",
                                    "PROJECT_KNOWLEDGE"};
      int v;
      if (!enum_from(s, names, 13, &v)) return false;
      r->type = static_cast<MemoryType>(v);
    } else if (k == "semantic_tier") {
      if (!jr->str(&s)) return false;
      SemanticTier t;
      if (!tier_from_str(s.c_str(), &t)) return false;
      r->tier = t;
    } else if (k == "residency") {
      if (!jr->str(&s)) return false;
      int v;
      static const char *names[] = {"COLD", "WARM", "HOT"};
      if (!enum_from(s, names, 3, &v)) return false;
      r->residency = static_cast<Residency>(v);
    } else if (k == "deletion_state") {
      if (!jr->str(&s)) return false;
      r->deletion = s == "ACTIVE" ? Deletion::ACTIVE : Deletion::TOMBSTONED;
    } else if (k == "created_epoch" || k == "modified_epoch" ||
               k == "last_access_epoch") {
      if (!jr->u64(&u)) return false;
      if (k[0] == 'c') r->created_epoch = u;
      else if (k[1] == 'o') r->modified_epoch = u;
      else r->last_access_epoch = u;
    } else if (k == "access_count" || k == "confidence_milli" ||
               k == "salience_milli" || k == "novelty_milli") {
      if (!jr->u64(&u)) return false;
      if (k[0] == 'a') r->access_count = u;
      else if (k[0] == 'c') r->confidence_milli = (uint32_t)u;
      else if (k[0] == 's') r->salience_milli = (uint32_t)u;
      else r->novelty_milli = (uint32_t)u;
    } else if (k == "expires_epoch") {
      jr->ws();
      if ((size_t)(jr->end - jr->p) >= 4 && !memcmp(jr->p, "null", 4)) {
        jr->p += 4;
        r->expires_epoch = -1;
      } else {
        if (!jr->i64(&r->expires_epoch)) return false;
      }
    } else if (k == "content_hash") {
      if (!jr->str(&r->content_hash)) return false;
    } else if (k == "session_scope" || k == "user_scope" ||
               k == "source_evidence_handle") {
      std::string *dst = k == "session_scope"   ? &r->session_scope
                         : k == "user_scope"    ? &r->user_scope
                                                : &r->source_evidence_handle;
      if (!jr->opt_str(dst)) return false;
    } else if (k == "pinned" || k == "verification_bound") {
      if (!jr->boolean(&b)) return false;
      if (k[0] == 'p') r->pinned = b;
      else r->verification_bound = b;
    } else if (k == "cog_bindings" || k == "payload" || k == "provenance") {
      if (k == "cog_bindings") {
        if (!jr->lit("[")) return false;
        jr->ws();
        if (jr->p < jr->end && *jr->p == ']') {
          jr->p++;
        } else {
          for (;;) {
            std::string item;
            if (!jr->str(&item)) return false;
            r->cog_bindings.push_back(item);
            jr->ws();
            if (jr->p < jr->end && *jr->p == ',') {
              jr->p++;
              continue;
            }
            if (jr->p < jr->end && *jr->p == ']') {
              jr->p++;
              break;
            }
            return false;
          }
        }
      } else if (k == "payload") {
        if (!jr->lit("{")) return false;
        bool pf = true;
        for (;;) {
          jr->ws();
          if (jr->p < jr->end && *jr->p == '}') {
            jr->p++;
            break;
          }
          if (!pf && !jr->lit(",")) return false;
          pf = false;
          std::string pk;
          if (!jr->str(&pk)) return false;
          jr->ws();
          if (!jr->lit(":")) return false;
          if (pk == "text") {
            if (!jr->str(&r->payload.text)) return false;
          } else if (pk == "negated") {
            if (!jr->boolean(&r->payload.negated)) return false;
          } else if (pk == "quantity") {
            if (!jr->opt_str(&r->payload.quantity)) return false;
          } else if (pk == "unit") {
            if (!jr->opt_str(&r->payload.unit)) return false;
          } else if (pk == "uncertainty_milli") {
            if (!jr->u64(&u)) return false;
            r->payload.uncertainty_milli = (uint32_t)u;
          } else if (pk == "perspective") {
            if (!jr->opt_str(&r->payload.perspective)) return false;
          } else if (!jr->skip()) {
            return false;
          }
        }
      } else { /* provenance */
        if (!jr->lit("{")) return false;
        bool pf = true;
        for (;;) {
          jr->ws();
          if (jr->p < jr->end && *jr->p == '}') {
            jr->p++;
            break;
          }
          if (!pf && !jr->lit(",")) return false;
          pf = false;
          std::string pk;
          if (!jr->str(&pk)) return false;
          jr->ws();
          if (!jr->lit(":")) return false;
          if (pk == "authority") {
            if (!jr->str(&s)) return false;
            static const char *names[] = {"EXTERNAL_GROUNDED", "USER_ASSERTED",
                                          "OBSERVATION",       "INFERENCE",
                                          "SYSTEM",            "PROJECT_APPROVED",
                                          "LEARNED_STATE"};
            int v;
            if (!enum_from(s, names, 7, &v)) return false;
            r->provenance.authority = static_cast<Authority>(v);
          } else if (pk == "source_id") {
            if (!jr->str(&r->provenance.source_id)) return false;
          } else if (pk == "evidence_handle") {
            if (!jr->opt_str(&r->provenance.evidence_handle)) return false;
          } else if (pk == "derivation_ids") {
            if (!jr->lit("[")) return false;
            jr->ws();
            if (jr->p < jr->end && *jr->p == ']') {
              jr->p++;
            } else {
              for (;;) {
                std::string item;
                if (!jr->str(&item)) return false;
                r->provenance.derivation_ids.push_back(item);
                jr->ws();
                if (jr->p < jr->end && *jr->p == ',') {
                  jr->p++;
                  continue;
                }
                if (jr->p < jr->end && *jr->p == ']') {
                  jr->p++;
                  break;
                }
                return false;
              }
            }
          } else if (!jr->skip()) {
            return false;
          }
        }
      }
    } else if (!jr->skip()) {
      return false;
    }
  }
  return false;
}
}  // namespace

bool Manager::import_state_json(const std::string &json, MemoryError *err) {
  JR jr{json.data(), json.data() + json.size(), true};
  records_.clear();
  journal_.clear();
  authorizations_.clear();
  epoch_ = 0;
  next_id_ = 1;
  if (!jr.lit("{")) goto fail;
  {
    bool first = true;
    for (;;) {
      jr.ws();
      if (jr.p < jr.end && *jr.p == '}') {
        jr.p++;
        break;
      }
      if (!first && !jr.lit(",")) goto fail;
      first = false;
      std::string k;
      if (!jr.str(&k)) goto fail;
      jr.ws();
      if (!jr.lit(":")) goto fail;
      if (k == "epoch") {
        if (!jr.u64(&epoch_)) goto fail;
      } else if (k == "next_id") {
        if (!jr.u64(&next_id_)) goto fail;
      } else if (k == "schema_version") {
        std::string v;
        if (!jr.str(&v)) goto fail;
        if (v != "aethercore.memory.v1") goto fail;
      } else if (k == "watermarks") {
        if (!jr.lit("{")) goto fail;
        bool wf = true;
        for (;;) {
          jr.ws();
          if (jr.p < jr.end && *jr.p == '}') {
            jr.p++;
            break;
          }
          if (!wf && !jr.lit(",")) goto fail;
          wf = false;
          std::string wk;
          if (!jr.str(&wk)) goto fail;
          jr.ws();
          if (!jr.lit(":")) goto fail;
          uint64_t u;
          if (!jr.u64(&u)) goto fail;
          if (wk == "ephemeral_limit") wm_.ephemeral = (uint32_t)u;
          else if (wk == "short_term_limit") wm_.short_term = (uint32_t)u;
          else if (wk == "working_limit") wm_.working = (uint32_t)u;
          else if (wk == "long_term_limit") wm_.long_term = (uint32_t)u;
        }
      } else if (k == "records") {
        if (!jr.lit("[")) goto fail;
        jr.ws();
        if (jr.p < jr.end && *jr.p == ']') {
          jr.p++;
        } else {
          for (;;) {
            Record r;
            if (!parse_record(&jr, &r)) goto fail;
            if (records_.count(r.memory_id)) goto fail; /* duplicate handle */
            records_[r.memory_id] = r;
            jr.ws();
            if (jr.p < jr.end && *jr.p == ',') {
              jr.p++;
              continue;
            }
            if (jr.p < jr.end && *jr.p == ']') {
              jr.p++;
              break;
            }
            goto fail;
          }
        }
      } else if (k == "journal_tail") {
        if (!jr.lit("[")) goto fail;
        jr.ws();
        if (jr.p < jr.end && *jr.p == ']') {
          jr.p++;
        } else {
          for (;;) {
            JournalEntry e;
            if (!jr.lit("{")) goto fail;
            bool jf = true;
            for (;;) {
              jr.ws();
              if (jr.p < jr.end && *jr.p == '}') {
                jr.p++;
                break;
              }
              if (!jf && !jr.lit(",")) goto fail;
              jf = false;
              std::string jk;
              if (!jr.str(&jk)) goto fail;
              jr.ws();
              if (!jr.lit(":")) goto fail;
              if (jk == "epoch") {
                if (!jr.u64(&e.epoch)) goto fail;
              } else if (jk == "operation") {
                if (!jr.str(&e.operation)) goto fail;
              } else if (jk == "memory_id") {
                if (!jr.str(&e.memory_id)) goto fail;
              } else if (jk == "reason") {
                if (!jr.str(&e.reason)) goto fail;
              } else if (jk == "from_tier" || jk == "to_tier") {
                std::string ts;
                if (!jr.opt_str(&ts)) goto fail;
                int v = -1;
                if (!ts.empty()) {
                  SemanticTier t;
                  if (!tier_from_str(ts.c_str(), &t)) goto fail;
                  v = static_cast<int>(t);
                }
                if (jk[0] == 'f') e.from_tier = v;
                else e.to_tier = v;
              } else if (!jr.skip()) {
                goto fail;
              }
            }
            journal_.push_back(e);
            jr.ws();
            if (jr.p < jr.end && *jr.p == ',') {
              jr.p++;
              continue;
            }
            if (jr.p < jr.end && *jr.p == ']') {
              jr.p++;
              break;
            }
            goto fail;
          }
        }
      } else if (!jr.skip()) {
        goto fail;
      }
    }
  }
  jr.ws();
  if (jr.p != jr.end) goto fail;
  return true;
fail:
  if (err) *err = {MemoryError::INVALID, "state json malformed"};
  return false;
}

bool store_save(const char *path, const Manager &m) {
  std::string state = m.export_state_json();
  std::string envelope = "{\"sha256\":\"" + sha256_hex(state) +
                         "\",\"state\":" + state + "}\n";
  std::string tmp = std::string(path) + ".tmp";
  FILE *f = fopen(tmp.c_str(), "wb");
  if (!f) return false;
  size_t wrote = fwrite(envelope.data(), 1, envelope.size(), f);
  if (wrote != envelope.size() || fflush(f) != 0) {
    fclose(f);
    return false;
  }
  fclose(f);
  return rename(tmp.c_str(), path) == 0;
}

bool store_load(const char *path, Manager *m, MemoryError *err) {
  FILE *f = fopen(path, "rb");
  if (!f) { /* missing file = fresh state */
    *m = Manager(m->watermarks());
    return true;
  }
  std::string data;
  char buf[8192];
  size_t n;
  while ((n = fread(buf, 1, sizeof(buf), f)) > 0) {
    data.append(buf, n);
    if (data.size() > (64u << 20)) { /* bounded: refuse absurd files */
      fclose(f);
      if (err) *err = {MemoryError::INVALID, "state file too large"};
      return false;
    }
  }
  fclose(f);
  while (!data.empty() && (data.back() == '\n' || data.back() == '\r'))
    data.pop_back();
  /* envelope: {"sha256":"<hex>","state":{...}} — parse at the top level. */
  JR jr{data.data(), data.data() + data.size(), true};
  if (!jr.lit("{")) goto fail;
  {
    std::string digest, state_json;
    bool have_digest = false, have_state = false;
    bool first = true;
    for (;;) {
      jr.ws();
      if (jr.p < jr.end && *jr.p == '}') {
        jr.p++;
        break;
      }
      if (!first && !jr.lit(",")) goto fail;
      first = false;
      std::string k;
      if (!jr.str(&k)) goto fail;
      jr.ws();
      if (!jr.lit(":")) goto fail;
      if (k == "sha256") {
        if (!jr.str(&digest)) goto fail;
        have_digest = true;
      } else if (k == "state") {
        /* capture the raw object bytes for the integrity check */
        const char *begin = jr.p;
        if (!jr.skip()) goto fail;
        state_json.assign(begin, (size_t)(jr.p - begin));
        have_state = true;
      } else {
        goto fail; /* Python envelope has exactly these two keys */
      }
    }
    jr.ws();
    if (jr.p != jr.end) goto fail;
    if (!have_digest || !have_state) goto fail;
    /* Integrity: Python hashes json.dumps(state, sort_keys=True, separators).
     * Our export is canonical, so a file written by either implementation has
     * canonical state bytes; verify against the digest. */
    if (digest != sha256_hex(state_json)) {
      if (err) *err = {MemoryError::INVALID, "state integrity check failed"};
      return false;
    }
    return m->import_state_json(state_json, err);
  }
fail:
  if (err) *err = {MemoryError::INVALID, "state envelope malformed"};
  return false;
}

}  // namespace acmem
