"""Finding construction helpers for validation rules."""

from typing import Any, Mapping, Optional, Sequence

from schemas.common import EvidencePointer, Severity
from schemas.finding import Finding
from schemas.validation_result import ValidatedContractField
from agents.validation.evidence_helpers import (
    _evidence_page,
    _evidence_text,
    _fallback_evidence,
    _primary_evidence,
)
from agents.validation.policy_helpers import (
    _approved_payment_terms,
    _field_severity,
    _payment_policy_severity,
)
from utils.payment_terms import extract_payment_terms as _extract_payment_terms

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
