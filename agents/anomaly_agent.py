"""Anomaly Agent for conflicting and unusual contract terms."""

from dataclasses import dataclass
from datetime import date
import re
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from schemas.anomaly_result import AnomalyResult
from schemas.common import EvidencePointer, Severity
from schemas.extracted_clause import ExtractedClause
from schemas.finding import Finding
from utils.artifacts import validate_run_dir, write_model_json
from utils.clauses import coerce_extracted_clauses
from utils.dates import extract_normalized_date, normalize_date
from utils.payment_terms import extract_payment_days
from utils.text import (
    is_non_empty as _is_non_empty,
    normalize_key as _normalize_key,
    optional_int as _optional_int,
    optional_str as _optional_str,
    truncate as _truncate,
)
from utils.run_manager import append_audit_event


class AnomalyAgentError(Exception):
    """Raised when anomaly review cannot complete."""


@dataclass(frozen=True)
class DateObservation:
    """One normalized date plus the evidence that supports it."""

    field_name: str
    normalized_date: str
    evidence: EvidencePointer
    source_text: str


@dataclass(frozen=True)
class UnusualPattern:
    """A high-signal unusual contract pattern."""

    pattern_id: str
    field_name: str
    title: str
    description: str
    regex: str
    severity: Severity
    recommendation: str


CHECKED_RULES: list[str] = [
    "conflicting_governing_law",
    "contradictory_payment_terms",
    "duplicate_clause_value_conflict",
    "suspicious_date_ordering",
    "unusual_contract_pattern",
]

KNOWN_GOVERNING_LAW_JURISDICTIONS: tuple[str, ...] = (
    "New York",
    "Delaware",
    "California",
    "Texas",
    "Florida",
    "England and Wales",
    "United Kingdom",
    "Germany",
    "France",
    "India",
    "Singapore",
    "Netherlands",
    "Ireland",
)

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "governing_law": (
        "governing_law",
        "governing law",
        "jurisdiction",
        "choice_of_law",
        "choice of law",
    ),
    "payment_terms": (
        "payment_terms",
        "payment terms",
        "payment",
        "fees",
        "compensation",
        "invoice",
        "invoicing",
        "billing",
    ),
    "effective_date": (
        "effective_date",
        "effective date",
        "commencement_date",
        "start_date",
        "agreement_date",
    ),
    "expiry_date": (
        "expiry_date",
        "expiry date",
        "expiration_date",
        "end_date",
        "contract_end_date",
        "term_expiry",
    ),
    "signature_date": (
        "signature",
        "signatures",
        "execution",
        "signed",
        "signature_date",
    ),
    "liability_cap": (
        "liability_cap",
        "limitation_of_liability",
        "limited_liability",
        "liability_limit",
    ),
    "auto_renewal": (
        "auto_renewal",
        "automatic renewal",
        "auto renewal",
        "renewal",
    ),
    "termination_terms": (
        "termination_terms",
        "termination",
        "term_and_termination",
        "term_and_duration",
    ),
    "confidentiality": ("confidentiality", "confidential information"),
    "data_protection": ("data_protection", "privacy", "personal data"),
    "indemnity": ("indemnity", "indemnification"),
}

DEDICATED_CONFLICT_FIELDS: set[str] = {"governing_law", "payment_terms"}

