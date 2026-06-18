"""Shared collection helpers."""

from collections.abc import Iterable
from typing import Any


def ordered_unique(
    values: Iterable[Any],
    *,
    drop_blank: bool = True,
    strip: bool = True,
) -> list[str]:
    """Return unique string values while preserving first-seen order."""
    seen: set[str] = set()
    ordered: list[str] = []
    for raw_value in values:
        value = str(raw_value)
        if strip:
            value = value.strip()
        if not value and drop_blank:
            continue
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered
