"""Shared evidence index helpers."""

from typing import Any, Mapping


def extract_evidence_records(
    evidence_index: Mapping[str, Any] | None,
) -> list[Mapping[str, Any]]:
    """Read evidence records from accepted evidence index shapes."""
    if evidence_index is None:
        return []

    candidate: Any = evidence_index
    if "evidence_index" in evidence_index:
        candidate = evidence_index["evidence_index"]

    if not isinstance(candidate, Mapping):
        return []

    records = candidate.get("records")
    if not isinstance(records, list):
        return []

    return [record for record in records if isinstance(record, Mapping)]
