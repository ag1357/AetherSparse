"""Bounded operational-intent signals ahead of COG construction.

This layer classifies what kind of user act occurred.  It does not retrieve an
entity, choose a tool, or select a controller operation.  Tool legality and
specialist activation still depend on COG obligations, capabilities, 5C, and
the legal action mask.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class NaturalRequestKind(StrEnum):
    QUESTION = "QUESTION"
    FOLLOW_UP = "FOLLOW_UP"
    CORRECTION = "CORRECTION"
    CANCEL = "CANCEL"
    RESET = "RESET"
    TOOL_TASK = "TOOL_TASK"
    MEMORY_TASK = "MEMORY_TASK"
    SOURCE_TASK = "SOURCE_TASK"
    UNSUPPORTED = "UNSUPPORTED"


class NaturalInputSignals(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_kind: NaturalRequestKind
    has_pronoun_reference: bool = False
    has_negation: bool = False
    has_incorrect_premise_signal: bool = False
    has_contradiction_signal: bool = False
    is_multi_clause: bool = False
    is_long_running_instruction: bool = False
    token_count: int = Field(ge=1)


def classify_natural_input(text: str) -> NaturalInputSignals:
    normalized = " ".join(text.strip().split())
    if not normalized:
        raise ValueError("natural input must not be empty")
    folded = normalized.casefold()
    tokens = re.findall(r"[\w'-]+", folded)
    if re.fullmatch(
        r"(?:cancel|stop|never mind|abort)(?:\s+(?:it|that|task|that task))?[.!]?", folded
    ):
        kind = NaturalRequestKind.CANCEL
    elif re.fullmatch(r"(?:reset|start over|new session|clear session)[.!]?", folded):
        kind = NaturalRequestKind.RESET
    elif re.search(r"\b(?:remember|forget|memory|what do you remember)\b", folded):
        kind = NaturalRequestKind.MEMORY_TASK
    elif re.search(
        r"\b(?:source tree|source code|repository|callers|callees|references|build|tests?)\b",
        folded,
    ):
        kind = NaturalRequestKind.SOURCE_TASK
    elif re.search(r"\b(?:run|open|create|write|apply|search|inspect|download)\b", folded):
        kind = NaturalRequestKind.TOOL_TASK
    elif re.search(r"\b(?:actually|correction|i meant|not that|rather)\b", folded):
        kind = NaturalRequestKind.CORRECTION
    elif re.match(r"(?:and\s+)?(?:what|how|where|when)\s+about\b", folded) or re.match(
        r"(?:and\s+)?(?:where|when|what|who)\s+(?:was|is|did)\s+(?:he|she|it|they)\b",
        folded,
    ):
        kind = NaturalRequestKind.FOLLOW_UP
    elif re.search(r"\b(?:sing a song|show video|take a photo)\b", folded):
        kind = NaturalRequestKind.UNSUPPORTED
    else:
        kind = NaturalRequestKind.QUESTION
    return NaturalInputSignals(
        request_kind=kind,
        has_pronoun_reference=bool(re.search(r"\b(?:he|she|it|they|them|his|her|their)\b", folded)),
        has_negation=bool(re.search(r"\b(?:no|not|never|without|except)\b", folded)),
        has_incorrect_premise_signal=bool(
            re.search(r"\b(?:isn't it true|why did .* when|assuming .* but)\b", folded)
        ),
        has_contradiction_signal=bool(
            re.search(r"\b(?:conflicts? with|contradicts?|but .* says)\b", folded)
        ),
        is_multi_clause=bool(re.search(r"[,;:]|\b(?:and then|while|but also)\b", folded)),
        is_long_running_instruction=bool(
            re.search(r"\b(?:until|iterate|keep trying|then .* then|long-running)\b", folded)
        ),
        token_count=max(1, len(tokens)),
    )
