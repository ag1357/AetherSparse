#!/usr/bin/env python3
"""Phase 1: controller failure taxonomy @10k (diagnostic only, no behavior changes).

Replays the pipeline harness from a Phase 0B trace cache with the
candidate+ranking+evidence oracles enabled (retrieval removed as a cause),
then classifies every non-matching answer case into exactly one
earliest-failing category from the mission taxonomy:

    1  VALUE_NOT_ENUMERATED       accepted value absent from all graph claims
    2  SUBJECT_BINDING_WRONG      accepted value present, bound to wrong subject
    3  RELATION_BINDING_WRONG     value bound to wrong relation/attribute
    4  TEMPORAL_SCOPE_WRONG       value correct modulo time scope
    5  ATTRIBUTION_BINDING_WRONG  quotation bound to wrong speaker
    6  WRONG_TYPE                 emitted value type incompatible with shape
    7  VALUE_MISRANKED            correct claim enumerated but not selected
    8  COMPOSITION_OPERATOR_MISSING  needs combination of >=2 values
    9  CANONICALIZATION_ONLY      deterministic canonicalization matches
    10 REALIZATION_ONLY           plan value right, realized text malformed
    11 DISPOSITION_WRONG          correct answer suppressed / wrong disposition

Classification uses the gold metadata as a diagnostic (mode-1 style); this
script never changes shipped behavior.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v050_common import conversation_order, load_benchmark  # noqa: E402
from v09_trace_cache import load_cache, pool_from_cache  # noqa: E402

from aethersparse.controller.framing import QueryFramer  # noqa: E402
from aethersparse.controller.pipeline import StructuredController  # noqa: E402
from aethersparse.selection.models import CandidateScore  # noqa: E402

# Reuse the harness's oracle + adapter plumbing verbatim (single source).
import v08_pipeline_eval as harness  # noqa: E402


# ---------------------------------------------------------------------------
# Deterministic canonicalization (also serves the dual metric's
# canonical_value_accuracy comparator).

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}
_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_UNIT_ALIASES = {
    "kilometers": "km", "kilometres": "km", "kilometer": "km", "kilometre": "km",
    "meters": "m", "metres": "m", "meter": "m", "metre": "m",
    "miles": "mi", "mile": "mi",
    "square kilometers": "km2", "square kilometres": "km2", "square miles": "mi2",
    "square kilometer": "km2", "square kilometre": "km2",
    "per square kilometre": "/km2", "per square kilometer": "/km2",
    "per square mile": "/mi2",
}


def canonicalize(value: str) -> str:
    """Deterministic surface -> canonical value (dates, quantities, casing)."""
    text = re.sub(r"\s+", " ", str(value).strip().lower())
    text = re.sub(r"^(the|a|an) ", "", text)
    text = text.strip(" .,\"'")
    # A leading positive sign carries no information ('+24%' == '24%');
    # the negative sign is semantic and stays.
    text = re.sub(r"^\+(?=\d)", "", text)
    # dates: "march 4, 1998" / "4 march 1998" / "1998-03-04" -> iso
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.fullmatch(r"([a-z]+) (\d{1,2})(?:st|nd|rd|th)?,? (\d{4})", text)
    if m and m.group(1) in _MONTHS:
        return f"{m.group(3)}-{_MONTHS[m.group(1)]:02d}-{int(m.group(2)):02d}"
    m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)? ([a-z]+),? (\d{4})", text)
    if m and m.group(2) in _MONTHS:
        return f"{m.group(3)}-{_MONTHS[m.group(2)]:02d}-{int(m.group(1)):02d}"
    m = re.fullmatch(r"([a-z]+) (\d{4})", text)
    if m and m.group(1) in _MONTHS:
        return f"{m.group(2)}-{_MONTHS[m.group(1)]:02d}"
    if re.fullmatch(r"(?:in |c\.?\s?|circa )?(\d{4})s?", text):
        return re.fullmatch(r"(?:in |c\.?\s?|circa )?(\d{4})s?", text).group(1)
    # quantities: number words, commas, unit aliases
    for word, digit in sorted(_NUM_WORDS.items(), key=lambda kv: -len(kv[0])):
        text = re.sub(rf"\b{word}\b", str(digit), text)
    m = re.fullmatch(
        r"(?:about |around |approximately |over |nearly |almost )?"
        r"([\d,.]+)\s*(million|billion|thousand)?\s*([a-z/%² ]*)", text
    )
    if m and re.fullmatch(r"[\d,.]+", m.group(1)):
        num = m.group(1).replace(",", "").rstrip(".")
        scale = {"million": "e6", "billion": "e9", "thousand": "e3"}.get(
            m.group(2), ""
        )
        unit = (m.group(3) or "").strip()
        unit = _UNIT_ALIASES.get(unit, unit)
        unit = unit.replace("²", "2").strip()
        return f"{num}{scale}{(' ' + unit) if unit else ''}"
    return text


def canonical_match(realized: str, accepted: str) -> bool:
    """Canonical value equality with date-granularity containment.

    A full-date realization matches a coarser accepted value at the accepted
    granularity (realized '2010-06-01' matches accepted '2010'); the inverse
    does not hold.  Everything else is canonical string equality.
    """
    r = canonicalize(realized)
    a = canonicalize(accepted)
    if r == a:
        return True
    if re.fullmatch(r"\d{4}", a) and re.fullmatch(rf"{a}-\d{{2}}(-\d{{2}})?", r):
        return True
    if re.fullmatch(r"\d{4}-\d{2}", a) and re.fullmatch(rf"{a}-\d{{2}}", r):
        return True
    return False


def _claims_values(result) -> list[tuple[str, object]]:
    """(canonical object value, claim) pairs enumerated in the graph."""
    out = []
    for claim in result.graph.claims:
        out.append((canonicalize(claim.object_value), claim))
        if claim.quantity_value:
            out.append((canonicalize(claim.quantity_value), claim))
        if claim.quotation:
            out.append((canonicalize(claim.quotation), claim))
    return out


def _value_type(text: str) -> str:
    c = canonicalize(text)
    if re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", c):
        return "date"
    if re.fullmatch(r"[\d.]+(e\d+)?(\s[a-z/%\d]+)?", c):
        return "quantity"
    return "text"


_SHAPE_TYPES = {
    "date": {"date"},
    "time_point": {"date"},
    "duration": {"quantity"},
    "quantity": {"quantity"},
    "percentage": {"quantity"},
    "name": {"text"},
    "place": {"text"},
    "quotation": {"text"},
    "free_text": {"text"},
    "list": {"text"},
    "boolean": {"text"},
    "comparison": {"text"},
}


def classify_case(case, result, exact_ok: bool) -> str | None:
    """Return the earliest-failing taxonomy label, or None when correct."""
    disposition_ok = str(result.disposition).endswith("ANSWER") or str(
        result.disposition
    ) == str(case.accepted_disposition)
    answered = result.answer is not None
    if exact_ok and disposition_ok:
        return None

    accepted = [canonicalize(a) for a in case.accepted_answers]
    claims_values = _claims_values(result)
    enumerated_hit = any(
        canonical_match(value, accepted_value)
        for value, _ in claims_values
        for accepted_value in case.accepted_answers
    )

    # 9/10: selection realized a value that canonicalizes to the accepted one.
    if answered and result.answer is not None:
        if any(canonical_match(result.answer.text, a) for a in case.accepted_answers):
            if not exact_ok:
                return "CANONICALIZATION_ONLY"
            return "DISPOSITION_WRONG"

    shape = str(getattr(case.required_answer_shape, "value", case.required_answer_shape))
    frame_entities = set(result.frame.candidate_entity_ids)

    selected_claim = None
    if result.selection is not None:
        wanted = set(result.selection.selected_claim_ids)
        for claim in result.graph.claims:
            if claim.claim_id in wanted:
                selected_claim = claim
                break

    # 1: accepted value never enumerated.  For list/union questions, split the
    # accepted surface into ';'-joined parts: if every part is enumerated, the
    # earliest failure is the missing UNION operator, not enumeration.
    if not enumerated_hit:
        parts = [
            p.strip()
            for a in case.accepted_answers
            for p in str(a).split(";")
        ]
        multi = any(
            "comparison" in c or "two_source" in c or "three" in c or "six" in c
            for c in case.categories
        )
        if shape == "list" and len(parts) > 1 and all(
            any(canonical_match(value, part) for value, _ in claims_values)
            for part in parts
        ):
            return "COMPOSITION_OPERATOR_MISSING"
        if multi and len(case.accepted_answers) > 1:
            return "COMPOSITION_OPERATOR_MISSING"
        if multi and shape in {"list", "comparison"}:
            return "COMPOSITION_OPERATOR_MISSING"
        return "VALUE_NOT_ENUMERATED"

    # The accepted value IS enumerated. Binding-level errors precede ranking.
    matching = [
        claim
        for value, claim in claims_values
        if any(canonical_match(value, a) for a in case.accepted_answers)
    ]
    if selected_claim is not None and all(
        claim.claim_id != selected_claim.claim_id for claim in matching
    ):
        # 2: wrong subject — value exists but bound to another entity.
        if any(
            claim.subject_entity_id not in frame_entities for claim in matching
        ) and (
            selected_claim.subject_entity_id in frame_entities
            or not frame_entities
        ):
            pass  # fall through to specific binding checks below
        # 3/4/5 binding mismatches on the selected claim vs matching claims.
        if any(
            claim.occurred_at != selected_claim.occurred_at
            and selected_claim.occurred_at is not None
            for claim in matching
        ):
            return "TEMPORAL_SCOPE_WRONG"
        if any(
            claim.speaker_entity_id != selected_claim.speaker_entity_id
            and selected_claim.speaker_entity_id is not None
            for claim in matching
        ):
            return "ATTRIBUTION_BINDING_WRONG"
        if any(
            claim.relation_family != selected_claim.relation_family
            for claim in matching
        ):
            return "RELATION_BINDING_WRONG"
        if frame_entities and any(
            claim.subject_entity_id in frame_entities for claim in matching
        ) and selected_claim.subject_entity_id not in frame_entities:
            return "SUBJECT_BINDING_WRONG"
        # 6: shape/type mismatch of the selected value.
        allowed = _SHAPE_TYPES.get(shape, {"text"})
        if answered and _value_type(result.answer.text) not in allowed:
            return "WRONG_TYPE"
        return "VALUE_MISRANKED"

    if selected_claim is not None and any(
        claim.claim_id == selected_claim.claim_id for claim in matching
    ):
        # Correct claim selected but answer still wrong.
        if not answered:
            return "DISPOSITION_WRONG"
        allowed = _SHAPE_TYPES.get(shape, {"text"})
        if _value_type(result.answer.text) not in allowed:
            return "WRONG_TYPE"
        if result.plan is not None and result.answer is not None:
            plan_text = getattr(result.plan, "value_text", None) or ""
            if plan_text and canonicalize(plan_text) in set(accepted):
                return "REALIZATION_ONLY"
        return "CANONICALIZATION_ONLY"

    return "DISPOSITION_WRONG"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", required=True)
    parser.add_argument("--trace-cache", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--per-case-output")
    args = parser.parse_args()

    cache = load_cache(Path(args.trace_cache))
    cached = {entry["case_id"]: entry for entry in cache["cases"]}

    # Run the harness WITH oracles so retrieval is not the failure cause, and
    # capture per-case ControllerResult objects for classification.
    report, outcomes, results = harness.run_evaluation_with_results(
        pack=Path(args.pack),
        benchmark_path=harness.BENCHMARK_PATH,
        limit=None,
        partitions=None,
        oracles=frozenset({"candidate", "ranking", "evidence"}),
        trace_cache=Path(args.trace_cache),
        cached=cached,
    )

    benchmark = load_benchmark()
    cases_by_id = {case.case_id: case for case in benchmark.cases}
    taxonomy = Counter()
    cross_shape = defaultdict(Counter)
    cross_category = defaultdict(Counter)
    cross_source = defaultdict(Counter)
    per_case = []
    answer_total = 0
    exact_total = 0
    for outcome, result in zip(outcomes, results):
        case = cases_by_id[outcome["case_id"]]
        if str(case.accepted_disposition) != "ANSWER":
            continue
        answer_total += 1
        exact_ok = bool(outcome.get("exact_answer"))
        if exact_ok:
            exact_total += 1
        label = classify_case(case, result, exact_ok)
        if label is None:
            continue
        taxonomy[label] += 1
        shape = str(
            getattr(case.required_answer_shape, "value", case.required_answer_shape)
        )
        cross_shape[shape][label] += 1
        for category in case.categories:
            cross_category[category][label] += 1
        sources = "single" if len(case.gold_evidence) == 1 else "multi"
        cross_source[sources][label] += 1
        per_case.append(
            {
                "case_id": case.case_id,
                "partition": outcome.get("partition"),
                "categories": list(case.categories),
                "shape": shape,
                "sources": len(case.gold_evidence),
                "taxonomy": label,
                "realized": result.answer.text if result.answer else None,
                "accepted": list(case.accepted_answers),
                "claims_enumerated": len(result.graph.claims),
            }
        )

    report_out = {
        "pack": args.pack,
        "trace_cache": args.trace_cache,
        "oracles": ["candidate", "ranking", "evidence"],
        "denominators": {
            "answer_cases": answer_total,
            "exact_answer_correct": exact_total,
            "classified_failures": sum(taxonomy.values()),
        },
        "taxonomy": dict(taxonomy.most_common()),
        "by_shape": {k: dict(v.most_common()) for k, v in cross_shape.items()},
        "by_category": {k: dict(v.most_common()) for k, v in cross_category.items()},
        "by_source_count": {k: dict(v.most_common()) for k, v in cross_source.items()},
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report_out, indent=2, sort_keys=True))
    if args.per_case_output:
        Path(args.per_case_output).write_text(
            json.dumps(per_case, indent=1, sort_keys=True) + "\n"
        )
    print(json.dumps(report_out["denominators"], indent=2))
    for label, count in taxonomy.most_common():
        print(f"  {label:32s} {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
