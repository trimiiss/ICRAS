"""Risk result and finding construction helpers."""

from typing import Any, Dict, List, Mapping, Optional, Sequence

from schemas.common import EvidencePointer, Severity
from schemas.extracted_clause import ExtractedClause
from schemas.finding import Finding
from schemas.risk_result import ClauseRisk, RiskResult
from agents.risk.helpers import (
    _clause_evidence,
    _coerce_findings,
    _overall_severity,
    _summary,
    _truncate,
)

def _legacy_aggregate(findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Keep the original aggregate-only call shape working."""
    finding_models = _coerce_findings(findings)
    overall_severity = _overall_severity([finding.severity for finding in finding_models])
    requires_review = any(
        finding.severity in {Severity.HIGH, Severity.CRITICAL}
        or finding.manual_review_required
        for finding in finding_models
    )
    result = RiskResult(
        overall_severity=overall_severity,
        findings=finding_models,
        requires_human_review=requires_review,
        summary=_summary(overall_severity, []),
        total_findings=len(finding_models),
    )
    return {
        "risk_result": result.model_dump(mode="json"),
        "findings": result.model_dump(mode="json")["findings"],
        "artifact_paths": {},
    }

def _make_clause_risk(
    field_name: str,
    issue_type: str,
    severity: Severity,
    explanation: str,
    action: str,
    context: Mapping[str, Any],
    clause: Optional[ExtractedClause],
    evidence: Optional[EvidencePointer] = None,
    clause_text: Optional[str] = None,
    tolerance_threshold: Optional[str] = None,
) -> ClauseRisk:
    """Create a ClauseRisk with evidence and legal-review flags."""
    primary_evidence = evidence
    if primary_evidence is None and clause is not None:
        primary_evidence = _clause_evidence(context, clause)
    if primary_evidence is None:
        primary_evidence = EvidencePointer(
            source_file=str(context.get("contract_file") or "unknown"),
            excerpt=clause_text or explanation,
        )
    text = clause_text or (clause.text if clause is not None else primary_evidence.excerpt)
    return ClauseRisk(
        risk_id="PENDING",
        clause_id=clause.clause_id if clause is not None else None,
        field_name=field_name,
        issue_type=issue_type,
        severity=severity,
        risk_explanation=explanation,
        recommended_action=action,
        clause_text=_truncate(text or explanation),
        source_page=primary_evidence.page_number,
        evidence_pointer=primary_evidence,
        legal_review_required=severity in {Severity.HIGH, Severity.CRITICAL},
        tolerance_threshold=tolerance_threshold,
    )


def _finding_from_clause_risk(index: int, risk: ClauseRisk) -> Finding:
    """Convert a ClauseRisk into the shared Finding schema."""
    return Finding(
        finding_id=f"RISK-{index:03d}",
        category="clause_risk",
        title=risk.issue_type.replace("_", " ").title(),
        description=risk.risk_explanation,
        severity=risk.severity,
        confidence=1.0,
        evidence=[risk.evidence_pointer],
        recommendation=risk.recommended_action,
        field_name=risk.field_name,
        issue_type=risk.issue_type,
        message=risk.risk_explanation,
        source_clause_text=risk.clause_text,
        source_page=risk.source_page,
        evidence_pointer=risk.evidence_pointer,
        manual_review_required=risk.legal_review_required,
        risk_engine_ready=True,
    )


def _deduplicate_clause_risks(risks: Sequence[ClauseRisk]) -> list[ClauseRisk]:
    """Remove duplicate risks and assign deterministic risk IDs."""
    deduped: list[ClauseRisk] = []
    seen: set[tuple[str, str, str, str]] = set()
    for risk in risks:
        key = (
            risk.field_name,
            risk.issue_type,
            risk.clause_text,
            risk.evidence_pointer.excerpt or "",
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(risk.model_copy(update={"risk_id": f"RISK-{len(deduped) + 1:03d}"}))
    return deduped
