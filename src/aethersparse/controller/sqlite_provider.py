"""Lazy bounded controller adapter for canonical v0.5 real-corpus SQLite packs."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
import unicodedata
from pathlib import Path
from typing import Literal

from pydantic import Field

from aethersparse.controller.framing import facets_for_shape
from aethersparse.controller.models import (
    AnswerShape,
    EntityCandidate,
    EntityMention,
    EvidenceRecord,
    ExactSourceSpan,
    FrozenModel,
    QueryFrame,
    RequiredFacet,
    ResolutionMethod,
    StructuredClaim,
)

TOKEN_RE = re.compile(r"[\w'-]{2,}", re.UNICODE)
SENTENCE_RE = re.compile(r"[^\n.!?]{3,480}[.!?]?")
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE_RE = re.compile(
    rf"\b(?:{MONTHS})\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b"
    rf"|\b\d{{1,2}}\s+(?:{MONTHS})\s+\d{{4}}\b"
    r"|\b(?:1[0-9]{3}|20[0-9]{2}|2100)-\d{2}-\d{2}\b"
    r"|\b(?:1[0-9]{3}|20[0-9]{2}|2100)\b",
    re.IGNORECASE,
)
QUANTITY_RE = re.compile(
    r"\b[-+]?\d[\d,.]*(?:\s*(?:km|m|kilomet(?:er|re)s?|miles?|met(?:er|re)s?|feet|ft|"
    r"kg|kilograms?|percent|%|people|inhabitants?|days?|years?|hours?|minutes?))\b",
    re.IGNORECASE,
)
QUOTE_RE = re.compile(r'["“]([^"”\n]{3,300})["”]')
ATTRIBUTION_RE = re.compile(
    r"([A-Z][\w'-]+(?:\s+[A-Z][\w'-]+){0,4})\s+"
    r"(?:said|stated|wrote|called|described)(?:\s+that)?\s*[:,]?\s*$"
)
LINK_RE = re.compile(r"\[\[([^]|#]+)(?:#[^]|]+)?(?:\|([^]]+))?\]\]")
DEFINITION_RE = re.compile(r"\b(?:is|are|was|were)\s+([^\n.!?]{3,300})", re.IGNORECASE)
INFOBOX_RE = re.compile(r"(?m)^\|\s*([\w -]+?)\s*=\s*([^\n]{1,300})")

RELATION_TERMS: dict[str, tuple[str, ...]] = {
    "birth": ("born", "birth"),
    "death": ("died", "death"),
    "date": ("date", "year", "when", "born", "died", "founded", "opened"),
    "location": ("located", "location", "capital", "where", "place"),
    "quantity": ("population", "distance", "height", "length", "many", "much"),
    "quotation": ("said", "stated", "wrote", "quote", "quotation"),
    "definition": (" is ", " are ", " was ", " were "),
    "comparison": ("height", "population", "length", "distance", "age"),
    "cause": ("because", "cause", "reason", "why"),
    "membership": ("member", "part", "included"),
    "event": ("happened", "occurred", "event"),
}

COMPETING_RELATION_TERMS: dict[str, tuple[str, ...]] = {
    "birth": ("died", "death"),
    "death": ("born", "birth"),
}

DYNAMIC_STOP = {
    "about",
    "all",
    "and",
    "are",
    "between",
    "could",
    "did",
    "does",
    "for",
    "from",
    "have",
    "how",
    "into",
    "is",
    "it",
    "its",
    "many",
    "much",
    "name",
    "of",
    "on",
    "refer",
    "that",
    "the",
    "their",
    "them",
    "they",
    "this",
    "to",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "would",
}


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value.replace("_", " ")).casefold().split())


def _entity_id(title: str) -> str:
    digest = hashlib.sha256(_normalize(title).encode("utf-8")).hexdigest()[:24]
    return f"as:v050:entity:{digest}"


def _edit_similarity(left: str, right: str) -> float:
    if left == right:
        return 1.0
    if not left or not right:
        return 0.0
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_character != right_character),
                )
            )
        previous = current
    return 1.0 - previous[-1] / max(len(left), len(right))


class ProviderWorkload(FrozenModel):
    operation: str
    strategy: Literal["lexical", "fusion"]
    requested_limit: int = Field(ge=1, le=64)
    candidate_rows: int = Field(ge=0)
    evidence_records: int = Field(ge=0)
    index_probes: int = Field(ge=0)
    payload_bytes: int = Field(ge=0)
    estimated_sqlite_blocks: int = Field(ge=0)
    sqlite_page_bytes: int = Field(gt=0)
    latency_ms: float = Field(ge=0.0)
    source_document_hashes: tuple[str, ...]
    measurement: Literal["measured_host_sqlite_payload"] = "measured_host_sqlite_payload"


class SQLiteControllerProvider:
    """Read-only FrameLinker/EvidenceProvider over the flat corpus schema.

    Candidate generation and evidence reads are capped. The adapter never scans
    hyperlinks, creates cells, or loads a complete entity registry.
    """

    def __init__(self, path: Path, *, maximum_limit: int = 64) -> None:
        if not path.is_file():
            raise FileNotFoundError(path)
        if maximum_limit < 1 or maximum_limit > 64:
            raise ValueError("maximum_limit must be between one and 64")
        self.path = path
        self.maximum_limit = maximum_limit
        self.db = sqlite3.connect(f"file:{path.resolve()}?mode=ro&immutable=1", uri=True)
        self.db.row_factory = sqlite3.Row
        self.page_bytes = int(self.db.execute("PRAGMA page_size").fetchone()[0])
        self.last_workload: ProviderWorkload | None = None
        self._entity_documents: dict[str, str] = {}
        self._query_started = 0
        self._query_probes = 0
        self._link_payload_bytes = 0
        self._validate_schema()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> SQLiteControllerProvider:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _validate_schema(self) -> None:
        required = {"documents", "chunks", "chunks_fts", "aliases", "redirects", "anchors"}
        tables = {
            str(row[0])
            for row in self.db.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            )
        }
        missing = required - tables
        if missing:
            raise ValueError(f"not a canonical v0.5 corpus pack; missing {sorted(missing)}")
        version = int(self.db.execute("PRAGMA user_version").fetchone()[0])
        if version != 500:
            raise ValueError(f"expected v0.5 schema user_version=500, received {version}")

    def _start_query(self) -> None:
        self._query_started = time.perf_counter_ns()
        self._query_probes = 0
        self._link_payload_bytes = 0
        self.last_workload = None

    def _rows(self, sql: str, parameters: tuple[object, ...]) -> list[sqlite3.Row]:
        rows = list(self.db.execute(sql, parameters))
        self._query_probes += 1
        self._link_payload_bytes += sum(
            len(json.dumps(dict(row), sort_keys=True, default=str).encode()) for row in rows
        )
        return rows

    def _dynamic_relations(self, frame: QueryFrame) -> tuple[str, ...]:
        if frame.requested_relation_families:
            return frame.requested_relation_families
        folded = frame.normalized_query.casefold()
        if "refer to" in folded or "refers to" in folded:
            return ("definition",)
        entity_terms = {
            token
            for mention in frame.entity_mentions
            for token in TOKEN_RE.findall(mention.surface.casefold())
        }
        candidates = [
            token
            for token in TOKEN_RE.findall(frame.normalized_query.casefold())
            if token not in DYNAMIC_STOP and token not in entity_terms and len(token) > 3
        ]
        return tuple(dict.fromkeys(candidates))[:2]

    def _target_document(self, title: str) -> sqlite3.Row | None:
        rows = self._rows(
            """SELECT document_id,title,normalized_title,source_url
                 FROM documents WHERE normalized_title=?
                 ORDER BY document_id LIMIT 2""",
            (_normalize(title),),
        )
        return rows[0] if len(rows) == 1 else None

    def _candidate_rows(self, surface: str) -> list[tuple[sqlite3.Row, ResolutionMethod, float]]:
        key = _normalize(surface)
        exact = self._rows(
            """SELECT d.document_id,d.title,d.normalized_title,d.redirect_target,
                      d.source_url,a.kind
                 FROM aliases AS a JOIN documents AS d USING(document_id)
                WHERE a.alias=? ORDER BY a.kind,d.document_id LIMIT 16""",
            (key,),
        )
        candidates: dict[str, tuple[sqlite3.Row, ResolutionMethod, float]] = {}
        for row in exact:
            redirect_target = row["redirect_target"]
            if redirect_target:
                target = self._target_document(str(redirect_target))
                if target is not None:
                    candidates[str(target["document_id"])] = (
                        target,
                        ResolutionMethod.REDIRECT,
                        0.99,
                    )
                continue
            method = (
                ResolutionMethod.EXACT_TITLE
                if str(row["kind"]) == "title"
                else ResolutionMethod.ALIAS
            )
            score = 1.0 if method is ResolutionMethod.EXACT_TITLE else 0.97
            candidates.setdefault(str(row["document_id"]), (row, method, score))
        if not candidates:
            anchor_rows = self._rows(
                """SELECT DISTINCT d.document_id,d.title,d.normalized_title,
                          d.source_url,NULL AS redirect_target,'anchor' AS kind
                     FROM anchors AS a JOIN documents AS d
                       ON d.normalized_title=lower(a.target_title)
                    WHERE a.anchor_text=? ORDER BY d.document_id LIMIT 16""",
                (key,),
            )
            for row in anchor_rows:
                candidates[str(row["document_id"])] = (
                    row,
                    ResolutionMethod.ANCHOR,
                    0.93,
                )
        if not candidates and len(key) >= 4:
            first = key[0]
            lower_length = max(1, len(key) - 3)
            upper_length = len(key) + 3
            fuzzy = self._rows(
                """SELECT document_id,title,normalized_title,source_url,
                          redirect_target,'fuzzy' AS kind
                     FROM documents
                    WHERE substr(normalized_title,1,1)=?
                      AND length(normalized_title) BETWEEN ? AND ?
                    ORDER BY abs(length(normalized_title)-?),normalized_title LIMIT 128""",
                (first, lower_length, upper_length, len(key)),
            )
            for row in fuzzy:
                similarity = _edit_similarity(key, str(row["normalized_title"]))
                if similarity >= 0.78 and not row["redirect_target"]:
                    candidates[str(row["document_id"])] = (
                        row,
                        ResolutionMethod.FUZZY,
                        0.88 * similarity,
                    )
        return sorted(
            candidates.values(), key=lambda item: (-item[2], str(item[0]["document_id"]))
        )[:8]

    def _relation_compatibility(
        self,
        document_id: str,
        relations: tuple[str, ...],
    ) -> float:
        terms = tuple(
            dict.fromkeys(
                term.casefold()
                for relation in relations
                for term in RELATION_TERMS.get(relation, (relation,))
                if len(term.strip()) > 2
            )
        )[:6]
        if not terms:
            return 1.0
        escaped = [term.replace('"', '""').strip() for term in terms]
        fts = " OR ".join(f'"{term}"' for term in escaped)
        rows = self._rows(
            """SELECT 1 FROM chunks_fts AS f JOIN chunks AS c ON c.chunk_id=f.chunk_id
                WHERE chunks_fts MATCH ? AND c.document_id=? LIMIT 1""",
            (fts, document_id),
        )
        return float(bool(rows))

    def _resolve(self, mention: EntityMention, frame: QueryFrame) -> EntityMention:
        query_terms = set(TOKEN_RE.findall(frame.normalized_query.casefold()))
        ranked: list[EntityCandidate] = []
        lookup_surface = re.sub(
            r"^(?:compare|name|list)\s+", "", mention.surface, flags=re.IGNORECASE
        )
        for row, method, name_score in self._candidate_rows(lookup_surface):
            title_terms = set(TOKEN_RE.findall(str(row["title"]).casefold()))
            context_score = len(query_terms & title_terms) / max(1, len(title_terms))
            relation_score = self._relation_compatibility(
                str(row["document_id"]), frame.requested_relation_families
            )
            confidence = min(
                1.0,
                0.62 * name_score + 0.12 + 0.16 * relation_score + 0.10 * context_score,
            )
            entity_id = _entity_id(str(row["title"]))
            self._entity_documents[entity_id] = str(row["document_id"])
            ranked.append(
                EntityCandidate(
                    entity_id=entity_id,
                    title=str(row["title"]),
                    method=method,
                    name_score=name_score,
                    type_score=1.0,
                    relation_score=relation_score,
                    context_score=context_score,
                    confidence=confidence,
                )
            )
        ranked.sort(key=lambda item: (-item.confidence, item.entity_id))
        ranked = ranked[:8]
        if not ranked:
            return mention
        top = ranked[0]
        runner_up = ranked[1].confidence if len(ranked) > 1 else 0.0
        if top.confidence < 0.82 or top.confidence - runner_up < 0.08:
            return mention.model_copy(
                update={
                    "candidates": tuple(ranked),
                    "selected_confidence": top.confidence,
                    "resolution_method": top.method,
                    "copy_status": "ambiguous",
                }
            )
        return mention.model_copy(
            update={
                "candidates": tuple(ranked),
                "selected_entity_id": top.entity_id,
                "selected_confidence": top.confidence,
                "resolution_method": top.method,
                "copy_status": "linked",
            }
        )

    def _implicit_exact_mentions(self, frame: QueryFrame) -> tuple[EntityMention, ...]:
        """Find longest non-overlapping aliases/titles/anchors with two index probes."""

        tokens = list(TOKEN_RE.finditer(frame.normalized_query))
        candidates: list[tuple[int, int, str, str]] = []
        for width in range(min(5, len(tokens)), 0, -1):
            for index in range(0, len(tokens) - width + 1):
                window = tokens[index : index + width]
                folded_tokens = {item.group(0).casefold() for item in window}
                if folded_tokens <= DYNAMIC_STOP:
                    continue
                start = window[0].start()
                end = window[-1].end()
                surface = frame.normalized_query[start:end]
                normalized = _normalize(surface)
                candidates.append((start, end, surface, normalized))
                if len(candidates) >= 32:
                    break
            if len(candidates) >= 32:
                break
        if not candidates:
            return ()
        keys = tuple(dict.fromkeys(item[3] for item in candidates))
        marks = ",".join("?" for _ in keys)
        alias_rows = self._rows(
            f"SELECT DISTINCT alias AS surface FROM aliases WHERE alias IN ({marks})",
            keys,
        )
        anchor_rows = self._rows(
            f"SELECT DISTINCT anchor_text AS surface FROM anchors WHERE anchor_text IN ({marks})",
            keys,
        )
        matches = {str(row["surface"]) for row in (*alias_rows, *anchor_rows)}
        attribution = {_normalize(item) for item in frame.attribution_constraints}
        eligible = [
            item for item in candidates if item[3] in matches and item[3] not in attribution
        ]
        if not eligible:
            return ()
        eligible.sort(key=lambda item: (-len(item[3].split()), -len(item[3]), item[0]))
        chosen_mentions: list[EntityMention] = []
        for start, end, surface, _normalized in eligible:
            if any(
                not (end <= mention.char_start or start >= mention.char_end)
                for mention in chosen_mentions
            ):
                continue
            chosen_mentions.append(EntityMention(surface=surface, char_start=start, char_end=end))
            if len(chosen_mentions) >= 4:
                break
        return tuple(sorted(chosen_mentions, key=lambda item: (item.char_start, item.char_end)))

    def link_frame(self, frame: QueryFrame) -> QueryFrame:
        self._start_query()
        relations = self._dynamic_relations(frame)
        inferred_shape = frame.answer_shape
        if inferred_shape is AnswerShape.UNKNOWN and relations == ("definition",):
            inferred_shape = AnswerShape.DEFINITION
        provisional = frame.model_copy(
            update={
                "requested_relation_families": relations,
                "answer_shape": inferred_shape,
                "required_facets": facets_for_shape(inferred_shape),
            }
        )
        attribution_keys = {_normalize(value) for value in frame.attribution_constraints}
        resolvable_mentions = tuple(
            mention
            for mention in frame.entity_mentions
            if not any(
                _normalize(mention.surface) in attribution for attribution in attribution_keys
            )
        )
        mentions = tuple(self._resolve(mention, provisional) for mention in resolvable_mentions)
        if (
            not any(mention.copy_status == "linked" for mention in mentions)
            or frame.answer_shape is AnswerShape.COMPARISON
        ):
            for implicit in self._implicit_exact_mentions(provisional):
                if any(
                    not (
                        implicit.char_end <= existing.char_start
                        or implicit.char_start >= existing.char_end
                    )
                    and existing.copy_status == "linked"
                    for existing in mentions
                ):
                    continue
                linked_implicit = self._resolve(implicit, provisional)
                if linked_implicit.copy_status == "linked":
                    mentions = (
                        *(
                            mention
                            for mention in mentions
                            if mention.char_end <= implicit.char_start
                            or mention.char_start >= implicit.char_end
                        ),
                        linked_implicit,
                    )
                    mentions = tuple(
                        sorted(mentions, key=lambda item: (item.char_start, item.char_end))
                    )
        selected = tuple(
            dict.fromkeys(
                (
                    *frame.candidate_entity_ids,
                    *(item.selected_entity_id for item in mentions if item.selected_entity_id),
                )
            )
        )
        ambiguous = any(item.copy_status == "ambiguous" for item in mentions)
        uncertainty = max(
            frame.uncertainty,
            max((1.0 - item.selected_confidence for item in mentions), default=0.0),
        )
        return provisional.model_copy(
            update={
                "entity_mentions": mentions,
                "candidate_entity_ids": selected,
                "uncertainty": uncertainty,
                "clarification_need": frame.clarification_need or ambiguous,
            }
        )

    def corpus_coverage(self, frame: QueryFrame) -> bool:
        return not any(
            mention.copy_status == "unknown_but_copyable" for mention in frame.entity_mentions
        )

    def _fts_rows(self, query: str, limit: int) -> list[sqlite3.Row]:
        terms = sorted(
            set(TOKEN_RE.findall(query.casefold())), key=lambda value: (-len(value), value)
        )
        escaped = [term.replace('"', '""') for term in terms if len(term) > 2][:7]
        if not escaped:
            return []
        fts = " OR ".join(f'"{term}"' for term in escaped)
        return self._rows(
            """SELECT c.chunk_id,c.document_id,c.section_path,c.raw_start,c.raw_end,
                      c.raw_text,c.normalized_text,c.source_span_sha256,d.title,
                      d.revision_id,d.source_url,d.source_text_sha256,
                      bm25(chunks_fts,1.8,1.2,1.0) AS lexical_rank
                 FROM chunks_fts AS f JOIN chunks AS c ON c.chunk_id=f.chunk_id
                 JOIN documents AS d USING(document_id)
                WHERE chunks_fts MATCH ? ORDER BY lexical_rank,c.chunk_id LIMIT ?""",
            (fts, limit),
        )

    def _entity_rows(self, entity_ids: tuple[str, ...], limit: int) -> list[sqlite3.Row]:
        documents = [
            self._entity_documents[value] for value in entity_ids if value in self._entity_documents
        ]
        documents = list(dict.fromkeys(documents))[:8]
        if not documents:
            return []
        marks = ",".join("?" for _ in documents)
        return self._rows(
            f"""SELECT c.chunk_id,c.document_id,c.section_path,c.raw_start,c.raw_end,
                       c.raw_text,c.normalized_text,c.source_span_sha256,d.title,
                       d.revision_id,d.source_url,d.source_text_sha256,0.0 AS lexical_rank
                  FROM chunks AS c JOIN documents AS d USING(document_id)
                 WHERE c.document_id IN ({marks})
                 ORDER BY c.document_id,c.raw_start LIMIT ?""",
            (*documents, limit),
        )

    @staticmethod
    def _region_score(frame: QueryFrame, text: str) -> int:
        folded = text.casefold()
        query_terms = set(TOKEN_RE.findall(frame.normalized_query.casefold()))
        score = len(query_terms & set(TOKEN_RE.findall(folded)))
        for relation in frame.requested_relation_families:
            score += 4 * sum(
                term.casefold() in folded for term in RELATION_TERMS.get(relation, (relation,))
            )
        return score

    def _regions(self, frame: QueryFrame, raw: str) -> list[tuple[int, int, str]]:
        regions = [
            (match.start(), match.end(), match.group(0)) for match in SENTENCE_RE.finditer(raw)
        ]
        regions.sort(key=lambda item: (-self._region_score(frame, item[2]), item[0]))
        return regions[:8]

    @staticmethod
    def _shape_facets(
        frame: QueryFrame,
        shape: AnswerShape,
        subject_entity_id: str,
    ) -> tuple[RequiredFacet, ...]:
        facets = {
            RequiredFacet.SUBJECT,
            RequiredFacet.RELATION,
            RequiredFacet.OBJECT,
            RequiredFacet.SOURCE,
        }
        if shape is AnswerShape.DATE:
            facets.add(RequiredFacet.TIME)
        if shape is AnswerShape.QUANTITY:
            facets.add(RequiredFacet.QUANTITY)
        if shape is AnswerShape.QUOTATION:
            facets.update({RequiredFacet.SPEAKER, RequiredFacet.QUOTATION})
        if "quotation" in frame.requested_relation_families:
            facets.update({RequiredFacet.SPEAKER, RequiredFacet.QUOTATION})
        if shape is AnswerShape.EXPLANATION:
            facets.add(RequiredFacet.REASON)
        if frame.answer_shape is AnswerShape.COMPARISON:
            if frame.candidate_entity_ids and subject_entity_id == frame.candidate_entity_ids[0]:
                facets.add(RequiredFacet.COMPARISON_A)
            if (
                len(frame.candidate_entity_ids) > 1
                and subject_entity_id == frame.candidate_entity_ids[1]
            ):
                facets.add(RequiredFacet.COMPARISON_B)
        return tuple(sorted(facets, key=lambda item: item.value))

    def _extractions(
        self,
        frame: QueryFrame,
        raw: str,
    ) -> list[tuple[int, int, str, AnswerShape, str | None, str | None]]:
        """Return local offsets, exact value, shape, speaker and unit."""

        results: list[tuple[int, int, str, AnswerShape, str | None, str | None]] = []
        regions = self._regions(frame, raw)
        relation = (
            frame.requested_relation_families[0] if frame.requested_relation_families else "unknown"
        )
        relation_terms = RELATION_TERMS.get(relation, (relation,))

        # Query-relevant infobox values are high precision and remain exact raw copies.
        for match in INFOBOX_RE.finditer(raw):
            field = _normalize(match.group(1))
            if not any(_normalize(term).strip() in field for term in relation_terms):
                continue
            value = match.group(2).strip()
            local = match.start(2) + (len(match.group(2)) - len(match.group(2).lstrip()))
            if frame.answer_shape is AnswerShape.DATE:
                for value_match in DATE_RE.finditer(value):
                    results.append(
                        (
                            local + value_match.start(),
                            local + value_match.end(),
                            value_match.group(0),
                            AnswerShape.DATE,
                            None,
                            None,
                        )
                    )
            elif frame.answer_shape in {AnswerShape.QUANTITY, AnswerShape.COMPARISON}:
                for value_match in QUANTITY_RE.finditer(value):
                    unit_match = re.search(r"[A-Za-z%]+", value_match.group(0))
                    results.append(
                        (
                            local + value_match.start(),
                            local + value_match.end(),
                            value_match.group(0),
                            AnswerShape.QUANTITY,
                            None,
                            unit_match.group(0) if unit_match else None,
                        )
                    )

        if frame.answer_shape is AnswerShape.DATE:
            for region_start, _region_end, region in regions:
                for match in DATE_RE.finditer(region):
                    results.append(
                        (
                            region_start + match.start(),
                            region_start + match.end(),
                            match.group(0),
                            AnswerShape.DATE,
                            None,
                            None,
                        )
                    )
        elif frame.answer_shape in {AnswerShape.QUANTITY, AnswerShape.COMPARISON}:
            for region_start, _region_end, region in regions:
                for match in QUANTITY_RE.finditer(region):
                    unit_match = re.search(r"[A-Za-z%]+", match.group(0))
                    results.append(
                        (
                            region_start + match.start(),
                            region_start + match.end(),
                            match.group(0),
                            AnswerShape.QUANTITY,
                            None,
                            unit_match.group(0) if unit_match else None,
                        )
                    )
        elif frame.answer_shape is AnswerShape.QUOTATION:
            for region_start, _region_end, region in regions:
                for match in QUOTE_RE.finditer(region):
                    results.append(
                        (
                            region_start + match.start(1),
                            region_start + match.end(1),
                            match.group(1),
                            AnswerShape.QUOTATION,
                            None,
                            None,
                        )
                    )
        elif frame.answer_shape is AnswerShape.ENTITY:
            for region_start, _region_end, region in regions:
                quote_positions = [
                    position for marker in ('"', "“") if (position := region.find(marker)) >= 0
                ]
                prefix = region[: min(quote_positions)] if quote_positions else region
                attribution = ATTRIBUTION_RE.search(prefix)
                if attribution:
                    results.append(
                        (
                            region_start + attribution.start(1),
                            region_start + attribution.end(1),
                            attribution.group(1),
                            AnswerShape.ENTITY,
                            attribution.group(1),
                            None,
                        )
                    )
                    continue
                links = list(LINK_RE.finditer(region))
                if links and any(term.casefold() in region.casefold() for term in relation_terms):
                    link = links[-1]
                    value = link.group(2) or link.group(1)
                    value_start = link.start(2) if link.group(2) is not None else link.start(1)
                    results.append(
                        (
                            region_start + value_start,
                            region_start + value_start + len(value),
                            value,
                            AnswerShape.ENTITY,
                            None,
                            None,
                        )
                    )
        elif frame.answer_shape in {AnswerShape.DEFINITION, AnswerShape.LIST}:
            for region_start, _region_end, region in regions:
                definition_match = DEFINITION_RE.search(region)
                if definition_match:
                    value = definition_match.group(1).strip()
                    local = (
                        region_start
                        + definition_match.start(1)
                        + (len(definition_match.group(1)) - len(definition_match.group(1).lstrip()))
                    )
                    results.append(
                        (local, local + len(value), value, AnswerShape.DEFINITION, None, None)
                    )
            if frame.answer_shape is AnswerShape.LIST and not results and regions:
                start, _end, region = regions[0]
                value = region.strip()
                local = start + (len(region) - len(region.lstrip()))
                results.append((local, local + len(value), value, AnswerShape.LIST, None, None))
        elif regions and frame.answer_shape in {
            AnswerShape.EVENT,
            AnswerShape.PROCESS,
            AnswerShape.EXPLANATION,
            AnswerShape.VERIFICATION,
        }:
            start, _end, region = regions[0]
            value = region.strip()
            local = start + (len(region) - len(region.lstrip()))
            results.append((local, local + len(value), value, frame.answer_shape, None, None))
        # Stable exact-deduplication; a chunk may contribute at most four values.
        unique: dict[
            tuple[int, int, str], tuple[int, int, str, AnswerShape, str | None, str | None]
        ] = {}
        for item in results:
            unique.setdefault((item[0], item[1], item[2]), item)
        return list(unique.values())[:4]

    def _records_for_row(self, frame: QueryFrame, row: sqlite3.Row) -> list[EvidenceRecord]:
        raw = str(row["raw_text"])
        subject = _entity_id(str(row["title"]))
        self._entity_documents[subject] = str(row["document_id"])
        relation = (
            frame.requested_relation_families[0] if frame.requested_relation_families else "unknown"
        )
        records: list[EvidenceRecord] = []
        for local_start, local_end, value, shape, speaker, unit in self._extractions(frame, raw):
            if raw[local_start:local_end] != value:
                continue
            char_start = int(row["raw_start"]) + local_start
            char_end = int(row["raw_start"]) + local_end
            bound = self._rows(
                """SELECT substr(raw_wikitext,?,?) AS exact_surface
                     FROM documents WHERE document_id=?""",
                (char_start + 1, char_end - char_start, str(row["document_id"])),
            )
            if len(bound) != 1 or str(bound[0]["exact_surface"]) != value:
                continue
            span_digest = hashlib.sha256(value.encode()).hexdigest()
            span_id = f"span:{row['chunk_id']}:{char_start}:{char_end}"
            span = ExactSourceSpan(
                span_id=span_id,
                document_id=str(row["document_id"]),
                source_title=str(row["title"]),
                source_revision=str(row["revision_id"]),
                source_url=str(row["source_url"]),
                source_family=str(row["source_url"]),
                char_start=char_start,
                char_end=char_end,
                text=value,
                text_hash=f"sha256:{span_digest}",
            )
            claim_digest = hashlib.sha256(
                f"{subject}:{relation}:{value}:{span_id}".encode()
            ).hexdigest()[:24]
            relation_terms = RELATION_TERMS.get(relation, (relation,))
            folded_raw = raw.casefold()
            distances: list[int] = []
            for term in relation_terms:
                normalized_term = term.casefold().strip()
                if not normalized_term:
                    continue
                term_pattern = rf"(?<![A-Za-z0-9]){re.escape(normalized_term)}(?![A-Za-z0-9])"
                for term_match in re.finditer(term_pattern, folded_raw):
                    if term_match.end() < local_start:
                        distance = local_start - term_match.end()
                    elif term_match.start() > local_end:
                        distance = term_match.start() - local_end
                    else:
                        distance = 0
                    distances.append(distance)
            nearest_relation = min(distances, default=10_000)
            relation_fit = max(0.0, 1.0 - nearest_relation / 160.0)
            competing_distances: list[int] = []
            for term in COMPETING_RELATION_TERMS.get(relation, ()):
                for term_match in re.finditer(
                    rf"(?<![A-Za-z0-9]){re.escape(term.casefold())}(?![A-Za-z0-9])",
                    folded_raw,
                ):
                    if term_match.end() < local_start:
                        distance = local_start - term_match.end()
                    elif term_match.start() > local_end:
                        distance = term_match.start() - local_end
                    else:
                        distance = 0
                    competing_distances.append(distance)
            if min(competing_distances, default=10_000) < nearest_relation:
                relation_fit *= 0.1
            line_start = raw.rfind("\n", 0, local_start) + 1
            field_prefix = raw[line_start:local_start]
            field_name = (
                _normalize(field_prefix.split("=", 1)[0].lstrip().lstrip("|"))
                if "=" in field_prefix
                else ""
            )
            exact_infobox_field = field_prefix.lstrip().startswith("|") and any(
                _normalize(term).strip() in field_name for term in relation_terms
            )
            preceding_surface = folded_raw[max(0, local_start - 128) : local_start]
            title_pattern = re.escape(str(row["title"]).casefold())
            subject_near_definition = float(
                re.search(
                    rf"{title_pattern}(?:'{{2,3}}|\]\])?\s+(?:is|are|was|were)\s+$",
                    preceding_surface,
                )
                is not None
            )
            if shape is AnswerShape.DATE:
                date_specificity = float(re.fullmatch(r"\d{4}", value) is None)
                claim_confidence = min(
                    1.0, 0.70 + 0.22 * relation_fit + 0.08 * date_specificity
                )
            elif shape is AnswerShape.DEFINITION:
                source_position = int(row["raw_start"]) + local_start
                lead_bias = max(0.0, 1.0 - source_position / 12_000.0)
                claim_confidence = (
                    0.70
                    + 0.15 * relation_fit
                    + 0.10 * subject_near_definition
                    + 0.05 * lead_bias
                )
            else:
                claim_confidence = 0.75 + 0.25 * relation_fit
            if exact_infobox_field:
                claim_confidence = 1.0
            polarity: Literal["positive", "negative"] = (
                "negative"
                if re.search(r"\b(?:not|no|never|false)\b", value, re.IGNORECASE)
                else "positive"
            )
            claim = StructuredClaim(
                claim_id=f"claim:{claim_digest}",
                subject_entity_id=subject,
                relation_family=relation,
                object_value=value,
                answer_shape=shape,
                source_span_ids=(span_id,),
                polarity=polarity,
                occurred_at=value if shape is AnswerShape.DATE else None,
                speaker_entity_id=_entity_id(speaker) if speaker else None,
                quotation=value if shape is AnswerShape.QUOTATION else None,
                quantity_value=value if shape is AnswerShape.QUANTITY else None,
                quantity_unit=unit,
                confidence=claim_confidence,
            )
            entity_fit = (
                1.0
                if not frame.candidate_entity_ids or subject in frame.candidate_entity_ids
                else 0.0
            )
            temporal_fit = (
                1.0
                if not frame.temporal_constraints
                else float(any(item in raw for item in frame.temporal_constraints))
            )
            records.append(
                EvidenceRecord(
                    claim=claim,
                    source_spans=(span,),
                    entity_fit=entity_fit,
                    relation_fit=relation_fit,
                    answerability=1.0,
                    answer_shape_fit=float(
                        shape is frame.answer_shape
                        or (
                            frame.answer_shape is AnswerShape.COMPARISON
                            and shape is AnswerShape.QUANTITY
                        )
                    ),
                    temporal_fit=temporal_fit,
                    attribution_fit=float(
                        not frame.attribution_constraints
                        or bool(speaker or shape is AnswerShape.QUOTATION)
                    ),
                    source_quality=1.0,
                    facet_coverage=self._shape_facets(frame, shape, subject),
                )
            )
        return records

    def _retrieve(
        self,
        frame: QueryFrame,
        *,
        limit: int,
        strategy: Literal["lexical", "fusion"],
    ) -> tuple[EvidenceRecord, ...]:
        if limit < 1 or limit > self.maximum_limit:
            raise ValueError(f"limit must be in [1,{self.maximum_limit}]")
        if not self._query_started:
            self._start_query()
        if not self.corpus_coverage(frame):
            payload_bytes = self._link_payload_bytes
            elapsed_ms = (time.perf_counter_ns() - self._query_started) / 1_000_000
            self.last_workload = ProviderWorkload(
                operation="controller_retrieve",
                strategy=strategy,
                requested_limit=limit,
                candidate_rows=0,
                evidence_records=0,
                index_probes=self._query_probes,
                payload_bytes=payload_bytes,
                estimated_sqlite_blocks=math.ceil(payload_bytes / self.page_bytes),
                sqlite_page_bytes=self.page_bytes,
                latency_ms=elapsed_ms,
                source_document_hashes=(),
            )
            self._query_started = 0
            return ()
        pool_limit = min(self.maximum_limit, max(limit, 32))
        rows = self._fts_rows(frame.normalized_query, pool_limit)
        if strategy == "fusion" and frame.candidate_entity_ids:
            entity_rows = self._entity_rows(frame.candidate_entity_ids, pool_limit)
            by_chunk = {str(row["chunk_id"]): row for row in entity_rows}
            by_chunk.update({str(row["chunk_id"]): row for row in rows})
            rows = list(by_chunk.values())[:pool_limit]
        records: list[EvidenceRecord] = []
        for row in rows:
            records.extend(self._records_for_row(frame, row))
        records.sort(
            key=lambda item: (
                -item.entity_fit,
                -item.relation_fit,
                -item.answer_shape_fit,
                -item.temporal_fit,
                -item.claim.confidence,
                item.claim.claim_id,
            )
        )
        records = records[:limit]
        payload_bytes = self._link_payload_bytes + sum(
            len(span.text.encode()) for record in records for span in record.source_spans
        )
        hashes = tuple(
            sorted({str(row["source_text_sha256"]) for row in rows if row["source_text_sha256"]})
        )
        elapsed_ms = (time.perf_counter_ns() - self._query_started) / 1_000_000
        self.last_workload = ProviderWorkload(
            operation="controller_retrieve",
            strategy=strategy,
            requested_limit=limit,
            candidate_rows=len(rows),
            evidence_records=len(records),
            index_probes=self._query_probes,
            payload_bytes=payload_bytes,
            estimated_sqlite_blocks=math.ceil(payload_bytes / self.page_bytes),
            sqlite_page_bytes=self.page_bytes,
            latency_ms=elapsed_ms,
            source_document_hashes=hashes,
        )
        self._query_started = 0
        return tuple(records)

    def retrieve(self, frame: QueryFrame, *, limit: int) -> tuple[EvidenceRecord, ...]:
        return self._retrieve(frame, limit=limit, strategy="fusion")

    def retrieve_lexical(self, frame: QueryFrame, *, limit: int) -> tuple[EvidenceRecord, ...]:
        return self._retrieve(frame, limit=limit, strategy="lexical")
