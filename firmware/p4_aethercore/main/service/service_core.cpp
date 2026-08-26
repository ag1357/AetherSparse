/* AetherCore V15 native service core — see service_core.h for the contract.
 *
 * This is a faithful C++17 port of the Python vertical execution path:
 *   src/aethersparse/agent/vertical.py        (orchestration)
 *   src/aethersparse/agent/conversation.py    (session/action engine)
 *   src/aethersparse/agent/realization.py     (evidence-copy realizer)
 *   src/aethersparse/controller/fuzzy_address.py  (EXACT + CHAR_NGRAM only)
 *   src/aethersparse/controller/micro_ops.py  (legal mask + execute)
 *   src/aethersparse/controller/adaptive_policy.py (int8 quantized policy)
 *   src/aethersparse/controller/answering.py  (plan/realize for VERIFY_PLAN)
 *   src/aethersparse/controller/verification.py  (exact verifier)
 *   src/aethersparse/controller/framing.py    (relation cues/constraints only)
 *   src/aethersparse/cognitive/{interpreter,graph}.py (COG-lite obligations)
 *
 * Scope notes (documented in phase-notes/phase9-native-service.md):
 *  - ASCII text plane (the deployed fixture and queries are ASCII).
 *  - Fuzzy EDIT_DISTANCE and SIMHASH_LSH channels are not used by
 *    vertical.py and are intentionally not ported.
 *  - LIST/COMPARISON plan construction is implemented; the comparison
 *    verifier direction uses plain numeric comparison (unit handling in
 *    evidence.py is out of fixture scope).
 */

#include "service_core.h"

#include <algorithm>
#include <cmath>
#include <initializer_list>
#include <map>
#include <new>
#include <set>
#include <stdio.h>
#include <string.h>

#include "sha256.h"

namespace aethercore {
namespace service {

namespace {

/* ---------------------------------------------------------------- */
/* text utilities (ASCII plane)                                      */

bool IsWordChar(char c) {  // [^\W_] for ASCII: letters and digits
  return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
         (c >= '0' && c <= '9');
}

char Lower(char c) {
  return (c >= 'A' && c <= 'Z') ? char(c - 'A' + 'a') : c;
}

std::string Lowercase(const std::string& value) {
  std::string out(value);
  for (size_t i = 0; i < out.size(); i++) out[i] = Lower(out[i]);
  return out;
}

bool IsSpace(char c) {
  return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f' ||
         c == '\v';
}

std::string Strip(const std::string& value) {
  size_t begin = 0, end = value.size();
  while (begin < end && IsSpace(value[begin])) ++begin;
  while (end > begin && IsSpace(value[end - 1])) --end;
  return value.substr(begin, end - begin);
}

// " ".join(text.strip().split()) — Python interpreter/framer normalization.
std::string NormalizeQuery(const std::string& text) {
  std::string out;
  size_t i = 0;
  while (i < text.size()) {
    while (i < text.size() && IsSpace(text[i])) ++i;
    size_t start = i;
    while (i < text.size() && !IsSpace(text[i])) ++i;
    if (i > start) {
      if (!out.empty()) out.push_back(' ');
      out.append(text, start, i - start);
    }
  }
  return out;
}

// _TOKEN_RE: [^\W_]+(?:['’-][^\W_]+)* — word tokens joined by ' ’ -.
struct Token {
  size_t start, end;  // char offsets into the scanned text
  std::string text;
};

bool IsJoiner(const std::string& value, size_t pos, size_t* width) {
  char c = value[pos];
  if (c == '\'' || c == '-') {
    *width = 1;
    return true;
  }
  // U+2019 RIGHT SINGLE QUOTATION MARK (0xE2 0x80 0x99)
  if (pos + 2 < value.size() && uint8_t(c) == 0xE2 &&
      uint8_t(value[pos + 1]) == 0x80 && uint8_t(value[pos + 2]) == 0x99) {
    *width = 3;
    return true;
  }
  return false;
}

std::vector<Token> TokenizeExtended(const std::string& value) {
  std::vector<Token> tokens;
  size_t i = 0;
  while (i < value.size()) {
    if (!IsWordChar(value[i])) {
      ++i;
      continue;
    }
    size_t start = i;
    size_t end = i;
    while (end < value.size() && IsWordChar(value[end])) ++end;
    for (;;) {  // continuation: joiner followed by word chars
      size_t width = 0;
      if (end >= value.size() || !IsJoiner(value, end, &width)) break;
      size_t probe = end + width;
      if (probe >= value.size() || !IsWordChar(value[probe])) break;
      end = probe;
      while (end < value.size() && IsWordChar(value[end])) ++end;
    }
    if (tokens.size() < 96) {
      tokens.push_back(Token{start, end, value.substr(start, end - start)});
    }
    i = end;
  }
  return tokens;
}

// re.findall(r"[^\W_]+", value) — plain word tokens (no joiner continuation).
std::vector<std::string> WordTokens(const std::string& value) {
  std::vector<std::string> out;
  size_t i = 0;
  while (i < value.size()) {
    if (!IsWordChar(value[i])) {
      ++i;
      continue;
    }
    size_t start = i;
    while (i < value.size() && IsWordChar(value[i])) ++i;
    out.push_back(value.substr(start, i - start));
  }
  return out;
}

// normalize_fuzzy_surface: '_'->' ', U+2019->'\'', casefold, retokenize.
std::string NormalizeFuzzySurface(const std::string& value) {
  std::string replaced;
  replaced.reserve(value.size());
  for (size_t i = 0; i < value.size(); i++) {
    if (value[i] == '_') {
      replaced.push_back(' ');
    } else if (i + 2 < value.size() && uint8_t(value[i]) == 0xE2 &&
               uint8_t(value[i + 1]) == 0x80 && uint8_t(value[i + 2]) == 0x99) {
      replaced.push_back('\'');
      i += 2;
    } else {
      replaced.push_back(Lower(value[i]));
    }
  }
  std::string out;
  for (const Token& token : TokenizeExtended(replaced)) {
    if (!out.empty()) out.push_back(' ');
    out += token.text;
  }
  return out;
}

bool ContainsWordToken(const std::vector<std::string>& tokens,
                       const std::string& needle) {
  for (const std::string& token : tokens)
    if (token == needle) return true;
  return false;
}

/* ---------------------------------------------------------------- */
/* conversation pattern matchers (hand-rolled Python `re` subsets)   */

bool HasWordBoundary(const std::string& text, size_t start, size_t end) {
  bool left_ok = start == 0 || !IsWordChar(text[start - 1]);
  bool right_ok = end >= text.size() || !IsWordChar(text[end]);
  return left_ok && right_ok;
}

// \b(he|him|his|she|her|hers|it|its|they|them|their)\b case-insensitive.
bool HasReferentPronoun(const std::string& text) {
  static const char* kPronouns[] = {"he",  "him", "his", "she",  "her",
                                    "hers", "it",  "its", "they", "them",
                                    "their"};
  for (const std::string& word : WordTokens(Lowercase(text))) {
    for (const char* pronoun : kPronouns)
      if (word == pronoun) return true;
  }
  return false;
}

// Framer PRONOUN_RE additionally includes the phrase "that one".
bool HasFramerPronoun(const std::string& normalized) {
  if (HasReferentPronoun(normalized)) return true;
  std::string folded = Lowercase(normalized);
  size_t pos = folded.find("that one");
  while (pos != std::string::npos) {
    if (HasWordBoundary(folded, pos, pos + 8)) return true;
    pos = folded.find("that one", pos + 1);
  }
  return false;
}

void SkipSpaces(const std::string& text, size_t* pos) {
  while (*pos < text.size() && IsSpace(text[*pos])) ++*pos;
}

bool MatchWordAt(const std::string& lowered, size_t* pos, const char* word) {
  size_t length = strlen(word);
  if (lowered.compare(*pos, length, word) != 0) return false;
  if (!HasWordBoundary(lowered, *pos, *pos + length)) return false;
  *pos += length;
  return true;
}

// ^\s*what\s+about\b
bool IsWhatAbout(const std::string& query) {
  std::string lowered = Lowercase(query);
  size_t pos = 0;
  SkipSpaces(lowered, &pos);
  if (!MatchWordAt(lowered, &pos, "what")) return false;
  if (pos >= lowered.size() || !IsSpace(lowered[pos])) return false;
  SkipSpaces(lowered, &pos);
  size_t save = pos;
  return MatchWordAt(lowered, &save, "about");
}

// ^\s*(?:no[, ]+)?(?:i\s+meant|rather|actually)\b
bool IsCorrection(const std::string& query) {
  std::string lowered = Lowercase(query);
  size_t pos = 0;
  SkipSpaces(lowered, &pos);
  if (lowered.compare(pos, 2, "no") == 0) {
    size_t after = pos + 2;
    if (after < lowered.size() &&
        (lowered[after] == ',' || lowered[after] == ' ')) {
      while (after < lowered.size() &&
             (lowered[after] == ',' || lowered[after] == ' ')) {
        ++after;
      }
      size_t probe = after;
      if (MatchWordAt(lowered, &probe, "rather") ||
          MatchWordAt(lowered, &probe, "actually")) {
        return true;
      }
      // "i meant"
      if (probe < lowered.size() && lowered[probe] == 'i' &&
          HasWordBoundary(lowered, probe, probe + 1)) {
        size_t p = probe + 1;
        if (p < lowered.size() && IsSpace(lowered[p])) {
          SkipSpaces(lowered, &p);
          if (MatchWordAt(lowered, &p, "meant")) return true;
        }
      }
    }
  }
  // Without the "no" prefix.
  size_t probe = pos;
  if (MatchWordAt(lowered, &probe, "rather") ||
      MatchWordAt(lowered, &probe, "actually")) {
    return true;
  }
  if (probe < lowered.size() && lowered[probe] == 'i' &&
      HasWordBoundary(lowered, probe, probe + 1)) {
    size_t p = probe + 1;
    if (p < lowered.size() && IsSpace(lowered[p])) {
      SkipSpaces(lowered, &p);
      if (MatchWordAt(lowered, &p, "meant")) return true;
    }
  }
  return false;
}

// ^\s*(?:ALT|ALT|...)\s*[.!]?\s*$ with internal \s* collapsed to single
// spaces (the Python patterns use \s* / \s+ between the words).
bool IsKeywordCommand(const std::string& query,
                      std::initializer_list<const char*> alternatives) {
  std::string collapsed = NormalizeQuery(Lowercase(Strip(query)));
  // optional single trailing . or ! (Python: \s*[.!]?\s*$)
  if (!collapsed.empty() &&
      (collapsed.back() == '.' || collapsed.back() == '!')) {
    collapsed.pop_back();
  }
  while (!collapsed.empty() && collapsed.back() == ' ') collapsed.pop_back();
  for (const char* alternative : alternatives) {
    if (collapsed == alternative) return true;
  }
  return false;
}

bool IsCancel(const std::string& query) {
  // ^\s*(?:cancel|stop|never\s*mind)\s*[.!]?\s*$ — \s* allows "nevermind".
  return IsKeywordCommand(query, {"cancel", "stop", "never mind", "nevermind"});
}

bool IsReset(const std::string& query) {
  // ^\s*(?:reset|start\s+over|new\s+conversation)\s*[.!]?\s*$
  return IsKeywordCommand(query,
                          {"reset", "start over", "new conversation"});
}

/* ---------------------------------------------------------------- */
/* framer-lite: relation cues, constraints, incompleteness           */

const char* const kRelationCues[] = {
    // DEFAULT_RELATION_CUES values, flattened; presence of ANY cue matters.
    "born",       "birth",     "birthplace", "died",      "death",
    "when",       "date",      "year",       "where",     "located",
    "place",      "capital",   "how many",   "how much",  "population",
    "distance",   "height",    "who said",   "quote",     "quotation",
    "stated",     "wrote",     "what is",    "what are",  "define",
    "meaning",    "compare",   "difference", "larger",    "smaller",
    "older",      "newer",     "why",        "reason",    "cause",
    "because",    "member",    "part of",    "belongs",   "included",
    "happened",   "occurred",  "event",
};

bool FramerHasRelationCue(const std::string& normalized) {
  std::string folded = Lowercase(normalized);
  for (const char* cue : kRelationCues) {
    if (folded.find(cue) != std::string::npos) return true;
  }
  return false;
}

// YEAR_RE: \b(?:1[0-9]{3}|20[0-9]{2}|2100)\b
bool HasYearToken(const std::string& normalized) {
  for (const std::string& word : WordTokens(normalized)) {
    if (word.size() != 4) continue;
    bool digits = true;
    for (char c : word) digits = digits && (c >= '0' && c <= '9');
    if (!digits) continue;
    int value = (word[0] - '0') * 1000 + (word[1] - '0') * 100 +
                (word[2] - '0') * 10 + (word[3] - '0');
    if ((value >= 1000 && value <= 1999) || (value >= 2000 && value <= 2099) ||
        value == 2100) {
      return true;
    }
  }
  return false;
}

bool IsCapitalizedWordStart(char c) { return c >= 'A' && c <= 'Z'; }

// \b(?:in|at|near|from)\s+([A-Z][\w'-]*(?:\s+[A-Z][\w'-]*){0,4})
// Case-sensitive, matching the Python pattern (no re.I).
bool HasLocationConstraint(const std::string& normalized) {
  size_t i = 0;
  while (i < normalized.size()) {
    // find next word start
    if (i > 0 && IsWordChar(normalized[i - 1])) {
      ++i;
      continue;
    }
    static const char* kPrep[] = {"in", "at", "near", "from"};
    size_t matched_len = 0;
    for (const char* prep : kPrep) {
      size_t length = strlen(prep);
      if (normalized.compare(i, length, prep) == 0 &&
          HasWordBoundary(normalized, i, i + length)) {
        matched_len = length;
        break;
      }
    }
    if (matched_len == 0) {
      ++i;
      continue;
    }
    size_t pos = i + matched_len;
    if (pos >= normalized.size() || !IsSpace(normalized[pos])) {
      ++i;
      continue;
    }
    SkipSpaces(normalized, &pos);
    if (pos < normalized.size() && IsCapitalizedWordStart(normalized[pos])) {
      return true;
    }
    ++i;
  }
  return false;
}

// ["“]([^"”]{2,200})["”]
bool HasQuotedAttribution(const std::string& normalized) {
  for (size_t i = 0; i < normalized.size(); i++) {
    bool opens = normalized[i] == '"';
    size_t open_width = 1;
    if (!opens && i + 2 < normalized.size() && uint8_t(normalized[i]) == 0xE2 &&
        uint8_t(normalized[i + 1]) == 0x80 &&
        uint8_t(normalized[i + 2]) == 0x9C) {  // U+201C
      opens = true;
      open_width = 3;
    }
    if (!opens) continue;
    size_t j = i + open_width;
    size_t content_start = j;
    while (j < normalized.size()) {
      bool closes = normalized[j] == '"';
      if (!closes && j + 2 < normalized.size() &&
          uint8_t(normalized[j]) == 0xE2 && uint8_t(normalized[j + 1]) == 0x80 &&
          uint8_t(normalized[j + 2]) == 0x9D) {  // U+201D
        closes = true;
      }
      if (closes) {
        size_t length = j - content_start;
        if (length >= 2 && length <= 200) return true;
        break;
      }
      ++j;
    }
    i = j;
  }
  return false;
}

bool FramerIncomplete(const std::string& normalized) {
  std::vector<std::string> words = WordTokens(normalized);
  // Python len(query.split()) counts whitespace-separated pieces; for the
  // queries handled here words == pieces (punctuation attaches to words).
  // Use whitespace splitting to be exact.
  size_t pieces = 0;
  size_t i = 0;
  while (i < normalized.size()) {
    while (i < normalized.size() && IsSpace(normalized[i])) ++i;
    if (i < normalized.size()) ++pieces;
    while (i < normalized.size() && !IsSpace(normalized[i])) ++i;
  }
  if (pieces < 3) return true;
  if (normalized.size() >= 3 &&
      normalized.compare(normalized.size() - 3, 3, " of") == 0) {
    return true;
  }
  if (normalized.size() >= 6 &&
      normalized.compare(normalized.size() - 6, 6, " about") == 0) {
    return true;
  }
  if (normalized.size() >= 8 &&
      normalized.compare(normalized.size() - 8, 8, " between") == 0) {
    return true;
  }
  std::string folded = Lowercase(normalized);
  return folded.find("what about it") != std::string::npos ||
         folded.find("refer to here") != std::string::npos ||
         folded.find("which one should i mean") != std::string::npos;
}

/* ---------------------------------------------------------------- */
/* COG-lite: the six core obligations + optional constraint extras   */

enum ObligationSlot {
  kOblIdentifySubject = 0,
  kOblEstablishRelation = 1,
  kOblLocateClaim = 2,
  kOblMatchAnswerType = 3,
  kOblBindClaim = 4,
  kOblVerifyEvidence = 5,
  kOblTemporal = 6,
  kOblLocation = 7,
  kOblAttribution = 8,
  kOblMax = 9,
};

const char* const kObligationKinds[kOblMax] = {
    "IDENTIFY_SUBJECT",     "ESTABLISH_RELATION",   "LOCATE_GROUNDED_CLAIM",
    "MATCH_ANSWER_TYPE",    "BIND_CLAIM_TO_SUBJECT", "VERIFY_EVIDENCE",
    "TEMPORAL_CONSTRAINT",  "LOCATION_CONSTRAINT",  "ATTRIBUTION_CONSTRAINT",
};

struct CogLite {
  bool present[kOblMax] = {};
  bool satisfied[kOblMax] = {};
  int unresolved_count = 0;
  int evidence_count = 0;
  // progress.verifier_state: 0 NOT_RUN, 2 ACCEPTED (others unused here)
  int verifier_state_code = 0;

