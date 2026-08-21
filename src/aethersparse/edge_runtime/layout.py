"""Paged address-index layout and deterministic cache accounting."""

from __future__ import annotations

import math
from collections import OrderedDict, defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class CacheProjection:
    cache_bytes: int
    queries: int
    directory_bytes: int
    cold_index_bytes: int
    bytes_per_query_mean: float
    pages_per_query_mean: float
    random_reads_per_query_mean: float
    sequential_reads_per_query_mean: float
    cache_hit_rate: float
    candidate_completeness: float


@dataclass(frozen=True)
class AddressLayoutProfile:
    page_bytes: int
    logical_index_bytes: int
    surface_count: int
    posting_count: int
    resident_surface_directory_bytes: int
    resident_top_postings_directory_bytes: int
    cold_index_bytes: int
    cache_projections: tuple[CacheProjection, ...]

    @property
    def resident_directory_bytes(self) -> int:
        return self.resident_surface_directory_bytes + self.resident_top_postings_directory_bytes


@dataclass(frozen=True)
class QueryIo:
    candidate_ids: tuple[int, ...]
    pages: int
    random_reads: int
    sequential_reads: int
    cache_hits: int
    cache_misses: int


class PagedPostingIndex:
    """A packed posting blob with page-read instrumentation and an LRU.

    Paging changes only where bytes live. All posting lists are read completely,
    so candidate completeness is identical to the monolithic logical index.
    """

    def __init__(
        self,
        postings: dict[str, tuple[int, ...]],
        *,
        page_bytes: int = 4096,
        cache_bytes: int = 0,
    ) -> None:
        if page_bytes < 512 or page_bytes & (page_bytes - 1):
            raise ValueError("page_bytes must be a power of two >=512")
        if cache_bytes < 0:
            raise ValueError("cache_bytes cannot be negative")
        self.page_bytes = page_bytes
        self.cache_pages = cache_bytes // page_bytes
        self.directory: dict[str, tuple[int, int, tuple[int, ...]]] = {}
        offset = 0
        for key in sorted(postings):
            values = tuple(postings[key])
            byte_length = len(values) * 4
            self.directory[key] = (offset, byte_length, values)
            offset += byte_length
        self.cold_posting_bytes = math.ceil(offset / page_bytes) * page_bytes
        self.directory_bytes = sum(len(key.encode("utf-8")) + 12 for key in self.directory)
        self._cache: OrderedDict[int, None] = OrderedDict()

    @classmethod
    def from_surfaces(
        cls,
        surfaces: list[str],
        *,
        page_bytes: int = 4096,
        cache_bytes: int = 0,
    ) -> PagedPostingIndex:
        postings: dict[str, list[int]] = defaultdict(list)
        for surface_id, surface in enumerate(surfaces, start=1):
            for gram in _trigrams(surface):
                postings[gram].append(surface_id)
        return cls(
            {key: tuple(values) for key, values in postings.items()},
            page_bytes=page_bytes,
            cache_bytes=cache_bytes,
        )

    def query(self, surface: str) -> QueryIo:
        candidates: set[int] = set()
        pages = random_reads = sequential_reads = hits = misses = 0
        def gram_order(key: str) -> tuple[int, str]:
            return self.directory.get(key, (0, 0, ()))[1], key

        for gram in sorted(_trigrams(surface), key=gram_order):
            entry = self.directory.get(gram)
            if entry is None:
                continue
            offset, length, values = entry
            candidates.update(values)
            if length == 0:
                continue
            first = offset // self.page_bytes
            last = (offset + length - 1) // self.page_bytes
            for page in range(first, last + 1):
                pages += 1
                if page in self._cache:
                    hits += 1
                    self._cache.move_to_end(page)
                    continue
                misses += 1
                if page == first:
                    random_reads += 1
                else:
                    sequential_reads += 1
                if self.cache_pages:
                    self._cache[page] = None
                    if len(self._cache) > self.cache_pages:
                        self._cache.popitem(last=False)
        return QueryIo(
            candidate_ids=tuple(sorted(candidates)),
            pages=pages,
            random_reads=random_reads,
            sequential_reads=sequential_reads,
            cache_hits=hits,
            cache_misses=misses,
        )


def _trigrams(value: str) -> tuple[str, ...]:
    normalized = " ".join(value.casefold().replace("_", " ").split())
    padded = f"  {normalized}  "
    return tuple(sorted({padded[index : index + 3] for index in range(len(padded) - 2)}))


def project_v12_edge_layout(
    cache_projections: tuple[CacheProjection, ...],
    *,
    page_bytes: int = 4096,
) -> AddressLayoutProfile:
    """Bind measured cache traces to the authoritative V12 397k logical size."""

    logical_index_bytes = 32_282_740
    return AddressLayoutProfile(
        page_bytes=page_bytes,
        logical_index_bytes=logical_index_bytes,
        surface_count=368_369,
        posting_count=5_909_296,
        # Four-byte surface fingerprint/slot and a 64K four-byte top directory.
        resident_surface_directory_bytes=368_369 * 4,
        resident_top_postings_directory_bytes=65_536 * 4,
        cold_index_bytes=math.ceil(logical_index_bytes / page_bytes) * page_bytes,
        cache_projections=cache_projections,
    )
