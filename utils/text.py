"""Shared text normalization helpers."""

import re
from typing import Any, Mapping, Optional, Sequence


def normalize_key(value: str) -> str:
    """Normalize free-form text for key and alias matching."""
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def is_non_empty(value: Any) -> bool:
    """Return whether a value carries meaningful non-empty content."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return any(is_non_empty(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(is_non_empty(item) for item in value)
    return True


def optional_str(value: Any) -> Optional[str]:
    """Return value as a string when non-empty, else None."""
    if not is_non_empty(value):
        return None
    return str(value)


def optional_int(value: Any) -> Optional[int]:
    """Return value as an int when possible, else None."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def truncate(value: str, max_chars: int = 500) -> str:
    """Return a compact whitespace-normalized text snippet."""
    normalized = " ".join(value.split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."
