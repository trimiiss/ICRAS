"""Cross-field and deep-audit validation rules."""

from typing import Any, Mapping, Sequence

from schemas.common import EvidencePointer, Severity
from schemas.extracted_clause import ExtractedClause
from schemas.finding import Finding
from agents.validation.analysis_helpers import (
    _contains_numeric_calculation,
    _detect_calculation_error,
    _extract_governing_law,
    _extract_party_names,
)
from agents.validation.constants import FIELD_ALIASES
from agents.validation.core_helpers import (
    _clause_matches_aliases,
    _find_clause,
    _find_clauses,
    _get_raw_context_value,
)
from agents.validation.evidence_helpers import (
    _clause_evidence,
    _evidence_text,
    _fallback_evidence,
    _field_evidence,
)
from agents.validation.findings import _make_finding
from agents.validation.policy_helpers import (
    _manual_review_confidence_threshold,
)
from utils.dates import (
    extract_normalized_date as _extract_normalized_date,
    normalize_date as _normalize_date,
)
from utils.text import truncate as _truncate


def _validate_suspicious_date_ordering(
    context: Mapping[str, Any],
    clauses: Sequence[ExtractedClause],
    evidence_records: Sequence[Mapping[str, Any]],
    normalized_fields: dict[str, str],
    findings: list[Finding],
) -> None:
    """Detect expiry or end dates that occur before the effective date."""
    effective_date = normalized_fields.get("effective_date")
    if effective_date is None:
        effective_date = _normalize_date(
            _get_raw_context_value(
                context,
                ("effective_date", "contract_effective_date", "agreement_date"),
            )
        )
    if effective_date is None:
        return

    expiry_clause = _find_clause(clauses, FIELD_ALIASES["expiry_date"])
    expiry_raw = _get_raw_context_value(
        context,
        ("expiry_date", "expiration_date", "end_date", "contract_end_date"),
    )
    expiry_date = _normalize_date(expiry_raw) if expiry_raw is not None else None
    if expiry_date is None and expiry_clause is not None:
        expiry_date = _extract_normalized_date(expiry_clause.text)
    if expiry_date is None:
        return

    if expiry_date >= effective_date:
        normalized_fields["expiry_date"] = expiry_date
        return

    evidence = _field_evidence(context, evidence_records, expiry_clause)
    findings.append(
        _make_finding(
            field_name="expiry_date",
            issue_type="suspicious_date_ordering",
            title="Suspicious date ordering",
            description=(
                "The contract expiry date appears before the effective date: "
                f"expiry_date={expiry_date}, effective_date={effective_date}."
            ),
            context=context,
            evidence=evidence,
            findings=findings,
            severity=Severity.HIGH,
            source_clause_text=_evidence_text(evidence),
            manual_review_required=True,
        )
    )


def _validate_governing_law_conflicts(
    context: Mapping[str, Any],
    clauses: Sequence[ExtractedClause],
    findings: list[Finding],
) -> None:
    """Detect multiple conflicting governing-law clauses."""
    governing_clauses = _find_clauses(clauses, FIELD_ALIASES["governing_law"])
    law_to_clauses: dict[str, list[ExtractedClause]] = {}
    for clause in governing_clauses:
        law = _extract_governing_law(clause.text)
        if law is None:
            continue
        law_to_clauses.setdefault(law, []).append(clause)

    if len(law_to_clauses) <= 1:
        return

    conflict_evidence = [
        _clause_evidence(context, clause)
        for clauses_for_law in law_to_clauses.values()
        for clause in clauses_for_law[:1]
    ]
    findings.append(
        _make_finding(
            field_name="governing_law",
            issue_type="conflicting_governing_law",
            title="Conflicting governing law clauses",
            description=(
                "Multiple governing-law jurisdictions were detected: "
                + ", ".join(sorted(law_to_clauses))
                + ". Resolve the conflict before risk scoring."
            ),
            context=context,
            evidence=conflict_evidence,
            findings=findings,
            severity=Severity.HIGH,
            source_clause_text=" | ".join(
                _truncate(clause.text)
                for clauses_for_law in law_to_clauses.values()
                for clause in clauses_for_law[:1]
            ),
            manual_review_required=True,
        )
    )


def _validate_payment_calculations(
    context: Mapping[str, Any],
    clauses: Sequence[ExtractedClause],
    findings: list[Finding],
) -> None:
    """Detect explicit arithmetic errors in payment or numeric clauses."""
    calculation_clauses = [
        clause
        for clause in clauses
        if _clause_matches_aliases(clause, FIELD_ALIASES["payment_terms"])
        or _contains_numeric_calculation(clause.text)
    ]
    for clause in calculation_clauses:
        error = _detect_calculation_error(clause.text)
        if error is None:
            continue
        findings.append(
            _make_finding(
                field_name="payment_terms",
                issue_type="calculation_error",
                title="Calculation error in contract values",
                description=error,
                context=context,
                evidence=[_clause_evidence(context, clause)],
                findings=findings,
                severity=Severity.HIGH,
                source_clause_text=_truncate(clause.text),
                manual_review_required=True,
            )
        )


