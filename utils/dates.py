"""Shared date parsing helpers."""

import re
from datetime import date, datetime
from typing import Any, Optional, Sequence


DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d",
    "%m/%d/%Y",
    "%m-%d-%Y",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d %B %Y",
    "%d %b %Y",
)

DATE_CANDIDATE_PATTERNS: tuple[str, ...] = (
    r"\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b",
    r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b",
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*"
    r"\s+\d{1,2},\s+\d{4}\b",
    r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*"
    r"\s+\d{4}\b",
)


def normalize_date(
    raw_value: Any,
    date_formats: Sequence[str] = DATE_FORMATS,
) -> Optional[str]:
    """Normalize a date-like value to an ISO 8601 date string."""
    if isinstance(raw_value, datetime):
        return raw_value.date().isoformat()
    if isinstance(raw_value, date):
        return raw_value.isoformat()
    if not isinstance(raw_value, str):
        return None

    value = raw_value.strip()
    if not value:
        return None

    iso_value = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(iso_value).date().isoformat()
    except ValueError:
        pass

    for date_format in date_formats:
        try:
            return datetime.strptime(value, date_format).date().isoformat()
        except ValueError:
            continue

    return None


def extract_normalized_date(
    text: str,
    patterns: Sequence[str] = DATE_CANDIDATE_PATTERNS,
    date_formats: Sequence[str] = DATE_FORMATS,
) -> Optional[str]:
    """Extract and normalize the first parseable date from text."""
    normalized_whitespace = " ".join(text.split())
    for pattern in patterns:
        match = re.search(pattern, normalized_whitespace, flags=re.IGNORECASE)
        if match is None:
            continue
        normalized_date = normalize_date(match.group(0), date_formats=date_formats)
        if normalized_date is not None:
            return normalized_date
    return None
