"""Shared payment-term parsing helpers."""

import re
from typing import Optional


def extract_payment_terms(payment_text: str) -> list[str]:
    """Extract canonical net payment terms from text."""
    found_terms: list[str] = []
    for days in extract_payment_days(payment_text):
        term = f"net-{days}"
        if term not in found_terms:
            found_terms.append(term)
    return found_terms


def extract_payment_days(text: str) -> list[int]:
    """Extract net payment day values."""
    days: list[int] = []
    for match in re.finditer(r"\bnet[\s-]?(\d{1,3})\b", text, re.IGNORECASE):
        value = int(match.group(1))
        if value not in days:
            days.append(value)
    return days


def canonical_payment_term(raw_term: str) -> Optional[str]:
    """Normalize a configured payment term such as net 30 into net-30."""
    match = re.fullmatch(r"\s*net[\s-]?(\d{1,3})\s*", raw_term, re.IGNORECASE)
    if match is None:
        return None
    return f"net-{int(match.group(1))}"
