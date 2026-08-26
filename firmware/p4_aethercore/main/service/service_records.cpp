#include "service_records.h"

#include <stdio.h>

namespace aethercore {
namespace service {

namespace {

// Cursor-based bounded parser for the grounded-record fixture shape.
struct Parser {
  const char* cur;
  const char* end;
  std::string* error;

  bool Fail(const char* message) {
    if (error && error->empty()) *error = message;
    return false;
  }

  void SkipWs() {
    while (cur < end && (*cur == ' ' || *cur == '\t' || *cur == '\n' ||
                         *cur == '\r')) {
      ++cur;
    }
  }

  bool Consume(char expected) {
    SkipWs();
    if (cur >= end || *cur != expected) return Fail("unexpected character");
    ++cur;
    return true;
  }

  bool At(char c) {
    SkipWs();
    return cur < end && *cur == c;
  }

  bool ParseString(std::string* out) {
    SkipWs();
    if (cur >= end || *cur != '"') return Fail("expected string");
    ++cur;
    out->clear();
    while (cur < end && *cur != '"') {
      char c = *cur;
      if (c == '\\') {
        ++cur;
        if (cur >= end) return Fail("truncated escape");
        char esc = *cur;
        const char* simple = nullptr;
        char decoded = 0;
        switch (esc) {
          case '"': decoded = '"'; break;
          case '\\': decoded = '\\'; break;
          case '/': decoded = '/'; break;
          case 'b': decoded = '\b'; break;
          case 'f': decoded = '\f'; break;
          case 'n': decoded = '\n'; break;
          case 'r': decoded = '\r'; break;
          case 't': decoded = '\t'; break;
          case 'u': simple = "unicode-escape"; break;
          default: return Fail("bad escape");
        }
        if (simple != nullptr) {
          // \uXXXX: decode BMP code point to UTF-8 (fixture is ASCII; this
          // keeps the parser total without promising full fidelity).
          if (end - cur < 5) return Fail("truncated unicode escape");
          unsigned code = 0;
          for (int i = 0; i < 4; i++) {
            char h = cur[1 + i];
            unsigned digit;
            if (h >= '0' && h <= '9') digit = unsigned(h - '0');
            else if (h >= 'a' && h <= 'f') digit = unsigned(h - 'a' + 10);
            else if (h >= 'A' && h <= 'F') digit = unsigned(h - 'A' + 10);
            else return Fail("bad unicode escape");
            code = code * 16u + digit;
          }
          cur += 5;
          if (code < 0x80) {
            out->push_back(char(code));
          } else if (code < 0x800) {
            out->push_back(char(0xC0 | (code >> 6)));
            out->push_back(char(0x80 | (code & 0x3F)));
          } else {
            out->push_back(char(0xE0 | (code >> 12)));
            out->push_back(char(0x80 | ((code >> 6) & 0x3F)));
            out->push_back(char(0x80 | (code & 0x3F)));
          }
        } else {
          out->push_back(decoded);
          ++cur;
        }
      } else {
        out->push_back(c);
        ++cur;
      }
      if (out->size() > kMaxStringBytes) return Fail("string exceeds bound");
    }
    if (cur >= end) return Fail("unterminated string");
    ++cur;  // closing quote
    return true;
  }

  bool ParseNumber(double* out) {
    SkipWs();
    const char* start = cur;
    if (cur < end && (*cur == '-' || *cur == '+')) ++cur;
    bool any = false;
    while (cur < end && ((*cur >= '0' && *cur <= '9') || *cur == '.' ||
                         *cur == 'e' || *cur == 'E' || *cur == '+' ||
                         *cur == '-')) {
      ++cur;
      any = true;
    }
    if (!any) return Fail("expected number");
    char buffer[40];
    size_t length = size_t(cur - start);
    if (length == 0 || length >= sizeof(buffer)) return Fail("number too long");
    memcpy_(buffer, start, length);
    buffer[length] = '\0';
    *out = strtod_(buffer);
    return true;
  }

