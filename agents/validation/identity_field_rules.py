"""Identity, date, and governing-law field validation rules."""

from typing import Any, Mapping, Sequence

from schemas.extracted_clause import ExtractedClause
from schemas.finding import Finding
from schemas.validation_result import ValidatedContractField
from agents.validation.constants import FIELD_ALIASES
from agents.validation.findings import (
    _record_invalid_field,
    _record_missing_field,
)
from agents.validation.core_helpers import (
    _find_clause,
    _get_context_value,
    _get_raw_context_value,
)
from agents.validation.evidence_helpers import (
    _context_value_evidence,
    _field_evidence,
)
from utils.dates import (
    extract_normalized_date as _extract_normalized_date,
    normalize_date as _normalize_date,
)
from utils.text import is_non_empty as _is_non_empty, truncate as _truncate


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