UNUSUAL_PATTERNS: tuple[UnusualPattern, ...] = (
    UnusualPattern(
        pattern_id="unlimited_liability",
        field_name="liability_cap",
        title="Unusual liability exposure",
        description="The contract appears to include unlimited or uncapped liability.",
        regex=r"\b(?:unlimited|uncapped)\s+liability\b|\bliability\s+(?:is\s+)?(?:unlimited|uncapped)\b",
        severity=Severity.HIGH,
        recommendation="Legal must confirm whether unlimited liability is intended.",
    ),
    UnusualPattern(
        pattern_id="indefinite_auto_renewal",
        field_name="auto_renewal",
        title="Unusual indefinite auto-renewal",
        description="The contract appears to auto-renew indefinitely or perpetually.",
        regex=r"\bauto(?:matically)?[-\s]?renew\w*\b.{0,100}\b(?:indefinitely|perpetual|forever)\b|\b(?:indefinitely|perpetual|forever)\b.{0,100}\bauto(?:matically)?[-\s]?renew\w*\b",
        severity=Severity.HIGH,
        recommendation="Legal must confirm the renewal term and opt-out mechanics.",
    ),
    UnusualPattern(
        pattern_id="unilateral_amendment",
        field_name="amendment",
        title="Unusual unilateral amendment right",
        description="One party may be able to amend the agreement unilaterally.",
        regex=r"\bmay\s+amend\b.{0,80}\b(?:without\s+notice|sole\s+discretion|unilaterally)\b|\bunilateral(?:ly)?\s+amend",
        severity=Severity.HIGH,
        recommendation="Legal must confirm amendment rights and notice requirements.",
    ),
    UnusualPattern(
        pattern_id="no_termination_right",
        field_name="termination_terms",
        title="Unusual termination restriction",
        description="The contract appears to restrict ordinary termination rights.",
        regex=r"\bmay\s+not\s+terminate\b|\bno\s+(?:right\s+to\s+)?terminate\b",
        severity=Severity.MEDIUM,
        recommendation="Legal must confirm whether the termination restriction is acceptable.",
    ),
    UnusualPattern(
        pattern_id="non_compete",
        field_name="non_compete",
        title="Unusual non-compete language",
        description="The contract contains non-compete language that may be unusual for the agreement type.",
        regex=r"\bnon[-\s]?compete\b|\bshall\s+not\s+compete\b",
        severity=Severity.HIGH,
        recommendation="Legal must review enforceability and business impact.",
    ),
)