  void Satisfy(ObligationSlot slot) {
    if (present[slot]) satisfied[slot] = true;
  }

  bool CanHaltSuccess() const {
    // VERIFIER_REQUIRED invariant is always installed by the interpreter.
    if (verifier_state_code != 2) return false;
    for (int i = 0; i < kOblMax; i++) {
      if (present[i] && !satisfied[i]) return false;
    }
    return true;
  }

  void FillResponse(ServiceResponse* resp) const {
    int open = 0, satisfied_count = 0, total = 0;
    resp->open_mandatory_obligations.clear();
    for (int i = 0; i < kOblMax; i++) {
      if (!present[i]) continue;
      ++total;
      if (satisfied[i]) {
        ++satisfied_count;
      } else {
        ++open;
        resp->open_mandatory_obligations.push_back(kObligationKinds[i]);
      }
    }
    uint16_t* s = resp->cog_state;
    int index = 0;
    s[index++] = 1;  // compact view schema_version
    s[index++] = 1;  // open_goals (the QA goal is never marked satisfied)
    s[index++] = uint16_t(open);
    s[index++] = uint16_t(satisfied_count);
    s[index++] = 0;  // blocked_or_failed
    s[index++] = 0;  // invariant_violations
    s[index++] = 0;  // active_hypotheses
    s[index++] = 0;  // competing_hypotheses
    s[index++] = 0;  // contradictions
    s[index++] = uint16_t(evidence_count);
    s[index++] = uint16_t(unresolved_count);
    s[index++] = 1;  // open_frontier (CLAIM_SEARCH item)
    s[index++] = 0;  // observed_state_count
    s[index++] = uint16_t(total == 0 ? 1000 : (1000 * satisfied_count) / total);
    s[index++] = 0;  // stagnant_steps
    s[index++] = 0;  // repeated_error_count
    s[index++] = 0;  // repeated_action_count
    s[index++] = uint16_t(verifier_state_code);
    s[index++] = uint16_t(CanHaltSuccess() ? 1 : 0);
  }
};

// InputStateInterpreter._natural_language, reduced to observable state.
CogLite InterpretLite(const std::string& normalized,
                      const std::vector<std::string>& prior_entity_ids) {
  CogLite cog;
  bool has_pronoun = HasFramerPronoun(normalized);
  bool subject_resolved = has_pronoun && !prior_entity_ids.empty();
  cog.present[kOblIdentifySubject] = true;
  cog.satisfied[kOblIdentifySubject] = subject_resolved;
  cog.present[kOblEstablishRelation] = true;
  cog.satisfied[kOblEstablishRelation] = FramerHasRelationCue(normalized);
  for (int slot = kOblLocateClaim; slot <= kOblVerifyEvidence; slot++) {
    cog.present[slot] = true;
  }
  bool temporal = HasYearToken(normalized);
  bool location = HasLocationConstraint(normalized);
  bool attribution = HasQuotedAttribution(normalized);
  if (temporal) cog.present[kOblTemporal] = true;
  if (location) cog.present[kOblLocation] = true;
  if (attribution) cog.present[kOblAttribution] = true;
  bool unresolved_discourse = has_pronoun && prior_entity_ids.empty();
  bool clarification_need = FramerIncomplete(normalized) || unresolved_discourse;
  cog.unresolved_count = (subject_resolved ? 0 : 1) + (clarification_need ? 1 : 0);
  return cog;
}

/* ---------------------------------------------------------------- */
/* unique helper preserving first-occurrence order                   */

void PushUnique(std::vector<std::string>* values, const std::string& item) {
  if (item.empty()) return;
  for (const std::string& existing : *values)
    if (existing == item) return;
  values->push_back(item);
}

bool Contains(const std::vector<std::string>& values, const std::string& item) {
  for (const std::string& existing : values)
    if (existing == item) return true;
  return false;
}

}  // namespace

/* ================================================================== */
/* ServiceCore::Impl                                                   */

struct ServiceCore::Impl {
  struct Hyp {
    std::string entity_id;
    std::string label;
    double confidence = 0.0;
    std::string surface;
  };

  struct Choice {
    std::string choice_id;
    std::string entity_id;
    std::string label;
  };

  struct Pending {
    std::string question;
    std::vector<Choice> choices;
    std::string original_query;
  };

  struct ResolvedEnt {
    std::string entity_id;
    std::string label;
  };

  struct Session {
    bool in_use = false;
    uint64_t last_used = 0;
    std::string id;
    bool has_current_query = false;
    std::string current_query;
    std::vector<ResolvedEnt> resolved;
    bool has_prev_relation = false;
    std::string prev_relation;
    bool has_pending = false;
    Pending pending;
    uint32_t turn_count = 0;
    bool has_utterances = false;
    std::vector<std::string> handles;  // ordered unique, cap 32
  };

  struct Action {
    enum Kind { kContinue, kAskClarification, kCancel, kReset } kind;
    std::vector<std::string> entity_ids;
    bool has_relation = false;
    std::string relation;
    Pending clarification;
  };

  // Fuzzy address index (EXACT + CHAR_NGRAM channels only).
  struct SurfaceRec {
    std::string entity_id;
    std::string title;
  };
  struct Surface {
    std::string normalized;
    std::vector<SurfaceRec> recs;  // sorted by (entity_id, title)
    std::vector<std::string> tokens;
    std::vector<std::string> grams;  // unique 3-grams of "^norm$"
  };
  struct Mention {
    size_t char_start, char_end;
    int surface_index;
    double score;
  };
  struct Proposal {
    std::string entity_id;
    std::string title;
    std::string matched_surface;
    double score;
    int channel;  // 0 = exact, 1 = char_ngram
  };

  std::vector<GroundedRecord> records;
  const int8_t* weights = nullptr;  // [34][38] row-major, ops 32..65
  std::vector<Surface> surfaces;    // sorted by normalized
  std::map<std::string, int> surface_by_norm;
  std::map<std::string, std::vector<int>> gram_postings;
  size_t max_surface_tokens = 1;
  std::vector<Session> sessions;
  uint64_t use_counter = 0;
  MeasSink meas_sink = nullptr;
  void* meas_ctx = nullptr;
  ClockFn clock_fn = nullptr;
  void* clock_ctx = nullptr;
  size_t max_steps = kMaxStepsDefault;

