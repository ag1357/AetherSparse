/* Pack-backed KnowledgeProvider for the V15 interactive service.
 *
 * Replaces the static V13 fixture vector in production: each query is
 * resolved through the on-device Semantic Address v2 trigram index
 * (ACP1IDX1) to a bounded candidate set, then evidence for the selected
 * entity is pulled from the Pack-v2/ACP1EVD1 occurrence blobs, and one
 * grounded record per entity is synthesized with values copied exactly
 * from the evidence context. Nothing outside the queried entities is
 * loaded into RAM: occurrence blobs are streamed through the pager and
 * only the best cleaned context is kept per entity.
 */
#pragma once

#include <stdint.h>
#include <stddef.h>

#include "pack_io.h"
#include "service/knowledge_provider.h"

namespace ac {
namespace knowledge {

/* Entity ids seen by the service core are "packv2:e<entity_idx>". */
constexpr char kEntityIdPrefix[] = "packv2:e";

class PackProvider : public aethercore::service::KnowledgeProvider {
 public:
  PackProvider() = default;
  ~PackProvider() override;

  // Creates the pager (PSRAM page cache) and the occurrence-blob scratch.
  // Requires pack_open/idx_open/ent_open/evd_open to have completed.
  bool start(size_t pager_bytes);

  bool Address(const std::string &text,
               std::vector<aethercore::service::AddressHyp> *out) override;
  bool FetchRecords(const std::vector<std::string> &entity_ids,
                    const std::string &query_text,
                    std::vector<aethercore::service::GroundedRecord> *out) override;

 private:
  Pager *pager_ = nullptr;
};

}  // namespace knowledge
}  // namespace ac