  // Low-level helpers kept as statics so no locale-dependent CRT calls leak
  // into the parse (strtod is locale-sensitive for '.'; the fixture uses
  // ASCII '.' and the C locale is the ESP-IDF/host default).
  static void memcpy_(char* dst, const char* src, size_t n) {
    for (size_t i = 0; i < n; i++) dst[i] = src[i];
  }
  static double strtod_(const char* text) {
    // Minimal ASCII strtod: sign, integer, fraction, exponent.
    const char* p = text;
    bool neg = false;
    if (*p == '-' || *p == '+') neg = (*p++ == '-');
    double value = 0.0;
    while (*p >= '0' && *p <= '9') value = value * 10.0 + double(*p++ - '0');
    if (*p == '.') {
      ++p;
      double scale = 0.1;
      while (*p >= '0' && *p <= '9') {
        value += double(*p++ - '0') * scale;
        scale *= 0.1;
      }
    }
    if (*p == 'e' || *p == 'E') {
      ++p;
      bool eneg = false;
      if (*p == '-' || *p == '+') eneg = (*p++ == '-');
      int exponent = 0;
      while (*p >= '0' && *p <= '9') exponent = exponent * 10 + (*p++ - '0');
      double factor = 1.0;
      for (int i = 0; i < exponent; i++) factor *= 10.0;
      value = eneg ? value / factor : value * factor;
    }
    return neg ? -value : value;
  }

  bool ParseStringArray(std::vector<std::string>* out, size_t cap) {
    if (!Consume('[')) return false;
    out->clear();
    SkipWs();
    if (cur < end && *cur == ']') {
      ++cur;
      return true;
    }
    for (;;) {
      std::string item;
      if (!ParseString(&item)) return false;
      if (out->size() >= cap) return Fail("array exceeds bound");
      out->push_back(item);
      SkipWs();
      if (cur < end && *cur == ',') {
        ++cur;
        continue;
      }
      if (cur < end && *cur == ']') {
        ++cur;
        return true;
      }
      return Fail("expected ',' or ']'");
    }
  }

  // Generic bounded skipper for keys the service does not consume.
  bool SkipValue(unsigned depth) {
    if (depth > kMaxJsonDepth) return Fail("json too deep");
    SkipWs();
    if (cur >= end) return Fail("unexpected end");
    char c = *cur;
    if (c == '"') {
      std::string ignored;
      return ParseString(&ignored);
    }
    if (c == '{') {
      ++cur;
      SkipWs();
      if (cur < end && *cur == '}') {
        ++cur;
        return true;
      }
      for (;;) {
        std::string key;
        if (!ParseString(&key)) return false;
        if (!Consume(':')) return false;
        if (!SkipValue(depth + 1)) return false;
        SkipWs();
        if (cur < end && *cur == ',') {
          ++cur;
          continue;
        }
        if (cur < end && *cur == '}') {
          ++cur;
          return true;
        }
        return Fail("expected ',' or '}'");
      }
    }
    if (c == '[') {
      ++cur;
      SkipWs();
      if (cur < end && *cur == ']') {
        ++cur;
        return true;
      }
      for (;;) {
        if (!SkipValue(depth + 1)) return false;
        SkipWs();
        if (cur < end && *cur == ',') {
          ++cur;
          continue;
        }
        if (cur < end && *cur == ']') {
          ++cur;
          return true;
        }
        return Fail("expected ',' or ']'");
      }
    }
    // number / true / false / null
    const char* start = cur;
    while (cur < end && *cur != ',' && *cur != '}' && *cur != ']' &&
           *cur != ' ' && *cur != '\t' && *cur != '\n' && *cur != '\r') {
      ++cur;
    }
    if (cur == start) return Fail("bad literal");
    return true;
  }

