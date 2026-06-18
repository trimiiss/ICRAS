"""Shared severity ordering helpers."""

from collections.abc import Iterable

from schemas.common import Severity


SEVERITY_RANK: dict[Severity, int] = {
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


def highest_severity(severities: Iterable[Severity]) -> Severity:
    """Return the highest severity, defaulting to LOW."""
    severity_list = list(severities)
    if not severity_list:
        return Severity.LOW
    return max(severity_list, key=lambda severity: SEVERITY_RANK[severity])