  uint64_t NowUs() const { return clock_fn ? clock_fn(clock_ctx) : 0; }

  void EmitMeas(const char* line) const {
    if (meas_sink) meas_sink(meas_ctx, line);
  }

  /* ---------------- address index build ---------------- */

  static std::vector<std::string> CharGrams(const std::string& normalized) {
    std::string compact = "^" + normalized + "$";
    std::vector<std::string> grams;
    if (compact.size() <= 3) {
      grams.push_back(compact);
      return grams;
    }
    for (size_t i = 0; i + 3 <= compact.size(); i++) {
      PushUnique(&grams, compact.substr(i, 3));
    }
    std::sort(grams.begin(), grams.end());
    return grams;
  }

  bool BuildIndex(std::string* error) {
    std::map<std::string, std::vector<SurfaceRec>> grouped;
    for (const GroundedRecord& rec : records) {
      for (const std::string& surface : rec.address_surfaces) {
        std::string normalized = NormalizeFuzzySurface(surface);
        if (normalized.empty()) {
          if (error) *error = "address surface must contain a token";
          return false;
        }
        SurfaceRec rec_entry{rec.entity_id, rec.canonical_title};
        std::vector<SurfaceRec>& list = grouped[normalized];
        bool found = false;
        for (const SurfaceRec& existing : list) {
          if (existing.entity_id == rec_entry.entity_id &&
              existing.title == rec_entry.title) {
            found = true;  // merged duplicate (support provenance dedup)
            break;
          }
        }
        if (!found) list.push_back(rec_entry);
      }
    }
    for (auto& entry : grouped) {
      Surface surface;
      surface.normalized = entry.first;
      surface.recs = entry.second;
      std::sort(surface.recs.begin(), surface.recs.end(),
                [](const SurfaceRec& a, const SurfaceRec& b) {
                  if (a.entity_id != b.entity_id) return a.entity_id < b.entity_id;
                  return a.title < b.title;
                });
      surface.tokens = WordTokens(surface.normalized);
      surface.grams = CharGrams(surface.normalized);
      max_surface_tokens =
          std::max(max_surface_tokens, surface.tokens.size());
      surfaces.push_back(surface);
    }
    for (size_t i = 0; i < surfaces.size(); i++) {
      surface_by_norm[surfaces[i].normalized] = int(i);
      for (const std::string& gram : surfaces[i].grams) {
        gram_postings[gram].push_back(int(i));
      }
    }
    return true;
  }

  /* ---------------- fuzzy spans & channels ---------------- */

  struct Span {
    size_t char_start, char_end;
    std::string fuzzy;
    int token_count;
  };

  std::vector<Span> MakeSpans(const std::string& query) const {
    std::vector<Token> tokens = TokenizeExtended(query);
    std::vector<Span> spans;
    size_t max_tokens = std::min(max_surface_tokens + 1, size_t(8));
    for (size_t start = 0; start < tokens.size(); start++) {
      size_t limit =
          std::min(max_tokens, tokens.size() - start);
      for (size_t width = 1; width <= limit; width++) {
        size_t char_start = tokens[start].start;
        size_t char_end = tokens[start + width - 1].end;
        std::string text = query.substr(char_start, char_end - char_start);
        std::string fuzzy = NormalizeFuzzySurface(text);
        if (fuzzy.size() < 3) continue;
        spans.push_back(Span{char_start, char_end, fuzzy, int(width)});
      }
    }
    std::sort(spans.begin(), spans.end(), [](const Span& a, const Span& b) {
      if (a.token_count != b.token_count) return a.token_count > b.token_count;
      if (a.char_start != b.char_start) return a.char_start < b.char_start;
      if (a.char_end != b.char_end) return a.char_end < b.char_end;
      return a.fuzzy < b.fuzzy;
    });
    if (spans.size() > kMaxSpanCandidates) spans.resize(kMaxSpanCandidates);
    return spans;
  }

  // Per-channel mention generation; channel 0 = EXACT, 1 = CHAR_NGRAM.
  std::vector<Mention> ChannelMentions(const std::vector<Span>& spans,
                                       int channel) const {
    std::vector<Mention> mentions;
    for (const Span& span : spans) {
      if (channel == 0) {
        auto it = surface_by_norm.find(span.fuzzy);
        if (it != surface_by_norm.end()) {
          mentions.push_back(Mention{span.char_start, span.char_end,
                                     it->second, 1.0});
        }
      } else {
        std::vector<std::string> grams = CharGrams(span.fuzzy);
        std::map<int, int> counts;
        for (const std::string& gram : grams) {
          auto posting = gram_postings.find(gram);
          if (posting == gram_postings.end()) continue;
          for (int surface_index : posting->second) counts[surface_index]++;
        }
        std::vector<std::pair<double, int>> scored;
        for (const auto& entry : counts) {
          int surface_index = entry.first;
          double overlap = double(entry.second);
          double gc = double(grams.size());
          double tc = double(surfaces[surface_index].grams.size());
          double dice = 2.0 * overlap / (gc + tc);
          double containment = overlap / std::max(1.0, std::min(gc, tc));
          double score = 0.7 * dice + 0.3 * containment;
          if (score >= 0.8) scored.push_back({score, surface_index});
        }
        std::sort(scored.begin(), scored.end(),
                  [&](const std::pair<double, int>& a,
                      const std::pair<double, int>& b) {
                    if (a.first != b.first) return a.first > b.first;
                    const std::string& an = surfaces[a.second].normalized;
                    const std::string& bn = surfaces[b.second].normalized;
                    if (an != bn) return an < bn;
                    return a.second < b.second;
                  });
        size_t kept = 0;
        for (const auto& entry : scored) {
          if (kept++ >= 8) break;  // per_span_cap
          mentions.push_back(Mention{span.char_start, span.char_end,
                                     entry.second, entry.first});
        }
      }
    }
    // Dedup by (start, end, surface) keeping max score, then order.
    std::vector<Mention> deduped;
    for (const Mention& mention : mentions) {
      bool merged = false;
      for (Mention& kept : deduped) {
        if (kept.char_start == mention.char_start &&
            kept.char_end == mention.char_end &&
            kept.surface_index == mention.surface_index) {
          if (mention.score > kept.score) kept.score = mention.score;
          merged = true;
          break;
        }
      }
      // Python derives address proposals from the full ordered mention list;
      // the mention_cap=64 display cap never prunes proposals.
      if (!merged) deduped.push_back(mention);
    }
    std::sort(deduped.begin(), deduped.end(),
              [&](const Mention& a, const Mention& b) {
                if (a.score != b.score) return a.score > b.score;
                if (a.char_start != b.char_start)
                  return a.char_start < b.char_start;
                if (a.char_end != b.char_end) return a.char_end < b.char_end;
                return surfaces[a.surface_index].normalized <
                       surfaces[b.surface_index].normalized;
              });
    return deduped;
  }

  std::vector<Proposal> ChannelProposals(const std::vector<Mention>& mentions,
                                         int channel) const {
    // Aggregate per entity: best = min(-score, matched_surface, entity_id).
    std::map<std::string, Proposal> best;
    for (const Mention& mention : mentions) {
      const Surface& surface = surfaces[mention.surface_index];
      for (const SurfaceRec& rec : surface.recs) {
        Proposal proposal{rec.entity_id, rec.title, surface.normalized,
                          mention.score, channel};
        auto it = best.find(rec.entity_id);
        if (it == best.end()) {
          best[rec.entity_id] = proposal;
          continue;
        }
        Proposal& current = it->second;
        bool better = false;
        if (proposal.score != current.score) {
          better = proposal.score > current.score;
        } else if (proposal.matched_surface != current.matched_surface) {
          better = proposal.matched_surface < current.matched_surface;
        } else {
          better = proposal.entity_id < current.entity_id;
        }
        if (better) current = proposal;
      }
    }
    std::vector<Proposal> ordered;
    for (const auto& entry : best) ordered.push_back(entry.second);
    std::sort(ordered.begin(), ordered.end(),
              [](const Proposal& a, const Proposal& b) {
                if (a.score != b.score) return a.score > b.score;
                if (a.matched_surface != b.matched_surface)
                  return a.matched_surface < b.matched_surface;
                return a.entity_id < b.entity_id;
              });
    return ordered;  // pre-cap list (address_cap=32 never binds at this size)
  }

  // vertical._address: EXACT + CHAR_NGRAM, union, hypotheses.
  std::vector<Hyp> Address(const std::string& text) const {
    std::vector<Span> spans = MakeSpans(text);
    std::vector<Proposal> exact = ChannelProposals(ChannelMentions(spans, 0), 0);
    std::vector<Proposal> ngram = ChannelProposals(ChannelMentions(spans, 1), 1);
    struct UnionEntry {
      std::string entity_id;
      std::string title;
      double best_score = 0.0;
      unsigned channels = 0;  // bit0 exact, bit1 char_ngram
      std::vector<std::string> matched_surfaces;  // sorted unique
    };
    std::map<std::string, UnionEntry> grouped;
    const std::vector<Proposal>* channel_lists[2] = {&exact, &ngram};
    for (const std::vector<Proposal>* list_ptr : channel_lists) {
      for (const Proposal& proposal : *list_ptr) {
        UnionEntry& entry = grouped[proposal.entity_id];
        entry.entity_id = proposal.entity_id;
        entry.title = proposal.title;
        entry.best_score = std::max(entry.best_score, proposal.score);
        entry.channels |= (proposal.channel == 0 ? 1u : 2u);
        PushUnique(&entry.matched_surfaces, proposal.matched_surface);
      }
    }
    std::vector<UnionEntry> unioned;
    for (auto& entry : grouped) {
      std::sort(entry.second.matched_surfaces.begin(),
                entry.second.matched_surfaces.end());
      unioned.push_back(entry.second);
    }
    std::sort(unioned.begin(), unioned.end(),
              [](const UnionEntry& a, const UnionEntry& b) {
                unsigned ac = __builtin_popcount(a.channels);
                unsigned bc = __builtin_popcount(b.channels);
                if (ac != bc) return ac > bc;
                if (a.best_score != b.best_score)
                  return a.best_score > b.best_score;
                if (a.title != b.title) return a.title < b.title;
                return a.entity_id < b.entity_id;
              });
    std::vector<Hyp> hypotheses;
    for (const UnionEntry& entry : unioned) {
      if (hypotheses.size() >= kMaxAddressResults) break;
      double confidence = entry.best_score;
      if (confidence < 0.0) confidence = 0.0;
      if (confidence > 1.0) confidence = 1.0;
      hypotheses.push_back(Hyp{entry.entity_id, entry.title, confidence,
                               entry.matched_surfaces.front()});
    }
    return hypotheses;
  }

  /* ---------------- relation scoring (vertical._relation) ---------------- */

  bool RelationOf(const std::string& text, std::string* out) const {
    std::vector<std::string> tokens = WordTokens(Lowercase(text));
    struct Scored {
      std::string relation;
      int matched;
      size_t term_count;
    };
    std::vector<Scored> scores;  // insertion order
    for (const GroundedRecord& rec : records) {
      int matched = 0;
      for (const std::string& term : rec.relation_terms) {
        if (ContainsWordToken(tokens, Lowercase(term))) ++matched;
      }
      if (matched == 0) continue;
      bool found = false;
      for (Scored& existing : scores) {
        if (existing.relation != rec.relation) continue;
        found = true;
        // tuple compare (matched, len(terms), relation)
        if (matched > existing.matched ||
            (matched == existing.matched &&
             rec.relation_terms.size() > existing.term_count) ||
            (matched == existing.matched &&
             rec.relation_terms.size() == existing.term_count &&
             rec.relation > existing.relation)) {
          existing.matched = matched;
          existing.term_count = rec.relation_terms.size();
        }
        break;
      }
      if (!found) {
        scores.push_back(
            Scored{rec.relation, matched, rec.relation_terms.size()});
      }
    }
    if (scores.empty()) return false;
    const Scored* best = &scores.front();
    for (const Scored& candidate : scores) {
      if (candidate.matched > best->matched ||
          (candidate.matched == best->matched &&
           candidate.term_count > best->term_count) ||
          (candidate.matched == best->matched &&
           candidate.term_count == best->term_count &&
           candidate.relation > best->relation)) {
        best = &candidate;  // Python max: strictly-greater replaces
      }
    }
    *out = best->relation;
    return true;
  }

