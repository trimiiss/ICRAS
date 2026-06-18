"""Synthetic extraction fallback fixture support."""

import json
import re
from pathlib import Path
from typing import Any, Mapping

from schemas.common import EvidencePointer
from schemas.extracted_clause import ClauseEvidenceSpan, ExtractedClause
from agents.extraction.constants import (
    CLAUSE_KEYWORDS,
    FALLBACK_FIXTURE_DIR,
    LOW_CONFIDENCE_THRESHOLD,
    MIN_REQUIRED_CLAUSE_COVERAGE,
)
from agents.extraction.errors import ExtractionAgentError
from agents.extraction.helpers import evidence_id_for_page, make_excerpt


def fallback_reason(clauses: list[ExtractedClause]) -> str | None:
    """Return a reason to use fallback data when extraction quality is too low."""
    if not clauses:
        return "no required clauses were extracted."

    coverage = len({clause.clause_type for clause in clauses}) / len(CLAUSE_KEYWORDS)
    if coverage < MIN_REQUIRED_CLAUSE_COVERAGE:
        return (
            f"required clause coverage {coverage:.0%} is below "
            f"{MIN_REQUIRED_CLAUSE_COVERAGE:.0%}."
        )

    average_confidence = sum(clause.confidence for clause in clauses) / len(clauses)
    if average_confidence < LOW_CONFIDENCE_THRESHOLD:
        return (
            f"average confidence {average_confidence:.2f} is below "
            f"{LOW_CONFIDENCE_THRESHOLD:.2f}."
        )

    low_confidence_count = sum(1 for clause in clauses if clause.manual_review_required)
    if low_confidence_count > len(clauses) / 2:
        return (
            f"{low_confidence_count} of {len(clauses)} extracted clauses require "
            "manual review."
        )

    return None


def find_fallback_fixture(bundle_data: Mapping[str, Any]) -> Path | None:
    """Find the best matching synthetic fallback fixture for a bundle."""
    manifest = bundle_data.get("manifest")
    if not isinstance(manifest, Mapping):
        return None

    candidate_names = [
        _fixture_key(manifest.get("bundle_name")),
        _fixture_key(manifest.get("contract_type")),
    ]

    for name in candidate_names:
        if name is None:
            continue
        fixture_path = FALLBACK_FIXTURE_DIR / f"{name}.json"
        if fixture_path.is_file():
            return fixture_path

    return None


def load_fallback_clauses(
    fixture_path: Path,
    evidence_index: Mapping[str, Any],
    primary_document: Mapping[str, Any],
) -> list[ExtractedClause]:
    """Load fallback clauses from a fixture and validate them as ExtractedClause."""
    try:
        with open(fixture_path, "r", encoding="utf-8") as file:
            fixture = json.load(file)
    except Exception as exc:
        raise ExtractionAgentError(
            f"Failed to load fallback fixture '{fixture_path}': {exc}"
        ) from exc

    raw_clauses = fixture.get("clauses") if isinstance(fixture, Mapping) else None
    if not isinstance(raw_clauses, list) or not raw_clauses:
        raise ExtractionAgentError(
            f"Fallback fixture '{fixture_path}' must contain a non-empty clauses list."
        )

    fallback_clauses: list[ExtractedClause] = []
    for raw_clause in raw_clauses:
        if not isinstance(raw_clause, Mapping):
            raise ExtractionAgentError(
                f"Fallback fixture '{fixture_path}' contains a non-object clause."
            )

        clause = _fallback_clause_from_mapping(
            raw_clause=raw_clause,
            clause_id=f"CL-{len(fallback_clauses) + 1:03d}",
            evidence_index=evidence_index,
            primary_document=primary_document,
        )
        fallback_clauses.append(clause)

    return fallback_clauses


def _fixture_key(value: Any) -> str | None:
    """Normalize a manifest field into a fallback fixture file stem."""
    if not isinstance(value, str):
        return None

    key = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    return key or None


def _fallback_clause_from_mapping(
    raw_clause: Mapping[str, Any],
    clause_id: str,
    evidence_index: Mapping[str, Any],
    primary_document: Mapping[str, Any],
) -> ExtractedClause:
    """Convert one fallback fixture clause into the extraction output schema."""
    clause_type = _required_fixture_str(raw_clause, "clause_type")
    title = _required_fixture_str(raw_clause, "title")
    text = _required_fixture_str(raw_clause, "text")
    section_reference = raw_clause.get("section_reference")
    if section_reference is not None and not isinstance(section_reference, str):
        raise ExtractionAgentError("Fallback clause section_reference must be a string.")

    raw_confidence = raw_clause.get("confidence", 0.85)
    if not isinstance(raw_confidence, (int, float)):
        raise ExtractionAgentError("Fallback clause confidence must be numeric.")
    confidence = float(raw_confidence)
    page_number = 1
    source_file = str(primary_document["relative_path"])
    document_id = str(primary_document["document_id"])
    evidence_id = evidence_id_for_page(evidence_index, page_number)
    excerpt = make_excerpt(text)
    evidence = EvidencePointer(
        evidence_id=evidence_id,
        document_id=document_id,
        source_file=source_file,
        page_number=page_number,
        clause_reference=section_reference,
        excerpt=excerpt,
    )
    evidence_span = ClauseEvidenceSpan(
        page_number=page_number,
        evidence_id=evidence_id,
        document_id=document_id,
        source_file=source_file,
        excerpt=excerpt,
    )
    manual_review_required = confidence < LOW_CONFIDENCE_THRESHOLD

    return ExtractedClause(
        clause_id=clause_id,
        clause_type=clause_type,
        title=title,
        text=text,
        clause_text=text,
        page_number=page_number,
        page_numbers=[page_number],
        section_reference=section_reference,
        confidence=confidence,
        confidence_score=confidence,
        evidence=evidence,
        evidence_pointer=evidence,
        evidence_spans=[evidence_span],
        manual_review_required=manual_review_required,
        bbox=None,
        bounding_box_coordinates=None,
    )


def _required_fixture_str(raw_clause: Mapping[str, Any], key: str) -> str:
    """Read a required string from a fallback fixture clause."""
    value = raw_clause.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ExtractionAgentError(f"Fallback clause field '{key}' must be a string.")
    return value.strip()
