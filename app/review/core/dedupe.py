"""Shared deterministic candidate deduplication."""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable
from typing import TypeVar


T = TypeVar("T")


def dedupe_prefer_longer_description(
    items: Iterable[T],
    *,
    key: Callable[[T], Hashable],
    description: Callable[[T], str] = lambda item: str(
        getattr(item, "description", "")
    ),
) -> list[T]:
    """Keep first-key order while retaining the more informative duplicate."""
    selected: dict[Hashable, T] = {}
    for item in items:
        item_key = key(item)
        current = selected.get(item_key)
        if current is None or len(description(item)) > len(description(current)):
            selected[item_key] = item
    return list(selected.values())
