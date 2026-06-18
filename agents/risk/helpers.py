"""Shared risk scoring helper functions."""

import re
from typing import Any, Iterable, Mapping, Optional, Sequence

from schemas.common import EvidencePointer, Severity
from schemas.extracted_clause import ExtractedClause
from schemas.finding import Finding
from schemas.risk_result import ClauseRisk
from agents.risk.constants import CLAUSE_ALIASES, KNOWN_JURISDICTIONS, SEVERITY_RANK
from agents.risk.errors import RiskAgentError
from utils.clauses import coerce_extracted_clauses
from utils.text import (
    is_non_empty as _is_non_empty,
    normalize_key as _normalize_key,
    truncate as _truncate,
)

def _coerce_clauses(raw_clauses: Any) -> list[ExtractedClause]:
    """Validate extracted clause dictionaries."""
    return coerce_extracted_clauses(
        raw_clauses,
        error_type=RiskAgentError,
        list_error="extracted_contract['clauses'] must be a list.",
        mapping_error=lambda index, _clause: (
            f"extracted_contract['clauses'][{index}] must be a mapping."
        ),
        missing_text_error=lambda index: (
            f"extracted_contract['clauses'][{index}] is missing text."
        ),
        invalid_error=lambda index, exc: (
            f"extracted_contract['clauses'][{index}] is invalid: {exc}"
        ),
    )


def _coerce_findings(raw_findings: Any) -> list[Finding]:
    """Validate shared finding dictionaries."""
    if not raw_findings:
        return []
    if not isinstance(raw_findings, list):
        raise RiskAgentError("validation findings must be a list.")
    findings: list[Finding] = []
    for index, raw_finding in enumerate(raw_findings):
        if not isinstance(raw_finding, Mapping):
            continue
        try:
            findings.append(Finding.model_validate(raw_finding))
        except Exception as exc:
            raise RiskAgentError(f"finding[{index}] is invalid: {exc}") from exc
    return findings


def _find_clauses(
    clauses: Sequence[ExtractedClause],
    aliases: Sequence[str],
) -> list[ExtractedClause]:
    """Return clauses matching any alias."""
    return [clause for clause in clauses if _clause_matches(clause, aliases)]


def _first_clause(
    clauses: Sequence[ExtractedClause],
    aliases: Sequence[str],
) -> Optional[ExtractedClause]:
    """Return first clause matching aliases."""
    matches = _find_clauses(clauses, aliases)
    return matches[0] if matches else None


def _clause_matches(clause: ExtractedClause, aliases: Sequence[str]) -> bool:
    """Return whether a clause matches any canonical alias."""
    normalized_aliases = {_normalize_key(alias) for alias in aliases}
    clause_type = _normalize_key(clause.clause_type)
    title = _normalize_key(clause.title)
    text = _normalize_key(clause.text)
    return any(
        alias == clause_type
        or alias == title
        or alias in clause_type
        or alias in title
        or alias in text
        for alias in normalized_aliases
    )


def _aliases_for_clause_type(clause_type: str) -> tuple[str, ...]:
    """Return aliases for a playbook clause type."""
    normalized = _normalize_key(clause_type)
    configured = CLAUSE_ALIASES.get(normalized)
    if configured is not None:
        return configured
    return (normalized,)


def _extract_jurisdictions(text: str) -> list[str]:
    """Extract known jurisdiction names from text."""
    return [
        jurisdiction
        for jurisdiction in KNOWN_JURISDICTIONS
        if _contains_word(text, jurisdiction)
    ]


def _extract_party_names(
    context: Mapping[str, Any],
    clauses: Sequence[ExtractedClause],
) -> list[str]:
    """Extract likely party names from context and party clauses."""
    parties: list[str] = []
    raw = context.get("party_names") or context.get("parties")
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        parties.extend(str(item).strip() for item in raw if _is_non_empty(item))
    elif isinstance(raw, Mapping):
        parties.extend(str(item).strip() for item in raw.values() if _is_non_empty(item))
    elif _is_non_empty(raw):
        parties.extend(_split_party_text(str(raw)))
    party_clause = _first_clause(clauses, CLAUSE_ALIASES["party_names"])
    if party_clause is not None:
        parties.extend(_split_party_text(party_clause.text))

    unique: list[str] = []
    for party in parties:
        cleaned = party.strip(" .;:")
        if len(cleaned) < 3:
            continue
        if cleaned.lower() not in {existing.lower() for existing in unique}:
            unique.append(cleaned)
    return unique