  /* ---------------- conversation engine ---------------- */

  Session& Load(const std::string& session_id) {
    for (Session& session : sessions) {
      if (session.in_use && session.id == session_id) {
        session.last_used = ++use_counter;
        return session;
      }
    }
    for (Session& session : sessions) {
      if (!session.in_use) {
        session = Session();
        session.in_use = true;
        session.id = session_id;
        session.last_used = ++use_counter;
        return session;
      }
    }
    // Bounded device store: evict the least recently used session.
    Session* lru = &sessions.front();
    for (Session& session : sessions) {
      if (session.last_used < lru->last_used) lru = &session;
    }
    *lru = Session();
    lru->in_use = true;
    lru->id = session_id;
    lru->last_used = ++use_counter;
    return *lru;
  }

  static bool Ambiguous(const std::vector<Hyp>& candidates) {
    std::vector<const Hyp*> plausible;
    for (const Hyp& hyp : candidates) {
      if (hyp.confidence >= 0.5) plausible.push_back(&hyp);
    }
    std::stable_sort(plausible.begin(), plausible.end(),
                     [](const Hyp* a, const Hyp* b) {
                       return a->confidence > b->confidence;
                     });
    return plausible.size() >= 2 &&
           plausible[0]->confidence - plausible[1]->confidence <= 0.12;
  }

  static std::string StripChoicePadding(const std::string& query) {
    // Python: query.casefold().strip(" .")
    std::string lowered = Lowercase(query);
    size_t begin = 0, end = lowered.size();
    while (begin < end && (lowered[begin] == ' ' || lowered[begin] == '.'))
      ++begin;
    while (end > begin && (lowered[end - 1] == ' ' || lowered[end - 1] == '.'))
      --end;
    return lowered.substr(begin, end - begin);
  }

  Action Accept(Session& state, const std::string& text,
                const std::vector<Hyp>& candidates_in, bool has_relation,
                const std::string& relation) {
    std::string query = Strip(text);
    if (IsReset(query)) {
      Session fresh;
      fresh.in_use = true;
      fresh.id = state.id;
      fresh.last_used = state.last_used;
      state = fresh;
      Action action;
      action.kind = Action::kReset;
      return action;
    }
    // utterance append (bounds: 12 turns; only the counter is observable)
    state.turn_count += 1;
    state.has_utterances = true;
    if (IsCancel(query)) {
      state.has_current_query = false;
      state.has_pending = false;
      Action action;
      action.kind = Action::kCancel;
      return action;
    }
    bool correction = IsCorrection(query);
    bool what_about = IsWhatAbout(query);
    bool referent = HasReferentPronoun(query);
    enum Intent { kDirect, kFollowUp, kReferent, kWhatAbout, kCorrection,
                  kClarificationResponse } intent = kDirect;
    if (state.has_pending) {
      intent = kClarificationResponse;
    } else if (correction) {
      intent = kCorrection;
    } else if (what_about) {
      intent = kWhatAbout;
    } else if (referent) {
      intent = kReferent;
    } else if (state.has_current_query) {
      intent = kFollowUp;
    }

    std::vector<Hyp> candidates = candidates_in;
    if (candidates.size() > kMaxCandidates) candidates.resize(kMaxCandidates);

    if (state.has_pending && candidates.empty()) {
      std::string normalized = StripChoicePadding(query);
      const Choice* selected_choice = nullptr;
      for (const Choice& choice : state.pending.choices) {
        if (normalized == Lowercase(choice.choice_id) ||
            normalized == Lowercase(choice.label)) {
          selected_choice = &choice;
          break;
        }
      }
      if (selected_choice != nullptr) {
        candidates.push_back(Hyp{selected_choice->entity_id,
                                 selected_choice->label, 1.0, query});
      } else {
        state.has_current_query = true;
        state.current_query = query;
        Action action;
        action.kind = Action::kAskClarification;
        action.clarification = state.pending;
        return action;
      }
    }

    if (Ambiguous(candidates)) {
      std::vector<const Hyp*> sorted;
      for (const Hyp& hyp : candidates) sorted.push_back(&hyp);
      std::stable_sort(sorted.begin(), sorted.end(),
                       [](const Hyp* a, const Hyp* b) {
                         return a->confidence > b->confidence;
                       });
      Pending pending;
      pending.question = "Which entity did you mean?";
      size_t index = 0;
      for (const Hyp* hyp : sorted) {
        if (pending.choices.size() >= kMaxChoices) break;
        Choice choice;
        choice.choice_id = "choice-" + std::to_string(++index);
        choice.entity_id = hyp->entity_id;
        choice.label = hyp->label;
        pending.choices.push_back(choice);
      }
      pending.original_query = query;
      state.has_current_query = true;
      state.current_query = query;
      state.has_pending = true;
      state.pending = pending;
      Action action;
      action.kind = Action::kAskClarification;
      action.clarification = pending;
      if (has_relation) {
        action.has_relation = true;
        action.relation = relation;
      } else if (state.has_prev_relation) {
        action.has_relation = true;
        action.relation = state.prev_relation;
      }
      return action;
    }

    const Hyp* selected = nullptr;
    for (const Hyp& hyp : candidates) {
      if (selected == nullptr || hyp.confidence > selected->confidence) {
        selected = &hyp;  // Python max: first maximal wins
      }
    }
    Action action;
    action.kind = Action::kContinue;
    if (selected != nullptr) {
      std::vector<ResolvedEnt> resolved;
      for (const ResolvedEnt& existing : state.resolved) {
        if (existing.entity_id != selected->entity_id) resolved.push_back(existing);
      }
      resolved.push_back(ResolvedEnt{selected->entity_id, selected->label});
      if (resolved.size() > kMaxResolved) {
        resolved.erase(resolved.begin(),
                       resolved.begin() + (resolved.size() - kMaxResolved));
      }
      state.resolved = resolved;
      action.entity_ids.push_back(selected->entity_id);
    } else if (referent && !state.resolved.empty()) {
      action.entity_ids.push_back(state.resolved.back().entity_id);
    }
    bool effective_has = has_relation;
    std::string effective = relation;
    if (!effective_has &&
        (what_about || referent || intent == kFollowUp) &&
        state.has_prev_relation) {
      effective_has = true;
      effective = state.prev_relation;
    }
    state.has_current_query = true;
    state.current_query = query;
    state.has_prev_relation = effective_has;
    state.prev_relation = effective;
    state.has_pending = false;
    action.has_relation = effective_has;
    action.relation = effective;
    return action;
  }

  void RecordAnswer(Session& state, const std::string& handle_id) {
    PushUnique(&state.handles, handle_id);
    if (state.handles.size() > kMaxHandles) {
      state.handles.erase(state.handles.begin(),
                          state.handles.begin() +
                              (state.handles.size() - kMaxHandles));
    }
  }

  /* ---------------- workspace & micro-operations ---------------- */

  static std::string ShapeOf(const std::string& answer_kind) {
    if (answer_kind == "DATE") return "date";
    if (answer_kind == "QUANTITY") return "quantity";
    if (answer_kind == "LIST") return "list";
    if (answer_kind == "COMPARISON") return "comparison";
    if (answer_kind == "QUOTATION") return "quotation";
    return "definition";
  }

  struct Claim {
    std::string claim_id;
    std::string subject;
    std::string relation;
    std::string value;       // object_value
    std::string answer_shape;
    std::string span_id;     // single source span per fixture record
    double confidence = 1.0;
    size_t record_index = 0;
  };
  struct SpanRec {
    std::string span_id;
    std::string text;
    std::string text_hash;
    std::string source_family;  // "CORPUS"
  };

  struct MicroState {
    // frame
    std::vector<std::string> frame_entities;
    std::vector<std::string> frame_relations;
    std::string answer_shape;
    bool facet_object = true;      // definition/date/... path
    bool facet_quantity = false;   // quantity path
    bool facet_quotation = false;  // quotation path
    // workspace
    std::vector<Claim> claims;
    std::vector<SpanRec> spans;
    // mutable controller slots
    std::vector<std::string> active, enumerated_values, enumerated_entities;
    std::vector<std::string> enumerated_relations, selected, rejected;
    std::vector<std::string> selected_sources, selected_entities, bound;
    std::vector<std::string> derived, plan_values, plan_claim_ids;
    std::vector<std::string> plan_source_ids;
    std::string plan_shape;
    bool has_plan_shape = false;
    std::string plan_answer_text;
    bool has_plan_answer_text = false;
    bool verification_passed = false;
    bool has_terminal = false;
    std::string terminal;
    std::vector<std::string> answer_values;
    uint32_t op_counts[256] = {};
    uint32_t total_actions = 0;
  };

  const Claim* FindClaim(const MicroState& state, const std::string& id) const {
    for (const Claim& claim : state.claims)
      if (claim.claim_id == id) return &claim;
    return nullptr;
  }

  static std::string ClaimValue(const Claim& claim, const std::string& shape) {
    (void)shape;  // claims carry object_value only (quotation absent)
    return claim.value;
  }

  const SpanRec* FindSpan(const MicroState& state,
                          const std::string& id) const {
    for (const SpanRec& span : state.spans)
      if (span.span_id == id) return &span;
    return nullptr;
  }

  bool ClaimCanPassStaticVerifier(const MicroState& state,
                                  const Claim& claim) const {
    if (!state.frame_entities.empty() &&
        !Contains(state.frame_entities, claim.subject)) {
      return false;  // object_entity_id absent on this plane
    }
    if (!state.frame_relations.empty() &&
        !Contains(state.frame_relations, claim.relation)) {
      return false;
    }
    std::string surface = ClaimValue(claim, state.answer_shape);
    if (surface.empty()) return false;
    const SpanRec* span = FindSpan(state, claim.span_id);
    return span != nullptr && span->text.find(surface) != std::string::npos;
  }

  /* ---------------- legal action mask (micro_ops.legal_actions) ---------------- */

  struct MicroAction {
    int op;
    std::string arg_key;    // claim_id | source_id | entity_id | event
    std::string arg_value;
    bool has_arg = false;
    std::string args_json;  // canonical {"key":"value"} for the tiebreak
  };

  bool OpLegal(const MicroState& state, int op) const {
    if (state.has_terminal) return false;
    uint8_t cap = 1;  // maximum_repeat_count (micro_ops._spec defaults)
    if (op == 43) cap = 8;
    else if (op == 44) cap = 16;
    else if (op == 45) cap = 8;
    else if (op == 46) cap = 4;
    if (state.op_counts[op] >= cap) return false;
    bool claims = !state.claims.empty();
    bool active = !state.active.empty();
    bool spans = !state.spans.empty();
    switch (op) {
      case 32: return claims;
      case 33: return active;
      case 34: return claims;
      case 35: return claims;
      case 36: return active && !state.frame_entities.empty();
      case 37: return active && !state.frame_relations.empty();
      case 38: return active && !state.answer_shape.empty();
      case 39: return active && !state.answer_shape.empty();
      case 40: return false;  // temporal constraints never set by vertical
      case 41: return false;  // attribution constraints never set
      case 42: return active && spans;
      case 43: return active;
      case 44: return active;
      case 45: return spans;
      case 46: return !state.enumerated_entities.empty();
      case 47: return !state.selected.empty();
      case 48: return !state.selected.empty();
      case 49: return active;
      case 50: return active;
      case 51: return active;
      case 52: return !state.bound.empty();
      case 53: return active;
      case 54: return !state.selected.empty();
      case 55: return !state.selected.empty();
      case 56: return !state.bound.empty();
      case 57: return !state.bound.empty() && !state.derived.empty();
      case 58: return !state.plan_values.empty();
      case 59: return !state.plan_values.empty() && !state.plan_claim_ids.empty();
      case 60: return state.verification_passed;
      case 61: return false;  // frame clarification_need always false here
      case 62: return true;
      case 63: return false;  // premise_refuted never set
      case 64: return false;  // contradictions never set
      case 65: return false;  // out_of_corpus never set
      default: return false;
    }
  }

