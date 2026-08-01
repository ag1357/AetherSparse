"""Author one isolated natural-query shard from immutable source material.

This process does not import or call the AetherSparse runtime. It emits questions,
candidate source coordinates, and adjudication intents, but never accepted answers
or grades.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from itertools import pairwise
from pathlib import Path
from typing import Any

from common import (
    AUTHOR_IDENTITIES,
    DATE_RE,
    NATURAL_SURFACE_RE,
    QUANTITY_RE,
    QUOTATION_RE,
    canonical_entity_id,
    connect_read_only,
    corpus_identity,
    iter_definition_candidates,
    normalize_surface,
    partition_for_document,
    row_to_candidate,
    stable_id,
    write_json,
)


def _draft(
    author: str,
    category: str,
    question: str,
    *,
    sources: list[dict[str, Any]] | None = None,
    intent: str,
    required_answer_shape: str,
    required_facets: list[str],
    required_entity_ids: list[str] | None = None,
    prior_draft_ids: list[str] | None = None,
    attributes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_items = sources or []
    source_ids = [str(item["document_id"]) for item in source_items]
    draft_id = stable_id("v050r1-draft", author, category, question, *source_ids)
    return {
        "draft_id": draft_id,
        "category": category,
        "question": question,
        "author_identity": AUTHOR_IDENTITIES[author][0],
        "author_process_identity": AUTHOR_IDENTITIES[author][1],
        "runtime_access": False,
        "intent": intent,
        "required_answer_shape": required_answer_shape,
        "required_facets": required_facets,
        "required_entity_ids": required_entity_ids or [],
        "source_candidates": source_items,
        "prior_draft_ids": prior_draft_ids or [],
        "attributes": attributes or {},
    }


def _misspell(title: str) -> str:
    words = title.split()
    for index, word in enumerate(words):
        letters = [position for position, value in enumerate(word) if value.isalpha()]
        if len(letters) < 5:
            continue
        for left, right in pairwise(letters):
            if word[left].casefold() == word[right].casefold():
                continue
            chars = list(word)
            chars[left], chars[right] = chars[right], chars[left]
            words[index] = "".join(chars)
            return " ".join(words)
    return title + "x"


def _author_alpha(connection: Any) -> list[dict[str, Any]]:
    definitions = list(iter_definition_candidates(connection))
    if len(definitions) < 360:
        raise ValueError("corpus lacks 360 clean definition candidates")
    drafts: list[dict[str, Any]] = []
    direct_by_document: dict[str, str] = {}
    for candidate in definitions[:360]:
        title = str(candidate["title"])
        item = _draft(
            "alpha",
            "direct_fact",
            f"What is {title}?",
            sources=[candidate],
            intent="extract_definition",
            required_answer_shape="definition",
            required_facets=["subject", "relation", "object", "source"],
            required_entity_ids=[canonical_entity_id(title)],
        )
        drafts.append(item)
        direct_by_document[str(candidate["document_id"])] = str(item["draft_id"])

    definitions_by_title = {
        str(item["normalized_title"]): item for item in definitions
    }
    aliases: list[tuple[str, dict[str, Any]]] = []
    seen_aliases: set[str] = set()
    rows = connection.execute(
        "SELECT anchor_text,target_title FROM anchors ORDER BY anchor_text,target_title"
    )
    for row in rows:
        surface = " ".join(str(row["anchor_text"]).split())
        target = normalize_surface(str(row["target_title"]))
        alias_candidate = definitions_by_title.get(target)
        key = normalize_surface(surface)
        if (
            alias_candidate is None
            or key in seen_aliases
            or key == target
            or NATURAL_SURFACE_RE.fullmatch(surface) is None
        ):
            continue
        seen_aliases.add(key)
        aliases.append((surface, alias_candidate))
    if len(aliases) < 110:
        raise ValueError("corpus lacks 110 exact anchor aliases with answerable targets")
    for surface, candidate in aliases[:110]:
        title = str(candidate["title"])
        drafts.append(
            _draft(
                "alpha",
                "alias",
                f"What does {surface} refer to?",
                sources=[candidate],
                intent="extract_definition",
                required_answer_shape="definition",
                required_facets=["subject", "relation", "object", "source"],
                required_entity_ids=[canonical_entity_id(title)],
                attributes={"mention_surface": surface, "canonical_title": title},
            )
        )

    redirects: list[tuple[str, dict[str, Any]]] = []
    seen_redirects: set[str] = set()
    rows = connection.execute(
        """SELECT source.title AS redirect_title,r.target_title
             FROM redirects r JOIN documents source
               ON source.document_id=r.source_document_id
            ORDER BY source.normalized_title"""
    )
    for row in rows:
        target = normalize_surface(str(row["target_title"]))
        redirect_candidate = definitions_by_title.get(target)
        redirect_title = str(row["redirect_title"])
        if (
            redirect_candidate is None
            or normalize_surface(redirect_title) in seen_redirects
        ):
            continue
        seen_redirects.add(normalize_surface(redirect_title))
        redirects.append((redirect_title, redirect_candidate))
    if len(redirects) < 110:
        raise ValueError("corpus lacks 110 answerable redirects")
    for redirect_title, candidate in redirects[:110]:
        title = str(candidate["title"])
        drafts.append(
            _draft(
                "alpha",
                "redirect",
                f"Which topic does {redirect_title} redirect to?",
                sources=[candidate],
                intent="extract_definition",
                required_answer_shape="definition",
                required_facets=["subject", "relation", "object", "source"],
                required_entity_ids=[canonical_entity_id(title)],
                attributes={
                    "redirect_surface": redirect_title,
                    "canonical_title": title,
                },
            )
        )

    for candidate in definitions[110:220]:
        title = str(candidate["title"])
        misspelled = _misspell(title)
        drafts.append(
            _draft(
                "alpha",
                "misspelling",
                f"What is {misspelled}?",
                sources=[candidate],
                intent="extract_definition",
                required_answer_shape="definition",
                required_facets=["subject", "relation", "object", "source"],
                required_entity_ids=[canonical_entity_id(title)],
                attributes={"mention_surface": misspelled, "canonical_title": title},
            )
        )

    direct_sources = definitions[:110]
    for index, candidate in enumerate(direct_sources, start=1):
        title = str(candidate["title"])
        prior = direct_by_document[str(candidate["document_id"])]
        drafts.append(
            _draft(
                "alpha",
                "pronoun",
                f"In the article we just discussed about {title}, what is it?",
                sources=[candidate],
                intent="extract_definition",
                required_answer_shape="definition",
                required_facets=["subject", "relation", "object", "source"],
                required_entity_ids=[canonical_entity_id(title)],
                prior_draft_ids=[prior],
                attributes={"discourse_surface": "it", "sequence": index},
            )
        )
        drafts.append(
            _draft(
                "alpha",
                "follow_up",
                f"And how does that source define {title}?",
                sources=[candidate],
                intent="extract_definition",
                required_answer_shape="definition",
                required_facets=["subject", "relation", "object", "source"],
                required_entity_ids=[canonical_entity_id(title)],
                prior_draft_ids=[prior],
                attributes={"sequence": index},
            )
        )
    return drafts


def _typed_candidates(connection: Any, kind: str, limit: int) -> list[dict[str, Any]]:
    pattern = {"date": DATE_RE, "quantity": QUANTITY_RE, "quotation": QUOTATION_RE}[kind]
    results: list[dict[str, Any]] = []
    seen_documents: set[str] = set()
    rows = connection.execute(
        """SELECT d.document_id,d.wiki_page_id,d.revision_id,d.title,
                  d.normalized_title,d.source_url,d.source_text_sha256,d.raw_wikitext,
                  c.chunk_id,c.raw_start,c.raw_end,c.raw_text,c.source_span_sha256
             FROM chunks c JOIN documents d USING(document_id)
            WHERE d.redirect_target IS NULL
            ORDER BY c.chunk_id"""
    )
    for row in rows:
        document_id = str(row["document_id"])
        if document_id in seen_documents:
            continue
        match = pattern.search(str(row["raw_text"]))
        if match is None:
            continue
        group = 1 if kind == "quotation" else 0
        surface = match.group(group)
        if kind == "quotation" and (
            "=" in surface
            or "|" in surface
            or "http" in surface.casefold()
            or len(surface.split()) < 3
        ):
            continue
        seen_documents.add(document_id)
        results.append(row_to_candidate(row, kind, match.start(group), match.end(group)))
        if len(results) == limit:
            break
    if len(results) < limit:
        raise ValueError(f"corpus lacks {limit} acceptable {kind} candidates")
    return results


def _ambiguous_surfaces(connection: Any) -> list[tuple[str, tuple[str, ...]]]:
    targets_by_surface: dict[str, set[str]] = defaultdict(set)
    display_by_surface: dict[str, str] = {}
    for row in connection.execute(
        "SELECT anchor_text,target_title FROM anchors ORDER BY anchor_text,target_title"
    ):
        surface = " ".join(str(row["anchor_text"]).split())
        normalized = normalize_surface(surface)
        if NATURAL_SURFACE_RE.fullmatch(surface) is None:
            continue
        targets_by_surface[normalized].add(str(row["target_title"]))
        display_by_surface.setdefault(normalized, surface)
    ambiguous = [
        (display_by_surface[surface], tuple(sorted(targets)))
        for surface, targets in targets_by_surface.items()
        if len(targets) >= 2
    ]
    return sorted(ambiguous, key=lambda item: (normalize_surface(item[0]), item[1]))


def _author_beta(connection: Any) -> list[dict[str, Any]]:
    drafts: list[dict[str, Any]] = []
    definitions = list(iter_definition_candidates(connection))
    quotations = _typed_candidates(connection, "quotation", 100)
    dates = _typed_candidates(connection, "date", 110)
    quantities = _typed_candidates(connection, "quantity", 110)
    for candidate in quotations:
        title = str(candidate["title"])
        drafts.append(
            _draft(
                "beta",
                "quotation",
                f"Which exact quoted words appear in the source passage about {title}?",
                sources=[candidate],
                intent="extract_quotation",
                required_answer_shape="quotation",
                required_facets=["subject", "quotation", "source"],
                required_entity_ids=[canonical_entity_id(title)],
            )
        )
    for candidate in dates:
        title = str(candidate["title"])
        drafts.append(
            _draft(
                "beta",
                "date",
                f"Which date is stated in the selected source passage about {title}?",
                sources=[candidate],
                intent="extract_date",
                required_answer_shape="date",
                required_facets=["subject", "time", "source"],
                required_entity_ids=[canonical_entity_id(title)],
            )
        )
    for candidate in quantities:
        title = str(candidate["title"])
        drafts.append(
            _draft(
                "beta",
                "quantity",
                f"Which quantity is stated in the selected source passage about {title}?",
                sources=[candidate],
                intent="extract_quantity",
                required_answer_shape="quantity",
                required_facets=["subject", "quantity", "source"],
                required_entity_ids=[canonical_entity_id(title)],
            )
        )
    for candidate in definitions[:110]:
        title = str(candidate["title"])
        drafts.append(
            _draft(
                "beta",
                "incorrect_premise",
                f"Is {title} accurately described as an ocean on Mars?",
                sources=[candidate],
                intent="reject_incorrect_premise",
                required_answer_shape="verification",
                required_facets=["subject", "relation", "object", "source"],
                required_entity_ids=[canonical_entity_id(title)],
            )
        )

    ambiguous = _ambiguous_surfaces(connection)
    if len(ambiguous) < 220:
        raise ValueError("corpus lacks 220 ambiguous anchor surfaces")
    for surface, targets in ambiguous[:110]:
        drafts.append(
            _draft(
                "beta",
                "ambiguous_entity",
                f"What does {surface} refer to here?",
                intent="request_clarification",
                required_answer_shape="unknown",
                required_facets=["subject"],
                attributes={"candidate_titles": list(targets)},
            )
        )
    for surface, targets in ambiguous[110:220]:
        drafts.append(
            _draft(
                "beta",
                "clarification",
                f"Tell me about {surface}—which one should I mean?",
                intent="request_clarification",
                required_answer_shape="unknown",
                required_facets=["subject"],
                attributes={"candidate_titles": list(targets)},
            )
        )

    known_titles = {normalize_surface(str(item["title"])) for item in definitions}
    for index in range(110):
        unknown = f"Qorvax-{sha256_short('unknown', index)}"
        if normalize_surface(unknown) in known_titles:
            raise AssertionError("synthetic unknown collided with corpus title")
        drafts.append(
            _draft(
                "beta",
                "unknown_entity",
                f"Who is {unknown}?",
                intent="abstain_unknown_entity",
                required_answer_shape="entity",
                required_facets=["subject"],
                attributes={"unknown_surface": unknown},
            )
        )
        remote = f"OffCorpus-{sha256_short('remote', index)}"
        drafts.append(
            _draft(
                "beta",
                "out_of_corpus",
                f"Find the official biography of {remote}, which is not in this corpus.",
                intent="out_of_corpus",
                required_answer_shape="definition",
                required_facets=["subject", "source"],
                attributes={"unknown_surface": remote},
            )
        )
        title = str(definitions[index]["title"])
        drafts.append(
            _draft(
                "beta",
                "incomplete",
                f"{title}—what about it?",
                intent="request_clarification",
                required_answer_shape="unknown",
                required_facets=["subject"],
                attributes={"known_surface": title},
            )
        )
        drafts.append(
            _draft(
                "beta",
                "abstention",
                f"What private password does the article about {title} omit?",
                intent="abstain_missing_evidence",
                required_answer_shape="unknown",
                required_facets=["subject", "source"],
                required_entity_ids=[canonical_entity_id(title)],
            )
        )
    return drafts


def sha256_short(prefix: str, index: int) -> str:
    return stable_id("name", prefix, index).split(":")[-1][:10]


def _quantity_candidates(connection: Any) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    rows = connection.execute(
        """SELECT d.document_id,d.wiki_page_id,d.revision_id,d.title,
                  d.normalized_title,d.source_url,d.source_text_sha256,d.raw_wikitext,
                  c.chunk_id,c.raw_start,c.raw_end,c.raw_text,c.source_span_sha256
             FROM chunks c JOIN documents d USING(document_id)
            WHERE d.redirect_target IS NULL
            ORDER BY c.chunk_id"""
    )
    for row in rows:
        match = QUANTITY_RE.search(str(row["raw_text"]))
        if match is None:
            continue
        key = (str(row["document_id"]), match.group("unit").casefold())
        if key in seen:
            continue
        seen.add(key)
        candidate = row_to_candidate(row, "quantity", match.start(0), match.end(0))
        candidate["quantity_value"] = float(match.group("value").replace(",", ""))
        candidate["quantity_unit"] = match.group("unit").casefold()
        results.append(candidate)
    return results


def _same_partition_groups(
    candidates: list[dict[str, Any]], size: int
) -> list[list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        grouped[partition_for_document(str(candidate["document_id"]))].append(candidate)
    groups: list[list[dict[str, Any]]] = []
    for partition in sorted(grouped):
        values = sorted(grouped[partition], key=lambda item: str(item["document_id"]))
        for start in range(0, len(values) - size + 1, size):
            group = values[start : start + size]
            if len({str(item["document_id"]) for item in group}) == size:
                groups.append(group)
    return sorted(groups, key=lambda group: tuple(str(item["document_id"]) for item in group))


def _author_gamma(connection: Any) -> list[dict[str, Any]]:
    drafts: list[dict[str, Any]] = []
    quantities = _quantity_candidates(connection)
    by_unit_partition: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for candidate in quantities:
        key = (
            str(candidate["quantity_unit"]),
            partition_for_document(str(candidate["document_id"])),
        )
        by_unit_partition[key].append(candidate)
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for key in sorted(by_unit_partition):
        values = sorted(by_unit_partition[key], key=lambda item: str(item["document_id"]))
        for left, right in zip(values[::2], values[1::2], strict=False):
            if left["quantity_value"] == right["quantity_value"]:
                continue
            pairs.append((left, right))
    if len(pairs) < 110:
        raise ValueError("corpus lacks 110 same-unit, same-partition comparison pairs")
    for left, right in pairs[:110]:
        unit = str(left["quantity_unit"])
        titles = [str(left["title"]), str(right["title"])]
        drafts.append(
            _draft(
                "gamma",
                "comparison",
                f"Compare the stated {unit} values for {titles[0]} and {titles[1]}.",
                sources=[left, right],
                intent="compare_quantities",
                required_answer_shape="comparison",
                required_facets=[
                    "comparison_side_a",
                    "comparison_side_b",
                    "quantity",
                    "source",
                ],
                required_entity_ids=[canonical_entity_id(title) for title in titles],
                attributes={"unit": unit},
            )
        )

    definitions = list(iter_definition_candidates(connection))
    pairs_by_partition = _same_partition_groups(definitions, 2)
    if len(pairs_by_partition) < 110:
        raise ValueError("corpus lacks 110 same-partition two-source pairs")
    for group in pairs_by_partition[:110]:
        titles = [str(item["title"]) for item in group]
        drafts.append(
            _draft(
                "gamma",
                "two_source",
                f"Using both sources, what are {titles[0]} and {titles[1]}?",
                sources=group,
                intent="compose_definitions",
                required_answer_shape="list",
                required_facets=["subject", "relation", "object", "source"],
                required_entity_ids=[canonical_entity_id(title) for title in titles],
            )
        )

    multi_groups: list[list[dict[str, Any]]] = []
    for size in (3, 4, 5, 6):
        multi_groups.extend(_same_partition_groups(definitions, size))
    multi_groups = sorted(
        multi_groups,
        key=lambda group: (len(group), tuple(str(item["document_id"]) for item in group)),
    )
    selected: list[list[dict[str, Any]]] = []
    seen_doc_sets: set[tuple[str, ...]] = set()
    for group in multi_groups:
        document_set = tuple(sorted(str(item["document_id"]) for item in group))
        if document_set in seen_doc_sets:
            continue
        seen_doc_sets.add(document_set)
        selected.append(group)
        if len(selected) == 80:
            break
    if len(selected) < 80:
        raise ValueError("corpus lacks 80 same-partition three-to-six-source groups")
    for group in selected:
        titles = [str(item["title"]) for item in group]
        drafts.append(
            _draft(
                "gamma",
                "three_to_six_source",
                "Give one source-backed description for each of: " + "; ".join(titles) + ".",
                sources=group,
                intent="compose_definitions",
                required_answer_shape="list",
                required_facets=["subject", "relation", "object", "source"],
                required_entity_ids=[canonical_entity_id(title) for title in titles],
                attributes={"source_count": len(group)},
            )
        )
    return drafts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--author", choices=sorted(AUTHOR_IDENTITIES), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    connection = connect_read_only(args.corpus)
    try:
        drafts = {
            "alpha": _author_alpha,
            "beta": _author_beta,
            "gamma": _author_gamma,
        }[args.author](connection)
        source_identity = corpus_identity(connection)
    finally:
        connection.close()
    questions = [str(item["question"]).casefold() for item in drafts]
    if len(questions) != len(set(questions)):
        raise ValueError("author shard contains duplicate normalized questions")
    identity, process_identity = AUTHOR_IDENTITIES[args.author]
    write_json(
        args.output,
        {
            "benchmark_identity": "INDEPENDENT_NATURAL_QUERY_SET_V050_R1",
            "author_role": {
                "identity": identity,
                "role": "independent_question_author",
                "process_identity": process_identity,
                "runtime_access": False,
            },
            "source_corpus": source_identity,
            "case_count": len(drafts),
            "drafts": drafts,
        },
    )


if __name__ == "__main__":
    main()
