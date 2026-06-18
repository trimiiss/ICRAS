"""Internal anomaly agent models."""

from dataclasses import dataclass

from schemas.common import EvidencePointer, Severity


@dataclass(frozen=True)
class DateObservation:
    """One normalized date plus the evidence that supports it."""

    field_name: str
    normalized_date: str
    evidence: EvidencePointer
    source_text: str


@dataclass(frozen=True)
class UnusualPattern:
    """A high-signal unusual contract pattern."""

    pattern_id: str
    field_name: str
    title: str
    description: str
    regex: str
    severity: Severity
    recommendation: str
