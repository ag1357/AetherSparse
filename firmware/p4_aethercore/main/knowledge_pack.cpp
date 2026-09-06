/* See knowledge_pack.h. All retrieval bounds live here; pack_io provides
 * the raw paged reads. Telemetry is emitted as single-line MEAS JSONL so
 * every interactive answer can be traced to an address candidate set, an
 * evidence blob offset, and an occurrence index (anti-theater evidence). */
#include "knowledge_pack.h"

#include <ctype.h>
#include <stdio.h>
#include <string.h>

#include <algorithm>
#include <string>
#include <vector>

#include "esp_timer.h"

namespace ac {
namespace knowledge {
namespace {

constexpr size_t kMaxContextKept = 1536;     // raw context bytes read per occ
constexpr size_t kMaxSnippetBytes = 360;     // realized quotation window
constexpr size_t kMaxQueryNorm = 400;        // bytes into the address index
constexpr uint32_t kMaxAddressCandidates = 8;
constexpr size_t kCopulaMinSubstance = 16;   // chars after copula verb

/* Generic English function words (standard IR stopwords; not topic rules). */
const char *const kStopwords[] = {
    "a",       "about", "an",     "and",   "any",   "are",    "as",
    "at",      "be",    "been",   "by",    "can",   "could",  "describe",
    "did",     "do",    "does",   "explain","for",  "from",   "get",
    "give",    "got",   "had",    "has",   "have",  "he",     "her",
    "here",    "his",   "how",    "i",     "in",    "into",   "is",
    "it",      "its",   "know",   "make",  "me",    "my",     "no",
    "not",     "of",    "on",     "or",    "our",   "over",   "please",
    "say",     "she",   "should", "show",  "so",    "some",   "such",
    "tell",    "than",  "that",   "the",   "their", "then",   "there",
    "they",    "think", "this",   "to",    "under", "us",     "want",
    "was",     "we",    "were",   "what",  "when",  "where",  "which",
    "who",     "why",   "will",   "with",  "would", "you",    "your",
};

/* When-question markers (question type, not topic). */
const char *const kWhenWords[] = {"when", "born", "died", "date", "year"};

/* Fiction/speculation markers: mild demerit so attributed fictional
 * statements lose ties to plain factual statements (presentational
 * heuristic, not a topic rule). */
const char *const kFictionMarkers[] = {
    "fiction", "episode", "according to the show", "tv series",
    "novel", " film ", "movie",
};

bool IsStopword(const std::string &token) {
  size_t lo = 0, hi = sizeof(kStopwords) / sizeof(kStopwords[0]);
  while (lo < hi) {
    size_t mid = (lo + hi) / 2;
    int cmp = strcmp(token.c_str(), kStopwords[mid]);
    if (cmp == 0) return true;
    if (cmp < 0) hi = mid;
    else lo = mid + 1;
  }
  return false;
}

/* Pack build parity (_norm): casefold, '_'->' ', whitespace collapse.
 * ASCII casefold only; non-ASCII bytes pass through unchanged so exact
 * byte-wise surfaces still match (mixed-case accented queries degrade to
 * trigram fuzz instead of folding). */
std::string Normalize(const std::string &text, size_t cap) {
  std::string out;
  out.reserve(text.size() < cap ? text.size() : cap);
  bool pending_space = false;
  for (char ch : text) {
    unsigned char c = (unsigned char)ch;
    if (c == '_' || isspace(c)) {
      pending_space = !out.empty();
      continue;
    }
    if (pending_space) {
      out.push_back(' ');
      pending_space = false;
    }
    out.push_back((char)((c >= 'A' && c <= 'Z') ? (c + 32) : c));
    if (out.size() >= cap) break;
  }
  return out;
}

std::string Lower(const std::string &text) {
  std::string out = text;
  for (char &c : out) {
    if (c >= 'A' && c <= 'Z') c = (char)(c + 32);
  }
  return out;
}

/* Content tokens: alphanumeric runs, length >= 2, stopwords removed. */
std::vector<std::string> ContentTokens(const std::string &query) {
  std::vector<std::string> tokens;
  std::string cur;
  for (char ch : query) {
    unsigned char c = (unsigned char)ch;
    if (isalnum(c)) {
      if (cur.size() < 32) cur.push_back((char)tolower(c));
    } else if (!cur.empty()) {
      if (cur.size() >= 2 && !IsStopword(cur) && tokens.size() < 24)
        tokens.push_back(cur);
      cur.clear();
    }
  }
  if (cur.size() >= 2 && !IsStopword(cur) && tokens.size() < 24)
    tokens.push_back(cur);
  return tokens;
}

/* Occurrence record header: `<I B B H H H` = doc_idx, split, state,
 * mention_len, context_len, reserved; then mention bytes, context bytes. */

/* -------------------------------------------------------------------------
 * Deterministic wikitext -> plaintext rendering (mirrors what the fixture
 * generator produced for V13). Both exact_text and the realized value are
 * derived from the SAME cleaned context, so the grounding contract
 * (value <= exact_text, span hash recorded) is preserved; the raw bytes
 * remain auditable through the packv2:entity:occ:blob_off handle.
 * ------------------------------------------------------------------------- */

void ReplaceAll(std::string *s, const char *from, const char *to) {
  size_t flen = strlen(from);
  size_t pos = 0;
  while ((pos = s->find(from, pos)) != std::string::npos) {
    s->replace(pos, flen, to);
    pos += strlen(to);
  }
}

bool StartsWithAt(const std::string &s, size_t pos, const char *prefix) {
  size_t plen = strlen(prefix);
  return pos + plen <= s.size() && s.compare(pos, plen, prefix) == 0;
}

std::string CleanWikitext(const std::string &in) {
  std::string s = in;
  // Unterminated window-cut remnants.
  {
    size_t p = s.rfind("[[");
    if (p != std::string::npos && s.find(']', p + 2) == std::string::npos)
      s.erase(p);
    size_t close = s.find("]]");
    if (close != std::string::npos && close <= 60 &&
        s.substr(0, close).find_first_of("[.\n") == std::string::npos)
      s.erase(0, close + 2);
    close = s.find("}}");
    if (close != std::string::npos && close <= 60 &&
        s.substr(0, close).find_first_of("{\n") == std::string::npos)
      s.erase(0, close + 2);
  }
  // Links: [[target|text]] -> text, [[target]] -> target.
  {
    std::string out;
    out.reserve(s.size());
    size_t i = 0;
    while (i < s.size()) {
      if (StartsWithAt(s, i, "[[")) {
        size_t j = s.find("]]", i + 2);
        if (j == std::string::npos) break;
        std::string body = s.substr(i + 2, j - (i + 2));
        size_t bar = body.rfind('|');
        out += (bar != std::string::npos) ? body.substr(bar + 1) : body;
        i = j + 2;
      } else {
        out.push_back(s[i++]);
      }
    }
    s.swap(out);
  }
  // References: <ref .../> and <ref ...>...</ref>; unterminated -> tag only.
  for (;;) {
    size_t p = s.find("<ref");
    if (p == std::string::npos) break;
    size_t gt = s.find('>', p);
    if (gt == std::string::npos) break;
    if (gt > p && s[gt - 1] == '/') {
      s.erase(p, gt + 1 - p);
    } else {
      size_t close = s.find("</ref>", gt);
      if (close != std::string::npos) s.erase(p, close + 6 - p);
      else s.erase(p, gt + 1 - p);
    }
  }
  // Remaining markup tags (<b>, </small>, ...): only when '<' is followed
  // by an ASCII letter or '/'+letter (so "a < b" is untouched).
  for (size_t i = 0; i < s.size();) {
    if (s[i] == '<' && i + 1 < s.size() &&
        (isalpha((unsigned char)s[i + 1]) ||
         (s[i + 1] == '/' && i + 2 < s.size() &&
          isalpha((unsigned char)s[i + 2])))) {
      size_t gt = s.find('>', i);
      if (gt == std::string::npos) break;
      s.erase(i, gt + 1 - i);
    } else {
      i++;
    }
  }
  // Templates: remove all balanced {...} spans (stack = any nesting depth).
  {
    std::vector<std::pair<size_t, size_t>> spans;
    std::vector<size_t> opens;
    for (size_t i = 0; i < s.size(); i++) {
      if (s[i] == '{') opens.push_back(i);
      else if (s[i] == '}' && !opens.empty()) {
        size_t p = opens.back();
        opens.pop_back();
        if (opens.empty()) spans.push_back({p, i});  // outermost only
      }
    }
    for (size_t k = spans.size(); k > 0; k--)
      s.erase(spans[k - 1].first, spans[k - 1].second - spans[k - 1].first + 1);
    size_t p = s.rfind("{{");
    if (p != std::string::npos && s.find("}}", p + 2) == std::string::npos)
      s.erase(p);  // trailing unterminated template
  }
  // Section headings "=== Title ===" act as sentence boundaries.
  for (size_t i = 0; (i = s.find("==", i)) != std::string::npos;) {
    size_t j = i;
    while (j < s.size() && s[j] == '=') j++;
    size_t close = s.find('=', j);
    if (close == std::string::npos) break;
    size_t k = close;
    while (k < s.size() && s[k] == '=') k++;
    s.replace(i, k - i, ". ");
    i += 2;
  }
  // Bold/italic quote runs and common entities (build-parity order).
  {
    std::string out;
    out.reserve(s.size());
    for (size_t i = 0; i < s.size();) {
      if (s[i] == '\'' && i + 1 < s.size() && s[i + 1] == '\'') {
        while (i < s.size() && s[i] == '\'') i++;
      } else {
        out.push_back(s[i++]);
      }
    }
    s.swap(out);
  }
  ReplaceAll(&s, "&ndash;", "\xE2\x80\x93");
  ReplaceAll(&s, "&mdash;", "\xE2\x80\x94");
  ReplaceAll(&s, "&amp;", "&");
  ReplaceAll(&s, "&lt;", "<");
  ReplaceAll(&s, "&gt;", ">");
  ReplaceAll(&s, "&quot;", "\"");
  // Tables dropped; list items ("*"/"#"/":" prefixes) become standalone
  // sentences so the snippetizer treats each item as one unit.
  {
    std::string out;
    size_t pos = 0;
    while (pos <= s.size()) {
      size_t nl = s.find('\n', pos);
      std::string line = s.substr(pos, nl == std::string::npos
                                           ? std::string::npos
                                           : nl - pos);
      size_t b = line.find_first_not_of(" \t");
      std::string trimmed = (b == std::string::npos) ? "" : line.substr(b);
      bool drop = StartsWithAt(trimmed, 0, "{|") ||
                  StartsWithAt(trimmed, 0, "|}") ||
                  StartsWithAt(trimmed, 0, "|-") ||
                  StartsWithAt(trimmed, 0, "|") || StartsWithAt(trimmed, 0, "!");
      if (!drop && !trimmed.empty()) {
        size_t m = 0;
        while (m < trimmed.size() &&
               (trimmed[m] == '*' || trimmed[m] == '#' || trimmed[m] == ':'))
          m++;
        if (m > 0) {
          while (m < trimmed.size() && trimmed[m] == ' ') m++;
          if (!out.empty()) out.push_back(' ');
          out += ". ";
          out += trimmed.substr(m);
        } else {
          if (!out.empty()) out.push_back(' ');
          out += trimmed;
        }
      }
      if (nl == std::string::npos) break;
      pos = nl + 1;
    }
    s.swap(out);
  }
  // Whitespace collapse, then collapse ". ." empty-sentence artifacts
  // left by dropped tables/headings ("planets. . " -> "planets. ").
  {
    std::string out;
    out.reserve(s.size());
    bool pending = false;
    for (char ch : s) {
      if (isspace((unsigned char)ch)) {
        pending = !out.empty();
        continue;
      }
      if (pending) {
        out.push_back(' ');
        pending = false;
      }
      out.push_back(ch);
    }
    s.swap(out);
  }
  for (size_t p; (p = s.find(". .")) != std::string::npos;) s.erase(p, 2);
  return s;
}

/* Sentence-boundary tests: '.'/'!'/'?' followed by space + uppercase/digit
 * (so "(d. 1934)" and "1.52" don't cut), or end of text. */
bool IsSentenceEnd(const std::string &ctx, size_t i) {
  char c = ctx[i];
  if (c != '.' && c != '!' && c != '?') return false;
  size_t j = i + 1;
  if (j < ctx.size() && (ctx[j] == '"' || ctx[j] == ')')) j++;
  if (j >= ctx.size()) return true;
  if (ctx[j] != ' ') return false;
  j++;
  if (j >= ctx.size()) return true;
  unsigned char n = (unsigned char)ctx[j];
  return (n >= 'A' && n <= 'Z') || (n >= '0' && n <= '9') || n == '"';
}

bool IsSentenceStart(const std::string &ctx, size_t i) {
  if (i == 0 || i + 1 >= ctx.size()) return false;
  char prev = ctx[i - 1];
  if (prev != '.' && prev != '!' && prev != '?') return false;
  unsigned char n = (unsigned char)ctx[i + 1];
  return (n >= 'A' && n <= 'Z') || (n >= '0' && n <= '9') || n == '"';
}

bool HasYearNear(const std::string &cl, size_t from, size_t to) {
  if (to > cl.size()) to = cl.size();
  for (size_t i = from; i < to; i++) {
    if (!isdigit((unsigned char)cl[i])) continue;
    size_t j = i;
    while (j < to && isdigit((unsigned char)cl[j])) j++;
    size_t digits = j - i;
    bool left = i == 0 || !isalnum((unsigned char)cl[i - 1]);
    bool right = j >= cl.size() || !isalnum((unsigned char)cl[j]);
    if (digits >= 3 && digits <= 4 && left && right) return true;
    i = j;
  }
  return false;
}

/* "( 1867" pattern within a short window after the mention. */
bool HasParenYear(const std::string &cl, size_t from, size_t to) {
  if (to > cl.size()) to = cl.size();
  for (size_t i = from; i < to; i++) {
    if (cl[i] != '(') continue;
    size_t j = i + 1;
    while (j < to && cl[j] == ' ') j++;
    size_t d = j;
    while (d < to && isdigit((unsigned char)cl[d])) d++;
    if (d - j >= 3 && d - j <= 4) return true;
  }
  return false;
}

/* "... 1867 – " or "1902 - " immediately before the mention (date-entry
 * pattern); handles ASCII '-', en dash (E2 80 93), em dash (E2 80 94). */
bool HasYearPrefix(const std::string &cl, size_t mpos) {
  if (mpos == 0) return false;
  size_t i = mpos;
  while (i > 0 && cl[i - 1] == ' ') i--;
  if (i == 0) return false;
  if (cl[i - 1] == '-') {
    i--;
  } else if (i >= 3 && (unsigned char)cl[i - 3] == 0xE2 &&
             (unsigned char)cl[i - 2] == 0x80 &&
             ((unsigned char)cl[i - 1] == 0x93 ||
              (unsigned char)cl[i - 1] == 0x94)) {
    i -= 3;
  } else {
    return false;
  }
  while (i > 0 && cl[i - 1] == ' ') i--;
  size_t d = i;
  while (d > 0 && isdigit((unsigned char)cl[d - 1])) d--;
  size_t digits = i - d;
  if (digits < 3 || digits > 4) return false;
  return d == 0 || !isalnum((unsigned char)cl[d - 1]);
}

bool IsWhenQuery(const std::string &query_lower) {
  size_t pos = 0;
  while (pos < query_lower.size()) {
    while (pos < query_lower.size() &&
           !isalnum((unsigned char)query_lower[pos]))
      pos++;
    size_t end = pos;
    while (end < query_lower.size() &&
           isalnum((unsigned char)query_lower[end]))
      end++;
    std::string word = query_lower.substr(pos, end - pos);
    for (const char *w : kWhenWords) {
      if (word == w) return true;
    }
    pos = end;
  }
  return false;
}

/* Occurrence score; -1 = unusable. Mirrors the host-validated algorithm
 * (fetch_final.py): coverage (near mention = full weight), structural
 * bonuses (title lead, canonical mention, copula with substance,
 * biographical year-parenthetical, date-entry prefix for when-queries),
 * junk demerits (numbered lists, digit/paren density, fiction markers),
 * plus a generic primary-source bonus for occurrences the pack flags as
 * coming from the entity's own article (occ_flags & 1). */
int ScoreOccurrence(const std::string &ctx, const std::string &raw,
                    const std::string &mention, const std::string &title_lower,
                    const std::vector<std::string> &informative,
                    bool when_query, int occ_flags) {
  if (ctx.size() < 30) return -1;
  std::string cl = Lower(ctx);
  std::string ml = Lower(mention);
  size_t mpos = ml.empty() ? std::string::npos : cl.find(ml);

  int cov = 0;
  for (const std::string &token : informative) {
    size_t pos = 0;
    while ((pos = cl.find(token, pos)) != std::string::npos) {
      bool left_ok = pos == 0 || !isalnum((unsigned char)cl[pos - 1]);
      size_t tend = pos + token.size();
      bool right_ok = tend >= cl.size() ||
                      !isalnum((unsigned char)cl[tend]);
      if (left_ok && right_ok) {
        size_t dist = (mpos != std::string::npos && pos < mpos)
                          ? mpos - pos
                          : (mpos != std::string::npos ? pos - mpos : 999);
        cov += (mpos != std::string::npos && dist <= 12) ? 4 : 1;
        break;
      }
      pos++;
    }
  }
  if (!informative.empty() && cov == 0) {
    bool near_year = false;
    if (when_query && mpos != std::string::npos) {
      size_t from = mpos > 16 ? mpos - 16 : 0;
      near_year = HasYearNear(cl, from, mpos + ml.size() + 16);
    }
    if (!near_year) return -1;  // gated: no informative token near this occ
  }

  int score = cov;
  if (cl.substr(0, cl.size() < 200 ? cl.size() : 200).find(title_lower) !=
      std::string::npos)
    score += 2;
  if (ml == title_lower) score += 1;
  if (mpos != std::string::npos) {
    size_t after = mpos + ml.size();
    while (after < cl.size() && cl[after] == ' ') after++;
    static const char *const kStrong[] = {"is ", "was ", "are ", "were "};
    static const char *const kWeak[] = {"became ", "refers "};
    bool matched = false;
    for (const char *c : kStrong) {
      if (StartsWithAt(cl, after, c)) {
        size_t j = after + strlen(c);
        size_t k = j;
        while (k < cl.size() && cl[k] != '.' && cl[k] != '!' && cl[k] != '?')
          k++;
        if (k - j >= kCopulaMinSubstance) score += 4;
        matched = true;
        break;
      }
    }
    if (!matched) {
      for (const char *c : kWeak) {
        if (StartsWithAt(cl, after, c)) {
          score += 2;
          break;
        }
      }
    }
    if (HasParenYear(cl, after, after + 20)) score += when_query ? 6 : 3;
    if (when_query) {
      size_t from = mpos > 60 ? mpos - 60 : 0;
      if (HasYearNear(cl, from, mpos + ml.size() + 60)) score += 2;
      if (HasYearPrefix(cl, mpos)) {
        size_t a2 = after;
        if (a2 < cl.size() && (cl[a2] == ',' || cl[a2] == '(')) score += 6;
      }
    }
  }
  int hashes = 0;
  for (char c : raw) {
    if (c == '#') hashes++;
  }
  score -= hashes * 2 > 8 ? 8 : hashes * 2;
  if (ctx.size() < 60) score -= 1;
  size_t heavy = 0;
  for (char c : ctx) {
    if (isdigit((unsigned char)c) || c == '(' || c == ')') heavy++;
  }
  if (heavy * 4 > ctx.size()) score -= 6;
  std::string padded = " " + cl + " ";
  for (const char *marker : kFictionMarkers) {
    if (padded.find(marker) != std::string::npos) {
      score -= 2;
      break;
    }
  }
  if (occ_flags & 1) score += 6;  // entity's own article (primary source)
  return score;
}

/* Sentence-aligned quotation window over the cleaned context, anchored at
 * the mention; short sentences extend forward. Exact substring by
 * construction. */
std::string PickSnippet(const std::string &context,
                        const std::string &mention,
                        const std::vector<std::string> &tokens) {
  std::string lower = Lower(context);
  size_t anchor = mention.empty() ? std::string::npos
                                  : lower.find(Lower(mention));
  if (anchor == std::string::npos) {
    anchor = 0;
    for (const std::string &token : tokens) {
      size_t pos = lower.find(token);
      if (pos != std::string::npos && (anchor == 0 || pos < anchor))
        anchor = pos;
    }
  }
  size_t start = 0;
  for (size_t i = anchor; i > 0; i--) {
    if (IsSentenceStart(context, i)) {
      start = i + 1;
      break;
    }
  }
  unsigned char c0 = context.empty() ? 0 : (unsigned char)context[0];
  if (start == 0 && anchor > 0 && c0 >= 'a' && c0 <= 'z') {
    // Window cut mid-word: skip the partial first word.
    size_t sp = context.find(' ');
    if (sp != std::string::npos && sp < anchor) start = sp + 1;
  }
  while (start < anchor &&
         (context[start] == '*' || context[start] == '#' ||
          context[start] == ':' || context[start] == ';' ||
          context[start] == ' '))
    start++;

  size_t limit = start + kMaxSnippetBytes;
  if (limit > context.size()) limit = context.size();
  size_t end = std::string::npos;
  for (size_t i = anchor > start ? anchor : start; i < limit; i++) {
    if (context[i] == '{') {
      limit = i;
      break;
    }
    if (IsSentenceEnd(context, i)) end = i + 1;
  }
  if (end == std::string::npos || end <= start) {
    end = limit;
    while (end > start + 40 && end <= context.size() &&
           context[end - 1] != ' ')
      end--;
    if (end > start && context[end - 1] == ' ') end--;
  }
  // Extend short sentences with the following sentence (readability).
  while (end - start < 60 && end < context.size()) {
    size_t nx = end;
    while (nx < context.size() &&
           (context[nx] == ' ' || context[nx] == '"'))
      nx++;
    size_t nend = std::string::npos;
    size_t cap2 = nx + 300 < context.size() ? nx + 300 : context.size();
    for (size_t i = nx; i < cap2; i++) {
      if (context[i] == '{') break;
      if (IsSentenceEnd(context, i)) {
        nend = i + 1;
        break;
      }
    }
    if (nend == std::string::npos || nend - start > kMaxSnippetBytes) break;
    size_t alnum = 0;
    for (size_t i = nx; i < nend; i++) {
      if (isalnum((unsigned char)context[i])) alnum++;
    }
    if (alnum < 8) break;
    end = nend;
  }
  std::string out = context.substr(start, end - start);
  if (out.size() > 2 && out.compare(out.size() - 2, 2, " .") == 0)
    out.resize(out.size() - 2);
  return out;
}

std::string EntityIdFor(uint32_t entity_idx) {
  char buf[24];
  snprintf(buf, sizeof(buf), "%s%lu", kEntityIdPrefix, (unsigned long)entity_idx);
  return buf;
}

bool ParseEntityId(const std::string &id, uint32_t *out) {
  size_t prefix_len = strlen(kEntityIdPrefix);
  if (id.compare(0, prefix_len, kEntityIdPrefix) != 0) return false;
  const char *digits = id.c_str() + prefix_len;
  if (*digits == 0) return false;
  char *end = nullptr;
  unsigned long value = strtoul(digits, &end, 10);
  if (end == nullptr || *end != 0) return false;
  *out = (uint32_t)value;
  return true;
}

void MeasJsonEscaped(FILE *stream, const std::string &text, size_t cap) {
  size_t emitted = 0;
  for (char c : text) {
    if (emitted++ >= cap) break;
    if (c == '"' || c == '\\') fputc('\\', stream);
    fputc(c == '\n' ? ' ' : c, stream);
  }
}

}  // namespace

PackProvider::~PackProvider() {
  if (pager_) pager_destroy(pager_);
}

bool PackProvider::start(size_t pager_bytes) {
  pager_ = pager_create(pager_bytes);
  return pager_ != nullptr;
}

bool PackProvider::Address(
    const std::string &text,
    std::vector<aethercore::service::AddressHyp> *out) {
  out->clear();
  if (!pager_) return false;
  int64_t t0 = esp_timer_get_time();
  PagerStats before, after;
  pager_stats(pager_, &before);

  std::string norm = Normalize(text, kMaxQueryNorm);
  if (norm.empty()) return true;  // no address, not an error

  /* Address on the content tokens (stopword-free): function-word trigrams
   * ("tell me about ...") otherwise dominate the union and surface
   * phrase-like titles instead of the subject. Queries made purely of
   * function words fall back to the full normalized text. */
  std::vector<std::string> tokens = ContentTokens(norm);
  std::string address_text;
  for (const std::string &token : tokens) {
    if (!address_text.empty()) address_text.push_back(' ');
    address_text += token;
  }
  if (address_text.empty()) address_text = norm;

  AddressCandidate cands[kMaxAddressCandidates];
  uint32_t n = idx_address_candidates(pager_, address_text.c_str(), cands,
                                      kMaxAddressCandidates);

  /* Floors: a candidate must share at least 4 trigrams and cover at least
   * 0.35 of its surface (containment). Confidence combines absolute
   * containment with overlap relative to the best candidate, so genuinely
   * tied names (polysemy) stay close and trigger clarification while weak
   * partial matches fall below the conversation plausibility threshold. */
  uint32_t best_overlap = 0;
  for (uint32_t i = 0; i < n; i++) {
    if (cands[i].overlap > best_overlap) best_overlap = cands[i].overlap;
  }
  struct Kept {
    AddressCandidate cand;
    double confidence;
  };
  Kept kept[kMaxAddressCandidates];
  size_t kept_n = 0;
  for (uint32_t i = 0; i < n && kept_n < kMaxAddressCandidates; i++) {
    const AddressCandidate &c = cands[i];
    double grams = (double)c.surface_len + 2.0;  // padded trigram estimate
    double containment = grams > 0.0 ? (double)c.overlap / grams : 0.0;
    if (c.overlap < 4 || containment < 0.35) continue;
    double confidence =
        containment * (best_overlap ? (double)c.overlap / best_overlap : 0.0);
    if (confidence > 1.0) confidence = 1.0;
    kept[kept_n].cand = c;
    kept[kept_n].confidence = confidence;
    kept_n++;
  }

  pager_stats(pager_, &after);
  int64_t t1 = esp_timer_get_time();
  printf("MEAS {\"phase\":\"knowledge.address\",\"q\":\"");
  MeasJsonEscaped(stdout, address_text, 64);
  printf("\",\"raw\":%lu,\"kept\":%zu,\"pages\":%llu,\"reads\":%llu,"
         "\"us\":%llu}\n",
         (unsigned long)n, kept_n,
         (unsigned long long)(after.pages_touched - before.pages_touched),
         (unsigned long long)(after.physical_reads - before.physical_reads),
         (unsigned long long)(t1 - t0));

  for (size_t i = 0; i < kept_n; i++) {
    aethercore::service::AddressHyp hyp;
    hyp.entity_id = EntityIdFor(kept[i].cand.entity_idx);
    hyp.confidence = kept[i].confidence;
    char title[160];
    if (ent_title_at(kept[i].cand.entity_idx, title, sizeof(title))) {
      hyp.label = title;
    } else {
      hyp.label = hyp.entity_id;
    }
    char surface[96];
    if (idx_surface_text(pager_, kept[i].cand.surface_id, surface,
                         sizeof(surface))) {
      hyp.matched_surface = surface;
    }
    printf("MEAS {\"phase\":\"knowledge.candidate\",\"rank\":%zu,"
           "\"entity\":%lu,\"overlap\":%lu,\"surface\":\"",
           i, (unsigned long)kept[i].cand.entity_idx,
           (unsigned long)kept[i].cand.overlap);
    MeasJsonEscaped(stdout, hyp.matched_surface, 48);
    printf("\",\"title\":\"");
    MeasJsonEscaped(stdout, hyp.label, 48);
    printf("\",\"confidence\":%.3f}\n", hyp.confidence);
    out->push_back(hyp);
  }
  return true;
}

bool PackProvider::FetchRecords(
    const std::vector<std::string> &entity_ids, const std::string &query_text,
    std::vector<aethercore::service::GroundedRecord> *out) {
  out->clear();
  if (!pager_) return false;
  std::string tokens = query_text;
  std::vector<std::string> content_tokens = ContentTokens(tokens);
  std::string query_lower = Lower(query_text);
  bool when_query = IsWhenQuery(query_lower);

  size_t fetched = 0;
  for (const std::string &id : entity_ids) {
    if (fetched >= aethercore::service::kMaxRecords) break;
    uint32_t entity_idx = 0;
    if (!ParseEntityId(id, &entity_idx)) continue;
    int64_t t0 = esp_timer_get_time();
    PagerStats before, after;
    pager_stats(pager_, &before);

    uint32_t blob_off = 0, blob_len = 0, occ_total = 0;
    if (!evd_lookup(pager_, entity_idx, &blob_off, &blob_len, &occ_total)) {
      printf("MEAS {\"phase\":\"knowledge.fetch\",\"entity\":%lu,"
             "\"found\":false}\n", (unsigned long)entity_idx);
      continue;
    }

    char title[160];
    if (!ent_title_at(entity_idx, title, sizeof(title))) {
      snprintf(title, sizeof(title), "entity-%lu", (unsigned long)entity_idx);
    }
    std::string title_lower = Lower(title);

    /* Informative tokens: content tokens that are not title words (they
     * match every occurrence and carry no selection signal). */
    std::vector<std::string> title_words = ContentTokens(title);
    std::vector<std::string> informative;
    for (const std::string &t : content_tokens) {
      bool in_title = false;
      for (const std::string &w : title_words) {
        if (t == w) {
          in_title = true;
          break;
        }
      }
      if (!in_title) informative.push_back(t);
    }

    /* Stream the FULL occurrence blob through the pager (no head cap):
     * each record is cleaned, scored, and only the best context is kept.
     * Per-query state is two strings plus the pager pages. */
    std::string best_ctx, best_mention, fb_ctx, fb_mention;
    int best_score = -1, fb_score = -1;
    size_t best_k = 0, fb_k = 0, occ_seen = 0;
    uint32_t pos = 0;
    while (pos + 12 <= blob_len) {
      uint8_t header[12];
      size_t got = 0;
      if (!evd_blob_read(pager_, blob_off, blob_len, pos, header, 12, &got) ||
          got < 12)
        break;
      uint16_t mention_len, context_len, occ_flags;
      memcpy(&mention_len, header + 6, 2);
      memcpy(&context_len, header + 8, 2);
      memcpy(&occ_flags, header + 10, 2);
      uint32_t rec_len = 12 + (uint32_t)mention_len + (uint32_t)context_len;
      if (pos + rec_len > blob_len) break;

      std::string mention;
      if (mention_len > 0 && mention_len <= 128) {
        mention.resize(mention_len);
        evd_blob_read(pager_, blob_off, blob_len, pos + 12,
                      (uint8_t *)mention.data(), mention_len, &got);
        mention.resize(got);
      }
      std::string raw;
      size_t want_ctx =
          context_len < kMaxContextKept ? context_len : kMaxContextKept;
      if (want_ctx > 0) {
        raw.resize(want_ctx);
        evd_blob_read(pager_, blob_off, blob_len, pos + 12 + mention_len,
                      (uint8_t *)raw.data(), want_ctx, &got);
        raw.resize(got);
      }
      pos += rec_len;
      occ_seen++;

      std::string ctx = CleanWikitext(raw);
      int s = ScoreOccurrence(ctx, raw, mention, title_lower, informative,
                              when_query, occ_flags);
      if (s > best_score) {
        best_score = s;
        best_ctx = ctx;
        best_mention = mention;
        best_k = occ_seen - 1;
      }
      std::vector<std::string> empty;
      int s2 = ScoreOccurrence(ctx, raw, mention, title_lower, empty,
                               when_query, occ_flags);
      if (s2 > fb_score) {
        fb_score = s2;
        fb_ctx = ctx;
        fb_mention = mention;
        fb_k = occ_seen - 1;
      }
    }

    bool used_fallback = best_ctx.empty() && !fb_ctx.empty();
    const std::string &context = used_fallback ? fb_ctx : best_ctx;
    const std::string &mention = used_fallback ? fb_mention : best_mention;
    size_t pick = used_fallback ? fb_k : best_k;
    if (context.empty()) continue;

    std::string snippet = PickSnippet(context, mention, content_tokens);
    if (snippet.empty()) continue;

    aethercore::service::GroundedRecord record;
    record.entity_id = id;
    record.canonical_title = title;
    record.address_surfaces.push_back(title);
    record.relation = "describe";
    record.relation_text = "describes";
    record.answer_kind = "QUOTATION";
    record.values.push_back(snippet);
    char handle[64];
    snprintf(handle, sizeof(handle), "packv2:%lu:%lu:%u",
             (unsigned long)entity_idx, (unsigned long)pick,
             (unsigned)(blob_off));
    record.evidence.handle_id = handle;
    record.evidence.source_namespace = "pack-v2";
    record.evidence.canonical_object_id = id;
    record.evidence.source_version = pack_id();
    char locator[64];
    snprintf(locator, sizeof(locator), "evd:%lu:%lu",
             (unsigned long)blob_off, (unsigned long)blob_len);
    record.evidence.source_locator = locator;
    record.evidence.exact_text = context;
    record.confidence = 1.0;
    out->push_back(record);
    fetched++;

    pager_stats(pager_, &after);
    int64_t t1 = esp_timer_get_time();
    printf("MEAS {\"phase\":\"knowledge.fetch\",\"entity\":%lu,"
           "\"blob_off\":%lu,\"blob_len\":%lu,\"occ_total\":%lu,"
           "\"occ_seen\":%zu,\"pick\":%zu,\"score\":%d,\"fb\":%s,"
           "\"ctx\":%zu,\"snippet\":%zu,"
           "\"reads\":%llu,\"us\":%llu}\n",
           (unsigned long)entity_idx, (unsigned long)blob_off,
           (unsigned long)blob_len, (unsigned long)occ_total, occ_seen, pick,
           used_fallback ? fb_score : best_score,
           used_fallback ? "true" : "false",
           context.size(), snippet.size(),
           (unsigned long long)(after.physical_reads - before.physical_reads),
           (unsigned long long)(t1 - t0));
  }
  return true;
}

}  // namespace knowledge
}  // namespace ac