  bool ParseEvidence(EvidenceHandleRec* ev) {
    if (!Consume('{')) return false;
    SkipWs();
    if (cur < end && *cur == '}') {
      ++cur;
      return true;
    }
    for (;;) {
      std::string key;
      if (!ParseString(&key)) return false;
      if (!Consume(':')) return false;
      bool ok;
      if (key == "handle_id") ok = ParseString(&ev->handle_id);
      else if (key == "source_namespace") ok = ParseString(&ev->source_namespace);
      else if (key == "canonical_object_id") ok = ParseString(&ev->canonical_object_id);
      else if (key == "source_version") ok = ParseString(&ev->source_version);
      else if (key == "source_locator") ok = ParseString(&ev->source_locator);
      else if (key == "exact_text") ok = ParseString(&ev->exact_text);
      else if (key == "supported_values")
        ok = ParseStringArray(&ev->supported_values, kMaxSupportedValues);
      else ok = SkipValue(1);
      if (!ok) return false;
      SkipWs();
      if (cur < end && *cur == ',') {
        ++cur;
        continue;
      }
      if (cur < end && *cur == '}') {
        ++cur;
        return true;
      }
      return Fail("expected ',' or '}' in evidence");
    }
  }

  bool ParseRecord(GroundedRecord* rec) {
    if (!Consume('{')) return false;
    for (;;) {
      std::string key;
      if (!ParseString(&key)) return false;
      if (!Consume(':')) return false;
      bool ok;
      if (key == "entity_id") ok = ParseString(&rec->entity_id);
      else if (key == "canonical_title") ok = ParseString(&rec->canonical_title);
      else if (key == "address_surfaces")
        ok = ParseStringArray(&rec->address_surfaces, kMaxSurfacesPerRecord);
      else if (key == "relation") ok = ParseString(&rec->relation);
      else if (key == "relation_terms")
        ok = ParseStringArray(&rec->relation_terms, kMaxRelationTerms);
      else if (key == "relation_text") ok = ParseString(&rec->relation_text);
      else if (key == "answer_kind") ok = ParseString(&rec->answer_kind);
      else if (key == "values") ok = ParseStringArray(&rec->values, kMaxValuesPerRecord);
      else if (key == "confidence") ok = ParseNumber(&rec->confidence);
      else if (key == "evidence") ok = ParseEvidence(&rec->evidence);
      else ok = SkipValue(1);
      if (!ok) return false;
      SkipWs();
      if (cur < end && *cur == ',') {
        ++cur;
        continue;
      }
      if (cur < end && *cur == '}') {
        ++cur;
        return true;
      }
      return Fail("expected ',' or '}' in record");
    }
  }
};

}  // namespace

bool ParseGroundedRecordsJson(const char* json, size_t length,
                              std::vector<GroundedRecord>* out,
                              std::string* error) {
  std::string local_error;
  Parser parser{json, json + length, &local_error};
  out->clear();
  if (!parser.Consume('[')) {
    if (error) *error = local_error;
    return false;
  }
  parser.SkipWs();
  if (parser.cur < parser.end && *parser.cur == ']') {
    ++parser.cur;
  } else {
    for (;;) {
      if (out->size() >= kMaxRecords) {
        if (error) *error = "record count exceeds bound";
        return false;
      }
      GroundedRecord rec;
      if (!parser.ParseRecord(&rec)) {
        if (error) *error = local_error;
        return false;
      }
      if (rec.entity_id.empty() || rec.address_surfaces.empty() ||
          rec.relation_terms.empty() || rec.values.empty() ||
          rec.evidence.handle_id.empty()) {
        if (error) *error = "record missing required grounded fields";
        return false;
      }
      out->push_back(rec);
      parser.SkipWs();
      if (parser.cur < parser.end && *parser.cur == ',') {
        ++parser.cur;
        continue;
      }
      if (parser.cur < parser.end && *parser.cur == ']') {
        ++parser.cur;
        break;
      }
      if (error) *error = "expected ',' or ']' at top level";
      return false;
    }
  }
  parser.SkipWs();
  if (parser.cur != parser.end) {
    if (error) *error = "trailing bytes after record array";
    return false;
  }
  if (out->empty()) {
    if (error) *error = "the vertical slice requires at least one grounded record";
    return false;
  }
  return true;
}

}  // namespace service
}  // namespace aethercore
