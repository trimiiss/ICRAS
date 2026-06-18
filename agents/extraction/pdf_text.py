"""Born-digital PDF text extraction for clause extraction."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pymupdf

from agents.extraction.constants import PAGE_NUMBER_PATTERN
from agents.extraction.errors import ExtractionAgentError
from agents.extraction.helpers import coerce_bbox, union_bboxes
from agents.extraction.models import PageText, TextLine
from schemas.extracted_clause import OcrMetadata, OcrPageResult


MIN_USEFUL_TEXT_CHARS = 20
OCR_ENGINE_NAME = "pymupdf_tesseract"


@dataclass(frozen=True)
class PdfTextExtractionResult:
    """PDF text extraction result with optional OCR metadata."""

    page_texts: list[PageText]
    text_extraction_method: str
    ocr_metadata: OcrMetadata | None


def extract_page_texts(contract_path: Path) -> list[PageText]:
    """Extract readable page text from a born-digital PDF."""
    return extract_pdf_text(contract_path).page_texts


def extract_pdf_text(contract_path: Path) -> PdfTextExtractionResult:
    """Extract page text, using OCR only for pages without useful text."""
    try:
        pdf = pymupdf.open(contract_path)
    except Exception as exc:
        raise ExtractionAgentError(
            f"Failed to open primary contract PDF '{contract_path.name}': {exc}"
        ) from exc

    page_texts: list[PageText] = []
    ocr_pages: list[OcrPageResult] = []
    try:
        for index, page in enumerate(pdf, start=1):
            lines = _extract_text_lines(page, index)
            if not _has_useful_text(lines):
                lines, ocr_page = _extract_ocr_text_lines(page, index)
                ocr_pages.append(ocr_page)

            text = "\n".join(line.text for line in lines)
            if lines:
                page_texts.append(PageText(page_number=index, text=text, lines=lines))
    finally:
        pdf.close()

    filtered_pages = _filter_repeated_page_artifacts(page_texts)
    ocr_metadata = _build_ocr_metadata(ocr_pages)
    text_extraction_method = _text_extraction_method(filtered_pages, ocr_metadata)
    return PdfTextExtractionResult(
        page_texts=filtered_pages,
        text_extraction_method=text_extraction_method,
        ocr_metadata=ocr_metadata,
    )


def _extract_text_lines(page: pymupdf.Page, page_number: int) -> list[TextLine]:
    """Extract text lines with bounding boxes from one PDF page."""
    raw_text = page.get_text("dict")
    return _extract_text_lines_from_raw(raw_text, page_number)


def _extract_text_lines_from_raw(
    raw_text: Mapping[str, Any],
    page_number: int,
) -> list[TextLine]:
    """Extract text lines from one PyMuPDF text dictionary."""
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
            line = _text_line_from_raw(raw_line, page_number, offset)
            if line is None:
                continue
            text_lines.append(line)
            offset = line.char_end + 1

    return text_lines


def _extract_ocr_text_lines(
    page: pymupdf.Page,
    page_number: int,
) -> tuple[list[TextLine], OcrPageResult]:
    """Extract text lines from one page using PyMuPDF OCR."""
    try:
        text_page = page.get_textpage_ocr(full=True)
        raw_text = page.get_text("dict", textpage=text_page)
    except Exception as exc:
        warning = (
            f"OCR unavailable for page {page_number}: {exc}. "
            "Install or configure Tesseract for PyMuPDF OCR."
        )
        return [], OcrPageResult(
            page_number=page_number,
            used=False,
            confidence=None,
            text_length=0,
            warning=warning,
        )

    lines = _extract_text_lines_from_raw(raw_text, page_number)
    normalized_text = _normalized_lines_text(lines)
    if not normalized_text:
        return [], OcrPageResult(
            page_number=page_number,
            used=False,
            confidence=0.0,
            text_length=0,
            warning=f"OCR produced no readable text on page {page_number}.",
        )

    confidence = _estimate_ocr_confidence(normalized_text)
    warning = (
        f"OCR confidence is low on page {page_number}."
        if confidence < 0.75
        else None
    )
    return lines, OcrPageResult(
        page_number=page_number,
        used=True,
        confidence=confidence,
        text_length=len(normalized_text),
        warning=warning,
    )


def _text_line_from_raw(
    raw_line: Mapping[str, Any],
    page_number: int,
    offset: int,
) -> TextLine | None:
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
        bbox = coerce_bbox(span.get("bbox"))
        if bbox is not None:
            bboxes.append(bbox)

    text = " ".join("".join(text_parts).split())
    if not text:
        return None

    line_bbox = coerce_bbox(raw_line.get("bbox")) or union_bboxes(bboxes)
    if line_bbox is None:
        return None

    return TextLine(
        page_number=page_number,
        text=text,
        bbox=line_bbox,
        char_start=offset,
        char_end=offset + len(text),
    )


def _filter_repeated_page_artifacts(page_texts: list[PageText]) -> list[PageText]:
    """Remove obvious repeated headers, footers, and standalone page numbers."""
    repeated_candidates: dict[str, set[int]] = {}
    for page_text in page_texts:
        edge_lines = [*page_text.lines[:2], *page_text.lines[-2:]]
        for line in edge_lines:
            key = _line_key(line.text)
            if key:
                repeated_candidates.setdefault(key, set()).add(page_text.page_number)

    repeated_keys = {
        key for key, page_numbers in repeated_candidates.items() if len(page_numbers) > 1
    }
    filtered_pages: list[PageText] = []
    for page_text in page_texts:
        filtered_lines = [
            line
            for line in page_text.lines
            if _line_key(line.text) not in repeated_keys
            and not PAGE_NUMBER_PATTERN.match(line.text)
        ]
        if filtered_lines:
            filtered_pages.append(
                PageText(
                    page_number=page_text.page_number,
                    text="\n".join(line.text for line in filtered_lines),
                    lines=filtered_lines,
                )
            )

    return filtered_pages


def _line_key(text: str) -> str:
    """Normalize a line for repeated header/footer detection."""
    return " ".join(text.casefold().split())


def _has_useful_text(lines: list[TextLine]) -> bool:
    """Return whether normal PDF extraction produced useful page text."""
    return len(_normalized_lines_text(lines)) >= MIN_USEFUL_TEXT_CHARS


def _normalized_lines_text(lines: list[TextLine]) -> str:
    """Return normalized text from extracted page lines."""
    return " ".join(line.text for line in lines).strip()


def _estimate_ocr_confidence(text: str) -> float:
    """Estimate OCR quality deterministically from text density and shape."""
    normalized = " ".join(text.split())
    if not normalized:
        return 0.0

    non_space = [char for char in normalized if not char.isspace()]
    if not non_space:
        return 0.0

    alnum_ratio = sum(char.isalnum() for char in non_space) / len(non_space)
    words = re.findall(r"[A-Za-z0-9]{2,}", normalized)
    word_density = min(len(words) / 60, 1.0)
    length_score = min(len(normalized) / 500, 1.0)
    confidence = 0.35 + (0.35 * alnum_ratio) + (0.20 * word_density) + (0.10 * length_score)
    return round(min(max(confidence, 0.0), 0.99), 2)


def _build_ocr_metadata(ocr_pages: list[OcrPageResult]) -> OcrMetadata | None:
    """Build run-level OCR metadata when OCR was attempted."""
    if not ocr_pages:
        return None

    used_pages = [page for page in ocr_pages if page.used]
    confidences = [
        float(page.confidence)
        for page in used_pages
        if page.confidence is not None
    ]
    average_confidence = (
        round(sum(confidences) / len(confidences), 2)
        if confidences
        else None
    )
    low_confidence = any(
        page.confidence is not None and page.confidence < 0.75
        for page in used_pages
    )
    unavailable = any(not page.used and page.warning for page in ocr_pages)
    reason = (
        "OCR was used because normal PDF extraction produced no useful text."
        if used_pages
        else "OCR was attempted because normal PDF extraction produced no useful text."
    )
    if unavailable and not used_pages:
        reason = "OCR was unavailable after normal PDF extraction produced no useful text."

    return OcrMetadata(
        used=bool(used_pages),
        engine=OCR_ENGINE_NAME,
        pages_processed=len(ocr_pages),
        average_confidence=average_confidence,
        low_confidence=low_confidence,
        manual_review_required=low_confidence,
        reason=reason,
        pages=ocr_pages,
    )


def _text_extraction_method(
    page_texts: list[PageText],
    ocr_metadata: OcrMetadata | None,
) -> str:
    """Return the primary extraction method for the contract artifact."""
    if ocr_metadata is not None and ocr_metadata.used:
        return "ocr"
    if page_texts:
        return "digital"
    return "none"
