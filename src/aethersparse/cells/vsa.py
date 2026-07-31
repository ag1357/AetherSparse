"""Deterministic binary VSA operations for approximate routing support."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable


def atom(value: str, *, dimensions: int = 1024) -> int:
    """Map a symbol to a stable binary hypervector without learned state."""
    if dimensions % 256:
        raise ValueError("dimensions must be a multiple of 256")
    blocks = [
        hashlib.sha256(f"{index}:{value}".encode()).digest() for index in range(dimensions // 256)
    ]
    return int.from_bytes(b"".join(blocks))


def bind(left: int, right: int) -> int:
    return left ^ right


def permute(value: int, shift: int, *, dimensions: int = 1024) -> int:
    shift %= dimensions
    mask = (1 << dimensions) - 1
    return ((value << shift) | (value >> (dimensions - shift))) & mask


def bundle(values: Iterable[int], *, dimensions: int = 1024) -> int:
    items = list(values)
    if not items:
        return 0
    threshold = len(items) / 2
    result = 0
    for bit in range(dimensions):
        ones = sum((item >> bit) & 1 for item in items)
        if ones > threshold or (ones == threshold and (atom(f"tie:{bit}") & 1)):
            result |= 1 << bit
    return result


def similarity(left: int, right: int, *, dimensions: int = 1024) -> float:
    return 1.0 - ((left ^ right).bit_count() / dimensions)


def encode_terms(terms: Iterable[str], *, dimensions: int = 1024) -> int:
    return bundle(
        (atom(term.casefold(), dimensions=dimensions) for term in set(terms)), dimensions=dimensions
    )