def _validate_low_confidence_signatures(
    context: Mapping[str, Any],
    clauses: Sequence[ExtractedClause],
    evidence_records: Sequence[Mapping[str, Any]],
    findings: list[Finding],
) -> None:
    """Flag signature or execution sections below the confidence threshold."""
    threshold = _manual_review_confidence_threshold(context)
    signature_clauses = _find_clauses(clauses, FIELD_ALIASES["signature"])
    for clause in signature_clauses:
        if clause.confidence >= threshold and not clause.manual_review_required:
            continue
        findings.append(
            _make_finding(
                field_name="signature",
                issue_type="low_confidence_signature",
                title="Low-confidence signature section",
                description=(
                    "A signature or execution section was extracted below the "
                    f"manual-review confidence threshold ({clause.confidence:.2f} "
                    f"< {threshold:.2f})."
                ),
                context=context,
                evidence=_field_evidence(context, evidence_records, clause),
                findings=findings,
                severity=Severity.MEDIUM,
                confidence=clause.confidence,
                source_clause_text=_truncate(clause.text),
                manual_review_required=True,
            )
        )


def _validate_low_ocr_confidence(
    context: Mapping[str, Any],
    extracted_contract: Mapping[str, Any] | None,
    evidence_records: Sequence[Mapping[str, Any]],
    findings: list[Finding],
) -> None:
    """Flag OCR output below the manual-review confidence threshold."""
    if not isinstance(extracted_contract, Mapping):
        return
    metadata = extracted_contract.get("ocr_metadata")
    if not isinstance(metadata, Mapping) or not bool(metadata.get("used")):
        return

    threshold = _manual_review_confidence_threshold(context)
    pages = metadata.get("pages")
    low_pages: list[Mapping[str, Any]] = []
    if isinstance(pages, list):
        for page in pages:
            if not isinstance(page, Mapping) or not bool(page.get("used")):
                continue
            confidence = _confidence_value(page.get("confidence"))
            if confidence is not None and confidence < threshold:
                low_pages.append(page)

    average_confidence = _confidence_value(metadata.get("average_confidence"))
    has_low_average = (
        average_confidence is not None and average_confidence < threshold
    )
    if not low_pages and not has_low_average and not bool(metadata.get("low_confidence")):
        return

    confidence = (
        min(
            _confidence_value(page.get("confidence")) or 0.0
            for page in low_pages
        )
        if low_pages
        else average_confidence or 0.0
    )
    page_numbers = [
        int(page["page_number"])
        for page in low_pages
        if isinstance(page.get("page_number"), int)
    ]
    page_text = (
        "pages " + ", ".join(str(page) for page in page_numbers)
        if page_numbers
        else "one or more pages"
    )
    excerpt = (
        f"OCR confidence for {page_text} is below the manual-review threshold "
        f"({confidence:.2f} < {threshold:.2f})."
    )
    fallback = _fallback_evidence(context, evidence_records)
    evidence = [
        EvidencePointer(
            evidence_id=fallback.evidence_id,
            document_id=fallback.document_id,
            source_file=fallback.source_file,
            page_number=page_numbers[0] if page_numbers else fallback.page_number,
            clause_reference=fallback.clause_reference,
            excerpt=excerpt,
        )
    ]
    findings.append(
        Finding(
            finding_id=f"VAL-{len(findings) + 1:03d}",
            category="contract_validation",
            title="Low OCR confidence",
            description=excerpt,
            severity=Severity.MEDIUM,
            confidence=confidence,
            evidence=evidence,
            recommendation="Manually review OCR text against the source PDF.",
            field_name="ocr_confidence",
            issue_type="low_ocr_confidence",
            message=excerpt,
            source_clause_text=excerpt,
            source_page=evidence[0].page_number,
            evidence_pointer=evidence[0],
            manual_review_required=True,
            risk_engine_ready=True,
        )
    )


def _validate_multi_party_fields(
    context: Mapping[str, Any],
    clauses: Sequence[ExtractedClause],
    evidence_records: Sequence[Mapping[str, Any]],
    findings: list[Finding],
) -> None:
    """Validate signature coverage when more than two parties are detected."""
    parties = _extract_party_names(context, clauses)
    if len(parties) <= 2:
        return

    signature_clauses = _find_clauses(clauses, FIELD_ALIASES["signature"])
    if not signature_clauses:
        findings.append(
            _make_finding(
                field_name="party_names",
                issue_type="multi_party_signature_missing",
                title="Missing multi-party signature section",
                description=(
                    f"The contract appears to include {len(parties)} parties, "
                    "but no signature section was found for validating all parties."
                ),
                context=context,
                evidence=[_fallback_evidence(context, evidence_records)],
                findings=findings,
                severity=Severity.HIGH,
                manual_review_required=True,
            )
        )
        return

    signature_text = " ".join(clause.text for clause in signature_clauses).lower()
    missing_parties = [
        party for party in parties if party.lower() not in signature_text
    ]
    if not missing_parties:
        return

    findings.append(
        _make_finding(
            field_name="party_names",
            issue_type="multi_party_signature_incomplete",
            title="Incomplete multi-party signature coverage",
            description=(
                "The contract appears to include more than two parties, but the "
                "signature section does not reference: "
                + ", ".join(missing_parties)
                + "."
            ),
            context=context,
            evidence=[_clause_evidence(context, signature_clauses[0])],
            findings=findings,
            severity=Severity.HIGH,
            source_clause_text=_truncate(signature_clauses[0].text),
            manual_review_required=True,
        )
    )


def _confidence_value(value: Any) -> float | None:
    """Return a normalized confidence value when possible."""
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    if confidence < 0.0 or confidence > 1.0:
        return None
    return confidence
