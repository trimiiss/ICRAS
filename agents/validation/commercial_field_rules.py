"""Commercial and legal-control field validation rules."""

from typing import Any, Mapping, Sequence

from schemas.extracted_clause import ExtractedClause
from schemas.finding import Finding
from schemas.validation_result import ValidatedContractField
from agents.validation.constants import FIELD_ALIASES
from agents.validation.findings import (
    _record_missing_field,
    _record_unapproved_payment_terms,
)
from agents.validation.core_helpers import (
    _find_clause,
    _get_context_value,
)
from agents.validation.evidence_helpers import _field_evidence
from agents.validation.policy_helpers import (
    _liability_cap_required,
    _normalize_payment_terms_value,
    _payment_terms_applicable,
)
from utils.text import truncate as _truncate


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
