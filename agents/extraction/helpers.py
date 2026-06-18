"""Shared extraction helper functions."""

from typing import Any, Mapping

from agents.extraction.errors import ExtractionAgentError
from utils.text import truncate


def require_str(data: Mapping[str, Any], key: str) -> str:
    """Read a string value with a developer-friendly extraction error."""
    value = data.get(key)
    if not isinstance(value, str):
        raise ExtractionAgentError(
            f"Expected bundle_data['{key}'] to be a string. "
            "Load the bundle with utils.bundle_loader.load_bundle first."
        )
    return value


def get_primary_document(document_inventory: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the primary contract entry from document_inventory.json data."""
    documents = document_inventory.get("documents")
    if not isinstance(documents, list):
        raise ExtractionAgentError(
            "Expected document_inventory['documents'] to be a list. "
            "Run the Intake Agent before extraction."
        )

    for document in documents:
        if isinstance(document, Mapping) and document.get("is_primary") is True:
            if document.get("document_type") != "contract":
                raise ExtractionAgentError(
                    "Primary document is not classified as a contract. "
                    "Check document_inventory.json before extraction."
                )
            return document

    raise ExtractionAgentError(
        "No primary contract document found in document_inventory.json. "
        "Run the Intake Agent and confirm contract.pdf is identified."
    )


def evidence_id_for_page(
    evidence_index: Mapping[str, Any],
    page_number: int,
) -> str | None:
    """Find the page-level evidence ID for a clause page."""
    records = evidence_index.get("records")
    if not isinstance(records, list):
        return None

    for record in records:
        if isinstance(record, Mapping) and record.get("page_number") == page_number:
            evidence_id = record.get("evidence_id")
            return str(evidence_id) if evidence_id is not None else None

    return None


def normalize_text(text: str) -> str:
    """Normalize extracted PDF text while preserving line boundaries."""
    normalized_lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in normalized_lines if line).strip()


def coerce_bbox(value: Any) -> list[float] | None:
    """Return a four-number bounding box or None."""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None

    bbox: list[float] = []
    for coordinate in value:
        if not isinstance(coordinate, (int, float)):
            return None
        bbox.append(round(float(coordinate), 2))

    return bbox


def union_bboxes(bboxes: list[list[float]]) -> list[float] | None:
    """Return the union of multiple page-local bounding boxes."""
    if not bboxes:
        return None

    return [
        round(min(bbox[0] for bbox in bboxes), 2),
        round(min(bbox[1] for bbox in bboxes), 2),
        round(max(bbox[2] for bbox in bboxes), 2),
        round(max(bbox[3] for bbox in bboxes), 2),
    ]


def make_excerpt(text: str, max_chars: int = 300) -> str:
    """Return a compact source excerpt."""
    return truncate(text, max_chars=max_chars)
