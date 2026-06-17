"""Validation Agent for required contract field checks.

The agent performs deterministic validation only. It uses bundle context,
playbook data, extracted clauses when available, and page-level evidence to
raise source-backed findings for incomplete contract metadata.

LLM logic will be added in a later user story.
"""

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from schemas.common import EvidencePointer, Severity
from schemas.extracted_clause import ExtractedClause
from schemas.finding import Finding
from schemas.validation_result import ValidatedContractField, ValidationResult
from utils.run_manager import append_audit_event


class ValidationAgentError(Exception):
    """Raised when validation cannot create its required artifact."""


FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "party_names": (
        "party_names",
        "parties",
        "counterparty",
        "party",
    ),
    "effective_date": (
        "effective_date",
        "commencement_date",
        "start_date",
        "agreement_date",
    ),
    "governing_law": (
        "governing_law",
        "jurisdiction",
        "choice_of_law",
    ),
    "payment_terms": (
        "payment_terms",
        "fees",
        "compensation",
        "invoicing",
        "payment",
    ),
    "termination_terms": (
        "termination_terms",
        "termination",
        "term_and_termination",
        "term_and_duration",
    ),
    "expiry_date": (
        "expiry_date",
        "expiration_date",
        "end_date",
        "contract_end_date",
        "term_expiry",
    ),
    "liability_cap": (
        "liability_cap",
        "limitation_of_liability",
        "limited_liability",
        "liability_limit",
    ),
    "signature": (
        "signature",
        "signatures",
        "execution",
        "signatory",
        "signed",
    ),
}

CONTRACT_TYPE_PAYMENT_HINTS: tuple[str, ...] = (
    "service",
    "statement of work",
    "sow",
    "vendor",
    "purchase",
    "procurement",
    "subscription",
    "license",
    "consulting",
)

DEFAULT_FIELD_SEVERITIES: dict[str, Severity] = {
    "party_names": Severity.HIGH,
    "effective_date": Severity.HIGH,
    "governing_law": Severity.HIGH,
    "payment_terms": Severity.HIGH,
    "termination_terms": Severity.MEDIUM,
    "expiry_date": Severity.HIGH,
    "liability_cap": Severity.HIGH,
    "signature": Severity.MEDIUM,
}

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

DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d",
    "%m/%d/%Y",
    "%m-%d-%Y",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d %B %Y",
    "%d %b %Y",
)

DATE_CANDIDATE_PATTERNS: tuple[str, ...] = (
    r"\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b",
    r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b",
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*"
    r"\s+\d{1,2},\s+\d{4}\b",
    r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*"
    r"\s+\d{4}\b",
)


