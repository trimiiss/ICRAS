"""Born-digital PDF text extraction for clause extraction."""

from pathlib import Path
from typing import Any, Mapping

import pymupdf

from agents.extraction.constants import PAGE_NUMBER_PATTERN
from agents.extraction.errors import ExtractionAgentError
from agents.extraction.helpers import coerce_bbox, union_bboxes
from agents.extraction.models import PageText, TextLine


def extract_page_texts(contract_path: Path) -> list[PageText]:
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
            lines = _extract_text_lines(page, index)
            text = "\n".join(line.text for line in lines)
            if lines:
                page_texts.append(
                    PageText(page_number=index, text=text, lines=lines)
                )
    finally:
        pdf.close()

    return _filter_repeated_page_artifacts(page_texts)


def _extract_text_lines(page: pymupdf.Page, page_number: int) -> list[TextLine]:
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
            line = _text_line_from_raw(raw_line, page_number, offset)
            if line is None:
                continue
            text_lines.append(line)
            offset = line.char_end + 1

    return text_lines


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
