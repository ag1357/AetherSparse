from aethersparse.edge_runtime.layout import PagedPostingIndex


def test_paging_preserves_candidate_completeness_across_cache_sizes() -> None:
    surfaces = ["alan turing", "alain turing", "turing machine", "grace hopper"]
    uncached = PagedPostingIndex.from_surfaces(surfaces, page_bytes=512, cache_bytes=0)
    cached = PagedPostingIndex.from_surfaces(surfaces, page_bytes=512, cache_bytes=2048)
    for query in surfaces:
        assert cached.query(query).candidate_ids == uncached.query(query).candidate_ids
    assert 1 in cached.query("alan turing").candidate_ids


def test_cache_reduces_physical_page_misses_without_changing_results() -> None:
    surfaces = [f"entity number {index}" for index in range(200)]
    index = PagedPostingIndex.from_surfaces(surfaces, page_bytes=512, cache_bytes=4096)
    first = index.query("entity number 100")
    second = index.query("entity number 100")
    assert second.candidate_ids == first.candidate_ids
    assert second.cache_misses <= first.cache_misses
