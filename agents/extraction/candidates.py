"""Clause candidate splitting from extracted page text."""

from agents.extraction.constants import SCHEDULE_PATTERN, SECTION_PATTERN
from agents.extraction.helpers import union_bboxes
from agents.extraction.models import CandidateSpan, ClauseCandidate, PageText, TextLine


def split_into_candidates(page_texts: list[PageText]) -> list[ClauseCandidate]:
    """Split page text into section-like clause candidates."""
    candidates: list[ClauseCandidate] = []
    active_candidate: ClauseCandidate | None = None

    for page_text in page_texts:
        current_title: str | None = None
        current_title_line: TextLine | None = None
        current_section: str | None = None
        current_lines: list[TextLine] = []

        for text_line in page_text.lines:
            heading = _match_heading(text_line.text)
            if heading is not None:
                candidate = _append_candidate(
                    candidates,
                    title=current_title,
                    title_line=current_title_line,
                    section_reference=current_section,
                    lines=current_lines,
                    page_number=page_text.page_number,
                )
                if candidate is not None:
                    active_candidate = candidate
                elif active_candidate is not None and current_lines:
                    active_candidate = _append_continuation_to_candidate(
                        candidates,
                        active_candidate,
                        current_lines,
                    )
                current_section = heading[0]
                current_title = heading[1]
                current_title_line = text_line
                current_lines = []
                continue

            current_lines.append(text_line)

        candidate = _append_candidate(
            candidates,
            title=current_title,
            title_line=current_title_line,
            section_reference=current_section,
            lines=current_lines,
            page_number=page_text.page_number,
        )
        if candidate is not None:
            active_candidate = candidate
        elif active_candidate is not None and current_lines:
            active_candidate = _append_continuation_to_candidate(
                candidates,
                active_candidate,
                current_lines,
            )

    return candidates


def _match_heading(text: str) -> tuple[str, str] | None:
    """Return section reference and title when a line is a clause boundary."""
    section_match = SECTION_PATTERN.match(text)
    if section_match is not None:
        return section_match.group("section"), section_match.group("title").strip()

    schedule_match = SCHEDULE_PATTERN.match(text)
    if schedule_match is not None:
        title = schedule_match.group("title").strip() or schedule_match.group("section")
        return schedule_match.group("section").strip(), title

    return None


def _append_candidate(
    candidates: list[ClauseCandidate],
    title: str | None,
    title_line: TextLine | None,
    section_reference: str | None,
    lines: list[TextLine],
    page_number: int,
) -> ClauseCandidate | None:
    """Append one candidate when it has useful text."""
    if title is None:
        return None

    combined_parts = [part for part in [title, *(line.text for line in lines)] if part]
    text = " ".join(" ".join(combined_parts).split())
    if not text:
        return None

    included_lines = [line for line in [title_line, *lines] if line is not None]
    span = _build_candidate_span(page_number, text, included_lines)

    candidate = ClauseCandidate(
        title=title or "Contract Text",
        text=text,
        page_number=page_number,
        page_numbers=[page_number],
        section_reference=section_reference,
        char_start=span.char_start,
        char_end=span.char_end,
        bbox=span.bbox,
        spans=[span],
    )
    candidates.append(candidate)
    return candidate


def _append_continuation_to_candidate(
    candidates: list[ClauseCandidate],
    candidate: ClauseCandidate,
    lines: list[TextLine],
) -> ClauseCandidate:
    """Merge page-leading continuation text into the prior candidate."""
    continuation_text = " ".join(line.text for line in lines)
    continuation_text = " ".join(continuation_text.split())
    if not continuation_text:
        return candidate

    page_number = lines[0].page_number
    span = _build_candidate_span(page_number, continuation_text, lines)
    merged_spans = [*candidate.spans, span]
    page_numbers = sorted({span.page_number for span in merged_spans})
    merged_text = f"{candidate.text} {continuation_text}".strip()
    merged_candidate = ClauseCandidate(
        title=candidate.title,
        text=merged_text,
        page_number=candidate.page_number,
        page_numbers=page_numbers,
        section_reference=candidate.section_reference,
        char_start=candidate.char_start,
        char_end=span.char_end,
        bbox=candidate.bbox,
        spans=merged_spans,
    )

    candidates[candidates.index(candidate)] = merged_candidate
    return merged_candidate


def _build_candidate_span(
    page_number: int,
    text: str,
    lines: list[TextLine],
) -> CandidateSpan:
    """Build one page-local span for a clause candidate."""
    if not lines:
        return CandidateSpan(
            page_number=page_number,
            text=text,
            char_start=None,
            char_end=None,
            bbox=None,
        )

    return CandidateSpan(
        page_number=page_number,
        text=text,
        char_start=min(line.char_start for line in lines),
        char_end=max(line.char_end for line in lines),
        bbox=union_bboxes([line.bbox for line in lines]),
    )
