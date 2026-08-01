"""Structured per-query cognition over the retained flat retrieval substrate."""

from aethersparse.controller.framing import QueryFramer
from aethersparse.controller.linking import EntityRegistry
from aethersparse.controller.pipeline import EvidenceProvider, FrameLinker, StructuredController

__all__ = [
    "EntityRegistry",
    "EvidenceProvider",
    "FrameLinker",
    "QueryFramer",
    "StructuredController",
]