  std::vector<MicroAction> LegalActions(const MicroState& state) const {
    if (state.has_terminal) return {};
    std::vector<int> specs;
    for (int op = 32; op <= 65; op++) {
      if (OpLegal(state, op)) specs.push_back(op);
    }
    const std::string& shape = state.answer_shape;
    if (state.verification_passed) {
      specs.erase(std::remove_if(specs.begin(), specs.end(),
                                 [](int op) { return op != 60; }),
                  specs.end());
    } else if (!state.plan_values.empty()) {
      specs.erase(std::remove_if(specs.begin(), specs.end(),
                                 [](int op) { return op != 59; }),
                  specs.end());
    } else if (shape == "comparison" && !state.selected.empty()) {
      int wanted;
      if (state.selected.size() < 2) wanted = 43;
      else if (state.bound.empty()) wanted = 48;
      else if (state.derived.empty()) wanted = 52;
      else wanted = 57;
      specs.erase(std::remove_if(specs.begin(), specs.end(),
                                 [&](int op) { return op != wanted; }),
                  specs.end());
    } else if (shape == "list" && !state.selected.empty()) {
      size_t list_target = 2;  // entity_mentions always empty: max(2, 0)
      int wanted;
      if (state.selected.size() < list_target) wanted = 43;
      else if (state.bound.size() < state.selected.size()) wanted = 47;
      else wanted = 56;
      specs.erase(std::remove_if(specs.begin(), specs.end(),
                                 [&](int op) { return op != wanted; }),
                  specs.end());
    } else if (!state.selected.empty() && shape != "list" &&
               shape != "comparison") {
      specs.erase(std::remove_if(specs.begin(), specs.end(),
                                 [](int op) { return op != 55; }),
                  specs.end());
    }

    std::vector<MicroAction> actions;
    for (int op : specs) {
      std::vector<std::string> arg_values;
      std::string arg_key;
      if (op == 43 || op == 44) {
        arg_key = "claim_id";
        std::vector<std::string> ids = state.active;
        if (op == 43) {
          std::vector<std::string> filtered;
          for (const std::string& id : ids) {
            if (Contains(state.selected, id)) continue;
            const Claim* claim = FindClaim(state, id);
            if (claim != nullptr && ClaimCanPassStaticVerifier(state, *claim)) {
              filtered.push_back(id);
            }
          }
          ids = filtered;
          size_t limit = shape == "list" ? 6 : shape == "comparison" ? 2 : 1;
          if ((shape == "list" || shape == "comparison") &&
              !state.selected.empty()) {
            std::set<std::string> selected_subjects;
            for (const std::string& id : state.selected) {
              const Claim* claim = FindClaim(state, id);
              if (claim) selected_subjects.insert(claim->subject);
            }
            std::vector<std::string> next;
            for (const std::string& id : ids) {
              const Claim* claim = FindClaim(state, id);
              if (claim && !selected_subjects.count(claim->subject)) {
                next.push_back(id);
              }
            }
            ids = next;
            if (shape == "comparison") {
              const Claim* first = FindClaim(state, state.selected.front());
              std::string relation = first ? first->relation : "";
              std::vector<std::string> rel_filtered;
              for (const std::string& id : ids) {
                const Claim* claim = FindClaim(state, id);
                if (claim && claim->relation == relation) {
                  rel_filtered.push_back(id);
                }
              }
              ids = rel_filtered;
            }
            if (shape == "list") {
              // All vertical spans share source_family "CORPUS", so the
              // family tuples of any two claims are identical.
              std::set<std::string> selected_families;
              for (const std::string& id : state.selected) {
                const Claim* claim = FindClaim(state, id);
                const SpanRec* span =
                    claim ? FindSpan(state, claim->span_id) : nullptr;
                selected_families.insert(span ? span->source_family : "");
              }
              std::vector<std::string> fam_filtered;
              for (const std::string& id : ids) {
                const Claim* claim = FindClaim(state, id);
                const SpanRec* span =
                    claim ? FindSpan(state, claim->span_id) : nullptr;
                std::string family = span ? span->source_family : "";
                if (!selected_families.count(family)) fam_filtered.push_back(id);
              }
              ids = fam_filtered;
            }
          }
          if (state.selected.size() >= limit) ids.clear();
        }
        arg_values = ids;
      } else if (op == 47) {
        arg_key = "claim_id";
        for (const std::string& id : state.selected) {
          if (!Contains(state.bound, id)) arg_values.push_back(id);
        }
      } else if (op == 45) {
        arg_key = "source_id";
        for (const SpanRec& span : state.spans) {
          if (!span.span_id.empty()) arg_values.push_back(span.span_id);
        }
      } else if (op == 42) {
        arg_key = "source_id";
        for (const std::string& id : state.active) {
          const Claim* claim = FindClaim(state, id);
          if (claim) PushUnique(&arg_values, claim->span_id);
        }
      } else if (op == 46 || op == 49) {
        arg_key = "entity_id";
        arg_values = state.enumerated_entities;
      } else if (op == 50) {
        arg_key = "event";
        // claims carry no occurred_at on this plane: no events
      }
      // Shape-incompatible plan constructors create dead branches only.
      bool incompatible =
          (op == 55 && (shape == "list" || shape == "comparison")) ||
          (op == 56 && shape != "list") || (op == 57 && shape != "comparison");
      if (incompatible) arg_values.clear();
      if (arg_values.size() > 64) arg_values.resize(64);  // argument_cap
      if (arg_key.empty()) {
        MicroAction action;
        action.op = op;
        action.args_json = "{}";
        actions.push_back(action);
      } else {
        for (const std::string& value : arg_values) {
          MicroAction action;
          action.op = op;
          action.arg_key = arg_key;
          action.arg_value = value;
          action.has_arg = true;
          action.args_json = "{\"" + arg_key + "\":\"" + value + "\"}";
          actions.push_back(action);
        }
      }
    }
    return actions;
  }

  /* ---------------- adaptive policy features (38) + int8 scoring ---------------- */

  // adaptive_policy._value_kind
  static std::string ValueKind(const std::string& value) {
    std::string lowered = Lowercase(Strip(value));
    if (lowered.size() >= 4 && isdigit_(lowered[0]) && isdigit_(lowered[1]) &&
        isdigit_(lowered[2]) && isdigit_(lowered[3])) {
      return "date";
    }
    for (char c : lowered)
      if (isdigit_(c)) return "quantity";
    return "text";
  }
  static bool isdigit_(char c) { return c >= '0' && c <= '9'; }

  // adaptive_policy._evidence_features
  void EvidenceFeatures(const MicroState& state, const Claim& claim,
                        const std::string& value, double* exact,
                        double* context, double* position,
                        double* occurrence_inverse) const {
    *exact = 0.0;
    *context = 0.0;
    *position = 0.0;
    *occurrence_inverse = 0.0;
    const SpanRec* span = FindSpan(state, claim.span_id);
    if (span == nullptr) return;
    std::string text = span->text;
    std::string lowered = Lowercase(text);
    std::string value_lower = Lowercase(value);
    if (value_lower.empty()) return;
    size_t pos = lowered.find(value_lower);
    if (pos == std::string::npos) return;
    *exact = 1.0;
    // Python str.count: non-overlapping occurrences
    size_t occurrences = 0;
    size_t cursor = 0;
    while (true) {
      size_t found = lowered.find(value_lower, cursor);
      if (found == std::string::npos) break;
      ++occurrences;
      cursor = found + value_lower.size();
    }
    *context = std::min(1.0, std::log2(1.0 + double(text.size())) / 10.0);
    *position =
        1.0 - std::min(1.0, double(pos) / double(std::max<size_t>(1, text.size())));
    *occurrence_inverse = 1.0 / double(std::max<size_t>(1, occurrences));
  }

  void ActionFeatures(const MicroState& state, const MicroAction& action,
                      double features[38]) const {
    for (int i = 0; i < 38; i++) features[i] = 0.0;
    const std::string& shape = state.answer_shape;
    const Claim* claim =
        (action.has_arg && action.arg_key == "claim_id")
            ? FindClaim(state, action.arg_value)
            : nullptr;
    // hypotheses: candidate_entity_ids -> 1.0 (entity_mentions always empty)
    bool hypotheses_nonempty = !state.frame_entities.empty();
    // required obligations (adaptive_policy._required_obligations)
    bool req[9] = {};
    req[0] = true;  // claim
    req[1] = true;  // answer_type
    req[2] = true;  // evidence
    // facets: subject/relation always present on the vertical frame
    req[3] = true;  // subject (facet or hypotheses)
    req[4] = true;  // relation (facet or requested families)
    req[5] = state.facet_object;      // object
    req[6] = shape == "date";         // time (no temporal constraints here)
    req[7] = false;                   // attribution
    req[8] = false;                   // location
    int required_count = 0;
    for (int i = 0; i < 9; i++) required_count += req[i] ? 1 : 0;
    if (required_count == 0) required_count = 1;

    double subject_match = 0.0, subject_conflict = 0.0, subject_conf = 0.0;
    double relation_match = 0.0, shape_match = 0.0, time_match = 0.0;
    double attribution_match = 0.0;
    double exact = 0.0, context = 0.0, position = 0.0, occurrence_inv = 0.0;
    double confidence = 0.0, confidence_contrast = 0.0;
    int satisfied = 0, violated = 0;
    if (claim != nullptr) {
      bool subject_in = Contains(state.frame_entities, claim->subject);
      subject_match = subject_in ? 1.0 : 0.0;
      subject_conflict =
          (hypotheses_nonempty && !subject_in) ? 1.0 : 0.0;
      subject_conf = subject_in ? 1.0 : 0.0;
      relation_match =
          (state.frame_relations.empty() ||
           Contains(state.frame_relations, claim->relation))
              ? 1.0
              : 0.0;
      std::string value = ClaimValue(*claim, shape);
      std::string expected_kind =
          (shape == "quantity" || shape == "comparison") ? "quantity" : shape;
      shape_match = (claim->answer_shape == shape ||
                     (shape == "list" &&
                      (claim->answer_shape == "definition" ||
                       claim->answer_shape == "entity" ||
                       claim->answer_shape == "person")) ||
                     ValueKind(value) == expected_kind)
                        ? 1.0
                        : 0.0;
      time_match = 1.0;  // shape != "date" && no temporal constraints
      attribution_match = 1.0;  // no attribution constraints
      EvidenceFeatures(state, *claim, value, &exact, &context, &position,
                       &occurrence_inv);
      confidence = claim->confidence;
      if (confidence < 0.0) confidence = 0.0;
      if (confidence > 1.0) confidence = 1.0;
      double peer_max = confidence;
      bool have_peer = false;
      for (const std::string& id : state.active) {
        const Claim* peer = FindClaim(state, id);
        if (peer == nullptr) continue;
        double pc = peer->confidence < 0.0 ? 0.0
                    : peer->confidence > 1.0 ? 1.0 : peer->confidence;
        if (!have_peer || pc > peer_max) peer_max = pc;
        have_peer = true;
      }
      // Python: max(peer_confidences, default=confidence); peers are the
      // active claims (which may include this claim itself).
      if (!have_peer) peer_max = confidence;
      confidence_contrast = confidence - peer_max;
      bool tests[9] = {};
      tests[0] = !value.empty();
      tests[1] = shape_match > 0.0;
      tests[2] = exact > 0.0;
      tests[3] = subject_match > 0.0;
      tests[4] = relation_match > 0.0;
      tests[5] = !value.empty();
      tests[6] = time_match > 0.0;
      tests[7] = attribution_match > 0.0;
      tests[8] = false;  // no location_entity_id on this plane
      for (int i = 0; i < 9; i++) {
        if (!req[i]) continue;
        if (tests[i]) ++satisfied;
        else ++violated;
      }
    }
    int competing = 0;
    for (const std::string& id : state.active) {
      if (!claim || id != claim->claim_id) ++competing;
    }

    double reqd = double(required_count);
    features[0] = 1.0;
    features[1] = (shape == "definition" || shape == "person" ||
                   shape == "entity")
                      ? 1.0
                      : 0.0;
    features[2] = shape == "date" ? 1.0 : 0.0;
    features[3] = shape == "quantity" ? 1.0 : 0.0;
    features[4] = shape == "quotation" ? 1.0 : 0.0;
    features[5] = shape == "list" ? 1.0 : 0.0;
    features[6] = shape == "comparison" ? 1.0 : 0.0;
    features[7] = state.active.empty() ? 0.0 : 1.0;
    features[8] = state.selected.empty() ? 0.0 : 1.0;
    features[9] = state.bound.empty() ? 0.0 : 1.0;
    features[10] = state.derived.empty() ? 0.0 : 1.0;
    features[11] = state.plan_values.empty() ? 0.0 : 1.0;
    features[12] = state.verification_passed ? 1.0 : 0.0;
    features[13] = std::min(1.0, double(state.selected.size()) / 6.0);
    features[14] = std::min(1.0, double(state.bound.size()) / 6.0);
    features[15] = std::min(1.0, double(state.total_actions) / 12.0);
    features[16] = std::min(1.0, double(state.op_counts[action.op]) / 8.0);
    features[17] = action.has_arg ? 1.0 : 0.0;
    features[18] = std::min(1.0, reqd / 9.0);
    features[19] = double(satisfied) / reqd;
    features[20] = double(violated) / reqd;
    features[21] = double(required_count - satisfied) / reqd;
    features[22] = double(satisfied) / reqd;
    features[23] = std::min(1.0, double(competing) / 32.0);
    features[24] = 0.0;  // contradiction
    features[25] = subject_match;
    features[26] = subject_conflict;
    features[27] = subject_conf;
    features[28] = relation_match;
    features[29] = shape_match;
    features[30] = time_match;
    features[31] = attribution_match;
    features[32] = exact;
    features[33] = context;
    features[34] = position;
    features[35] = occurrence_inv;
    features[36] = confidence;
    features[37] = confidence_contrast;
  }

