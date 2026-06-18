"""Risk assessment public entry point."""

from pathlib import Path
from typing import Any, Dict, List, Optional

from schemas.risk_result import ClauseAnalysisResult, ClauseRisk, RiskResult
from agents.risk.errors import RiskAgentError
from agents.risk.helpers import (
    _coerce_clauses,
    _coerce_findings,
    _overall_severity,
    _summary,
)
from agents.risk.io import _load_playbook, _read_context_packet, _read_json_artifact
from agents.risk.results import (
    _deduplicate_clause_risks,
    _finding_from_clause_risk,
    _legacy_aggregate,
)
from agents.risk.context_scoring import (
    _score_high_risk_jurisdiction,
    _score_multi_jurisdiction,
    _score_multi_party,
    _score_validation_findings,
)
from agents.risk.policy_scoring import (
    _score_auto_renewal,
    _score_gdpr_requirements,
    _score_payment_terms,
    _score_playbook_variance,
    _score_prohibited_clauses,
)
from utils.artifacts import validate_run_dir, write_model_json
from utils.run_manager import append_audit_event


def run_risk_assessment(
    findings: Optional[List[Dict[str, Any]]] = None,
    context: Optional[Dict[str, Any]] = None,
    extracted_contract: Optional[Dict[str, Any]] = None,
    validation_result: Optional[Dict[str, Any]] = None,
    run_dir: str | Path | None = None,
    playbook_path: str | Path | None = None,
) -> Dict[str, Any]:
    """Score extracted clauses and validation findings.

    Args:
        findings: Optional legacy list of shared findings. If no clause context is
            provided, risk assessment returns an aggregate RiskResult only.
        context: Context packet data containing playbook and approval policy.
        extracted_contract: Extracted contract data from clause extraction.
        validation_result: Findings from validation.
        run_dir: Run directory used to read and write artifacts.
        playbook_path: Optional YAML playbook override.

    Returns:
        A dictionary with ``clause_analysis``, ``risk_result``, ``findings``, and
        artifact paths.
    """
    run_path = (
        validate_run_dir(
            run_dir,
            error_type=RiskAgentError,
            before_action="risk scoring",
        )
        if run_dir is not None
        else None
    )
    context_payload = context or _read_context_packet(run_path)
    extracted_payload = extracted_contract or _read_json_artifact(
        run_path,
        "extracted_contract.json",
        required=context_payload is not None,
    )
    validation_payload = validation_result or _read_json_artifact(
        run_path,
        "validation_findings.json",
        required=False,
    )

    if context_payload is None and extracted_payload is None:
        return _legacy_aggregate(findings or [])

    if context_payload is None:
        raise RiskAgentError(
            "Risk assessment requires context data. Provide context or run_dir with "
            "context_packet.json."
        )
    if extracted_payload is None:
        raise RiskAgentError(
            "Risk assessment requires extracted clauses. Provide extracted_contract or "
            "run_dir with extracted_contract.json."
        )

    playbook = _load_playbook(context_payload, playbook_path)
    approval_policy = context_payload.get("approval_policy")
    approval_policy = approval_policy if isinstance(approval_policy, dict) else {}
    validation_findings = _coerce_findings(
        (validation_payload or {}).get("findings", []) if validation_payload else []
    )
    if findings:
        validation_findings.extend(_coerce_findings(findings))

    clauses = _coerce_clauses(extracted_payload.get("clauses", []))
    run_id = str(
        context_payload.get("run_id")
        or extracted_payload.get("run_id")
        or "unknown-run"
    )

    clause_risks: list[ClauseRisk] = []
    _score_payment_terms(clauses, context_payload, clause_risks)
    _score_validation_findings(validation_findings, context_payload, clause_risks)
    _score_high_risk_jurisdiction(context_payload, clauses, clause_risks)
    _score_auto_renewal(clauses, context_payload, approval_policy, clause_risks)
    _score_gdpr_requirements(clauses, context_payload, approval_policy, clause_risks)
    _score_multi_jurisdiction(clauses, context_payload, clause_risks)
    _score_multi_party(clauses, context_payload, validation_findings, clause_risks)
    _score_playbook_variance(clauses, context_payload, playbook, clause_risks)
    _score_prohibited_clauses(clauses, context_payload, playbook, clause_risks)

    clause_risks = _deduplicate_clause_risks(clause_risks)
    findings_out = [
        _finding_from_clause_risk(index=index, risk=risk)
        for index, risk in enumerate(clause_risks, start=1)
    ]
    overall_severity = _overall_severity([risk.severity for risk in clause_risks])
    requires_legal_review = any(risk.legal_review_required for risk in clause_risks)
    summary = _summary(overall_severity, clause_risks)

    clause_analysis = ClauseAnalysisResult(
        run_id=run_id,
        overall_severity=overall_severity,
        clause_risks=clause_risks,
        findings=findings_out,
        requires_legal_review=requires_legal_review,
        summary=summary,
    )
    risk_result = RiskResult(
        overall_severity=overall_severity,
        findings=findings_out,
        requires_human_review=requires_legal_review,
        summary=summary,
        total_findings=len(findings_out),
    )

    artifact_paths: dict[str, str] = {}
    if run_path is not None:
        output_path = run_path / "clause_analysis.json"
        write_model_json(
            output_path,
            clause_analysis,
            error_type=RiskAgentError,
            failure_message="Failed to write clause analysis '{path}': {exc}",
        )
        append_audit_event(
            run_path,
            {
                "event": "risk_scoring_completed",
                "agent": "risk_agent",
                "message": "Risk assessment scored clauses against playbook and policy rules.",
                "artifacts": [output_path.name],
                "risk_count": len(clause_risks),
                "overall_severity": overall_severity.value,
                "requires_legal_review": requires_legal_review,
            },
        )
        artifact_paths["clause_analysis"] = str(output_path)

    return {
        "clause_analysis": clause_analysis.model_dump(mode="json"),
        "risk_result": risk_result.model_dump(mode="json"),
        "findings": [finding.model_dump(mode="json") for finding in findings_out],
        "artifact_paths": artifact_paths,
    }


__all__ = [
    "RiskAgentError",
    "run_risk_assessment",
]
