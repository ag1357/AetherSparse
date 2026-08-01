"""Deterministic builder for the retained flat structured substrate."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from itertools import pairwise
from typing import Any

from aethersparse.compiler import stable_json
from aethersparse.substrate.models import (
    AliasKind,
    AliasRecord,
    AnchorRecord,
    ChunkRecord,
    ClaimSeed,
    DocumentRecord,
    EntityRecord,
    ExplicitAliasSeed,
    FlatIndexes,
    FlatStructuredPack,
    HeadingRecord,
    Posting,
    RedirectRecord,
    SourceBinding,
    SourcePage,
    StructuredClaim,
    SubstrateMetadata,
)

TOKEN_RE = re.compile("[^\\W_]+(?:['\\u2019-][^\\W_]+)*", re.UNICODE)
REDIRECT_RE = re.compile(r"^\s*#redirect\s*\[\[([^\]|#]+)", re.IGNORECASE)
ANCHOR_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]")
HEADING_RE = re.compile(r"(?m)^(={1,6})\s*(.*?)\s*\1\s*$")
BLOCK_RE = re.compile(r"\S(?:.*?\S)?(?=\n\s*\n|\Z)", re.DOTALL)


class SubstrateBuildError(ValueError):
    """Raised when a source or exact provenance invariant is violated."""


def sha256_text(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def normalize_surface(value: str) -> str:
    """Stable lookup normalization, independent of source-text preservation."""

    normalized = unicodedata.normalize("NFKC", value.replace("_", " "))
    return " ".join(normalized.strip().split()).casefold()


def tokenize(value: str) -> tuple[str, ...]:
    return tuple(match.group(0).casefold() for match in TOKEN_RE.finditer(value))


def _stable_id(kind: str, *parts: object) -> str:
    material = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"as:v050:{kind}:{hashlib.sha256(material).hexdigest()[:24]}"


def _document_id(page: SourcePage) -> str:
    # Page and revision identity are authoritative. Content hash is deliberately absent.
    return _stable_id("document", page.namespace, page.page_id, page.revision_id)


def _entity_id(normalized_title: str) -> str:
    return _stable_id("entity", normalized_title)


def _page_sha256(page: SourcePage) -> str:
    actual = sha256_text(page.text)
    if page.source_sha256 is not None and page.source_sha256 != actual:
        raise SubstrateBuildError(
            f"source hash mismatch for page {page.page_id}: {page.source_sha256} != {actual}"
        )
    return actual


def _byte_offset(text: str, char_offset: int) -> int:
    return len(text[:char_offset].encode("utf-8"))


class _BindingFactory:
    def __init__(self, documents_by_page: Mapping[str, DocumentRecord]) -> None:
        self.documents_by_page = documents_by_page
        self._bindings: dict[tuple[str, int, int], SourceBinding] = {}

    def make(self, page_id: str, start: int, end: int) -> SourceBinding:
        document = self.documents_by_page[page_id]
        if start < 0 or end > len(document.text) or end <= start:
            raise SubstrateBuildError(
                f"invalid source interval for page {page_id}: [{start}, {end})"
            )
        key = (document.document_id, start, end)
        existing = self._bindings.get(key)
        if existing is not None:
            return existing
        surface = document.text[start:end]
        byte_start = _byte_offset(document.text, start)
        byte_end = _byte_offset(document.text, end)
        binding = SourceBinding(
            binding_id=_stable_id("binding", document.document_id, start, end),
            document_id=document.document_id,
            page_id=document.page_id,
            revision_id=document.revision_id,
            source_sha256=document.source_sha256,
            char_start=start,
            char_end=end,
            byte_start=byte_start,
            byte_end=byte_end,
            surface_sha256=sha256_text(surface),
            surface=surface,
        )
        self._bindings[key] = binding
        return binding

    def values(self) -> tuple[SourceBinding, ...]:
        return tuple(sorted(self._bindings.values(), key=lambda item: item.binding_id))


def _split_block(start: int, text: str, max_chunk_chars: int) -> Iterable[tuple[int, int]]:
    cursor = 0
    while len(text) - cursor > max_chunk_chars:
        limit = cursor + max_chunk_chars
        cut = text.rfind(" ", cursor, limit + 1)
        if cut <= cursor:
            cut = limit
        yield start + cursor, start + cut
        cursor = cut
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
    if cursor < len(text):
        yield start + cursor, start + len(text)


def _posting_tuple(
    values: Mapping[str, tuple[set[str], set[str], set[str]]],
) -> tuple[Posting, ...]:
    return tuple(
        Posting(
            key=key,
            document_ids=tuple(sorted(documents)),
            chunk_ids=tuple(sorted(chunks)),
            claim_ids=tuple(sorted(claims)),
        )
        for key, (documents, chunks, claims) in sorted(values.items())
    )


def _new_posting_map() -> defaultdict[str, tuple[set[str], set[str], set[str]]]:
    return defaultdict(lambda: (set(), set(), set()))


def validate_source_bindings(pack: FlatStructuredPack) -> None:
    """Recompute every immutable source and surface hash and both coordinate systems."""

    documents = {document.document_id: document for document in pack.documents}
    for document in pack.documents:
        if sha256_text(document.text) != document.source_sha256:
            raise SubstrateBuildError(f"document source hash mismatch: {document.document_id}")
        if len(document.text.encode("utf-8")) != document.source_bytes:
            raise SubstrateBuildError(f"document byte count mismatch: {document.document_id}")
    for binding in pack.source_bindings:
        bound_document = documents.get(binding.document_id)
        if bound_document is None:
            raise SubstrateBuildError(f"binding has unknown document: {binding.binding_id}")
        if (
            binding.page_id != bound_document.page_id
            or binding.revision_id != bound_document.revision_id
        ):
            raise SubstrateBuildError(f"binding source identity mismatch: {binding.binding_id}")
        if binding.source_sha256 != bound_document.source_sha256:
            raise SubstrateBuildError(f"binding source hash mismatch: {binding.binding_id}")
        surface = bound_document.text[binding.char_start : binding.char_end]
        source_bytes = bound_document.text.encode("utf-8")
        byte_surface = source_bytes[binding.byte_start : binding.byte_end]
        if surface != binding.surface or byte_surface.decode("utf-8") != binding.surface:
            raise SubstrateBuildError(f"binding coordinates mismatch: {binding.binding_id}")
        if sha256_text(surface) != binding.surface_sha256:
            raise SubstrateBuildError(f"binding surface hash mismatch: {binding.binding_id}")


class StructuredSubstrateBuilder:
    """Compile immutable pages and adjudicated claims into deterministic flat indexes."""

    def __init__(self, metadata: SubstrateMetadata, *, max_chunk_chars: int = 1024) -> None:
        if max_chunk_chars < 128:
            raise ValueError("max_chunk_chars must be at least 128")
        self.metadata = metadata
        self.max_chunk_chars = max_chunk_chars

    def build(
        self,
        pages: Sequence[SourcePage],
        *,
        claim_seeds: Sequence[ClaimSeed] = (),
        explicit_aliases: Sequence[ExplicitAliasSeed] = (),
        entity_types: Mapping[str, str] | None = None,
    ) -> FlatStructuredPack:
        if not pages:
            raise SubstrateBuildError("at least one source page is required")
        page_ids = [page.page_id for page in pages]
        if len(page_ids) != len(set(page_ids)):
            raise SubstrateBuildError("page_id must be unique even when source text is identical")

        sorted_pages = sorted(
            pages,
            key=lambda page: (
                page.namespace,
                0 if page.page_id.isdigit() else 1,
                int(page.page_id) if page.page_id.isdigit() else page.page_id,
            ),
        )
        pages_by_id = {page.page_id: page for page in sorted_pages}
        redirects_raw: dict[str, tuple[str, re.Match[str]]] = {}
        canonical_page_titles: dict[str, SourcePage] = {}
        for page in sorted_pages:
            redirect_match = REDIRECT_RE.match(page.text)
            normalized_title = normalize_surface(page.title)
            if redirect_match is not None:
                redirects_raw[page.page_id] = (redirect_match.group(1).strip(), redirect_match)
            elif page.namespace == 0:
                if normalized_title in canonical_page_titles:
                    raise SubstrateBuildError(f"duplicate canonical title: {page.title}")
                canonical_page_titles[normalized_title] = page

        title_to_entity = {
            title: _entity_id(title) for title in sorted(canonical_page_titles)
        }
        redirect_title_to_entity: dict[str, str] = {}
        for page_id, (target_title, _) in redirects_raw.items():
            target_id = title_to_entity.get(normalize_surface(target_title))
            if target_id is not None:
                redirect_page = pages_by_id[page_id]
                redirect_title_to_entity[normalize_surface(redirect_page.title)] = target_id

        documents: list[DocumentRecord] = []
        documents_by_page: dict[str, DocumentRecord] = {}
        for page in sorted_pages:
            normalized_title = normalize_surface(page.title)
            entity_id = title_to_entity.get(normalized_title) or redirect_title_to_entity.get(
                normalized_title
            )
            document = DocumentRecord(
                document_id=_document_id(page),
                page_id=page.page_id,
                namespace=page.namespace,
                revision_id=page.revision_id,
                revision_timestamp=page.revision_timestamp,
                title=page.title,
                normalized_title=normalized_title,
                source_url=page.source_url,
                license=page.license,
                source_sha256=_page_sha256(page),
                source_bytes=len(page.text.encode("utf-8")),
                text=page.text,
                canonical_entity_id=entity_id,
                is_redirect=page.page_id in redirects_raw,
            )
            documents.append(document)
            documents_by_page[page.page_id] = document

        normalized_entity_types = {
            normalize_surface(key): value for key, value in (entity_types or {}).items()
        }
        entities = tuple(
            EntityRecord(
                entity_id=title_to_entity[normalized_title],
                canonical_title=page.title,
                normalized_title=normalized_title,
                document_id=documents_by_page[page.page_id].document_id,
                entity_type=normalized_entity_types.get(normalized_title, "unknown"),
            )
            for normalized_title, page in sorted(canonical_page_titles.items())
        )
        binding_factory = _BindingFactory(documents_by_page)

        redirects: list[RedirectRecord] = []
        aliases_grouped: defaultdict[tuple[str, str, AliasKind], set[str]] = defaultdict(set)
        for entity in entities:
            aliases_grouped[(entity.canonical_title, entity.entity_id, AliasKind.TITLE)]

        for page_id, (target_title, match) in sorted(redirects_raw.items()):
            document = documents_by_page[page_id]
            target_entity_id = title_to_entity.get(normalize_surface(target_title))
            if target_entity_id is None:
                continue
            target_start, target_end = match.span(1)
            binding = binding_factory.make(page_id, target_start, target_end)
            redirect = RedirectRecord(
                redirect_id=_stable_id("redirect", document.document_id, target_entity_id),
                source_document_id=document.document_id,
                surface_title=document.title,
                target_title=target_title,
                target_entity_id=target_entity_id,
                binding_id=binding.binding_id,
            )
            redirects.append(redirect)
            aliases_grouped[(document.title, target_entity_id, AliasKind.REDIRECT)].add(
                binding.binding_id
            )

        anchors: list[AnchorRecord] = []
        for page in sorted_pages:
            if page.page_id in redirects_raw:
                continue
            document = documents_by_page[page.page_id]
            for match in ANCHOR_RE.finditer(page.text):
                target_title = match.group(1).strip()
                target_entity_id = title_to_entity.get(normalize_surface(target_title))
                if target_entity_id is None:
                    target_entity_id = redirect_title_to_entity.get(normalize_surface(target_title))
                if target_entity_id is None:
                    continue
                surface_group = 2 if match.group(2) is not None else 1
                surface = match.group(surface_group).strip()
                raw_start, _ = match.span(surface_group)
                leading = len(match.group(surface_group)) - len(match.group(surface_group).lstrip())
                start = raw_start + leading
                end = start + len(surface)
                binding = binding_factory.make(page.page_id, start, end)
                anchor = AnchorRecord(
                    anchor_id=_stable_id(
                        "anchor", document.document_id, start, end, target_entity_id
                    ),
                    source_document_id=document.document_id,
                    surface=surface,
                    normalized_surface=normalize_surface(surface),
                    target_title=target_title,
                    target_entity_id=target_entity_id,
                    binding_id=binding.binding_id,
                )
                anchors.append(anchor)
                aliases_grouped[(surface, target_entity_id, AliasKind.ANCHOR)].add(
                    binding.binding_id
                )

        for alias_seed in sorted(
            explicit_aliases, key=lambda item: (item.surface, item.target_title)
        ):
            entity_id = title_to_entity.get(normalize_surface(alias_seed.target_title))
            if entity_id is None:
                entity_id = redirect_title_to_entity.get(
                    normalize_surface(alias_seed.target_title)
                )
            if entity_id is None:
                raise SubstrateBuildError(
                    f"explicit alias has unknown target: {alias_seed.target_title}"
                )
            aliases_grouped[(alias_seed.surface, entity_id, AliasKind.EXPLICIT)]

        aliases = tuple(
            AliasRecord(
                alias_id=_stable_id("alias", normalize_surface(surface), entity_id, kind.value),
                surface=surface,
                normalized_surface=normalize_surface(surface),
                entity_id=entity_id,
                kind=kind,
                support_binding_ids=tuple(sorted(binding_ids)),
            )
            for (surface, entity_id, kind), binding_ids in sorted(
                aliases_grouped.items(),
                key=lambda item: (
                    normalize_surface(item[0][0]),
                    item[0][1],
                    item[0][2].value,
                ),
            )
        )

        headings: list[HeadingRecord] = []
        heading_ranges: dict[str, list[tuple[int, str]]] = defaultdict(list)
        for page in sorted_pages:
            document = documents_by_page[page.page_id]
            for match in HEADING_RE.finditer(page.text):
                text = match.group(2).strip()
                raw_start = match.start(2)
                leading = len(match.group(2)) - len(match.group(2).lstrip())
                start = raw_start + leading
                end = start + len(text)
                binding = binding_factory.make(page.page_id, start, end)
                headings.append(
                    HeadingRecord(
                        heading_id=_stable_id("heading", document.document_id, start, end),
                        document_id=document.document_id,
                        level=len(match.group(1)),
                        text=text,
                        normalized_text=normalize_surface(text),
                        binding_id=binding.binding_id,
                    )
                )
                heading_ranges[page.page_id].append((match.end(), text))

        chunks: list[ChunkRecord] = []
        chunks_by_document: defaultdict[
            str, list[tuple[ChunkRecord, SourceBinding]]
        ] = defaultdict(list)
        for page in sorted_pages:
            document = documents_by_page[page.page_id]
            ordinal = 0
            page_heading_boundaries = sorted(heading_ranges[page.page_id])
            for block_match in BLOCK_RE.finditer(page.text):
                for start, end in _split_block(
                    block_match.start(), block_match.group(0), self.max_chunk_chars
                ):
                    if end <= start:
                        continue
                    heading: str | None = None
                    for heading_end, heading_text in page_heading_boundaries:
                        if heading_end <= start:
                            heading = heading_text
                        else:
                            break
                    binding = binding_factory.make(page.page_id, start, end)
                    chunk = ChunkRecord(
                        chunk_id=_stable_id("chunk", document.document_id, ordinal, start, end),
                        document_id=document.document_id,
                        ordinal=ordinal,
                        heading=heading,
                        text=page.text[start:end],
                        binding_id=binding.binding_id,
                    )
                    chunks.append(chunk)
                    chunks_by_document[document.document_id].append((chunk, binding))
                    ordinal += 1

        claims: list[StructuredClaim] = []
        for claim_seed in sorted(
            claim_seeds,
            key=lambda item: (
                item.page_id,
                item.subject_title,
                item.relation_family,
                item.object_value,
            ),
        ):
            claim_document = documents_by_page.get(claim_seed.page_id)
            if claim_document is None:
                raise SubstrateBuildError(
                    f"claim has unknown page_id: {claim_seed.page_id}"
                )
            subject_entity_id = title_to_entity.get(
                normalize_surface(claim_seed.subject_title)
            )
            if subject_entity_id is None:
                subject_entity_id = redirect_title_to_entity.get(
                    normalize_surface(claim_seed.subject_title)
                )
            if subject_entity_id is None:
                raise SubstrateBuildError(
                    f"claim has unknown canonical subject: {claim_seed.subject_title}"
                )
            if claim_seed.char_start is not None and claim_seed.char_end is not None:
                start, end = claim_seed.char_start, claim_seed.char_end
                if (
                    claim_seed.evidence_text is not None
                    and claim_document.text[start:end] != claim_seed.evidence_text
                ):
                    raise SubstrateBuildError("claim offsets do not match evidence_text")
            else:
                assert claim_seed.evidence_text is not None
                start = claim_document.text.find(claim_seed.evidence_text)
                if start < 0:
                    raise SubstrateBuildError(
                        f"claim evidence not present in page {claim_seed.page_id}: "
                        f"{claim_seed.evidence_text!r}"
                    )
                if claim_document.text.find(claim_seed.evidence_text, start + 1) >= 0:
                    raise SubstrateBuildError(
                        f"claim evidence is ambiguous in page {claim_seed.page_id}; "
                        "provide offsets"
                    )
                end = start + len(claim_seed.evidence_text)
            binding = binding_factory.make(claim_seed.page_id, start, end)
            object_entity_id = title_to_entity.get(
                normalize_surface(claim_seed.object_value)
            )
            if object_entity_id is None:
                object_entity_id = redirect_title_to_entity.get(
                    normalize_surface(claim_seed.object_value)
                )
            claims.append(
                StructuredClaim(
                    claim_id=_stable_id(
                        "claim",
                        subject_entity_id,
                        claim_seed.relation_family,
                        claim_seed.object_value,
                        binding.binding_id,
                    ),
                    subject_entity_id=subject_entity_id,
                    relation_family=normalize_surface(claim_seed.relation_family),
                    object_value=claim_seed.object_value,
                    object_entity_id=object_entity_id,
                    object_kind=claim_seed.object_kind,
                    claim_kind=claim_seed.claim_kind,
                    source_binding_ids=(binding.binding_id,),
                    source_document_ids=(claim_document.document_id,),
                    attributes=claim_seed.attributes,
                )
            )

        bindings = binding_factory.values()
        binding_by_id = {binding.binding_id: binding for binding in bindings}
        lexical = _new_posting_map()
        title = _new_posting_map()
        heading_index = _new_posting_map()
        phrase = _new_posting_map()
        relation = _new_posting_map()
        entity_index = _new_posting_map()

        for document in documents:
            for term in set(tokenize(document.title)):
                title[term][0].add(document.document_id)
            if document.canonical_entity_id is not None:
                entity_index[document.canonical_entity_id][0].add(document.document_id)
        for heading_record in headings:
            for term in set(tokenize(heading_record.text)):
                heading_index[term][0].add(heading_record.document_id)
        for chunk in chunks:
            terms = tokenize(chunk.text)
            for term in set(terms):
                lexical[term][0].add(chunk.document_id)
                lexical[term][1].add(chunk.chunk_id)
            for pair in set(pairwise(terms)):
                key = f"{pair[0]} {pair[1]}"
                phrase[key][0].add(chunk.document_id)
                phrase[key][1].add(chunk.chunk_id)
        for claim in claims:
            posting = relation[claim.relation_family]
            posting[2].add(claim.claim_id)
            posting[0].update(claim.source_document_ids)
            entity_posting = entity_index[claim.subject_entity_id]
            entity_posting[2].add(claim.claim_id)
            entity_posting[0].update(claim.source_document_ids)
            for binding_id in claim.source_binding_ids:
                binding = binding_by_id[binding_id]
                for chunk, chunk_binding in chunks_by_document[binding.document_id]:
                    if (
                        chunk_binding.char_start <= binding.char_start
                        and chunk_binding.char_end >= binding.char_end
                    ):
                        posting[1].add(chunk.chunk_id)
                        entity_posting[1].add(chunk.chunk_id)

        indexes = FlatIndexes(
            lexical=_posting_tuple(lexical),
            title=_posting_tuple(title),
            heading=_posting_tuple(heading_index),
            phrase=_posting_tuple(phrase),
            relation=_posting_tuple(relation),
            entity=_posting_tuple(entity_index),
        )
        ordered_redirects = tuple(sorted(redirects, key=lambda item: item.redirect_id))
        ordered_anchors = tuple(sorted(anchors, key=lambda item: item.anchor_id))
        ordered_heading_records = tuple(sorted(headings, key=lambda item: item.heading_id))
        ordered_chunks = tuple(sorted(chunks, key=lambda item: item.chunk_id))
        ordered_claims = tuple(sorted(claims, key=lambda item: item.claim_id))
        unsigned: dict[str, Any] = {
            "metadata": self.metadata.model_dump(mode="json"),
            "documents": [item.model_dump(mode="json") for item in documents],
            "source_bindings": [item.model_dump(mode="json") for item in bindings],
            "entities": [item.model_dump(mode="json") for item in entities],
            "aliases": [item.model_dump(mode="json") for item in aliases],
            "redirects": [item.model_dump(mode="json") for item in ordered_redirects],
            "anchors": [item.model_dump(mode="json") for item in ordered_anchors],
            "headings": [item.model_dump(mode="json") for item in ordered_heading_records],
            "chunks": [item.model_dump(mode="json") for item in ordered_chunks],
            "claims": [item.model_dump(mode="json") for item in ordered_claims],
            "indexes": indexes.model_dump(mode="json"),
        }
        pack = FlatStructuredPack(
            metadata=self.metadata,
            documents=tuple(documents),
            source_bindings=bindings,
            entities=entities,
            aliases=aliases,
            redirects=ordered_redirects,
            anchors=ordered_anchors,
            headings=ordered_heading_records,
            chunks=ordered_chunks,
            claims=ordered_claims,
            indexes=indexes,
            manifest_sha256=sha256_bytes(stable_json(unsigned)),
        )
        validate_source_bindings(pack)
        return pack
