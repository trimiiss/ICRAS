"""Shared helpers for normalizing extracted clause payloads."""

from collections.abc import Callable
from typing import Any, Mapping, TypeVar

from schemas.common import EvidencePointer
from schemas.extracted_clause import ExtractedClause
from utils.text import is_non_empty, optional_int, optional_str, truncate

ErrorT = TypeVar("ErrorT", bound=Exception)


def coerce_extracted_clauses(
    raw_clauses: Any,
    *,
    error_type: type[ErrorT],
    list_error: str,
    mapping_error: Callable[[int, Any], str],
    missing_text_error: Callable[[int], str],
    invalid_error: Callable[[int, Exception], str],
    require_clause_type: bool = False,
    missing_clause_type_error: Callable[[int], str] | None = None,
    accept_clause_text_fallback: bool = True,
) -> list[ExtractedClause]:
    """Convert extracted clause dictionaries to ``ExtractedClause`` models."""
    if not isinstance(raw_clauses, list):
        raise error_type(list_error)

    clauses: list[ExtractedClause] = []
    for index, raw_clause in enumerate(raw_clauses, start=1):
        zero_based_index = index - 1
        if not isinstance(raw_clause, Mapping):
            raise error_type(mapping_error(zero_based_index, raw_clause))

        clause_data = dict(raw_clause)
        clause_type_value = clause_data.get("clause_type")
        if require_clause_type and not is_non_empty(clause_type_value):
            if missing_clause_type_error is None:
                raise error_type(f"clauses[{zero_based_index}] is missing clause_type.")
            raise error_type(missing_clause_type_error(zero_based_index))

        clause_type = str(clause_type_value or f"clause_{index}")
        text_value = clause_data.get("text")
        if accept_clause_text_fallback:
            text = str(text_value or clause_data.get("clause_text") or "")
            if not text.strip():
                raise error_type(missing_text_error(zero_based_index))
        else:
            if not is_non_empty(text_value):
                raise error_type(missing_text_error(zero_based_index))
            text = str(text_value)

        clause_data.setdefault("clause_id", f"CLAUSE-{index:03d}")
        clause_data.setdefault("clause_type", clause_type)
        clause_data.setdefault("title", clause_type.replace("_", " ").title())
        clause_data.setdefault("text", text)
        clause_data.setdefault("clause_text", text)
        clause_data.setdefault("confidence", 1.0)
        clause_data.setdefault("confidence_score", clause_data["confidence"])
        if "page_numbers" not in clause_data and clause_data.get("page_number") is not None:
            clause_data["page_numbers"] = [clause_data["page_number"]]
        clause_data.setdefault(
            "evidence",
            EvidencePointer(
                source_file="unknown",
                page_number=optional_int(clause_data.get("page_number")),
                clause_reference=optional_str(clause_data.get("section_reference")),
                excerpt=truncate(text),
            ).model_dump(mode="json"),
        )
        clause_data.setdefault("evidence_pointer", clause_data["evidence"])
        clause_data.setdefault(
            "manual_review_required",
            float(clause_data["confidence"]) < 0.75,
        )

        try:
            clauses.append(ExtractedClause.model_validate(clause_data))
        except Exception as exc:
            raise error_type(invalid_error(zero_based_index, exc)) from exc

    return clauses