def run_validation(
    context: Dict[str, Any],
    clauses: Optional[List[Dict[str, Any]]] = None,
    run_dir: str | Path | None = None,
    evidence_index: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Validate required contract fields and optionally write the result artifact.

    Args:
        context: Context packet data from the Intake Agent.
        clauses: Extracted clauses available for deterministic validation.
            When omitted, the agent reads ``extracted_contract.json`` from
            ``run_dir`` if available.
        run_dir: Optional run directory where ``validation_findings.json`` is written.
        evidence_index: Optional page-level evidence index for source pointers.

    Returns:
        A dictionary containing the validation result, findings, and artifact paths.

    Raises:
        ValidationAgentError: If inputs are malformed or the artifact cannot be saved.
    """
    run_path = _validate_run_dir(run_dir) if run_dir is not None else None
    clause_payload = clauses
    if clause_payload is None or not clause_payload:
        clause_payload = _read_extracted_contract_clauses(run_path)
    clause_models = _coerce_clauses(clause_payload)
    evidence_records = _extract_evidence_records(evidence_index)

    run_id = str(context.get("run_id") or "unknown-run")
    normalized_fields: dict[str, str] = {}
    validated_fields: list[ValidatedContractField] = []
    findings: list[Finding] = _read_existing_findings(run_path)

    _validate_party_names(
        context=context,
        clauses=clause_models,
        evidence_records=evidence_records,
        normalized_fields=normalized_fields,
        validated_fields=validated_fields,
        findings=findings,
    )
    _validate_effective_date(
        context=context,
        clauses=clause_models,
        evidence_records=evidence_records,
        normalized_fields=normalized_fields,
        validated_fields=validated_fields,
        findings=findings,
    )
    _validate_governing_law(
        context=context,
        clauses=clause_models,
        evidence_records=evidence_records,
        normalized_fields=normalized_fields,
        validated_fields=validated_fields,
        findings=findings,
    )
    _validate_payment_terms(
        context=context,
        clauses=clause_models,
        evidence_records=evidence_records,
        normalized_fields=normalized_fields,
        validated_fields=validated_fields,
        findings=findings,
    )
    _validate_termination_terms(
        context=context,
        clauses=clause_models,
        evidence_records=evidence_records,
        normalized_fields=normalized_fields,
        validated_fields=validated_fields,
        findings=findings,
    )
    _validate_liability_cap(
        context=context,
        clauses=clause_models,
        evidence_records=evidence_records,
        normalized_fields=normalized_fields,
        validated_fields=validated_fields,
        findings=findings,
    )
    _validate_suspicious_date_ordering(
        context=context,
        clauses=clause_models,
        evidence_records=evidence_records,
        normalized_fields=normalized_fields,
        findings=findings,
    )
    _validate_governing_law_conflicts(
        context=context,
        clauses=clause_models,
        findings=findings,
    )
    _validate_payment_calculations(
        context=context,
        clauses=clause_models,
        findings=findings,
    )
    _validate_low_confidence_signatures(
        context=context,
        clauses=clause_models,
        evidence_records=evidence_records,
        findings=findings,
    )
    _validate_multi_party_fields(
        context=context,
        clauses=clause_models,
        evidence_records=evidence_records,
        findings=findings,
    )
    findings = _deduplicate_findings(findings)

    validation_result = ValidationResult(
        run_id=run_id,
        normalized_fields=normalized_fields,
        validated_fields=validated_fields,
        findings=findings,
    )

    artifact_paths: dict[str, str] = {}
    if run_path is not None:
        output_path = run_path / "validation_findings.json"
        _write_model_json(output_path, validation_result)
        append_audit_event(
            run_path,
            {
                "event": "validation_completed",
                "agent": "validation_agent",
                "message": "Validation Agent checked required contract fields.",
                "artifacts": [output_path.name],
                "finding_count": len(findings),
                "normalized_fields": sorted(normalized_fields.keys()),
            },
        )
        artifact_paths["validation_findings"] = str(output_path)

    result = validation_result.model_dump(mode="json")
    return {
        "validation_result": result,
        "findings": result["findings"],
        "artifact_paths": artifact_paths,
    }


def _validate_party_names(
    context: Mapping[str, Any],
    clauses: Sequence[ExtractedClause],
    evidence_records: Sequence[Mapping[str, Any]],
    normalized_fields: dict[str, str],
    validated_fields: list[ValidatedContractField],
    findings: list[Finding],
) -> None:
    """Validate that at least one party name is available."""
    context_value = _get_context_value(context, ("party_names", "parties", "counterparty"))
    clause = _find_clause(clauses, FIELD_ALIASES["party_names"])
    evidence = _field_evidence(context, evidence_records, clause)

    if context_value:
        normalized_fields["party_names"] = context_value
        validated_fields.append(
            ValidatedContractField(
                field_name="party_names",
                is_present=True,
                normalized_value=context_value,
                source="context",
                evidence=evidence,
            )
        )
        return

    if clause is not None:
        normalized_fields["party_names"] = _truncate(clause.text)
        validated_fields.append(
            ValidatedContractField(
                field_name="party_names",
                is_present=True,
                normalized_value=_truncate(clause.text),
                source="clause",
                evidence=evidence,
            )
        )
        return

    _record_missing_field(
        field_name="party_names",
        title="Missing party names",
        description=(
            "The contract does not identify the required contracting party names. "
            "Add clear legal names for the contracting parties before review."
        ),
        context=context,
        evidence_records=evidence_records,
        validated_fields=validated_fields,
        findings=findings,
    )


def _validate_effective_date(
    context: Mapping[str, Any],
    clauses: Sequence[ExtractedClause],
    evidence_records: Sequence[Mapping[str, Any]],
    normalized_fields: dict[str, str],
    validated_fields: list[ValidatedContractField],
    findings: list[Finding],
) -> None:
    """Validate and normalize the effective date."""
    raw_value = _get_raw_context_value(
        context,
        ("effective_date", "contract_effective_date", "agreement_date"),
    )
    clause = _find_clause(clauses, FIELD_ALIASES["effective_date"])
    evidence = _field_evidence(context, evidence_records, clause)

    if raw_value is not None and _is_non_empty(raw_value):
        normalized_date = _normalize_date(raw_value)
        if normalized_date is not None:
            normalized_fields["effective_date"] = normalized_date
            validated_fields.append(
                ValidatedContractField(
                    field_name="effective_date",
                    is_present=True,
                    normalized_value=normalized_date,
                    source="context",
                    evidence=evidence,
                )
            )
            return

        _record_invalid_field(
            field_name="effective_date",
            title="Invalid effective date",
            description=(
                f"The effective date value '{raw_value}' could not be parsed. "
                "Use an ISO 8601 date such as 2025-01-15."
            ),
            context=context,
            evidence_records=evidence_records,
            validated_fields=validated_fields,
            findings=findings,
            evidence_override=_context_value_evidence(context, "effective_date", raw_value),
        )
        return

    if clause is not None:
        normalized_date = _extract_normalized_date(clause.text)
        if normalized_date is not None:
            normalized_fields["effective_date"] = normalized_date
            validated_fields.append(
                ValidatedContractField(
                    field_name="effective_date",
                    is_present=True,
                    normalized_value=normalized_date,
                    source="clause",
                    evidence=evidence,
                )
            )
            return

        _record_invalid_field(
            field_name="effective_date",
            title="Invalid effective date",
            description=(
                "The effective date clause was found, but no parseable date was "
                "detected. Use an ISO 8601 date such as 2025-01-15."
            ),
            context=context,
            evidence_records=evidence_records,
            validated_fields=validated_fields,
            findings=findings,
            evidence_override=evidence,
        )
        return

    _record_missing_field(
        field_name="effective_date",
        title="Missing effective date",
        description=(
            "The contract does not include an effective date. Add the date when "
            "the contract becomes effective."
        ),
        context=context,
        evidence_records=evidence_records,
        validated_fields=validated_fields,
        findings=findings,
    )


def _validate_governing_law(
    context: Mapping[str, Any],
    clauses: Sequence[ExtractedClause],
    evidence_records: Sequence[Mapping[str, Any]],
    normalized_fields: dict[str, str],
    validated_fields: list[ValidatedContractField],
    findings: list[Finding],
) -> None:
    """Validate governing law or jurisdiction."""
    context_value = _get_context_value(
        context,
        ("governing_law", "governing_jurisdiction", "jurisdiction"),
    )
    clause = _find_clause(clauses, FIELD_ALIASES["governing_law"])
    evidence = _field_evidence(context, evidence_records, clause)

    if context_value:
        normalized_fields["governing_law"] = context_value
        validated_fields.append(
            ValidatedContractField(
                field_name="governing_law",
                is_present=True,
                normalized_value=context_value,
                source="context",
                evidence=evidence,
            )
        )
        return

    if clause is not None:
        normalized_value = _truncate(clause.text)
        normalized_fields["governing_law"] = normalized_value
        validated_fields.append(
            ValidatedContractField(
                field_name="governing_law",
                is_present=True,
                normalized_value=normalized_value,
                source="clause",
                evidence=evidence,
            )
        )
        return

    _record_missing_field(
        field_name="governing_law",
        title="Missing governing law",
        description=(
            "The contract does not specify the governing law or jurisdiction. "
            "Add a governing law clause before approval."
        ),
        context=context,
        evidence_records=evidence_records,
        validated_fields=validated_fields,
        findings=findings,
    )


def _validate_payment_terms(
    context: Mapping[str, Any],
    clauses: Sequence[ExtractedClause],
    evidence_records: Sequence[Mapping[str, Any]],
    normalized_fields: dict[str, str],
    validated_fields: list[ValidatedContractField],
    findings: list[Finding],
) -> None:
    """Validate payment terms when the contract type or playbook requires them."""
    if not _payment_terms_applicable(context):
        validated_fields.append(
            ValidatedContractField(
                field_name="payment_terms",
                is_present=True,
                normalized_value="not_applicable",
                source="playbook",
            )
        )
        return

    context_value = _get_context_value(
        context,
        ("payment_terms", "fees", "compensation", "billing_terms"),
    )
    clause = _find_clause(clauses, FIELD_ALIASES["payment_terms"])
    evidence = _field_evidence(context, evidence_records, clause)

    if context_value:
        normalized_value = _normalize_payment_terms_value(context_value)
        normalized_fields["payment_terms"] = normalized_value
        validated_fields.append(
            ValidatedContractField(
                field_name="payment_terms",
                is_present=True,
                normalized_value=normalized_value,
                source="context",
                evidence=evidence,
            )
        )
        _record_unapproved_payment_terms(
            payment_text=context_value,
            context=context,
            evidence=evidence,
            findings=findings,
        )
        return

    if clause is not None:
        normalized_value = _normalize_payment_terms_value(clause.text)
        normalized_fields["payment_terms"] = normalized_value
        validated_fields.append(
            ValidatedContractField(
                field_name="payment_terms",
                is_present=True,
                normalized_value=normalized_value,
                source="clause",
                evidence=evidence,
            )
        )
        _record_unapproved_payment_terms(
            payment_text=clause.text,
            context=context,
            evidence=evidence,
            findings=findings,
        )
        return

    _record_missing_field(
        field_name="payment_terms",
        title="Missing payment terms",
        description=(
            "The contract appears to require payment terms, but no payment, fee, "
            "or invoicing provision was found."
        ),
        context=context,
        evidence_records=evidence_records,
        validated_fields=validated_fields,
        findings=findings,
    )


def _validate_termination_terms(
    context: Mapping[str, Any],
    clauses: Sequence[ExtractedClause],
    evidence_records: Sequence[Mapping[str, Any]],
    normalized_fields: dict[str, str],
    validated_fields: list[ValidatedContractField],
    findings: list[Finding],
) -> None:
    """Validate termination terms."""
    context_value = _get_context_value(
        context,
        ("termination_terms", "termination", "term_and_termination"),
    )
    clause = _find_clause(clauses, FIELD_ALIASES["termination_terms"])
    evidence = _field_evidence(context, evidence_records, clause)

    if context_value:
        normalized_fields["termination_terms"] = context_value
        validated_fields.append(
            ValidatedContractField(
                field_name="termination_terms",
                is_present=True,
                normalized_value=context_value,
                source="context",
                evidence=evidence,
            )
        )
        return

    if clause is not None:
        normalized_value = _truncate(clause.text)
        normalized_fields["termination_terms"] = normalized_value
        validated_fields.append(
            ValidatedContractField(
                field_name="termination_terms",
                is_present=True,
                normalized_value=normalized_value,
                source="clause",
                evidence=evidence,
            )
        )
        return

    _record_missing_field(
        field_name="termination_terms",
        title="Missing termination terms",
        description=(
            "The contract does not define termination rights, notice periods, or "
            "other termination mechanics."
        ),
        context=context,
        evidence_records=evidence_records,
        validated_fields=validated_fields,
        findings=findings,
    )


def _validate_liability_cap(
    context: Mapping[str, Any],
    clauses: Sequence[ExtractedClause],
    evidence_records: Sequence[Mapping[str, Any]],
    normalized_fields: dict[str, str],
    validated_fields: list[ValidatedContractField],
    findings: list[Finding],
) -> None:
    """Validate that a required limitation of liability clause is present."""
    if not _liability_cap_required(context):
        return

    clause = _find_clause(clauses, FIELD_ALIASES["liability_cap"])
    if clause is not None:
        evidence = _field_evidence(context, evidence_records, clause)
        normalized_fields["liability_cap"] = _truncate(clause.text)
        validated_fields.append(
            ValidatedContractField(
                field_name="liability_cap",
                is_present=True,
                normalized_value=_truncate(clause.text),
                source="clause",
                evidence=evidence,
            )
        )
        return

    _record_missing_field(
        field_name="liability_cap",
        title="Missing liability cap",
        description=(
            "The contract does not include a limitation of liability or liability "
            "cap clause required by approval_policy.yaml."
        ),
        context=context,
        evidence_records=evidence_records,
        validated_fields=validated_fields,
        findings=findings,
    )


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


def _record_missing_field(
    field_name: str,
    title: str,
    description: str,
    context: Mapping[str, Any],
    evidence_records: Sequence[Mapping[str, Any]],
    validated_fields: list[ValidatedContractField],
    findings: list[Finding],
) -> None:
    """Append a missing-field validation result and finding."""
    evidence = [_fallback_evidence(context, evidence_records)]
    validated_fields.append(
        ValidatedContractField(
            field_name=field_name,
            is_present=False,
            source=None,
            evidence=evidence,
        )
    )
    findings.append(
        _make_finding(
            field_name=field_name,
            issue_type="missing_field",
            title=title,
            description=description,
            context=context,
            evidence=evidence,
            findings=findings,
        )
    )


def _record_invalid_field(
    field_name: str,
    title: str,
    description: str,
    context: Mapping[str, Any],
    evidence_records: Sequence[Mapping[str, Any]],
    validated_fields: list[ValidatedContractField],
    findings: list[Finding],
    evidence_override: Optional[Sequence[EvidencePointer]] = None,
) -> None:
    """Append an invalid-field validation result and finding."""
    evidence = list(evidence_override or [_fallback_evidence(context, evidence_records)])
    validated_fields.append(
        ValidatedContractField(
            field_name=field_name,
            is_present=True,
            source=None,
            evidence=evidence,
        )
    )
    findings.append(
        _make_finding(
            field_name=field_name,
            issue_type="invalid_field",
            title=title,
            description=description,
            context=context,
            evidence=evidence,
            findings=findings,
        )
    )


def _record_unapproved_payment_terms(
    payment_text: str,
    context: Mapping[str, Any],
    evidence: Sequence[EvidencePointer],
    findings: list[Finding],
) -> None:
    """Create a finding when detected payment terms are outside YAML policy."""
    detected_terms = _extract_payment_terms(payment_text)
    if not detected_terms:
        return

    approved_terms = _approved_payment_terms(context)
    unapproved_terms = [
        term for term in detected_terms if term not in approved_terms
    ]
    if not unapproved_terms:
        return

    findings.append(
        Finding(
            finding_id=f"VAL-{len(findings) + 1:03d}",
            category="contract_validation",
            title="Unapproved payment terms",
            description=(
                "Detected payment terms are not approved by approval_policy.yaml: "
                + ", ".join(unapproved_terms)
                + ". Approved terms are: "
                + ", ".join(sorted(approved_terms))
                + "."
            ),
            severity=_payment_policy_severity(context),
            confidence=1.0,
            evidence=list(evidence),
            recommendation=(
                "Update the payment clause to use an approved payment term, "
                "or update approval_policy.yaml if the policy has changed."
            ),
            field_name="payment_terms",
            issue_type="payment_terms_policy_violation",
            message=(
                "Detected payment terms are not approved by approval_policy.yaml: "
                + ", ".join(unapproved_terms)
                + "."
            ),
            source_clause_text=payment_text,
            source_page=_evidence_page(evidence),
            evidence_pointer=_primary_evidence(evidence),
            manual_review_required=True,
            risk_engine_ready=True,
        )
    )


def _make_finding(
    field_name: str,
    issue_type: str,
    title: str,
    description: str,
    context: Mapping[str, Any],
    evidence: Sequence[EvidencePointer],
    findings: Sequence[Finding],
    severity: Optional[Severity] = None,
    confidence: float = 1.0,
    source_clause_text: Optional[str] = None,
    manual_review_required: bool = True,
) -> Finding:
    """Create a Pydantic finding for a validation issue."""
    finding_evidence = list(evidence)
    return Finding(
        finding_id=f"VAL-{len(findings) + 1:03d}",
        category="contract_validation",
        title=title,
        description=description,
        severity=severity or _field_severity(context, field_name),
        confidence=confidence,
        evidence=finding_evidence,
        recommendation=(
            f"Add or correct the {field_name.replace('_', ' ')} before approval."
        ),
        field_name=field_name,
        issue_type=issue_type,
        message=description,
        source_clause_text=source_clause_text or _evidence_text(finding_evidence),
        source_page=_evidence_page(finding_evidence),
        evidence_pointer=_primary_evidence(finding_evidence),
        manual_review_required=manual_review_required,
        risk_engine_ready=True,
    )


def _read_extracted_contract_clauses(run_path: Optional[Path]) -> list[dict[str, Any]]:
    """Read extracted clauses from run-local extracted_contract.json if present."""
    if run_path is None:
        return []

    extracted_path = run_path / "extracted_contract.json"
    if not extracted_path.exists():
        return []

    try:
        with open(extracted_path, "r", encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationAgentError(
            f"Failed to read extracted contract artifact '{extracted_path}': {exc}"
        ) from exc

    if not isinstance(payload, Mapping):
        raise ValidationAgentError(
            f"Expected '{extracted_path}' to contain a JSON object."
        )
    raw_clauses = payload.get("clauses", [])
    if not isinstance(raw_clauses, list):
        raise ValidationAgentError(
            f"Expected '{extracted_path}' field 'clauses' to be a list."
        )
    return [dict(clause) for clause in raw_clauses if isinstance(clause, Mapping)]


def _read_existing_findings(run_path: Optional[Path]) -> list[Finding]:
    """Read existing validation findings so reruns update instead of discard."""
    if run_path is None:
        return []

    validation_path = run_path / "validation_findings.json"
    if not validation_path.exists():
        return []

    try:
        with open(validation_path, "r", encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationAgentError(
            f"Failed to read existing validation artifact '{validation_path}': {exc}"
        ) from exc

    if not isinstance(payload, Mapping):
        return []
    raw_findings = payload.get("findings", [])
    if not isinstance(raw_findings, list):
        return []

    findings: list[Finding] = []
    for index, raw_finding in enumerate(raw_findings):
        if not isinstance(raw_finding, Mapping):
            continue
        try:
            findings.append(Finding.model_validate(raw_finding))
        except Exception as exc:
            raise ValidationAgentError(
                "Existing validation_findings.json contains an invalid finding at "
                f"index {index}: {exc}"
            ) from exc
    return findings


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


def _coerce_clauses(clauses: List[Dict[str, Any]]) -> list[ExtractedClause]:
    """Convert clause dictionaries to ExtractedClause models."""
    if not isinstance(clauses, list):
        raise ValidationAgentError(
            "Expected clauses to be a list of dictionaries from the Extraction Agent."
        )

    clause_models: list[ExtractedClause] = []
    for index, clause in enumerate(clauses, start=1):
        if not isinstance(clause, Mapping):
            raise ValidationAgentError(
                f"Expected clauses[{index - 1}] to be a mapping, "
                f"got {type(clause).__name__}."
            )

        clause_data = dict(clause)
        clause_type = clause_data.get("clause_type")
        if not _is_non_empty(clause_type):
            raise ValidationAgentError(
                f"clauses[{index - 1}] is missing required 'clause_type'. "
                "Provide extraction output that includes clause_type."
            )
        if not _is_non_empty(clause_data.get("text")):
            raise ValidationAgentError(
                f"clauses[{index - 1}] is missing required 'text'. "
                "Provide extraction output that includes clause text."
            )

        clause_data.setdefault("clause_id", f"CLAUSE-{index:03d}")
        clause_data.setdefault("title", str(clause_type).replace("_", " ").title())
        clause_data.setdefault("confidence", 1.0)
        clause_data.setdefault("clause_text", str(clause_data["text"]))
        clause_data.setdefault("confidence_score", clause_data["confidence"])
        if "page_numbers" not in clause_data and clause_data.get("page_number") is not None:
            clause_data["page_numbers"] = [clause_data["page_number"]]
        clause_data.setdefault(
            "evidence",
            EvidencePointer(
                source_file="unknown",
                page_number=_optional_int(clause_data.get("page_number")),
                clause_reference=_optional_str(clause_data.get("section_reference")),
                excerpt=_truncate(str(clause_data["text"])),
            ).model_dump(mode="json"),
        )
        clause_data.setdefault("evidence_pointer", clause_data["evidence"])
        clause_data.setdefault(
            "manual_review_required",
            float(clause_data["confidence"]) < 0.75,
        )
        try:
            clause_models.append(ExtractedClause.model_validate(clause_data))
        except Exception as exc:
            raise ValidationAgentError(
                f"clauses[{index - 1}] could not be validated as an "
                f"ExtractedClause: {exc}"
            ) from exc

    return clause_models


def _validate_run_dir(run_dir: str | Path) -> Path:
    """Return a valid run directory path or raise a clear validation error."""
    run_path = Path(run_dir).resolve()
    if not run_path.exists():
        raise ValidationAgentError(
            f"Run directory does not exist: {run_path}. "
            "Create it with create_run_folder before running validation."
        )
    if not run_path.is_dir():
        raise ValidationAgentError(f"Run path is not a directory: {run_path}")
    return run_path


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


def _payment_terms_applicable(context: Mapping[str, Any]) -> bool:
    """Return whether payment terms must be validated for this contract."""
    explicit = _get_raw_context_value(
        context,
        ("payment_terms_required", "requires_payment_terms", "payment_required"),
    )
    if isinstance(explicit, bool):
        return explicit

    playbook = context.get("playbook")
    if isinstance(playbook, Mapping):
        required_clauses = playbook.get("required_clauses")
        if isinstance(required_clauses, list):
            for required_clause in required_clauses:
                if not isinstance(required_clause, Mapping):
                    continue
                clause_type = _normalize_key(str(required_clause.get("clause_type", "")))
                if "payment" in clause_type or "fee" in clause_type:
                    return True

    contract_type = _get_context_value(context, ("contract_type",)) or ""
    normalized_contract_type = contract_type.lower()
    return any(hint in normalized_contract_type for hint in CONTRACT_TYPE_PAYMENT_HINTS)


def _liability_cap_required(context: Mapping[str, Any]) -> bool:
    """Return whether policy or playbook requires a liability cap."""
    approval_policy = context.get("approval_policy")
    if isinstance(approval_policy, Mapping):
        requirements = approval_policy.get("liability_cap_requirements")
        if isinstance(requirements, Mapping):
            required = requirements.get("required")
            if isinstance(required, bool):
                return required

    playbook = context.get("playbook")
    if isinstance(playbook, Mapping):
        required_clauses = playbook.get("required_clauses")
        if isinstance(required_clauses, list):
            aliases = {
                _normalize_key(alias) for alias in FIELD_ALIASES["liability_cap"]
            }
            for required_clause in required_clauses:
                if not isinstance(required_clause, Mapping):
                    continue
                clause_type = _normalize_key(str(required_clause.get("clause_type", "")))
                if clause_type in aliases or any(alias in clause_type for alias in aliases):
                    return True

    return False


def _field_severity(context: Mapping[str, Any], field_name: str) -> Severity:
    """Return the configured or default severity for a missing field."""
    if field_name == "liability_cap":
        policy_severity = _liability_cap_missing_severity(context)
        if policy_severity is not None:
            return policy_severity
    playbook_severity = _playbook_missing_severity(context, field_name)
    if playbook_severity is not None:
        return playbook_severity
    return DEFAULT_FIELD_SEVERITIES[field_name]


def _liability_cap_missing_severity(context: Mapping[str, Any]) -> Optional[Severity]:
    """Read liability-cap missing severity from approval policy."""
    approval_policy = context.get("approval_policy")
    if not isinstance(approval_policy, Mapping):
        return None
    requirements = approval_policy.get("liability_cap_requirements")
    if not isinstance(requirements, Mapping):
        return None
    raw_severity = str(requirements.get("severity_if_missing", "")).upper()
    try:
        return Severity(raw_severity)
    except ValueError:
        return None


def _playbook_missing_severity(
    context: Mapping[str, Any],
    field_name: str,
) -> Optional[Severity]:
    """Read severity_if_missing from matching playbook required clauses."""
    playbook = context.get("playbook")
    if not isinstance(playbook, Mapping):
        return None

    required_clauses = playbook.get("required_clauses")
    if not isinstance(required_clauses, list):
        return None

    aliases = {_normalize_key(alias) for alias in FIELD_ALIASES[field_name]}
    for required_clause in required_clauses:
        if not isinstance(required_clause, Mapping):
            continue
        clause_type = _normalize_key(str(required_clause.get("clause_type", "")))
        if clause_type not in aliases and not any(alias in clause_type for alias in aliases):
            continue
        raw_severity = str(required_clause.get("severity_if_missing", "")).upper()
        try:
            return Severity(raw_severity)
        except ValueError:
            continue

    return None


def _approved_payment_terms(context: Mapping[str, Any]) -> set[str]:
    """Return approved payment terms from approval_policy.yaml rules."""
    approval_policy = context.get("approval_policy")
    if not isinstance(approval_policy, Mapping):
        return {"net-30"}

    approved_payment_terms = approval_policy.get("approved_payment_terms")
    if not isinstance(approved_payment_terms, Mapping):
        return {"net-30"}

    terms = approved_payment_terms.get("terms")
    if not isinstance(terms, list):
        return {"net-30"}

    normalized_terms = {
        normalized
        for term in terms
        if (normalized := _canonical_payment_term(str(term))) is not None
    }
    return normalized_terms or {"net-30"}


def _payment_policy_severity(context: Mapping[str, Any]) -> Severity:
    """Return YAML-configured severity for unapproved payment terms."""
    approval_policy = context.get("approval_policy")
    if not isinstance(approval_policy, Mapping):
        return Severity.HIGH

    approved_payment_terms = approval_policy.get("approved_payment_terms")
    if not isinstance(approved_payment_terms, Mapping):
        return Severity.HIGH

    raw_severity = str(
        approved_payment_terms.get("severity_if_unapproved", Severity.HIGH.value)
    ).upper()
    try:
        return Severity(raw_severity)
    except ValueError:
        return Severity.HIGH


def _normalize_payment_terms_value(payment_text: str) -> str:
    """Return detected canonical payment terms or a compact clause excerpt."""
    detected_terms = _extract_payment_terms(payment_text)
    if detected_terms:
        return "; ".join(detected_terms)
    return _truncate(payment_text)


def _extract_payment_terms(payment_text: str) -> list[str]:
    """Extract canonical net payment terms from text."""
    found_terms: list[str] = []
    for match in re.finditer(r"\bnet[\s-]?(\d{1,3})\b", payment_text, re.IGNORECASE):
        term = f"net-{int(match.group(1))}"
        if term not in found_terms:
            found_terms.append(term)
    return found_terms


def _canonical_payment_term(raw_term: str) -> Optional[str]:
    """Normalize a configured payment term such as net 30 into net-30."""
    match = re.fullmatch(r"\s*net[\s-]?(\d{1,3})\s*", raw_term, re.IGNORECASE)
    if match is None:
        return None
    return f"net-{int(match.group(1))}"


def _manual_review_confidence_threshold(context: Mapping[str, Any]) -> float:
    """Return the configured confidence threshold for manual review."""
    approval_policy = context.get("approval_policy")
    if not isinstance(approval_policy, Mapping):
        return 0.75
    threshold = approval_policy.get("manual_review_confidence_threshold", 0.75)
    try:
        numeric_threshold = float(threshold)
    except (TypeError, ValueError):
        return 0.75
    return min(max(numeric_threshold, 0.0), 1.0)


def _extract_governing_law(text: str) -> Optional[str]:
    """Extract a known governing-law jurisdiction from clause text."""
    compact_text = " ".join(text.split())
    for jurisdiction in KNOWN_GOVERNING_LAW_JURISDICTIONS:
        if re.search(rf"\b{re.escape(jurisdiction)}\b", compact_text, re.IGNORECASE):
            return jurisdiction.lower()

    patterns = (
        r"laws?\s+of\s+([A-Z][A-Za-z .&-]+?)(?:,|\.|;|\s+and\s+|$)",
        r"governed\s+by\s+([A-Z][A-Za-z .&-]+?)\s+law",
        r"jurisdiction\s+of\s+([A-Z][A-Za-z .&-]+?)(?:,|\.|;|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, compact_text)
        if match is None:
            continue
        return _normalize_governing_law(match.group(1))
    return None


def _normalize_governing_law(value: str) -> str:
    """Return a compact comparable governing-law value."""
    cleaned = re.sub(
        r"\b(usa|u\.s\.a\.|united states|state of|laws of|the)\b",
        "",
        value,
        flags=re.IGNORECASE,
    )
    return " ".join(cleaned.replace(",", " ").split()).lower()


def _contains_numeric_calculation(text: str) -> bool:
    """Return whether text contains an explicit arithmetic expression."""
    return bool(
        re.search(
            r"\d[\d,]*(?:\.\d+)?\s*(?:x|\*|\+|times|plus)\s*\d",
            text,
            re.IGNORECASE,
        )
        or re.search(r"\btotal(?:s|ing)?\b", text, re.IGNORECASE)
    )


def _detect_calculation_error(text: str) -> Optional[str]:
    """Detect simple multiplication or addition errors in contract text."""
    compact_text = " ".join(text.split())
    multiplication_patterns = (
        r"(?P<a>\d[\d,]*(?:\.\d+)?)\s*(?:x|\*|times)\s*"
        r"(?P<b>\d[\d,]*(?:\.\d+)?)\s*(?:=|equals|total(?:s|ing)?(?:\s+(?:of|to))?)"
        r"\s*\$?(?P<c>\d[\d,]*(?:\.\d+)?)",
        r"monthly\s+fee\s+of\s+\$?(?P<a>\d[\d,]*(?:\.\d+)?)\s+for\s+"
        r"(?P<b>\d[\d,]*(?:\.\d+)?)\s+months?.*?"
        r"(?:total(?:s|ing)?(?:\s+(?:of|to))?)\s+\$?(?P<c>\d[\d,]*(?:\.\d+)?)",
    )
    for pattern in multiplication_patterns:
        match = re.search(pattern, compact_text, re.IGNORECASE)
        if match is None:
            continue
        left = _parse_decimal(match.group("a"))
        right = _parse_decimal(match.group("b"))
        stated = _parse_decimal(match.group("c"))
        if left is None or right is None or stated is None:
            continue
        expected = left * right
        if not _amounts_close(expected, stated):
            return (
                f"Detected calculation mismatch: {left:g} x {right:g} should equal "
                f"{expected:g}, but the clause states {stated:g}."
            )

    addition_pattern = (
        r"(?P<a>\d[\d,]*(?:\.\d+)?)\s*(?:\+|plus)\s*"
        r"(?P<b>\d[\d,]*(?:\.\d+)?)\s*(?:=|equals|total(?:s|ing)?(?:\s+(?:of|to))?)"
        r"\s*\$?(?P<c>\d[\d,]*(?:\.\d+)?)"
    )
    match = re.search(addition_pattern, compact_text, re.IGNORECASE)
    if match is None:
        return None
    left = _parse_decimal(match.group("a"))
    right = _parse_decimal(match.group("b"))
    stated = _parse_decimal(match.group("c"))
    if left is None or right is None or stated is None:
        return None
    expected = left + right
    if _amounts_close(expected, stated):
        return None
    return (
        f"Detected calculation mismatch: {left:g} + {right:g} should equal "
        f"{expected:g}, but the clause states {stated:g}."
    )


def _parse_decimal(raw_value: str) -> Optional[float]:
    """Parse a numeric contract value."""
    try:
        return float(raw_value.replace(",", ""))
    except ValueError:
        return None


def _amounts_close(left: float, right: float) -> bool:
    """Return whether two amounts are effectively equal for validation."""
    return abs(left - right) <= 0.01


def _extract_party_names(
    context: Mapping[str, Any],
    clauses: Sequence[ExtractedClause],
) -> list[str]:
    """Extract likely party names from context and party clauses."""
    parties: list[str] = []
    raw_parties = _get_raw_context_value(context, ("party_names", "parties"))
    if isinstance(raw_parties, Sequence) and not isinstance(raw_parties, (str, bytes)):
        parties.extend(str(party).strip() for party in raw_parties if _is_non_empty(party))
    elif isinstance(raw_parties, Mapping):
        parties.extend(str(value).strip() for value in raw_parties.values() if _is_non_empty(value))
    elif _is_non_empty(raw_parties):
        parties.extend(_split_party_text(str(raw_parties)))

    party_clause = _find_clause(clauses, FIELD_ALIASES["party_names"])
    if party_clause is not None:
        parties.extend(_split_party_text(party_clause.text))

    unique_parties: list[str] = []
    for party in parties:
        cleaned_party = _clean_party_name(party)
        if not cleaned_party or cleaned_party.lower() in {p.lower() for p in unique_parties}:
            continue
        unique_parties.append(cleaned_party)
    return unique_parties


def _split_party_text(text: str) -> list[str]:
    """Split common party-list language into likely legal names."""
    match = re.search(
        r"(?:between|among)\s+(.+?)(?:\.|,?\s+each\s+a\s+|,?\s+collectively\s+)",
        text,
        re.IGNORECASE,
    )
    party_text = match.group(1) if match is not None else text
    party_text = re.sub(r"\s+\([^)]*\)", "", party_text)
    return [
        item
        for item in re.split(r"\s*,\s*|\s+and\s+|\s+&\s+", party_text)
        if _is_non_empty(item)
    ]


def _clean_party_name(value: str) -> str:
    """Normalize a likely party name for comparison."""
    cleaned = value.strip(" .;:")
    cleaned = re.sub(r"^(this agreement is|by and between)\s+", "", cleaned, flags=re.IGNORECASE)
    if len(cleaned) < 3:
        return ""
    return cleaned


def _field_evidence(
    context: Mapping[str, Any],
    evidence_records: Sequence[Mapping[str, Any]],
    clause: Optional[ExtractedClause],
) -> list[EvidencePointer]:
    """Return clause evidence when available, else run-level fallback evidence."""
    if clause is not None:
        return [_clause_evidence(context, clause)]
    return [_fallback_evidence(context, evidence_records)]


def _primary_evidence(
    evidence: Sequence[EvidencePointer],
) -> Optional[EvidencePointer]:
    """Return the primary evidence pointer for finding compatibility fields."""
    return evidence[0] if evidence else None


def _evidence_text(evidence: Sequence[EvidencePointer]) -> Optional[str]:
    """Return the best evidence excerpt for source_clause_text."""
    primary = _primary_evidence(evidence)
    if primary is None:
        return None
    return primary.excerpt


def _evidence_page(evidence: Sequence[EvidencePointer]) -> Optional[int]:
    """Return the primary evidence page number."""
    primary = _primary_evidence(evidence)
    if primary is None:
        return None
    return primary.page_number


def _clause_evidence(
    context: Mapping[str, Any],
    clause: ExtractedClause,
) -> EvidencePointer:
    """Build an evidence pointer from an extracted clause."""
    return EvidencePointer(
        source_file=str(context.get("contract_file") or "unknown"),
        page_number=clause.page_number,
        clause_reference=clause.section_reference,
        excerpt=_truncate(clause.text),
    )


def _fallback_evidence(
    context: Mapping[str, Any],
    evidence_records: Sequence[Mapping[str, Any]],
) -> EvidencePointer:
    """Return the best available source pointer for a validation finding."""
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
            excerpt=_optional_str(record.get("excerpt")),
        )

    return EvidencePointer(source_file=str(context.get("contract_file") or "unknown"))


def _context_value_evidence(
    context: Mapping[str, Any],
    field_name: str,
    raw_value: Any,
) -> list[EvidencePointer]:
    """Build an evidence pointer for a malformed context field value."""
    return [
        EvidencePointer(
            source_file=str(context.get("contract_file") or "context_packet.json"),
            excerpt=f"{field_name}: {raw_value}",
        )
    ]


def _extract_evidence_records(
    evidence_index: Optional[Mapping[str, Any]],
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


def _normalize_date(raw_value: Any) -> Optional[str]:
    """Normalize a date-like value to an ISO 8601 date string."""
    if isinstance(raw_value, datetime):
        return raw_value.date().isoformat()
    if isinstance(raw_value, date):
        return raw_value.isoformat()
    if not isinstance(raw_value, str):
        return None

    value = raw_value.strip()
    if not value:
        return None

    iso_value = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(iso_value).date().isoformat()
    except ValueError:
        pass

    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(value, date_format).date().isoformat()
        except ValueError:
            continue

    return None


def _extract_normalized_date(text: str) -> Optional[str]:
    """Extract and normalize the first parseable date from clause text."""
    normalized_whitespace = " ".join(text.split())
    for pattern in DATE_CANDIDATE_PATTERNS:
        match = re.search(pattern, normalized_whitespace, flags=re.IGNORECASE)
        if match is None:
            continue
        normalized_date = _normalize_date(match.group(0))
        if normalized_date is not None:
            return normalized_date
    return None


def _normalize_key(value: str) -> str:
    """Normalize a free-form key or clause type for alias matching."""
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _is_non_empty(value: Any) -> bool:
    """Return whether a value carries meaningful non-empty content."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return any(_is_non_empty(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_is_non_empty(item) for item in value)
    return True


def _optional_str(value: Any) -> Optional[str]:
    """Return value as a string when non-empty, else None."""
    if not _is_non_empty(value):
        return None
    return str(value)


def _optional_int(value: Any) -> Optional[int]:
    """Return value as an int when possible, else None."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _truncate(value: str, max_chars: int = 500) -> str:
    """Return a compact snippet for normalized field values and evidence."""
    normalized = " ".join(value.split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


def _write_model_json(path: Path, model: ValidationResult) -> None:
    """Write a ValidationResult model as deterministic, formatted JSON."""
    try:
        with open(path, "w", encoding="utf-8") as file:
            json.dump(model.model_dump(mode="json"), file, indent=2, ensure_ascii=False)
            file.write("\n")
    except OSError as exc:
        raise ValidationAgentError(
            f"Failed to write validation artifact '{path}': {exc}"
        ) from exc