  // Python round() is round-half-even; nearbyint under the default
  // FE_TONEAREST environment matches it exactly.
  static long QuantizeFeature(double value) {
    return long(std::nearbyint(value * 256.0));
  }

  int64_t ScoreAction(const MicroState& state, const MicroAction& action) const {
    double features[38];
    ActionFeatures(state, action, features);
    const int8_t* row = weights + size_t(action.op - 32) * 38;
    int64_t score = 0;
    for (int i = 0; i < 38; i++) {
      score += int64_t(row[i]) * int64_t(QuantizeFeature(features[i]));
    }
    return score;
  }

  bool PolicySelect(const MicroState& state, MicroAction* out) const {
    std::vector<MicroAction> actions = LegalActions(state);
    if (actions.empty()) return false;
    // Python max key: (score, -index, -operation_id, canonical_args_json)
    // Python max key: (score, -index, -operation_id, canonical_args_json).
    // Iteration is in ascending index order, so any later action loses every
    // tie on -index; only a strictly greater score displaces the incumbent.
    size_t best = 0;
    int64_t best_score = ScoreAction(state, actions[0]);
    for (size_t i = 1; i < actions.size(); i++) {
      int64_t score = ScoreAction(state, actions[i]);
      if (score > best_score) {
        best = i;
        best_score = score;
      }
    }
    *out = actions[best];
    return true;
  }

  /* ---------------- exact verifier (VERIFY_PLAN internals) ---------------- */

  static bool FindNumber(const std::string& text, double* out) {
    // micro_ops._numeric: first [-+]?\d[\d,]*(?:\.\d+)? with commas stripped
    for (size_t i = 0; i < text.size(); i++) {
      bool sign = (text[i] == '-' || text[i] == '+');
      size_t start = sign ? i + 1 : i;
      if (start >= text.size() || !isdigit_(text[start])) continue;
      if (sign && i > 0 && (isdigit_(text[i - 1]) || text[i - 1] == '.')) {
        continue;  // not a match start (regex leftmost-first approximation)
      }
      size_t end = start + 1;
      while (end < text.size() &&
             (isdigit_(text[end]) || text[end] == ',')) {
        ++end;
      }
      if (end < text.size() && text[end] == '.' && end + 1 < text.size() &&
          isdigit_(text[end + 1])) {
        end += 2;
        while (end < text.size() && isdigit_(text[end])) ++end;
      }
      std::string number;
      for (size_t k = start; k < end; k++) {
        if (text[k] != ',') number.push_back(text[k]);
      }
      double value = 0.0;
      size_t dot = number.find('.');
      std::string whole = dot == std::string::npos ? number : number.substr(0, dot);
      for (char c : whole) value = value * 10.0 + double(c - '0');
      if (dot != std::string::npos) {
        double scale = 0.1;
        for (size_t k = dot + 1; k < number.size(); k++) {
          value += double(number[k] - '0') * scale;
          scale *= 0.1;
        }
      }
      *out = (sign && text[i] == '-') ? -value : value;
      return true;
    }
    return false;
  }

  // verify_realization reduced to the vertical workspace planes (direct /
  // list / comparison shapes; quotation/date/quantity extras included).
  bool VerifyPlan(MicroState& state) {
    // plan_source_ids (unique over plan claims)
    std::vector<std::string> source_ids;
    for (const std::string& id : state.plan_claim_ids) {
      const Claim* claim = FindClaim(state, id);
      if (claim) PushUnique(&source_ids, claim->span_id);
    }
    state.plan_source_ids = source_ids;

    std::string shape = state.has_plan_shape ? state.plan_shape : state.answer_shape;
    // make_answer_plan + realize_plan
    struct Planned {
      const Claim* claim;
      std::string surface;
    };
    std::vector<Planned> planned;
    std::string comparison_operator;
    if (shape == "comparison") {
      if (state.plan_claim_ids.size() != 2) return false;
      for (const std::string& id : state.plan_claim_ids) {
        const Claim* claim = FindClaim(state, id);
        if (!claim) return false;
        planned.push_back(Planned{claim, claim->value});
      }
      // operator = first of < = > found in answer_text with spaces
      std::string answer_text = state.plan_values.size() >= 2
          ? state.plan_values[0] + " " + state.plan_values[1]
          : "";
      (void)answer_text;
      // vertical builds answer_text from derived operator; recover from plan
      if (!state.derived.empty()) comparison_operator = state.derived[0];
      if (comparison_operator != "<" && comparison_operator != "=" &&
          comparison_operator != ">") {
        return false;
      }
    } else if (shape == "list") {
      for (const std::string& id : state.plan_claim_ids) {
        const Claim* claim = FindClaim(state, id);
        if (!claim) return false;
        planned.push_back(Planned{claim, claim->value});
      }
    } else {
      if (state.plan_claim_ids.empty()) return false;
      const Claim* claim = FindClaim(state, state.plan_claim_ids.front());
      if (!claim) return false;
      std::string answer_text;
      for (size_t i = 0; i < state.plan_values.size(); i++) {
        if (i) answer_text += "; ";
        answer_text += state.plan_values[i];
      }
      planned.push_back(Planned{claim, answer_text});
    }
    // realize_plan
    std::string text;
    if (shape == "comparison") {
      text = planned[0].surface + " " + comparison_operator + " " +
             planned[1].surface + ".";
    } else if (shape == "list") {
      for (size_t i = 0; i < planned.size(); i++) {
        if (i) text += "; ";
        text += planned[i].surface;
      }
    } else {
      text = planned[0].surface;
    }
    // bindings: locate each surface from a moving cursor
    struct Binding {
      const Planned* planned;
      size_t start, end;
    };
    std::vector<Binding> bindings;
    size_t cursor = 0;
    for (const Planned& item : planned) {
      size_t found = text.find(item.surface, cursor);
      if (found == std::string::npos) return false;
      bindings.push_back(Binding{&item, found, found + item.surface.size()});
      cursor = found + item.surface.size();
    }
    // verify_realization checks
    bool ok = true;
    // HAS_BINDINGS
    ok = ok && !bindings.empty();
    // SOURCE_HASH per graph span
    for (const SpanRec& span : state.spans) {
      char digest[65];
      Sha256Hex(span.text.c_str(), digest);
      std::string plain = digest;
      std::string prefixed = "sha256:" + plain;
      ok = ok && (span.text_hash == plain || span.text_hash == prefixed);
    }
    std::set<std::string> covered;
    for (const Binding& binding : bindings) {
      // SURFACE_OFFSET
      ok = ok && text.compare(binding.start, binding.end - binding.start,
                              binding.planned->surface) == 0;
      // CLAIM_SOURCE / SPAN_PRESENT / SOURCE_CONTAINS_SURFACE /
      // ENTITY_DIRECTION / RELATION_DIRECTION
      const Claim* claim = binding.planned->claim;
      ok = ok && claim != nullptr;
      if (claim == nullptr) continue;
      const SpanRec* span = FindSpan(state, claim->span_id);
      ok = ok && span != nullptr;
      if (span != nullptr) {
        ok = ok && span->text.find(binding.planned->surface) !=
                       std::string::npos;
      }
      covered.insert(claim->claim_id);
      if (!state.frame_entities.empty()) {
        ok = ok && Contains(state.frame_entities, claim->subject);
      }
      if (!state.frame_relations.empty()) {
        ok = ok && Contains(state.frame_relations, claim->relation);
      }
      if (shape == "date") {
        ok = ok && HasYearToken(binding.planned->surface) &&
             HasYearToken(claim->value);
      }
      if (shape == "quantity") {
        double ignored;
        ok = ok && FindNumber(binding.planned->surface, &ignored) &&
             FindNumber(claim->value, &ignored);
      }
    }
    // PLAN_COVERAGE: every planned claim surfaced exactly once
    std::set<std::string> expected;
    for (const Planned& item : planned) expected.insert(item.claim->claim_id);
    ok = ok && expected == covered;
    // SOURCE_LINEAGE_DIVERSITY for multi-claim compositions: vertical spans
    // all carry family "CORPUS", so >1 planned claims always fail here —
    // exactly as in Python (identical family tuples are rejected).
    if (planned.size() > 1) {
      std::set<std::string> families;
      bool distinct = true;
      for (const Planned& item : planned) {
        const SpanRec* span = FindSpan(state, item.claim->span_id);
        std::string family = span ? span->source_family : "";
        if (!families.insert(family).second) distinct = false;
      }
      ok = ok && distinct;
    }
    if (shape == "comparison" && expected.size() == 2) {
      double left, right;
      if (!FindNumber(planned[0].claim->value, &left) ||
          !FindNumber(planned[1].claim->value, &right)) {
        ok = false;
      } else {
        std::string expected_op =
            left < right ? "<" : left > right ? ">" : "=";
        ok = ok && comparison_operator == expected_op;
      }
    }
    // NO_GRAPH_CONTRADICTION: the vertical workspace never sets any.
    state.verification_passed = ok;
    if (ok) {
      state.plan_values.clear();
      for (const Planned& item : planned) state.plan_values.push_back(item.surface);
      state.plan_answer_text = text;
      state.has_plan_answer_text = true;
    }
    return ok;
  }

  /* ---------------- execute_action ---------------- */

