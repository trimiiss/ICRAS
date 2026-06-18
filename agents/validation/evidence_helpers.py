"""Validation evidence pointer helpers."""

from typing import Any, Mapping, Optional, Sequence

from schemas.common import EvidencePointer
from schemas.extracted_clause import ExtractedClause
from utils.evidence import extract_evidence_records as _shared_extract_evidence_records
from utils.text import (
    is_non_empty as _is_non_empty,
    optional_int as _optional_int,
    optional_str as _optional_str,
    truncate as _truncate,
)


def _field_evidence(
    context: Mapping[str, Any],
    evidence_records: Sequence[Mapping[str, Any]],
    clause: Optional[ExtractedClause],
) -> list[EvidencePointer]:
    """Return clause evidence when available, else run-level fallback evidence."""
    if clause is not None:
        return [_clause_evidence(context, clause)]
    return [_fallback_evidence(context, evidence_records)]


def _primary_evidence(
    evidence: Sequence[EvidencePointer],
) -> Optional[EvidencePointer]:
    """Return the primary evidence pointer for finding compatibility fields."""
    return evidence[0] if evidence else None


def _evidence_text(evidence: Sequence[EvidencePointer]) -> Optional[str]:
    """Return the best evidence excerpt for source_clause_text."""
    primary = _primary_evidence(evidence)
    if primary is None:
        return None
    return primary.excerpt


def _evidence_page(evidence: Sequence[EvidencePointer]) -> Optional[int]:
    """Return the primary evidence page number."""
    primary = _primary_evidence(evidence)
    if primary is None:
        return None
    return primary.page_number


def _clause_evidence(
    context: Mapping[str, Any],
    clause: ExtractedClause,
) -> EvidencePointer:
    """Build an evidence pointer from an extracted clause."""
    return EvidencePointer(
        source_file=str(context.get("contract_file") or "unknown"),
        page_number=clause.page_number,
        clause_reference=clause.section_reference,
        excerpt=_truncate(clause.text),
    )


def _fallback_evidence(
    context: Mapping[str, Any],
    evidence_records: Sequence[Mapping[str, Any]],
) -> EvidencePointer:
    """Return the best available source pointer for a validation finding."""
    for record in evidence_records:
        source_file = record.get("source_file")
        if not _is_non_empty(source_file):
            continue
        return EvidencePointer(
            evidence_id=_optional_str(record.get("evidence_id")),
            document_id=_optional_str(record.get("document_id")),
            source_file=str(source_file),
            page_number=_optional_int(record.get("page_number")),
            clause_reference=_optional_str(record.get("section_reference")),
            excerpt=_optional_str(record.get("excerpt")),
        )

    return EvidencePointer(source_file=str(context.get("contract_file") or "unknown"))


def _context_value_evidence(
    context: Mapping[str, Any],
    field_name: str,
    raw_value: Any,
) -> list[EvidencePointer]:
    """Build an evidence pointer for a malformed context field value."""
    return [
        EvidencePointer(
            source_file=str(context.get("contract_file") or "context_packet.json"),
            excerpt=f"{field_name}: {raw_value}",
        )
    ]


def _extract_evidence_records(
    evidence_index: Optional[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Read evidence records from accepted evidence index shapes."""
    return _shared_extract_evidence_records(evidence_index)
