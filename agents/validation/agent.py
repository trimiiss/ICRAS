"""Validation Agent for required contract field checks."""

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from schemas.finding import Finding
from schemas.validation_result import ValidatedContractField, ValidationResult
from agents.validation.commercial_field_rules import (
    _validate_liability_cap,
    _validate_payment_terms,
    _validate_termination_terms,
)
from agents.validation.consistency_rules import (
    _validate_governing_law_conflicts,
    _validate_low_confidence_signatures,
    _validate_multi_party_fields,
    _validate_payment_calculations,
    _validate_suspicious_date_ordering,
)
from agents.validation.core_helpers import _coerce_clauses, _deduplicate_findings
from agents.validation.errors import ValidationAgentError
from agents.validation.evidence_helpers import _extract_evidence_records
from agents.validation.identity_field_rules import (
    _validate_effective_date,
    _validate_governing_law,
    _validate_party_names,
)
from agents.validation.io import (
    _read_existing_findings,
    _read_extracted_contract_clauses,
)
from utils.artifacts import validate_run_dir, write_model_json
from utils.run_manager import append_audit_event


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
    run_path = (
        validate_run_dir(
            run_dir,
            error_type=ValidationAgentError,
            before_action="running validation",
        )
        if run_dir is not None
        else None
    )
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
        write_model_json(
            output_path,
            validation_result,
            error_type=ValidationAgentError,
            failure_message="Failed to write validation artifact '{path}': {exc}",
        )
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
