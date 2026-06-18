"""Required clause extraction and evidence mapping."""

from typing import Any, Mapping

from schemas.common import EvidencePointer
from schemas.extracted_clause import (
    ClauseEvidenceSpan,
    ExtractedClause,
    ExtractionWarning,
)
from agents.extraction.constants import CLAUSE_KEYWORDS, LOW_CONFIDENCE_THRESHOLD
from agents.extraction.helpers import evidence_id_for_page, make_excerpt
from agents.extraction.models import ClauseCandidate


def extract_required_clauses(
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

        evidence = build_evidence_pointer(
            candidate=candidate,
            evidence_index=evidence_index,
            primary_document=primary_document,
        )
        evidence_spans = build_evidence_spans(
            candidate=candidate,
            evidence_index=evidence_index,
            primary_document=primary_document,
        )
        confidence = _score_confidence(clause_type, candidate)
        manual_review_required = confidence < LOW_CONFIDENCE_THRESHOLD
        clauses.append(
            ExtractedClause(
                clause_id=f"CL-{len(clauses) + 1:03d}",
                clause_type=clause_type,
                title=candidate.title,
                text=candidate.text,
                clause_text=candidate.text,
                page_number=candidate.page_number,
                page_numbers=candidate.page_numbers,
                section_reference=candidate.section_reference,
                confidence=confidence,
                confidence_score=confidence,
                evidence=evidence,
                evidence_pointer=evidence,
                evidence_spans=evidence_spans,
                manual_review_required=manual_review_required,
                char_start=candidate.char_start,
                char_end=candidate.char_end,
                bbox=candidate.bbox,
                bounding_box_coordinates=candidate.bbox,
            )
        )

        if manual_review_required:
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


def build_evidence_pointer(
    candidate: ClauseCandidate,
    evidence_index: Mapping[str, Any],
    primary_document: Mapping[str, Any],
) -> EvidencePointer:
    """Build an EvidencePointer from page-level evidence records."""
    evidence_id = evidence_id_for_page(evidence_index, candidate.page_number)
    source_file = str(primary_document["relative_path"])
    excerpt = make_excerpt(candidate.text)

    return EvidencePointer(
        evidence_id=evidence_id,
        document_id=str(primary_document["document_id"]),
        source_file=source_file,
        page_number=candidate.page_number,
        clause_reference=candidate.section_reference,
        excerpt=excerpt,
    )


def build_evidence_spans(
    candidate: ClauseCandidate,
    evidence_index: Mapping[str, Any],
    primary_document: Mapping[str, Any],
) -> list[ClauseEvidenceSpan]:
    """Build page-local evidence spans for an extracted clause."""
    source_file = str(primary_document["relative_path"])
    document_id = str(primary_document["document_id"])

    return [
        ClauseEvidenceSpan(
            page_number=span.page_number,
            evidence_id=evidence_id_for_page(evidence_index, span.page_number),
            document_id=document_id,
            source_file=source_file,
            char_start=span.char_start,
            char_end=span.char_end,
            bbox=span.bbox,
            excerpt=make_excerpt(span.text),
        )
        for span in candidate.spans
    ]


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
