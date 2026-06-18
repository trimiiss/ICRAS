"""Clause-to-obligation extraction helpers."""

import re
from typing import Any, Mapping, Optional, Sequence

from schemas.common import EvidencePointer
from schemas.extracted_clause import ExtractedClause
from schemas.obligation_result import ObligationRecord
from utils.clauses import coerce_extracted_clauses
from utils.dates import DATE_CANDIDATE_PATTERNS, normalize_date
from utils.text import (
    normalize_key as _normalize_key,
    truncate as _truncate,
)

from .constants import (
    DATE_FORMATS,
    OBLIGATION_CUES,
    OBLIGATION_TYPE_BY_CLAUSE,
    RESPONSIBLE_PARTIES,
    TIMING_PATTERNS,
)
from .errors import ObligationRegisterError


def extract_obligations(
    context: Mapping[str, Any],
    clauses: Sequence[ExtractedClause],
) -> list[ObligationRecord]:
    """Extract obligation records from clauses."""
    obligations: list[ObligationRecord] = []
    for clause in clauses:
        obligation_type = _obligation_type_for_clause(clause)
        if obligation_type is None:
            continue
        if not _has_obligation_signal(clause.text):
            continue
        if _is_negative_renewal_clause(clause):
            continue

        evidence = _clause_evidence(context, clause)
        obligation = ObligationRecord(
            obligation_id=f"OBL-{len(obligations) + 1:03d}",
            obligation_type=obligation_type,
            responsible_party=_extract_responsible_party(clause.text),
            obligation_summary=_summarize_obligation(clause.text),
            due_date=_extract_due_date(clause.text),
            timing_trigger=_extract_timing_trigger(clause.text),
            is_recurring=_is_recurring(clause.text),
            recurrence_frequency=_recurrence_frequency(clause.text, obligation_type),
            source_clause_text=_truncate(clause.text),
            source_file=evidence.source_file,
            source_page=evidence.page_number,
            evidence_id=evidence.evidence_id,
            document_id=evidence.document_id,
            clause_reference=evidence.clause_reference,
            evidence_pointer=evidence,
        )
        obligations.append(obligation)

    return obligations


def coerce_clauses(raw_clauses: Any) -> list[ExtractedClause]:
    """Convert extracted clause dictionaries to Pydantic models."""
    return coerce_extracted_clauses(
        raw_clauses,
        error_type=ObligationRegisterError,
        list_error="Expected extracted_contract['clauses'] to be a list.",
        mapping_error=lambda index, _clause: (
            f"Expected extracted_contract['clauses'][{index}] to be a mapping."
        ),
        missing_text_error=lambda index: (
            f"extracted_contract['clauses'][{index}] is missing clause text."
        ),
        invalid_error=lambda index, exc: (
            f"extracted_contract['clauses'][{index}] is invalid: {exc}"
        ),
    )


def _obligation_type_for_clause(clause: ExtractedClause) -> Optional[str]:
    """Return the obligation type for a clause, when supported."""
    candidates = (
        _normalize_key(clause.clause_type),
        _normalize_key(clause.title),
    )
    for candidate in candidates:
        for alias, obligation_type in OBLIGATION_TYPE_BY_CLAUSE.items():
            normalized_alias = _normalize_key(alias)
            if candidate == normalized_alias or normalized_alias in candidate:
                return obligation_type
    return None


def _has_obligation_signal(text: str) -> bool:
    """Return whether text contains deterministic obligation language."""
    normalized_text = text.lower()
    return any(re.search(rf"\b{re.escape(cue)}\b", normalized_text) for cue in OBLIGATION_CUES)


def _is_negative_renewal_clause(clause: ExtractedClause) -> bool:
    """Skip renewal clauses that explicitly say there is no renewal."""
    if _obligation_type_for_clause(clause) != "renewal":
        return False
    return bool(
        re.search(
            r"\b(?:does not|will not|shall not|must not)\s+auto[- ]?renew\b",
            clause.text,
            re.IGNORECASE,
        )
    )


def _extract_responsible_party(text: str) -> str:
    """Extract the responsible party from obligation text."""
    for party in RESPONSIBLE_PARTIES:
        if re.search(rf"\b{re.escape(party)}\b", text, re.IGNORECASE):
            return party
    return "Unspecified"


def _summarize_obligation(text: str) -> str:
    """Return a compact sentence-level obligation summary."""
    normalized = " ".join(text.split())
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", normalized)
        if sentence.strip()
    ]
    for sentence in sentences:
        if _has_obligation_signal(sentence):
            return _truncate(sentence, max_chars=240)
    return _truncate(normalized, max_chars=240)


def _extract_due_date(text: str) -> Optional[str]:
    """Extract an absolute due date as ISO 8601 when present."""
    for pattern in DATE_CANDIDATE_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match is None:
            continue
        normalized = normalize_date(match.group(0), date_formats=DATE_FORMATS)
        if normalized is not None:
            return normalized
    return None


def _extract_timing_trigger(text: str) -> Optional[str]:
    """Extract relative timing language for obligation tracking."""
    normalized_text = " ".join(text.split())
    for pattern in TIMING_PATTERNS:
        match = re.search(pattern, normalized_text, re.IGNORECASE)
        if match is not None:
            return match.group(0)
    return None


def _is_recurring(text: str) -> bool:
    """Return whether obligation text indicates recurrence."""
    return _recurrence_frequency(text, obligation_type=None) is not None


def _recurrence_frequency(
    text: str,
    obligation_type: Optional[str],
) -> Optional[str]:
    """Return a recurring cadence when detectable."""
    normalized = text.lower()
    if re.search(r"\bmonthly\b|\beach month\b", normalized):
        return "monthly"
    if re.search(r"\bannually\b|\beach year\b|\byearly\b", normalized):
        return "annually"
    if "automatic renewal" in normalized or "auto-renew" in normalized:
        return "annually"
    if obligation_type == "payment" and re.search(r"\binvoice|invoices\b", normalized):
        return "per invoice"
    return None


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

