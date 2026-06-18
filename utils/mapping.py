"""Shared mapping coercion helpers."""

from typing import Any, Mapping


def as_mapping(value: Any) -> Mapping[str, Any]:
    """Return value when it is a mapping, else an empty mapping."""
    return value if isinstance(value, Mapping) else {}
