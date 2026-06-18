"""Evidence helpers for counterparty resolution."""

from typing import Any, Dict, Mapping, Optional, Sequence

from schemas.common import EvidencePointer


def extract_evidence_records(
    evidence_index: Optional[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Extract the evidence records list from the evidence index."""
    if evidence_index is None:
        return []
    records = evidence_index.get("records", [])
    if isinstance(records, list):
        return records
    return []


def build_evidence_pointer(
    context: Dict[str, Any],
    evidence_records: Sequence[Mapping[str, Any]],
) -> Optional[EvidencePointer]:
    """Build an evidence pointer from context and evidence records."""
    if evidence_records:
        first = evidence_records[0]
        return EvidencePointer(
            evidence_id=first.get("evidence_id"),
            document_id=first.get("document_id"),
            source_file=str(first.get("source_file", "unknown")),
            page_number=first.get("page_number"),
            excerpt=first.get("excerpt"),
        )

    source_file = str(context.get("contract_file", "unknown"))
    counterparty = context.get("counterparty", "")
    return EvidencePointer(
        source_file=source_file,
        excerpt=f"Counterparty: {counterparty}" if counterparty else None,
    )
