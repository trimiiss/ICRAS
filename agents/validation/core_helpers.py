"""Core validation helpers for clauses, context, and finding identity."""

from typing import Any, Iterable, Mapping, Optional, Sequence

from schemas.extracted_clause import ExtractedClause
from schemas.finding import Finding
from agents.validation.errors import ValidationAgentError
from agents.validation.evidence_helpers import _primary_evidence
from utils.clauses import coerce_extracted_clauses
from utils.text import (
    is_non_empty as _is_non_empty,
    normalize_key as _normalize_key,
)


def _deduplicate_findings(findings: Sequence[Finding]) -> list[Finding]:
    """Remove duplicate findings and reassign deterministic validation IDs."""
    deduped: list[Finding] = []
    seen: set[tuple[str, str, str, str]] = set()
    for finding in findings:
        primary_evidence = finding.evidence_pointer or _primary_evidence(finding.evidence)
        evidence_key = ""
        if primary_evidence is not None:
            evidence_key = "|".join(
                [
                    primary_evidence.source_file,
                    str(primary_evidence.page_number or ""),
                    primary_evidence.clause_reference or "",
                    primary_evidence.excerpt or "",
                ]
            )
        key = (
            finding.field_name or "",
            finding.issue_type or finding.title,
            finding.description,
            evidence_key,
        )
        if key in seen:
            continue
        seen.add(key)
        next_id = f"VAL-{len(deduped) + 1:03d}"
        deduped.append(finding.model_copy(update={"finding_id": next_id}))
    return deduped


def _coerce_clauses(clauses: Any) -> list[ExtractedClause]:
    """Convert clause dictionaries to ExtractedClause models."""
    return coerce_extracted_clauses(
        clauses,
        error_type=ValidationAgentError,
        list_error="Expected clauses to be a list of dictionaries from the Extraction Agent.",
        mapping_error=lambda index, clause: (
            f"Expected clauses[{index}] to be a mapping, "
            f"got {type(clause).__name__}."
        ),
        missing_clause_type_error=lambda index: (
            f"clauses[{index}] is missing required 'clause_type'. "
            "Provide extraction output that includes clause_type."
        ),
        missing_text_error=lambda index: (
            f"clauses[{index}] is missing required 'text'. "
            "Provide extraction output that includes clause text."
        ),
        invalid_error=lambda index, exc: (
            f"clauses[{index}] could not be validated as an ExtractedClause: {exc}"
        ),
        require_clause_type=True,
        accept_clause_text_fallback=False,
    )


def _get_context_value(context: Mapping[str, Any], keys: Iterable[str]) -> Optional[str]:
    """Return a normalized string context value for any matching key."""
    raw_value = _get_raw_context_value(context, keys)
    if raw_value is None or not _is_non_empty(raw_value):
        return None

    if isinstance(raw_value, str):
        return raw_value.strip()
    if isinstance(raw_value, Sequence) and not isinstance(raw_value, (str, bytes)):
        values = [str(item).strip() for item in raw_value if _is_non_empty(item)]
        return "; ".join(values) if values else None
    if isinstance(raw_value, Mapping):
        values = [
            f"{key}: {value}"
            for key, value in raw_value.items()
            if _is_non_empty(value)
        ]
        return "; ".join(values) if values else None
    return str(raw_value).strip()


def _get_raw_context_value(
    context: Mapping[str, Any],
    keys: Iterable[str],
) -> Optional[Any]:
    """Return the first raw context value found for a set of key aliases."""
    normalized_keys = {_normalize_key(key): key for key in context.keys()}
    for key in keys:
        actual_key = normalized_keys.get(_normalize_key(key))
        if actual_key is not None:
            return context.get(actual_key)
    return None


def _find_clause(
    clauses: Sequence[ExtractedClause],
    aliases: Sequence[str],
) -> Optional[ExtractedClause]:
    """Find the first extracted clause matching any alias."""
    matching_clauses = _find_clauses(clauses, aliases)
    return matching_clauses[0] if matching_clauses else None


def _find_clauses(
    clauses: Sequence[ExtractedClause],
    aliases: Sequence[str],
) -> list[ExtractedClause]:
    """Find all extracted clauses matching any alias."""
    return [
        clause
        for clause in clauses
        if _clause_matches_aliases(clause, aliases)
    ]


def _clause_matches_aliases(
    clause: ExtractedClause,
    aliases: Sequence[str],
) -> bool:
    """Return whether a clause type, title, or text matches any alias."""
    normalized_aliases = {_normalize_key(alias) for alias in aliases}
    clause_type = _normalize_key(clause.clause_type)
    title = _normalize_key(clause.title)
    text = _normalize_key(clause.text)
    if clause_type in normalized_aliases or title in normalized_aliases:
        return True
    return any(
        alias in clause_type or alias in title or alias in text
        for alias in normalized_aliases
    )
