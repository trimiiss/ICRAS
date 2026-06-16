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
}

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
    clauses: List[Dict[str, Any]],
    run_dir: str | Path | None = None,
    evidence_index: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Validate required contract fields and optionally write the result artifact.

    Args:
        context: Context packet data from the Intake Agent.
        clauses: Extracted clauses available for deterministic validation.
        run_dir: Optional run directory where ``validation_findings.json`` is written.
        evidence_index: Optional page-level evidence index for source pointers.

    Returns:
        A dictionary containing the validation result, findings, and artifact paths.

    Raises:
        ValidationAgentError: If inputs are malformed or the artifact cannot be saved.
    """
    run_path = _validate_run_dir(run_dir) if run_dir is not None else None
    clause_models = _coerce_clauses(clauses)
    evidence_records = _extract_evidence_records(evidence_index)

    run_id = str(context.get("run_id") or "unknown-run")
    normalized_fields: dict[str, str] = {}
    validated_fields: list[ValidatedContractField] = []
    findings: list[Finding] = []

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
        normalized_fields["payment_terms"] = context_value
        validated_fields.append(
            ValidatedContractField(
                field_name="payment_terms",
                is_present=True,
                normalized_value=context_value,
                source="context",
                evidence=evidence,
            )
        )
        return

    if clause is not None:
        normalized_value = _truncate(clause.text)
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
            title=title,
            description=description,
            context=context,
            evidence=evidence,
            findings=findings,
        )
    )


def _make_finding(
    field_name: str,
    title: str,
    description: str,
    context: Mapping[str, Any],
    evidence: Sequence[EvidencePointer],
    findings: Sequence[Finding],
) -> Finding:
    """Create a Pydantic finding for a validation issue."""
    return Finding(
        finding_id=f"VAL-{len(findings) + 1:03d}",
        category="contract_validation",
        title=title,
        description=description,
        severity=_field_severity(context, field_name),
        confidence=1.0,
        evidence=list(evidence),
        recommendation=(
            f"Add or correct the {field_name.replace('_', ' ')} before approval."
        ),
    )


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
    normalized_aliases = {_normalize_key(alias) for alias in aliases}
    for clause in clauses:
        clause_type = _normalize_key(clause.clause_type)
        title = _normalize_key(clause.title)
        if clause_type in normalized_aliases or title in normalized_aliases:
            return clause
        if any(alias in clause_type or alias in title for alias in normalized_aliases):
            return clause
    return None


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


def _field_severity(context: Mapping[str, Any], field_name: str) -> Severity:
    """Return the configured or default severity for a missing field."""
    playbook_severity = _playbook_missing_severity(context, field_name)
    if playbook_severity is not None:
        return playbook_severity
    return DEFAULT_FIELD_SEVERITIES[field_name]


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


def _field_evidence(
    context: Mapping[str, Any],
    evidence_records: Sequence[Mapping[str, Any]],
    clause: Optional[ExtractedClause],
) -> list[EvidencePointer]:
    """Return clause evidence when available, else run-level fallback evidence."""
    if clause is not None:
        return [_clause_evidence(context, clause)]
    return [_fallback_evidence(context, evidence_records)]


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
