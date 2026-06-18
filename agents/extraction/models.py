"""Internal extraction data structures."""

from dataclasses import dataclass


@dataclass(frozen=True)
class TextLine:
    """One extracted PDF text line with page-local layout coordinates."""

    page_number: int
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
class CandidateSpan:
    """Page-local text span for a clause candidate."""

    page_number: int
    text: str
    char_start: int | None
    char_end: int | None
    bbox: list[float] | None


@dataclass(frozen=True)
class ClauseCandidate:
    """A possible clause section found in the source text."""

    title: str
    text: str
    page_number: int
    page_numbers: list[int]
    section_reference: str | None
    char_start: int | None
    char_end: int | None
    bbox: list[float] | None
    spans: list[CandidateSpan]