def run_anomaly_review(
    context: Mapping[str, Any],
    extracted_contract: Mapping[str, Any],
    run_dir: str | Path | None = None,
    evidence_index: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run deterministic anomaly checks over extracted contract clauses."""
    run_path = (
        validate_run_dir(
            run_dir,
            error_type=AnomalyAgentError,
            before_action="running anomaly review",
        )
        if run_dir is not None
        else None
    )
    clauses = _coerce_clauses(extracted_contract.get("clauses", []))
    evidence_records = _extract_evidence_records(evidence_index)
    run_id = str(
        context.get("run_id")
        or extracted_contract.get("run_id")
        or "unknown-run"
    )

    findings: list[Finding] = []
    _detect_conflicting_governing_law(context, clauses, findings)
    _detect_contradictory_payment_terms(context, clauses, findings)
    _detect_duplicate_clause_value_conflicts(context, clauses, findings)
    _detect_suspicious_date_combinations(
        context=context,
        clauses=clauses,
        evidence_records=evidence_records,
        findings=findings,
    )
    _detect_unusual_contract_patterns(context, clauses, findings)

    result = AnomalyResult(
        run_id=run_id,
        findings=findings,
        checked_rules=CHECKED_RULES,
        requires_legal_review=any(
            finding.manual_review_required
            or finding.severity in {Severity.HIGH, Severity.CRITICAL}
            for finding in findings
        ),
    )

    artifact_paths: dict[str, str] = {}
    if run_path is not None:
        output_path = run_path / "anomaly_findings.json"
        write_model_json(
            output_path,
            result,
            error_type=AnomalyAgentError,
            failure_message="Failed to write anomaly artifact '{path}': {exc}",
        )
        append_audit_event(
            run_path,
            {
                "event": "anomaly_review_completed",
                "agent": "anomaly_agent",
                "message": "Anomaly Agent checked conflicting and unusual contract terms.",
                "artifacts": [output_path.name],
                "finding_count": len(findings),
                "checked_rules": CHECKED_RULES,
                "requires_legal_review": result.requires_legal_review,
            },
        )
        artifact_paths["anomaly_findings"] = str(output_path)

    serialized = result.model_dump(mode="json")
    return {
        "anomaly_result": serialized,
        "findings": serialized["findings"],
        "artifact_paths": artifact_paths,
    }


def _detect_conflicting_governing_law(
    context: Mapping[str, Any],
    clauses: Sequence[ExtractedClause],
    findings: list[Finding],
) -> None:
    """Detect multiple governing-law clauses naming different jurisdictions."""
    law_to_clauses: dict[str, list[ExtractedClause]] = {}
    for clause in _find_clauses(clauses, FIELD_ALIASES["governing_law"]):
        law = _extract_governing_law(clause.text)
        if law is None:
            continue
        law_to_clauses.setdefault(law, []).append(clause)

    if len(law_to_clauses) <= 1:
        return

    evidence_clauses = [
        clauses_for_law[0]
        for _law, clauses_for_law in sorted(law_to_clauses.items())
    ]
    evidence = [_clause_evidence(context, clause) for clause in evidence_clauses]
    jurisdictions = ", ".join(sorted(law_to_clauses))
    findings.append(
        _make_finding(
            findings=findings,
            category="contract_anomaly",
            title="Conflicting governing law clauses",
            description=(
                "Multiple governing-law jurisdictions were detected: "
                f"{jurisdictions}."
            ),
            severity=Severity.HIGH,
            confidence=1.0,
            evidence=evidence,
            recommendation="Legal must resolve the governing-law conflict before approval.",
            field_name="governing_law",
            issue_type="conflicting_governing_law",
            source_clause_text=" | ".join(item.excerpt or "" for item in evidence),
        )
    )


def _detect_contradictory_payment_terms(
    context: Mapping[str, Any],
    clauses: Sequence[ExtractedClause],
    findings: list[Finding],
) -> None:
    """Detect payment clauses that name contradictory payment timing."""
    term_to_clauses: dict[str, list[ExtractedClause]] = {}
    for clause in _find_clauses(clauses, FIELD_ALIASES["payment_terms"]):
        for term in _extract_payment_term_values(clause.text):
            term_to_clauses.setdefault(term, []).append(clause)

    if len(term_to_clauses) <= 1:
        return

    evidence_clauses = [
        clauses_for_term[0]
        for _term, clauses_for_term in sorted(term_to_clauses.items())
    ]
    evidence = [_clause_evidence(context, clause) for clause in evidence_clauses]
    terms = ", ".join(sorted(term_to_clauses))
    findings.append(
        _make_finding(
            findings=findings,
            category="contract_anomaly",
            title="Contradictory payment terms",
            description=f"Payment clauses contain contradictory timing values: {terms}.",
            severity=Severity.HIGH,
            confidence=1.0,
            evidence=evidence,
            recommendation="Finance must confirm the intended payment timing.",
            field_name="payment_terms",
            issue_type="contradictory_payment_terms",
            source_clause_text=" | ".join(item.excerpt or "" for item in evidence),
        )
    )


def _detect_duplicate_clause_value_conflicts(
    context: Mapping[str, Any],
    clauses: Sequence[ExtractedClause],
    findings: list[Finding],
) -> None:
    """Detect duplicate clause families that carry different extracted values."""
    grouped_clauses: dict[str, list[ExtractedClause]] = {}
    for clause in clauses:
        field_name = _canonical_field_name(clause)
        if field_name is None or field_name in DEDICATED_CONFLICT_FIELDS:
            continue
        grouped_clauses.setdefault(field_name, []).append(clause)

    for field_name, field_clauses in sorted(grouped_clauses.items()):
        if len(field_clauses) <= 1:
            continue
        value_to_clauses: dict[str, list[ExtractedClause]] = {}
        for clause in field_clauses:
            value = _extract_comparable_clause_value(field_name, clause.text)
            if value is None:
                continue
            value_to_clauses.setdefault(value, []).append(clause)
        if len(value_to_clauses) <= 1:
            continue

        evidence_clauses = [
            clauses_for_value[0]
            for _value, clauses_for_value in sorted(value_to_clauses.items())
        ]
        evidence = [_clause_evidence(context, clause) for clause in evidence_clauses]
        values = ", ".join(sorted(value_to_clauses))
        findings.append(
            _make_finding(
                findings=findings,
                category="contract_anomaly",
                title="Duplicate clauses contain different values",
                description=(
                    f"Duplicate {field_name.replace('_', ' ')} clauses contain "
                    f"different values: {values}."
                ),
                severity=Severity.HIGH,
                confidence=0.95,
                evidence=evidence,
                recommendation="Legal must confirm which duplicate clause value controls.",
                field_name=field_name,
                issue_type="duplicate_clause_value_conflict",
                source_clause_text=" | ".join(item.excerpt or "" for item in evidence),
            )
        )


def _detect_suspicious_date_combinations(
    context: Mapping[str, Any],
    clauses: Sequence[ExtractedClause],
    evidence_records: Sequence[Mapping[str, Any]],
    findings: list[Finding],
) -> None:
    """Detect date combinations that are internally suspicious."""
    observations = _collect_date_observations(context, clauses, evidence_records)
    effective_dates = observations.get("effective_date", [])
    expiry_dates = observations.get("expiry_date", [])
    signature_dates = observations.get("signature_date", [])

    for effective in effective_dates[:1]:
        for expiry in expiry_dates[:1]:
            if _date_value(expiry.normalized_date) >= _date_value(effective.normalized_date):
                continue
            findings.append(
                _make_finding(
                    findings=findings,
                    category="contract_anomaly",
                    title="Suspicious date ordering",
                    description=(
                        "The contract expiry date appears before the effective date: "
                        f"expiry_date={expiry.normalized_date}, "
                        f"effective_date={effective.normalized_date}."
                    ),
                    severity=Severity.HIGH,
                    confidence=1.0,
                    evidence=[effective.evidence, expiry.evidence],
                    recommendation="Legal must correct or confirm the contract dates.",
                    field_name="expiry_date",
                    issue_type="suspicious_date_ordering",
                    source_clause_text=f"{effective.source_text} | {expiry.source_text}",
                )
            )
            return

    if not expiry_dates:
        return
    expiry = expiry_dates[0]
    for signature in signature_dates[:1]:
        if _date_value(signature.normalized_date) <= _date_value(expiry.normalized_date):
            continue
        findings.append(
            _make_finding(
                findings=findings,
                category="contract_anomaly",
                title="Suspicious signature date",
                description=(
                    "The signature date appears after the contract expiry date: "
                    f"signature_date={signature.normalized_date}, "
                    f"expiry_date={expiry.normalized_date}."
                ),
                severity=Severity.MEDIUM,
                confidence=0.95,
                evidence=[signature.evidence, expiry.evidence],
                recommendation="Legal must confirm the execution and expiry dates.",
                field_name="signature_date",
                issue_type="suspicious_date_ordering",
                source_clause_text=f"{signature.source_text} | {expiry.source_text}",
            )
        )
        return


def _detect_unusual_contract_patterns(
    context: Mapping[str, Any],
    clauses: Sequence[ExtractedClause],
    findings: list[Finding],
) -> None:
    """Flag high-signal unusual contract language."""
    for clause in clauses:
        text = " ".join(clause.text.split())
        for pattern in UNUSUAL_PATTERNS:
            if pattern.pattern_id == "indefinite_auto_renewal" and _negates_auto_renewal(text):
                continue
            if re.search(pattern.regex, text, flags=re.IGNORECASE) is None:
                continue
            evidence = [_clause_evidence(context, clause)]
            findings.append(
                _make_finding(
                    findings=findings,
                    category="contract_anomaly",
                    title=pattern.title,
                    description=pattern.description,
                    severity=pattern.severity,
                    confidence=0.9,
                    evidence=evidence,
                    recommendation=pattern.recommendation,
                    field_name=pattern.field_name,
                    issue_type="unusual_contract_pattern",
                    source_clause_text=evidence[0].excerpt,
                )
            )


def _coerce_clauses(raw_clauses: Any) -> list[ExtractedClause]:
    """Validate extracted clause dictionaries for anomaly checks."""
    return coerce_extracted_clauses(
        raw_clauses,
        error_type=AnomalyAgentError,
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


def _find_clauses(
    clauses: Sequence[ExtractedClause],
    aliases: Sequence[str],
) -> list[ExtractedClause]:
    """Return clauses whose type, title, or text matches any alias."""
    normalized_aliases = {
        _normalize_key(alias)
        for alias in aliases
        if str(alias).strip()
    }
    matches: list[ExtractedClause] = []
    for clause in clauses:
        haystack = _normalize_key(
            " ".join([clause.clause_type, clause.title, clause.text])
        )
        if any(alias in haystack for alias in normalized_aliases):
            matches.append(clause)
    return matches


def _extract_governing_law(text: str) -> Optional[str]:
    """Extract a comparable governing-law value from clause text."""
    compact_text = " ".join(text.split())
    for jurisdiction in KNOWN_GOVERNING_LAW_JURISDICTIONS:
        if re.search(rf"\b{re.escape(jurisdiction)}\b", compact_text, re.IGNORECASE):
            return _normalize_governing_law(jurisdiction)

    patterns = (
        r"laws?\s+of\s+(?:the\s+state\s+of\s+)?([A-Z][A-Za-z .&-]+?)(?:,|\.|;|\s+without\s+|\s+and\s+|$)",
        r"governed\s+by\s+(?:the\s+)?(?:laws?\s+of\s+)?([A-Z][A-Za-z .&-]+?)(?:\s+law|,|\.|;|$)",
        r"jurisdiction\s+of\s+([A-Z][A-Za-z .&-]+?)(?:,|\.|;|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, compact_text)
        if match is not None:
            return _normalize_governing_law(match.group(1))
    return None


def _normalize_governing_law(value: str) -> str:
    """Return a compact comparable governing-law value."""
    cleaned = re.sub(
        r"\b(usa|u\.s\.a\.|united states|state of|laws of|law|the)\b",
        "",
        value,
        flags=re.IGNORECASE,
    )
    return " ".join(cleaned.replace(",", " ").split()).lower()


def _extract_payment_term_values(text: str) -> list[str]:
    """Return comparable payment timing values from text."""
    values = [f"net-{days}" for days in extract_payment_days(text)]
    compact = " ".join(text.split()).lower()
    if re.search(r"\b(?:due\s+upon\s+receipt|payable\s+immediately|immediate\s+payment)\b", compact):
        values.append("due-on-receipt")
    if re.search(r"\b(?:prepaid|payment\s+in\s+advance|paid\s+in\s+advance)\b", compact):
        values.append("prepaid")
    return _ordered_unique(values)


def _canonical_field_name(clause: ExtractedClause) -> Optional[str]:
    """Return the canonical field family for a clause, if one is known."""
    haystack = _normalize_key(" ".join([clause.clause_type, clause.title]))
    text_key = _normalize_key(clause.text)
    for field_name, aliases in FIELD_ALIASES.items():
        normalized_aliases = {_normalize_key(alias) for alias in aliases}
        if any(
            alias == haystack
            or alias in haystack
            or (field_name in {"governing_law", "payment_terms"} and alias in text_key)
            for alias in normalized_aliases
        ):
            return field_name
    return None


def _extract_comparable_clause_value(
    field_name: str,
    text: str,
) -> Optional[str]:
    """Extract a stable comparable value for duplicate-clause checks."""
    if field_name in {"effective_date", "expiry_date", "signature_date"}:
        return extract_normalized_date(text)
    if field_name == "liability_cap":
        return _extract_liability_cap_value(text)
    if field_name == "auto_renewal":
        return _extract_auto_renewal_value(text)
    if field_name == "termination_terms":
        return _extract_notice_period_value(text)
    if field_name in {"confidentiality", "data_protection", "indemnity"}:
        return _extract_clause_standard_value(text)
    return None


def _extract_liability_cap_value(text: str) -> Optional[str]:
    """Extract a comparable liability-cap value."""
    compact = " ".join(text.split()).lower()
    if re.search(r"\b(?:unlimited|uncapped)\s+liability\b|\bliability\s+(?:is\s+)?(?:unlimited|uncapped)\b", compact):
        return "unlimited"
    fees_match = re.search(r"\bfees?\s+paid\s+(?:in|during|over)\s+(?:the\s+)?(?:last\s+)?(\d{1,2})\s+months?\b", compact)
    if fees_match is not None:
        return f"fees_paid_{int(fees_match.group(1))}_months"
    amount_match = re.search(
        r"(?:\$|usd\s*)\s*(\d[\d,]*(?:\.\d+)?)|(\d[\d,]*(?:\.\d+)?)\s*(?:usd|dollars?)\b",
        compact,
        flags=re.IGNORECASE,
    )
    if amount_match is None:
        return None
    raw_amount = amount_match.group(1) or amount_match.group(2)
    return f"amount_{raw_amount.replace(',', '')}"


def _extract_auto_renewal_value(text: str) -> Optional[str]:
    """Extract whether auto-renewal is present or negated."""
    compact = " ".join(text.split()).lower()
    if _negates_auto_renewal(compact):
        return "no_auto_renewal"
    if re.search(r"\bauto(?:matically)?[-\s]?renew\w*\b|\bautomatic\s+renewal\b", compact):
        return "auto_renewal"
    return None


def _extract_notice_period_value(text: str) -> Optional[str]:
    """Extract a termination notice period value."""
    compact = " ".join(text.split()).lower()
    match = re.search(r"\b(\d{1,3})\s+days?\s+(?:prior\s+)?(?:written\s+)?notice\b", compact)
    if match is not None:
        return f"notice_{int(match.group(1))}_days"
    if re.search(r"\bmay\s+not\s+terminate\b|\bno\s+(?:right\s+to\s+)?terminate\b", compact):
        return "no_termination"
    return None


def _extract_clause_standard_value(text: str) -> Optional[str]:
    """Extract high-signal boolean-like values for common legal clauses."""
    compact = " ".join(text.split()).lower()
    if "unlimited" in compact or "uncapped" in compact:
        return "unlimited"
    if "shall not" in compact or "prohibited" in compact:
        return "restrictive"
    if "may" in compact and "without consent" in compact:
        return "permissive_without_consent"
    return None


def _collect_date_observations(
    context: Mapping[str, Any],
    clauses: Sequence[ExtractedClause],
    evidence_records: Sequence[Mapping[str, Any]],
) -> dict[str, list[DateObservation]]:
    """Collect date observations from context and clauses."""
    observations: dict[str, list[DateObservation]] = {
        "effective_date": [],
        "expiry_date": [],
        "signature_date": [],
    }

    for field_name in observations:
        for clause in _find_clauses(clauses, FIELD_ALIASES[field_name]):
            normalized_date = extract_normalized_date(clause.text)
            if normalized_date is None:
                continue
            observations[field_name].append(
                DateObservation(
                    field_name=field_name,
                    normalized_date=normalized_date,
                    evidence=_clause_evidence(context, clause),
                    source_text=_truncate(clause.text),
                )
            )

    context_aliases = {
        "effective_date": ("effective_date", "contract_effective_date", "agreement_date"),
        "expiry_date": ("expiry_date", "expiration_date", "end_date", "contract_end_date"),
        "signature_date": ("signature_date", "execution_date", "signed_date"),
    }
    for field_name, aliases in context_aliases.items():
        if observations[field_name]:
            continue
        raw_value = _get_context_value(context, aliases)
        normalized_date = normalize_date(raw_value)
        if normalized_date is None:
            continue
        observations[field_name].append(
            DateObservation(
                field_name=field_name,
                normalized_date=normalized_date,
                evidence=_context_or_fallback_evidence(
                    context=context,
                    evidence_records=evidence_records,
                    field_name=field_name,
                    raw_value=raw_value,
                ),
                source_text=f"{field_name}: {raw_value}",
            )
        )

    return observations


def _date_value(value: str) -> date:
    """Convert an ISO date string to a date object."""
    return date.fromisoformat(value)


def _clause_evidence(
    context: Mapping[str, Any],
    clause: ExtractedClause,
) -> EvidencePointer:
    """Build an evidence pointer from an extracted clause."""
    return EvidencePointer(
        evidence_id=clause.evidence.evidence_id,
        document_id=clause.evidence.document_id,
        source_file=str(context.get("contract_file") or clause.evidence.source_file),
        page_number=clause.page_number or clause.evidence.page_number,
        clause_reference=clause.section_reference or clause.evidence.clause_reference,
        excerpt=_truncate(clause.text),
    )


def _context_or_fallback_evidence(
    context: Mapping[str, Any],
    evidence_records: Sequence[Mapping[str, Any]],
    field_name: str,
    raw_value: Any,
) -> EvidencePointer:
    """Build context-backed evidence with a source fallback."""
    for record in evidence_records:
        source_file = record.get("source_file")
        if not _is_non_empty(source_file):
            continue
        return EvidencePointer(
            evidence_id=_optional_str(record.get("evidence_id")),
            document_id=_optional_str(record.get("document_id")),
            source_file=str(source_file),
            page_number=_optional_int(record.get("page_number")),
            clause_reference=_optional_str(record.get("section_reference")),
            excerpt=f"{field_name}: {raw_value}",
        )
    return EvidencePointer(
        source_file=str(context.get("contract_file") or "context_packet.json"),
        excerpt=f"{field_name}: {raw_value}",
    )


def _make_finding(
    findings: Sequence[Finding],
    category: str,
    title: str,
    description: str,
    severity: Severity,
    confidence: float,
    evidence: Sequence[EvidencePointer],
    recommendation: str,
    field_name: str,
    issue_type: str,
    source_clause_text: str | None,
) -> Finding:
    """Create a shared anomaly finding."""
    finding_evidence = list(evidence)
    primary_evidence = finding_evidence[0]
    return Finding(
        finding_id=f"ANM-{len(findings) + 1:03d}",
        category=category,
        title=title,
        description=description,
        severity=severity,
        confidence=confidence,
        evidence=finding_evidence,
        recommendation=recommendation,
        field_name=field_name,
        issue_type=issue_type,
        message=description,
        source_clause_text=source_clause_text or primary_evidence.excerpt,
        source_page=primary_evidence.page_number,
        evidence_pointer=primary_evidence,
        manual_review_required=True,
        risk_engine_ready=True,
    )


def _extract_evidence_records(
    evidence_index: Mapping[str, Any] | None,
) -> list[Mapping[str, Any]]:
    """Read evidence records from accepted evidence index shapes."""
    if evidence_index is None:
        return []

    candidate: Any = evidence_index
    if "evidence_index" in evidence_index:
        candidate = evidence_index["evidence_index"]

    if not isinstance(candidate, Mapping):
        return []

    records = candidate.get("records")
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, Mapping)]


def _get_context_value(
    context: Mapping[str, Any],
    aliases: Sequence[str],
) -> Optional[Any]:
    """Return the first context value matching a normalized alias."""
    normalized_context_keys = {_normalize_key(key): key for key in context}
    for alias in aliases:
        key = normalized_context_keys.get(_normalize_key(alias))
        if key is not None:
            value = context.get(key)
            if _is_non_empty(value):
                return value
    return None


def _negates_auto_renewal(text: str) -> bool:
    """Return whether text negates auto-renewal."""
    compact = " ".join(text.split()).lower()
    return bool(
        re.search(
            r"\b(?:does\s+not|will\s+not|shall\s+not|no)\s+auto(?:matically)?[-\s]?renew\w*\b|\bno\s+automatic\s+renewal\b",
            compact,
        )
    )


def _ordered_unique(values: Sequence[str]) -> list[str]:
    """Return unique values in first-seen order."""
    unique_values: list[str] = []
    for value in values:
        if value not in unique_values:
            unique_values.append(value)
    return unique_values


__all__ = [
    "AnomalyAgentError",
    "run_anomaly_review",
]