  // micro_ops._value_kind (fullmatch variants)
  static std::string MicroValueKind(const std::string& value) {
    std::string lowered = Lowercase(Strip(value));
    // \d{4}(-\d{2}(-\d{2})?)?
    auto all_digits = [](const std::string& s) {
      if (s.empty()) return false;
      for (char c : s)
        if (!isdigit_(c)) return false;
      return true;
    };
    std::vector<std::string> parts;
    size_t begin = 0;
    for (size_t i = 0; i <= lowered.size(); i++) {
      if (i == lowered.size() || lowered[i] == '-') {
        parts.push_back(lowered.substr(begin, i - begin));
        begin = i + 1;
      }
    }
    if ((parts.size() >= 1 && parts.size() <= 3) && parts[0].size() == 4 &&
        all_digits(parts[0]) &&
        (parts.size() < 2 || (parts[1].size() == 2 && all_digits(parts[1]))) &&
        (parts.size() < 3 || (parts[2].size() == 2 && all_digits(parts[2])))) {
      return "date";
    }
    // [-+]?\d[\d,.]*(\s*[a-z%²/]+)?
    size_t pos = 0;
    if (pos < lowered.size() && (lowered[pos] == '-' || lowered[pos] == '+')) {
      ++pos;
    }
    if (pos < lowered.size() && isdigit_(lowered[pos])) {
      ++pos;
      while (pos < lowered.size() &&
             (isdigit_(lowered[pos]) || lowered[pos] == ',' ||
              lowered[pos] == '.')) {
        ++pos;
      }
      while (pos < lowered.size() && IsSpace(lowered[pos])) ++pos;
      size_t unit_start = pos;
      while (pos < lowered.size()) {
        char c = lowered[pos];
        if ((c >= 'a' && c <= 'z') || c == '%' || c == '/') {
          ++pos;
        } else if (uint8_t(c) == 0xC2 && pos + 1 < lowered.size() &&
                   uint8_t(lowered[pos + 1]) == 0xB2) {  // '²'
          pos += 2;
        } else {
          break;
        }
      }
      if (pos == lowered.size()) return "quantity";
      (void)unit_start;
    }
    return "text";
  }

  bool Execute(MicroState& state, const MicroAction& action) {
    if (!OpLegal(state, action.op)) return false;
    state.op_counts[action.op] += 1;
    state.total_actions += 1;
    switch (action.op) {
      case 32:  // ENUMERATE_CLAIMS
        state.active.clear();
        for (const Claim& claim : state.claims) state.active.push_back(claim.claim_id);
        return true;
      case 33: {  // ENUMERATE_VALUES
        state.enumerated_values.clear();
        for (const std::string& id : state.active) {
          const Claim* claim = FindClaim(state, id);
          if (claim) PushUnique(&state.enumerated_values, ClaimValue(*claim, state.answer_shape));
        }
        return true;
      }
      case 34: {  // ENUMERATE_ENTITIES
        state.enumerated_entities.clear();
        for (const Claim& claim : state.claims) {
          PushUnique(&state.enumerated_entities, claim.subject);
        }
        return true;
      }
      case 35: {  // ENUMERATE_RELATIONS
        state.enumerated_relations.clear();
        for (const Claim& claim : state.claims) {
          PushUnique(&state.enumerated_relations, claim.relation);
        }
        return true;
      }
      case 36: {  // FILTER_SUBJECT
        std::vector<std::string> kept;
        for (const std::string& id : state.active) {
          const Claim* claim = FindClaim(state, id);
          if (claim && Contains(state.frame_entities, claim->subject)) {
            kept.push_back(id);
          }
        }
        state.active = kept;
        return true;
      }
      case 37: {  // FILTER_RELATION
        std::vector<std::string> kept;
        for (const std::string& id : state.active) {
          const Claim* claim = FindClaim(state, id);
          if (claim && Contains(state.frame_relations, claim->relation)) {
            kept.push_back(id);
          }
        }
        state.active = kept;
        return true;
      }
      case 38: {  // FILTER_ANSWER_SHAPE
        std::vector<std::string> kept;
        for (const std::string& id : state.active) {
          const Claim* claim = FindClaim(state, id);
          if (claim && claim->answer_shape == state.answer_shape) {
            kept.push_back(id);
          }
        }
        state.active = kept;
        return true;
      }
      case 39: {  // FILTER_VALUE_KIND
        std::string allowed = state.answer_shape == "date" ? "date"
                              : state.answer_shape == "quantity" ? "quantity"
                                                                 : "text";
        std::vector<std::string> kept;
        for (const std::string& id : state.active) {
          const Claim* claim = FindClaim(state, id);
          if (claim &&
              MicroValueKind(ClaimValue(*claim, state.answer_shape)) == allowed) {
            kept.push_back(id);
          }
        }
        state.active = kept;
        return true;
      }
      case 42: {  // FILTER_SOURCE
        std::vector<std::string> kept;
        for (const std::string& id : state.active) {
          const Claim* claim = FindClaim(state, id);
          if (claim && claim->span_id == action.arg_value) kept.push_back(id);
        }
        state.active = kept;
        return true;
      }
      case 43: {  // SELECT_CLAIM
        if (!Contains(state.active, action.arg_value)) return false;
        if (Contains(state.selected, action.arg_value)) return false;
        PushUnique(&state.selected, action.arg_value);
        return true;
      }
      case 44: {  // REJECT_CLAIM
        if (!Contains(state.active, action.arg_value)) return false;
        PushUnique(&state.rejected, action.arg_value);
        std::vector<std::string> kept;
        for (const std::string& id : state.active) {
          if (id != action.arg_value) kept.push_back(id);
        }
        state.active = kept;
        return true;
      }
      case 45: {  // SELECT_SOURCE
        if (FindSpan(state, action.arg_value) == nullptr) return false;
        PushUnique(&state.selected_sources, action.arg_value);
        return true;
      }
      case 46: {  // SELECT_ENTITY
        if (!Contains(state.enumerated_entities, action.arg_value)) return false;
        PushUnique(&state.selected_entities, action.arg_value);
        return true;
      }
      case 47: {  // BIND_LIST_SLOT
        if (!Contains(state.selected, action.arg_value)) return false;
        if (Contains(state.bound, action.arg_value)) return false;
        PushUnique(&state.bound, action.arg_value);
        return true;
      }
      case 48: {  // PAIR_COMPARISON_VALUES
        if (state.selected.size() < 2) return false;
        state.bound.assign(state.selected.begin(), state.selected.begin() + 2);
        return true;
      }
      case 49: {  // JOIN_BY_ENTITY
        std::vector<std::string> kept;
        for (const std::string& id : state.active) {
          const Claim* claim = FindClaim(state, id);
          if (claim && claim->subject == action.arg_value) kept.push_back(id);
        }
        state.active = kept;
        return true;
      }
      case 50:   // JOIN_BY_EVENT: no occurred_at on this plane
        state.active.clear();
        return true;
      case 51: {  // ORDER_TEMPORAL: all occurred_at empty -> sort by claim_id
        std::stable_sort(state.active.begin(), state.active.end());
        return true;
      }
      case 52: {  // COMPARE_VALUES
        if (state.bound.size() != 2) return false;
        const Claim* left = FindClaim(state, state.bound[0]);
        const Claim* right = FindClaim(state, state.bound[1]);
        double lv, rv;
        if (!left || !right || !FindNumber(left->value, &lv) ||
            !FindNumber(right->value, &rv)) {
          return false;
        }
        state.derived.clear();
        state.derived.push_back(lv < rv ? "<" : lv > rv ? ">" : "=");
        return true;
      }
      case 53: {  // COUNT_VALUES
        std::vector<std::string> unique_values;
        for (const std::string& id : state.active) {
          const Claim* claim = FindClaim(state, id);
          if (claim) PushUnique(&unique_values, ClaimValue(*claim, ""));
        }
        state.derived.clear();
        state.derived.push_back(std::to_string(unique_values.size()));
        return true;
      }
      case 54: {  // NORMALIZE_QUANTITY
        state.derived.clear();
        for (const std::string& id : state.selected) {
          const Claim* claim = FindClaim(state, id);
          if (!claim) continue;
          std::string value = claim->value;
          std::string cleaned;
          for (char c : value)
            if (c != ',') cleaned.push_back(c);
          PushUnique(&state.derived, Strip(cleaned));
        }
        return true;
      }
      case 55: {  // BUILD_DIRECT_PLAN
        const Claim* claim = FindClaim(state, state.selected.front());
        if (!claim) return false;
        state.plan_values.clear();
        state.plan_values.push_back(ClaimValue(*claim, state.answer_shape));
        state.plan_claim_ids.assign(1, state.selected.front());
        state.plan_shape = state.answer_shape;
        state.has_plan_shape = true;
        return true;
      }
      case 56: {  // BUILD_LIST_PLAN
        state.plan_values.clear();
        for (const std::string& id : state.bound) {
          const Claim* claim = FindClaim(state, id);
          if (!claim) return false;
          state.plan_values.push_back(claim->value);
        }
        state.plan_claim_ids = state.bound;
        state.plan_shape = "list";
        state.has_plan_shape = true;
        return true;
      }
      case 57: {  // BUILD_COMPARISON_PLAN
        state.plan_values.clear();
        for (const std::string& id : state.bound) {
          const Claim* claim = FindClaim(state, id);
          if (!claim) return false;
          state.plan_values.push_back(claim->value);
        }
        state.plan_claim_ids = state.bound;
        state.plan_shape = "comparison";
        state.has_plan_shape = true;
        return true;
      }
      case 58: {  // BUILD_VERIFICATION_PLAN
        state.plan_source_ids.clear();
        for (const std::string& id : state.plan_claim_ids) {
          const Claim* claim = FindClaim(state, id);
          if (claim) PushUnique(&state.plan_source_ids, claim->span_id);
        }
        return true;
      }
      case 59:  // VERIFY_PLAN
        VerifyPlan(state);
        return true;
      case 60: {  // ANSWER
        state.has_terminal = true;
        state.terminal = "ANSWER";
        state.answer_values.clear();
        if (state.plan_shape == "comparison" && state.has_plan_answer_text) {
          state.answer_values.push_back(state.plan_answer_text);
        } else {
          state.answer_values = state.plan_values;
        }
        return true;
      }
      case 61:
        state.has_terminal = true;
        state.terminal = "CLARIFY";
        return true;
      case 62:
        state.has_terminal = true;
        state.terminal = "ABSTAIN";
        return true;
      case 63:
        state.has_terminal = true;
        state.terminal = "INCORRECT_PREMISE";
        return true;
      case 64:
        state.has_terminal = true;
        state.terminal = "CONFLICTING_EVIDENCE";
        return true;
      case 65:
        state.has_terminal = true;
        state.terminal = "OUT_OF_CORPUS";
        return true;
      default:
        return false;
    }
  }

  /* ---------------- workspace construction (vertical._workspace) ---------------- */

  static std::string ClaimId(const GroundedRecord& record, size_t index) {
    Sha256 hash;
    hash.Update(reinterpret_cast<const uint8_t*>(record.entity_id.data()),
                record.entity_id.size());
    hash.Update(reinterpret_cast<const uint8_t*>("\0"), 1);
    hash.Update(reinterpret_cast<const uint8_t*>(record.relation.data()),
                record.relation.size());
    hash.Update(reinterpret_cast<const uint8_t*>("\0"), 1);
    std::string index_text = std::to_string(index);
    hash.Update(reinterpret_cast<const uint8_t*>(index_text.data()),
                index_text.size());
    hash.Update(reinterpret_cast<const uint8_t*>("\0"), 1);
    const std::string& value = record.values[index];
    hash.Update(reinterpret_cast<const uint8_t*>(value.data()), value.size());
    uint8_t digest[32];
    hash.Final(digest);
    static const char kHex[] = "0123456789abcdef";
    std::string out = "v13:claim:";
    for (int i = 0; i < 12; i++) {  // 24 hex chars
      out.push_back(kHex[digest[i] >> 4]);
      out.push_back(kHex[digest[i] & 0x0F]);
    }
    return out;
  }

