/* Host harness for the native memory subsystem: runs a fixed operation
 * script and prints the same normalized trace as parity_check.py does for
 * the Python implementation. Byte-identical output = behavioral parity. */
#include <cstdio>
#include <string>

#include "../main/memory/memory_native.h"

using namespace acmem;

static void emit_record(const Record &r) {
  printf("{\"record\":\"%s\",\"tier\":\"%s\",\"residency\":\"%s\","
         "\"hash\":\"%s\",\"epoch\":%llu,\"access\":%llu,"
         "\"deletion\":\"%s\",\"pinned\":%s,\"vb\":%s}\n",
         r.memory_id.c_str(), to_str(r.tier), to_str(r.residency),
         r.content_hash.c_str(), (unsigned long long)r.modified_epoch,
         (unsigned long long)r.access_count, to_str(r.deletion),
         r.pinned ? "true" : "false", r.verification_bound ? "true" : "false");
}

static void emit_err(const char *op, const MemoryError &e) {
  printf("{\"op\":\"%s\",\"error\":%d,\"detail\":\"%s\"}\n", op, (int)e.code,
         e.detail.c_str());
}

int main(int argc, char **argv) {
  const char *store_path = argc > 1 ? argv[1] : nullptr;
  Watermarks wm;
  wm.ephemeral = 4;
  wm.short_term = 3;
  wm.working = 4;
  wm.long_term = 8;
  Manager m(wm);
  UserMemory um(&m);
  Record r;

  /* 1. explicit-remember matcher */
  printf("{\"remember\":\"%s\"}\n",
         explicit_remember_payload("Remember that my favorite color is green.").c_str());
  printf("{\"remember\":\"%s\"}\n",
         explicit_remember_payload("remember that  Mercury is interesting ").c_str());
  printf("{\"remember\":\"%s\"}\n",
         explicit_remember_payload("please remember this").c_str());
  printf("{\"remember\":\"%s\"}\n",
         explicit_remember_payload("REMEMBER THAT Case Matters").c_str());

  /* 2. authorized user-memory CRUD */
  um.write("operator", "my favorite color is green", "authz-1", "uart-user", 700, &r);
  emit_record(r);
  std::string mem_id = r.memory_id;
  MemoryError e = um.write("operator", "unauthorized attempt", "", "uart-user", 500, &r);
  emit_err("write-noauth", e);
  {
    auto hits = um.search("operator", "favorite color");
    if (!hits.empty()) emit_record(hits[0]);
  }
  um.edit("operator", mem_id, "my favorite color is blue", "authz-2", &r);
  emit_record(r);
  um.read("operator", mem_id, &r);
  emit_record(r);
  e = um.read("other-user", mem_id, &r);
  emit_err("read-wrong-user", e);

  /* 3. conversation turns + promotion/demotion */
  Provenance sys;
  sys.authority = Authority::SYSTEM;
  sys.source_id = "trace";
  for (int i = 0; i < 3; i++) {
    Payload p;
    p.text = "user turn " + std::to_string(i);
    m.create(MemoryType::CONVERSATION_TURN, SemanticTier::SHORT_TERM,
             Residency::HOT, p, sys, "", -1, 1000, 100 + i, 50, {}, "sess-1", "",
             false, false, "", &r);
    emit_record(r);
  }
  m.promote("mem-00000003", "recall", &r);
  emit_record(r);
  m.demote("mem-00000003", SemanticTier::SHORT_TERM, "stale", &r);
  emit_record(r);
  e = m.demote("mem-00000003", SemanticTier::LONG_TERM, "bad", &r);
  emit_err("demote-longer", e);

  /* 4. watermark eviction (short_term limit 3; 4th create evicts) */
  {
    Payload p;
    p.text = "overflow turn";
    m.create(MemoryType::CONVERSATION_TURN, SemanticTier::SHORT_TERM,
             Residency::HOT, p, sys, "", -1, 1000, 5, 50, {}, "sess-1", "",
             false, false, "", &r);
    emit_record(r);
  }

  /* 5. TTL expiry + pinned survival */
  {
    Payload p;
    p.text = "temporary probe";
    m.create(MemoryType::SCRATCH, SemanticTier::SHORT_TERM, Residency::HOT, p,
             sys, "", 2, 1000, 10, 10, {}, "sess-1", "", false, false, "", &r);
    emit_record(r);
    Payload p2;
    p2.text = "pinned probe";
    m.create(MemoryType::SCRATCH, SemanticTier::SHORT_TERM, Residency::HOT, p2,
             sys, "", 2, 1000, 10, 10, {}, "sess-1", "", true, false, "", &r);
    emit_record(r);
    uint32_t reclaimed = m.advance(3);
    printf("{\"reclaimed\":%u}\n", reclaimed);
  }

  /* 6. promotion score candidates */
  for (const auto &c : m.promotion_candidates(0))
    printf("{\"candidate\":\"%s\",\"score\":%llu}\n", c.memory_id.c_str(),
           (unsigned long long)Manager::promotion_score(c));

  /* 7. selected evidence + verification binding */
  {
    Payload p;
    p.text = "evidence blob head";
    Provenance prov;
    prov.authority = Authority::EXTERNAL_GROUNDED;
    prov.source_id = "pack";
    prov.evidence_handle = "evh-1";
    m.create(MemoryType::SELECTED_EVIDENCE, SemanticTier::WORKING, Residency::HOT,
             p, prov, "evh-1", -1, 1000, 0, 0, {"cog-1"}, "sess-1", "", true,
             false, "", &r);
    emit_record(r);
    m.mark_verification_bound(r.memory_id, &r);
    emit_record(r);
  }

  /* 8. tombstone + compact */
  um.erase("operator", mem_id, "authz-3", &r);
  emit_record(r);
  printf("{\"compacted\":%u}\n", m.compact_tombstones(m.epoch()));

  /* 9. export/import round-trip + persistence */
  std::string exported = m.export_state_json();
  printf("{\"export_sha256\":\"sha256:%s\",\"export_bytes\":%zu}\n",
         sha256_hex(exported).c_str(), exported.size());
  if (store_path) {
    if (!store_save(store_path, m)) {
      printf("{\"store\":\"save-failed\"}\n");
      return 1;
    }
    Manager loaded;
    MemoryError le = {};
    if (!store_load(store_path, &loaded, &le)) {
      printf("{\"store\":\"load-failed\",\"detail\":\"%s\"}\n", le.detail.c_str());
      return 1;
    }
    std::string reexported = loaded.export_state_json();
    printf("{\"roundtrip\":\"%s\"}\n",
           reexported == exported ? "identical" : "MISMATCH");
  }

  /* 10. full canonical state dump for byte-exact diff */
  printf("STATE_BEGIN\n%s\nSTATE_END\n", exported.c_str());
  return 0;
}