def _split_party_text(text: str) -> list[str]:
    """Split common party-list phrasing into names."""
    match = re.search(
        r"(?:between|among)\s+(.+?)(?:\.|,?\s+each\s+a\s+|,?\s+collectively\s+)",
        text,
        re.IGNORECASE,
    )
    party_text = match.group(1) if match is not None else text
    party_text = re.sub(r"\s+\([^)]*\)", "", party_text)
    return [
        item.strip()
        for item in re.split(r"\s*,\s*|\s+and\s+|\s+&\s+", party_text)
        if item.strip()
    ]


def _risk_tolerance_thresholds(context: Mapping[str, Any]) -> dict[str, float]:
    """Return configured or default tolerance thresholds."""
    policy = _as_mapping(context.get("approval_policy"))
    thresholds = _as_mapping(policy.get("risk_tolerance_thresholds"))
    return {
        "minor_missing_required_ratio": _float_or_default(
            thresholds.get("minor_missing_required_ratio"),
            0.25,
        ),
        "material_missing_required_ratio": _float_or_default(
            thresholds.get("material_missing_required_ratio"),
            0.50,
        ),
    }


def _minor_variance_severity(configured_severity: Severity) -> Severity:
    """Downgrade a missing-clause issue when tolerance classifies it as minor."""
    if configured_severity == Severity.CRITICAL:
        return Severity.HIGH
    if configured_severity == Severity.HIGH:
        return Severity.MEDIUM
    if configured_severity == Severity.MEDIUM:
        return Severity.LOW
    return Severity.LOW


def _overall_severity(severities: Iterable[Severity]) -> Severity:
    """Return highest severity, defaulting to LOW."""
    severity_list = list(severities)
    if not severity_list:
        return Severity.LOW
    return max(severity_list, key=lambda severity: SEVERITY_RANK[severity])


def _summary(overall_severity: Severity, risks: Sequence[ClauseRisk]) -> str:
    """Build a concise risk summary."""
    if not risks:
        return "No clause-level risks were detected."
    high_or_critical = sum(
        1 for risk in risks if risk.severity in {Severity.HIGH, Severity.CRITICAL}
    )
    return (
        f"Risk assessment identified {len(risks)} clause-level risk(s); "
        f"{high_or_critical} require legal review. Overall severity is "
        f"{overall_severity.value}."
    )

def _clause_evidence(
    context: Mapping[str, Any],
    clause: ExtractedClause,
) -> EvidencePointer:
    """Build a source pointer from an extracted clause."""
    return EvidencePointer(
        evidence_id=clause.evidence.evidence_id,
        document_id=clause.evidence.document_id,
        source_file=str(context.get("contract_file") or clause.evidence.source_file),
        page_number=clause.page_number or clause.evidence.page_number,
        clause_reference=clause.section_reference or clause.evidence.clause_reference,
        excerpt=_truncate(clause.text),
    )


def _primary_evidence(evidence: Sequence[EvidencePointer]) -> Optional[EvidencePointer]:
    """Return first evidence pointer when present."""
    return evidence[0] if evidence else None


def _as_mapping(value: Any) -> Mapping[str, Any]:
    """Return value if it is a mapping, else an empty mapping."""
    return value if isinstance(value, Mapping) else {}


def _severity_from_value(value: Any, default: Severity) -> Severity:
    """Parse a Severity value."""
    try:
        return Severity(str(value).upper())
    except ValueError:
        return default


def _contains_word(text: str, needle: str) -> bool:
    """Case-insensitive word-ish containment for jurisdiction names."""
    if not text or not needle:
        return False
    return bool(re.search(rf"\b{re.escape(needle)}\b", text, re.IGNORECASE))


def _float_or_default(value: Any, default: float) -> float:
    """Return a float threshold with fallback."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