  void BuildWorkspace(const std::vector<std::string>& entity_ids,
                      const std::string& relation, MicroState* state) const {
    state->frame_entities = entity_ids;
    state->frame_relations.assign(1, relation);
    std::string shape = "unknown";
    bool first = true;
    for (size_t r = 0; r < records.size(); r++) {
      const GroundedRecord& record = records[r];
      if (!Contains(entity_ids, record.entity_id) ||
          record.relation != relation) {
        continue;
      }
      if (first) {
        shape = ShapeOf(record.answer_kind);
        first = false;
      }
      SpanRec span;
      span.span_id = record.evidence.handle_id;
      span.text = record.evidence.exact_text;
      char digest[65];
      Sha256Hex(span.text.c_str(), digest);
      span.text_hash = digest;
      span.source_family = "CORPUS";
      if (state->spans.size() < kMaxSpans) state->spans.push_back(span);
      for (size_t index = 0; index < record.values.size(); index++) {
        if (state->claims.size() >= kMaxClaims) break;
        Claim claim;
        claim.claim_id = ClaimId(record, index);
        claim.subject = record.entity_id;
        claim.relation = record.relation;
        claim.value = record.values[index];
        claim.answer_shape = ShapeOf(record.answer_kind);
        claim.span_id = span.span_id;
        claim.confidence = record.confidence;
        claim.record_index = r;
        state->claims.push_back(claim);
      }
    }
    state->answer_shape = shape;
    state->facet_object = shape != "quantity" && shape != "quotation";
    state->facet_quantity = shape == "quantity";
    state->facet_quotation = shape == "quotation";
  }

  /* ---------------- realizer (GroundedAnswerRealizer) ---------------- */

  // Returns true and fills text/evidence ids, or false + failure_reason.
  bool Realize(const GroundedRecord& record,
               const std::vector<std::string>& values, std::string* text,
               std::vector<std::string>* handle_ids,
               std::string* failure) const {
    for (const std::string& value : values) {
      bool supported = record.evidence.exact_text.find(value) != std::string::npos;
      if (!supported) {
        for (const std::string& item : record.evidence.supported_values) {
          if (item == value) {
            supported = true;
            break;
          }
        }
      }
      if (!supported) {
        // GroundingError: str(error) of the Python message; repr uses
        // single quotes for the ASCII values on this plane.
        *failure = "value is not an exact copy from evidence: '" + value + "'";
        return false;
      }
      PushUnique(handle_ids, record.evidence.handle_id);
    }
    const std::string& kind = record.answer_kind;
    const std::string& subject = record.canonical_title;
    const std::string& relation =
        record.relation_text.empty() ? std::string("is") : record.relation_text;
    if (kind == "FACTUAL_VALUE" || kind == "ENTITY" || kind == "DATE" ||
        kind == "QUANTITY") {
      *text = subject + " " + relation + " " + values.front() + ".";
    } else if (kind == "LIST") {
      *text = subject + ": ";
      for (size_t i = 0; i < values.size(); i++) {
        if (i) *text += ", ";
        *text += values[i];
      }
      *text += ".";
    } else if (kind == "QUOTATION") {
      *text = subject + ": \"" + values.front() + "\"";
    } else {
      // COMPARISON requires labels the vertical never sets (the Python
      // contract raises); fail closed instead of inventing a rendering.
      *failure = "unsupported answer kind: " + kind;
      return false;
    }
    return true;
  }

  /* ---------------- clarification text ---------------- */

  static std::string ClarificationText(const Pending& pending) {
    std::string text = pending.question;
    if (!text.empty()) text += " ";
    for (size_t i = 0; i < pending.choices.size(); i++) {
      if (i) text += ", ";
      text += pending.choices[i].choice_id + ": " + pending.choices[i].label;
    }
    return text;
  }
};

/* ================================================================== */

ServiceCore::ServiceCore() : impl_(nullptr) {}

ServiceCore::~ServiceCore() { delete impl_; }

void ServiceCore::SetMeasSink(MeasSink sink, void* ctx) {
  if (impl_ == nullptr) return;
  impl_->meas_sink = sink;
  impl_->meas_ctx = ctx;
}

void ServiceCore::SetClock(ClockFn clock, void* ctx) {
  if (impl_ == nullptr) return;
  impl_->clock_fn = clock;
  impl_->clock_ctx = ctx;
}

bool ServiceCore::Init(std::vector<GroundedRecord> records_in,
                       const int8_t* policy_weights, size_t policy_weight_count,
                       std::string* error) {
  if (records_in.empty()) {
    if (error) *error = "the vertical slice requires at least one grounded record";
    return false;
  }
  if (records_in.size() > kMaxRecords) {
    if (error) *error = "record count exceeds service bound";
    return false;
  }
  if (policy_weights == nullptr || policy_weight_count != 34u * 38u) {
    if (error) *error = "policy weight table must be 34 x 38 int8";
    return false;
  }
  Impl* impl = new (std::nothrow) Impl();
  if (impl == nullptr) {
    if (error) *error = "allocation failed";
    return false;
  }
  impl->records = std::move(records_in);
  impl->weights = policy_weights;
  impl->sessions.resize(kMaxSessions);
  if (!impl->BuildIndex(error)) {
    delete impl;
    return false;
  }
  delete impl_;
  impl_ = impl;
  return true;
}

ServiceResponse ServiceCore::Query(const std::string& session_id,
                                   const std::string& text) {
  ServiceResponse resp;
  resp.session_id = session_id;
  Impl& self = *impl_;
  uint64_t t_start = self.NowUs();

  Impl::Session& before = self.Load(session_id);
  std::vector<std::string> prior;
  {
    size_t begin = before.resolved.size() > 8 ? before.resolved.size() - 8 : 0;
    for (size_t i = begin; i < before.resolved.size(); i++) {
      prior.push_back(before.resolved[i].entity_id);
    }
  }
  std::string normalized = NormalizeQuery(text);
  CogLite cog = InterpretLite(normalized, prior);

  std::string relation;
  bool has_relation = self.RelationOf(text, &relation);
  if (!has_relation && before.has_pending) {
    has_relation = self.RelationOf(before.pending.original_query, &relation);
  }

  uint64_t t_address_start = self.NowUs();
  std::vector<Impl::Hyp> candidates = self.Address(text);
  uint64_t t_address_done = self.NowUs();

  Impl::Action action =
      self.Accept(before, text, candidates, has_relation, relation);

  std::vector<std::string> candidate_ids;
  for (const Impl::Hyp& hyp : candidates) candidate_ids.push_back(hyp.entity_id);

  if (!action.entity_ids.empty()) {
    cog.Satisfy(kOblIdentifySubject);
    cog.unresolved_count = 0;  // SUBJECT_ENTITY / DISCOURSE_REFERENCE removed
  }
  if (action.has_relation) {
    cog.Satisfy(kOblEstablishRelation);
  }

  size_t controller_steps = 0;
  size_t workspace_claims = 0;
  uint64_t t_controller_start = self.NowUs();

  auto finish = [&](const char* disposition, const std::string& body,
                    bool grounded, bool with_candidates) -> ServiceResponse& {
    resp.disposition = disposition;
    resp.text = body;
    resp.grounded = grounded;
    // RESET/CANCELLED responses leave semantic_address_candidate_ids at the
    // contract default (empty), exactly as vertical.py does.
    if (with_candidates) resp.candidate_ids = candidate_ids;
    cog.FillResponse(&resp);
    return resp;
  };

  if (action.kind == Impl::Action::kReset) {
    finish("RESET", "Conversation state reset.", false, false);
  } else if (action.kind == Impl::Action::kCancel) {
    finish("CANCELLED", "Cancelled.", false, false);
  } else if (action.kind == Impl::Action::kAskClarification) {
    resp.verifier_accepted = true;
    resp.clarify_question = action.clarification.question;
    for (const Impl::Choice& choice : action.clarification.choices) {
      resp.clarify_choices.push_back(choice.choice_id + ": " + choice.label);
    }
    finish("CLARIFY", Impl::ClarificationText(action.clarification), true, true);
  } else if (action.entity_ids.empty() || !action.has_relation) {
    resp.has_failure = true;
    resp.failure_reason = "UNRESOLVED_ADDRESS_OR_RELATION";
    finish("ABSTAIN",
           "I do not have a grounded address and relation for that request.",
           false, true);
  } else {
    Impl::MicroState state;
    self.BuildWorkspace(action.entity_ids, action.relation, &state);
    workspace_claims = state.claims.size();
    if (state.claims.empty()) {
      resp.has_failure = true;
      resp.failure_reason = "VALUE_UNAVAILABLE";
      finish("ABSTAIN",
             "I found the entity, but no exact grounded claim for that relation.",
             false, true);
    } else {
      cog.Satisfy(kOblLocateClaim);
      cog.Satisfy(kOblMatchAnswerType);
      // learned controller loop (vertical: <= max_controller_steps)
      for (size_t step = 0; step < self.max_steps; step++) {
        Impl::MicroAction selected;
        if (!self.PolicySelect(state, &selected)) break;
        if (resp.operations.size() < kMaxOpsLog) {
          resp.operations.push_back(selected.op);
        }
        controller_steps++;
        if (!self.Execute(state, selected)) break;  // defensive; mask-legal only
        if (state.has_terminal) break;
      }
      if (state.terminal != "ANSWER" || !state.verification_passed) {
        resp.has_failure = true;
        resp.failure_reason =
            state.has_terminal ? state.terminal : "CONTROLLER_INCOMPLETE";
        finish("ABSTAIN",
               "The learned controller did not produce a verifier-accepted "
               "answer plan.",
               false, true);
      } else {
        const Impl::Claim* first_claim =
            self.FindClaim(state, state.plan_claim_ids.empty()
                                      ? std::string()
                                      : state.plan_claim_ids.front());
        if (first_claim == nullptr) {
          // Python raises RuntimeError here; fail closed identically-shaped.
          resp.has_failure = true;
          resp.failure_reason = "CONTROLLER_INCOMPLETE";
          finish("ABSTAIN",
                 "The learned controller did not produce a verifier-accepted "
                 "answer plan.",
                 false, true);
        } else {
          const GroundedRecord& record = self.records[first_claim->record_index];
          std::string answer_text;
          std::vector<std::string> handle_ids;
          std::string grounding_failure;
          if (!self.Realize(record, state.plan_values, &answer_text,
                            &handle_ids, &grounding_failure)) {
            resp.has_failure = true;
            resp.failure_reason = grounding_failure;
            finish("ABSTAIN",
                   "The verified plan could not be copied from exact evidence.",
                   false, true);
          } else {
            cog.evidence_count = 1;
            cog.Satisfy(kOblBindClaim);
            cog.Satisfy(kOblVerifyEvidence);
            cog.verifier_state_code = 2;  // ACCEPTED
            if (!cog.CanHaltSuccess()) {
              resp.verifier_accepted = true;
              resp.has_failure = true;
              resp.failure_reason = "COG_OBLIGATIONS_OPEN";
              finish("ABSTAIN",
                     "The exact answer is grounded, but mandatory cognitive "
                     "obligations remain.",
                     false, true);
            } else {
              self.RecordAnswer(before, record.evidence.handle_id);
              resp.verifier_accepted = true;
              resp.evidence_handle_ids = handle_ids;
              finish("ANSWER", answer_text, true, true);
            }
          }
        }
      }
    }
  }

  // MEAS-style JSONL telemetry for later on-device latency breakdowns.
  if (self.meas_sink) {
    uint64_t t_done = self.NowUs();
    char line[512];
    std::string escaped;
    for (char c : session_id) {
      if (c == '"' || c == '\\') escaped.push_back('\\');
      escaped.push_back(c);
    }
    snprintf(line, sizeof(line),
             "MEAS {\"phase\":\"service.query\",\"session\":\"%s\","
             "\"disposition\":\"%s\",\"candidates\":%u,\"claims\":%u,"
             "\"steps\":%u,\"address_us\":%llu,\"controller_us\":%llu,"
             "\"total_us\":%llu}",
             escaped.c_str(), resp.disposition.c_str(),
             (unsigned)candidate_ids.size(), (unsigned)workspace_claims,
             (unsigned)controller_steps,
             (unsigned long long)(t_address_done - t_address_start),
             (unsigned long long)(t_done - t_controller_start),
             (unsigned long long)(t_done - t_start));
    self.EmitMeas(line);
  }
  return resp;
}

}  // namespace service
}  // namespace aethercore
