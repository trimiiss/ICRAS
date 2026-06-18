"""Validation helpers for clause text analysis."""

import re
from typing import Any, Mapping, Optional, Sequence

from schemas.extracted_clause import ExtractedClause
from agents.validation.constants import FIELD_ALIASES, KNOWN_GOVERNING_LAW_JURISDICTIONS
from agents.validation.core_helpers import _find_clause, _get_raw_context_value
from utils.text import is_non_empty as _is_non_empty


def _extract_governing_law(text: str) -> Optional[str]:
    """Extract a known governing-law jurisdiction from clause text."""
    compact_text = " ".join(text.split())
    for jurisdiction in KNOWN_GOVERNING_LAW_JURISDICTIONS:
        if re.search(rf"\b{re.escape(jurisdiction)}\b", compact_text, re.IGNORECASE):
            return jurisdiction.lower()

    patterns = (
        r"laws?\s+of\s+([A-Z][A-Za-z .&-]+?)(?:,|\.|;|\s+and\s+|$)",
        r"governed\s+by\s+([A-Z][A-Za-z .&-]+?)\s+law",
        r"jurisdiction\s+of\s+([A-Z][A-Za-z .&-]+?)(?:,|\.|;|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, compact_text)
        if match is None:
            continue
        return _normalize_governing_law(match.group(1))
    return None


def _normalize_governing_law(value: str) -> str:
    """Return a compact comparable governing-law value."""
    cleaned = re.sub(
        r"\b(usa|u\.s\.a\.|united states|state of|laws of|the)\b",
        "",
        value,
        flags=re.IGNORECASE,
    )
    return " ".join(cleaned.replace(",", " ").split()).lower()


def _contains_numeric_calculation(text: str) -> bool:
    """Return whether text contains an explicit arithmetic expression."""
    return bool(
        re.search(
            r"\d[\d,]*(?:\.\d+)?\s*(?:x|\*|\+|times|plus)\s*\d",
            text,
            re.IGNORECASE,
        )
        or re.search(r"\btotal(?:s|ing)?\b", text, re.IGNORECASE)
    )


def _detect_calculation_error(text: str) -> Optional[str]:
    """Detect simple multiplication or addition errors in contract text."""
    compact_text = " ".join(text.split())
    multiplication_patterns = (
        r"(?P<a>\d[\d,]*(?:\.\d+)?)\s*(?:x|\*|times)\s*"
        r"(?P<b>\d[\d,]*(?:\.\d+)?)\s*(?:=|equals|total(?:s|ing)?(?:\s+(?:of|to))?)"
        r"\s*\$?(?P<c>\d[\d,]*(?:\.\d+)?)",
        r"monthly\s+fee\s+of\s+\$?(?P<a>\d[\d,]*(?:\.\d+)?)\s+for\s+"
        r"(?P<b>\d[\d,]*(?:\.\d+)?)\s+months?.*?"
        r"(?:total(?:s|ing)?(?:\s+(?:of|to))?)\s+\$?(?P<c>\d[\d,]*(?:\.\d+)?)",
    )
    for pattern in multiplication_patterns:
        match = re.search(pattern, compact_text, re.IGNORECASE)
        if match is None:
            continue
        left = _parse_decimal(match.group("a"))
        right = _parse_decimal(match.group("b"))
        stated = _parse_decimal(match.group("c"))
        if left is None or right is None or stated is None:
            continue
        expected = left * right
        if not _amounts_close(expected, stated):
            return (
                f"Detected calculation mismatch: {left:g} x {right:g} should equal "
                f"{expected:g}, but the clause states {stated:g}."
            )

    addition_pattern = (
        r"(?P<a>\d[\d,]*(?:\.\d+)?)\s*(?:\+|plus)\s*"
        r"(?P<b>\d[\d,]*(?:\.\d+)?)\s*(?:=|equals|total(?:s|ing)?(?:\s+(?:of|to))?)"
        r"\s*\$?(?P<c>\d[\d,]*(?:\.\d+)?)"
    )
    match = re.search(addition_pattern, compact_text, re.IGNORECASE)
    if match is None:
        return None
    left = _parse_decimal(match.group("a"))
    right = _parse_decimal(match.group("b"))
    stated = _parse_decimal(match.group("c"))
    if left is None or right is None or stated is None:
        return None
    expected = left + right
    if _amounts_close(expected, stated):
        return None
    return (
        f"Detected calculation mismatch: {left:g} + {right:g} should equal "
        f"{expected:g}, but the clause states {stated:g}."
    )


def _parse_decimal(raw_value: str) -> Optional[float]:
    """Parse a numeric contract value."""
    try:
        return float(raw_value.replace(",", ""))
    except ValueError:
        return None


def _amounts_close(left: float, right: float) -> bool:
    """Return whether two amounts are effectively equal for validation."""
    return abs(left - right) <= 0.01


def _extract_party_names(
    context: Mapping[str, Any],
    clauses: Sequence[ExtractedClause],
) -> list[str]:
    """Extract likely party names from context and party clauses."""
    parties: list[str] = []
    raw_parties = _get_raw_context_value(context, ("party_names", "parties"))
    if isinstance(raw_parties, Sequence) and not isinstance(raw_parties, (str, bytes)):
        parties.extend(str(party).strip() for party in raw_parties if _is_non_empty(party))
    elif isinstance(raw_parties, Mapping):
        parties.extend(str(value).strip() for value in raw_parties.values() if _is_non_empty(value))
    elif _is_non_empty(raw_parties):
        parties.extend(_split_party_text(str(raw_parties)))

    party_clause = _find_clause(clauses, FIELD_ALIASES["party_names"])
    if party_clause is not None:
        parties.extend(_split_party_text(party_clause.text))

    unique_parties: list[str] = []
    for party in parties:
        cleaned_party = _clean_party_name(party)
        if not cleaned_party or cleaned_party.lower() in {p.lower() for p in unique_parties}:
            continue
        unique_parties.append(cleaned_party)
    return unique_parties


def _split_party_text(text: str) -> list[str]:
    """Split common party-list language into likely legal names."""
    match = re.search(
        r"(?:between|among)\s+(.+?)(?:\.|,?\s+each\s+a\s+|,?\s+collectively\s+)",
        text,
        re.IGNORECASE,
    )
    party_text = match.group(1) if match is not None else text
    party_text = re.sub(r"\s+\([^)]*\)", "", party_text)
    return [
        item
        for item in re.split(r"\s*,\s*|\s+and\s+|\s+&\s+", party_text)
        if _is_non_empty(item)
    ]


def _clean_party_name(value: str) -> str:
    """Normalize a likely party name for comparison."""
    cleaned = value.strip(" .;:")
    cleaned = re.sub(r"^(this agreement is|by and between)\s+", "", cleaned, flags=re.IGNORECASE)
    if len(cleaned) < 3:
        return ""
    return cleaned
