"""Extraction Agent — extracts clauses and key terms from clean PDFs.

This agent implements deterministic born-digital PDF extraction.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pymupdf

from schemas.common import EvidencePointer
from schemas.extracted_clause import (
    ExtractedClause,
    ExtractedContract,
    ExtractionWarning,
)
from utils.run_manager import append_audit_event


class ExtractionAgentError(Exception):
    """Raised when extraction cannot produce the required artifact."""


@dataclass(frozen=True)
class TextLine:
    """One extracted PDF text line with page-local layout coordinates."""

    text: str
    bbox: list[float]
    char_start: int
    char_end: int


@dataclass(frozen=True)
class PageText:
    """Text extracted from one source page."""

    page_number: int
    text: str
    lines: list[TextLine]


@dataclass(frozen=True)
class ClauseCandidate:
    """A possible clause section found in the source text."""

    title: str
    text: str
    page_number: int
    section_reference: str | None
    char_start: int | None
    char_end: int | None
    bbox: list[float] | None


CLAUSE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "parties": ("parties", "between", "counterparty"),
    "effective_date": ("effective date", "commencement date"),
    "termination": ("termination", "terminate", "expiration"),
    "payment_terms": ("payment", "invoice", "net 30", "fees"),
    "liability_cap": ("liability", "cap", "limitation of liability"),
    "indemnity": ("indemnity", "indemnification", "indemnify"),
    "governing_law": ("governing law", "jurisdiction", "delaware"),
    "auto_renewal": ("auto-renewal", "automatic renewal", "renewal"),
    "data_protection": ("data protection", "personal data", "gdpr", "privacy"),
    "confidentiality": ("confidential", "confidentiality", "non-disclosure"),
}

SECTION_PATTERN = re.compile(
    r"^\s*(?P<section>\d+(?:\.\d+)*)[.)]?\s+(?P<title>[A-Z][^\n]{2,})\s*$"
)


def run_extraction(
    bundle_data: Mapping[str, Any],
    document_inventory: Mapping[str, Any],
    evidence_index: Mapping[str, Any],
    run_id: str,
    run_dir: str | Path,
) -> dict[str, Any]:
    """Extract structured clauses from the primary contract PDF.

    Args:
        bundle_data: Validated bundle data from the bundle loader.
        document_inventory: Document inventory produced by the intake agent.
        evidence_index: Evidence index produced for the primary contract.
        run_id: Unique run identifier.
        run_dir: Directory where run artifacts must be written.

    Returns:
        A dictionary containing the extracted contract and artifact path.

    Raises:
        ExtractionAgentError: If the primary contract cannot be extracted.
    """
    run_path = _validate_run_dir(run_dir)
    primary_document = _get_primary_document(document_inventory)
    contract_path = Path(_require_str(bundle_data, "contract_path")).resolve()

    if not contract_path.is_file():
        raise ExtractionAgentError(
            f"Primary contract file does not exist: {contract_path}. "
            "Validate the bundle before running extraction."
        )

    page_texts = _extract_page_texts(contract_path)
    if not page_texts:
        raise ExtractionAgentError(
            f"No extractable text found in primary contract PDF: {contract_path.name}. "
            "Use a born-digital PDF for US-07 or add fallback data in US-08."
        )

    candidates = _split_into_candidates(page_texts)
    clauses, warnings = _extract_required_clauses(
        candidates=candidates,
        evidence_index=evidence_index,
        primary_document=primary_document,
    )

    extracted_contract = ExtractedContract(
        run_id=run_id,
        document_id=str(primary_document["document_id"]),
        source_file=str(primary_document["relative_path"]),
        clauses=clauses,
        warnings=warnings,
    )

    output_path = run_path / "extracted_contract.json"
    _write_model_json(output_path, extracted_contract)

    low_confidence_count = sum(1 for clause in clauses if clause.confidence < 0.75)
    append_audit_event(
        run_path,
        {
            "event": "extraction_completed",
            "agent": "extraction_agent",
            "message": "Extraction Agent created structured clause artifacts.",
            "artifacts": [output_path.name],
            "clause_count": len(extracted_contract.clauses),
            "warning_count": len(extracted_contract.warnings),
            "low_confidence_count": low_confidence_count,
        },
    )

    return {
        "extracted_contract": extracted_contract.model_dump(mode="json"),
        "artifact_paths": {
            "extracted_contract": str(output_path),
        },
    }


def _validate_run_dir(run_dir: str | Path) -> Path:
    """Return a valid run directory path or raise a clear extraction error."""
    run_path = Path(run_dir).resolve()
    if not run_path.exists():
        raise ExtractionAgentError(
            f"Run directory does not exist: {run_path}. "
            "Create it with create_run_folder before running extraction."
        )
    if not run_path.is_dir():
        raise ExtractionAgentError(f"Run path is not a directory: {run_path}")
    return run_path


def _require_str(data: Mapping[str, Any], key: str) -> str:
    """Read a string value with a developer-friendly extraction error."""
    value = data.get(key)
    if not isinstance(value, str):
        raise ExtractionAgentError(
            f"Expected bundle_data['{key}'] to be a string. "
            "Load the bundle with utils.bundle_loader.load_bundle first."
        )
    return value


def _get_primary_document(document_inventory: Mapping[str, Any]) -> Mapping[str, Any]:
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


def _extract_page_texts(contract_path: Path) -> list[PageText]:
    """Extract readable page text from a born-digital PDF."""
    try:
        pdf = pymupdf.open(contract_path)
    except Exception as exc:
        raise ExtractionAgentError(
            f"Failed to open primary contract PDF '{contract_path.name}': {exc}"
        ) from exc

    page_texts: list[PageText] = []
    try:
        for index, page in enumerate(pdf, start=1):
            lines = _extract_text_lines(page)
            text = "\n".join(line.text for line in lines)
            if lines:
                page_texts.append(
                    PageText(page_number=index, text=text, lines=lines)
                )
    finally:
        pdf.close()

    return page_texts


def _extract_text_lines(page: pymupdf.Page) -> list[TextLine]:
    """Extract text lines with bounding boxes from one PDF page."""
    raw_text = page.get_text("dict")
    blocks = raw_text.get("blocks", [])
    if not isinstance(blocks, list):
        return []

    text_lines: list[TextLine] = []
    offset = 0
    for block in blocks:
        if not isinstance(block, Mapping) or block.get("type") != 0:
            continue
        raw_lines = block.get("lines", [])
        if not isinstance(raw_lines, list):
            continue

        for raw_line in raw_lines:
            if not isinstance(raw_line, Mapping):
                continue
            line = _text_line_from_raw(raw_line, offset)
            if line is None:
                continue
            text_lines.append(line)
            offset = line.char_end + 1

    return text_lines


def _text_line_from_raw(raw_line: Mapping[str, Any], offset: int) -> TextLine | None:
    """Convert a PyMuPDF raw line dictionary into a TextLine."""
    spans = raw_line.get("spans", [])
    if not isinstance(spans, list):
        return None

    text_parts: list[str] = []
    bboxes: list[list[float]] = []
    for span in spans:
        if not isinstance(span, Mapping):
            continue
        span_text = span.get("text")
        if isinstance(span_text, str):
            text_parts.append(span_text)
        bbox = _coerce_bbox(span.get("bbox"))
        if bbox is not None:
            bboxes.append(bbox)

    text = " ".join("".join(text_parts).split())
    if not text:
        return None

    line_bbox = _coerce_bbox(raw_line.get("bbox")) or _union_bboxes(bboxes)
    if line_bbox is None:
        return None

    return TextLine(
        text=text,
        bbox=line_bbox,
        char_start=offset,
        char_end=offset + len(text),
    )


def _split_into_candidates(page_texts: list[PageText]) -> list[ClauseCandidate]:
    """Split page text into section-like clause candidates."""
    candidates: list[ClauseCandidate] = []

    for page_text in page_texts:
        current_title: str | None = None
        current_title_line: TextLine | None = None
        current_section: str | None = None
        current_lines: list[TextLine] = []

        for text_line in page_text.lines:
            match = SECTION_PATTERN.match(text_line.text)
            if match is not None:
                _append_candidate(
                    candidates=candidates,
                    title=current_title,
                    title_line=current_title_line,
                    section_reference=current_section,
                    lines=current_lines,
                    page_number=page_text.page_number,
                )
                current_section = match.group("section")
                current_title = match.group("title").strip()
                current_title_line = text_line
                current_lines = []
                continue

            if current_title is None:
                current_title = "Contract Text"
                current_title_line = None
            current_lines.append(text_line)

        _append_candidate(
            candidates=candidates,
            title=current_title,
            title_line=current_title_line,
            section_reference=current_section,
            lines=current_lines,
            page_number=page_text.page_number,
        )

    return candidates


def _append_candidate(
    candidates: list[ClauseCandidate],
    title: str | None,
    title_line: TextLine | None,
    section_reference: str | None,
    lines: list[TextLine],
    page_number: int,
) -> None:
    """Append one candidate when it has useful text."""
    if title is None and not lines:
        return

    combined_parts = [part for part in [title, *(line.text for line in lines)] if part]
    text = " ".join(" ".join(combined_parts).split())
    if not text:
        return

    included_lines = [line for line in [title_line, *lines] if line is not None]
    if included_lines:
        char_start = min(line.char_start for line in included_lines)
        char_end = max(line.char_end for line in included_lines)
        bbox = _union_bboxes([line.bbox for line in included_lines])
    else:
        char_start = None
        char_end = None
        bbox = None

    candidates.append(
        ClauseCandidate(
            title=title or "Contract Text",
            text=text,
            page_number=page_number,
            section_reference=section_reference,
            char_start=char_start,
            char_end=char_end,
            bbox=bbox,
        )
    )


def _extract_required_clauses(
    candidates: list[ClauseCandidate],
    evidence_index: Mapping[str, Any],
    primary_document: Mapping[str, Any],
) -> tuple[list[ExtractedClause], list[ExtractionWarning]]:
    """Extract ticket-required clause categories from candidates."""
    clauses: list[ExtractedClause] = []
    warnings: list[ExtractionWarning] = []

    for clause_type in CLAUSE_KEYWORDS:
        candidate = _best_candidate_for_type(clause_type, candidates)
        if candidate is None:
            warnings.append(
                ExtractionWarning(
                    warning_id=f"WARN-{len(warnings) + 1:03d}",
                    clause_type=clause_type,
                    message=(
                        f"No likely text found for required clause category "
                        f"'{clause_type}'."
                    ),
                )
            )
            continue

        evidence = _build_evidence_pointer(
            candidate=candidate,
            evidence_index=evidence_index,
            primary_document=primary_document,
        )
        confidence = _score_confidence(clause_type, candidate)
        clauses.append(
            ExtractedClause(
                clause_id=f"CL-{len(clauses) + 1:03d}",
                clause_type=clause_type,
                title=candidate.title,
                text=candidate.text,
                page_number=candidate.page_number,
                section_reference=candidate.section_reference,
                confidence=confidence,
                evidence=evidence,
                char_start=candidate.char_start,
                char_end=candidate.char_end,
                bbox=candidate.bbox,
            )
        )

        if confidence < 0.75:
            warnings.append(
                ExtractionWarning(
                    warning_id=f"WARN-{len(warnings) + 1:03d}",
                    clause_type=clause_type,
                    message=(
                        f"Low confidence extraction for required clause category "
                        f"'{clause_type}'."
                    ),
                )
            )

    return clauses, warnings


def _best_candidate_for_type(
    clause_type: str,
    candidates: list[ClauseCandidate],
) -> ClauseCandidate | None:
    """Return the strongest matching candidate for a clause category."""
    keywords = CLAUSE_KEYWORDS[clause_type]
    best_candidate: ClauseCandidate | None = None
    best_score = 0

    for candidate in candidates:
        searchable = f"{candidate.title} {candidate.text}".casefold()
        score = sum(2 for keyword in keywords if keyword in searchable)
        score += sum(1 for token in clause_type.split("_") if token in searchable)

        if score > best_score:
            best_candidate = candidate
            best_score = score

    if best_score == 0:
        return None
    return best_candidate


def _score_confidence(clause_type: str, candidate: ClauseCandidate) -> float:
    """Score extraction confidence using deterministic text signals."""
    searchable = f"{candidate.title} {candidate.text}".casefold()
    keywords = CLAUSE_KEYWORDS[clause_type]
    matched_keywords = sum(1 for keyword in keywords if keyword in searchable)
    confidence = 0.6 + (0.1 * matched_keywords)

    if candidate.section_reference is not None:
        confidence += 0.1
    if len(candidate.text) >= 80:
        confidence += 0.05

    return min(round(confidence, 2), 0.99)


def _build_evidence_pointer(
    candidate: ClauseCandidate,
    evidence_index: Mapping[str, Any],
    primary_document: Mapping[str, Any],
) -> EvidencePointer:
    """Build an EvidencePointer from page-level evidence records."""
    evidence_id = _evidence_id_for_page(evidence_index, candidate.page_number)
    source_file = str(primary_document["relative_path"])
    excerpt = _make_excerpt(candidate.text)

    return EvidencePointer(
        evidence_id=evidence_id,
        document_id=str(primary_document["document_id"]),
        source_file=source_file,
        page_number=candidate.page_number,
        clause_reference=candidate.section_reference,
        excerpt=excerpt,
    )


def _evidence_id_for_page(
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


def _normalize_text(text: str) -> str:
    """Normalize extracted PDF text while preserving line boundaries."""
    normalized_lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in normalized_lines if line).strip()


def _coerce_bbox(value: Any) -> list[float] | None:
    """Return a four-number bounding box or None."""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None

    bbox: list[float] = []
    for coordinate in value:
        if not isinstance(coordinate, (int, float)):
            return None
        bbox.append(round(float(coordinate), 2))

    return bbox


def _union_bboxes(bboxes: list[list[float]]) -> list[float] | None:
    """Return the union of multiple page-local bounding boxes."""
    if not bboxes:
        return None

    return [
        round(min(bbox[0] for bbox in bboxes), 2),
        round(min(bbox[1] for bbox in bboxes), 2),
        round(max(bbox[2] for bbox in bboxes), 2),
        round(max(bbox[3] for bbox in bboxes), 2),
    ]


def _make_excerpt(text: str, max_chars: int = 300) -> str:
    """Return a compact source excerpt."""
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _write_model_json(path: Path, model: ExtractedContract) -> None:
    """Write an ExtractedContract model as deterministic, formatted JSON."""
    with open(path, "w", encoding="utf-8") as file:
        json.dump(model.model_dump(mode="json"), file, indent=2, ensure_ascii=False)
        file.write("\n")
