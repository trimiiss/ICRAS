"""Build vendor-neutral CLM posting payloads from pipeline outputs."""

from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from schemas.approval_packet import ApprovalRoute, ApprovalStatus
from schemas.common import Severity
from schemas.finding import Finding
from schemas.obligation_result import ObligationRecord
from schemas.posting_payload import (
    ApprovalPostingData,
    ArtifactReference,
    ContractPostingData,
    CounterpartyPostingData,
    DecisionPostingData,
    ObligationPostingData,
    PostingPayload,
    RiskFindingPostingData,
    RiskPostingData,
)
from utils.collections import ordered_unique


def build_posting_payload(
    run_id: str,
    context: Mapping[str, Any],
    document_inventory: Mapping[str, Any],
    counterparty_resolution: Mapping[str, Any],
    decision_status: ApprovalStatus,
    decision_rationale: str,
    overall_severity: Severity,
    requires_human_review: bool,
    risk_summary: str,
    final_findings: Sequence[Finding],
    approval_routes: Sequence[ApprovalRoute],
    obligation_register: Mapping[str, Any],
    artifact_paths: Mapping[str, str],
) -> PostingPayload:
    """Build the vendor-neutral CLM posting payload."""
    return PostingPayload(
        run_id=run_id,
        contract=_contract_posting_data(context, document_inventory),
        counterparty=_counterparty_posting_data(context, counterparty_resolution),
        decision=DecisionPostingData(
            status=decision_status,
            approved=decision_status == ApprovalStatus.AUTO_APPROVE,
            rationale=decision_rationale,
            requires_human_review=requires_human_review,
        ),
        risk=_risk_posting_data(
            overall_severity=overall_severity,
            risk_summary=risk_summary,
            final_findings=final_findings,
        ),
        approval=ApprovalPostingData(
            approval_required=decision_status != ApprovalStatus.AUTO_APPROVE,
            routes=list(approval_routes),
            next_approvers=_next_approvers(approval_routes),
        ),
        obligations=_obligation_posting_data(obligation_register),
        artifacts=_artifact_references(artifact_paths),
        artifact_references=dict(artifact_paths),
        source_contract_file=str(context.get("contract_file") or ""),
    )


def _contract_posting_data(
    context: Mapping[str, Any],
    document_inventory: Mapping[str, Any],
) -> ContractPostingData:
    """Build contract metadata for the CLM payload."""
    primary_contract_id = _primary_contract_document_id(document_inventory)
    contract_file = str(context.get("contract_file") or "unknown_contract")
    return ContractPostingData(
        contract_id=_contract_id(context, primary_contract_id),
        document_id=primary_contract_id,
        bundle_name=str(context.get("bundle_name") or "unknown_bundle"),
        contract_type=str(context.get("contract_type") or "unknown_contract_type"),
        source_file=contract_file,
        jurisdiction=str(context.get("jurisdiction") or "unknown_jurisdiction"),
        effective_date=(
            str(context["effective_date"])
            if context.get("effective_date") is not None
            else None
        ),
    )


def _counterparty_posting_data(
    context: Mapping[str, Any],
    counterparty_resolution: Mapping[str, Any],
) -> CounterpartyPostingData:
    """Build counterparty metadata and matching summary for the CLM payload."""
    matches = counterparty_resolution.get("matches", [])
    match = _best_counterparty_match(matches if isinstance(matches, list) else [])
    if match is None:
        return CounterpartyPostingData(
            name=str(context.get("counterparty") or "unknown_counterparty"),
        )

    return CounterpartyPostingData(
        name=str(
            context.get("counterparty")
            or match.get("original_party_name")
            or "unknown_counterparty"
        ),
        resolution_status=str(match.get("match_status") or "unknown"),
        vendor_id=(
            str(match["vendor_id"])
            if match.get("vendor_id") is not None
            else None
        ),
        matched_vendor_name=(
            str(match["matched_vendor_name"])
            if match.get("matched_vendor_name") is not None
            else None
        ),
        match_confidence=(
            float(match["similarity_score"])
            if isinstance(match.get("similarity_score"), (int, float))
            else None
        ),
        manual_review_required=bool(match.get("manual_review_required")),
    )


def _risk_posting_data(
    overall_severity: Severity,
    risk_summary: str,
    final_findings: Sequence[Finding],
) -> RiskPostingData:
    """Build risk summary data for the CLM payload."""
    return RiskPostingData(
        overall_severity=overall_severity,
        summary=risk_summary,
        final_finding_count=len(final_findings),
        critical_finding_count=sum(
            1 for finding in final_findings if finding.severity == Severity.CRITICAL
        ),
        high_finding_count=sum(
            1 for finding in final_findings if finding.severity == Severity.HIGH
        ),
        categories=sorted({finding.category for finding in final_findings}),
        findings=[_risk_finding_posting_data(finding) for finding in final_findings],
    )


def _risk_finding_posting_data(finding: Finding) -> RiskFindingPostingData:
    """Map an internal final finding into the CLM risk-finding schema."""
    return RiskFindingPostingData(
        finding_id=finding.finding_id,
        category=finding.category,
        title=finding.title,
        description=finding.description,
        severity=finding.severity,
        confidence=finding.confidence,
        evidence=list(finding.evidence),
        recommendation=finding.recommendation,
        field_name=finding.field_name,
        issue_type=finding.issue_type,
    )


def _obligation_posting_data(
    obligation_register: Mapping[str, Any],
) -> list[ObligationPostingData]:
    """Map obligation-register records into the CLM payload schema."""
    raw_obligations = obligation_register.get("obligations", [])
    if not isinstance(raw_obligations, Sequence) or isinstance(
        raw_obligations, (str, bytes)
    ):
        return []

    mapped: list[ObligationPostingData] = []
    for raw_obligation in raw_obligations:
        obligation = ObligationRecord.model_validate(raw_obligation)
        mapped.append(
            ObligationPostingData(
                obligation_id=obligation.obligation_id,
                obligation_type=obligation.obligation_type,
                responsible_party=obligation.responsible_party,
                obligation_summary=obligation.obligation_summary,
                due_date=obligation.due_date,
                timing_trigger=obligation.timing_trigger,
                is_recurring=obligation.is_recurring,
                recurrence_frequency=obligation.recurrence_frequency,
                source_file=obligation.source_file,
                source_page=obligation.source_page,
                evidence_id=obligation.evidence_id,
                document_id=obligation.document_id,
                clause_reference=obligation.clause_reference,
                evidence_pointer=obligation.evidence_pointer,
            )
        )
    return mapped


def _artifact_references(artifact_paths: Mapping[str, str]) -> list[ArtifactReference]:
    """Build structured artifact references for CLM consumers."""
    return [
        ArtifactReference(
            name=name,
            path=path,
            artifact_type=_artifact_type(path),
            required=True,
        )
        for name, path in sorted(artifact_paths.items())
    ]


def _artifact_type(path: str) -> str:
    """Return a stable artifact type from a generated artifact path."""
    suffix = Path(path).suffix.lower().lstrip(".")
    if suffix == "md":
        return "markdown"
    if suffix == "jsonl":
        return "jsonl"
    return suffix or "file"


def _next_approvers(approval_routes: Sequence[ApprovalRoute]) -> list[str]:
    """Return flattened unique approvers in route order."""
    return ordered_unique(
        (
            approver
            for route in approval_routes
            for approver in route.approvers
        ),
        drop_blank=False,
        strip=False,
    )


def _best_counterparty_match(matches: Sequence[Any]) -> Optional[Mapping[str, Any]]:
    """Return the highest-confidence counterparty match, when available."""
    mapping_matches = [dict(match) for match in matches if isinstance(match, Mapping)]
    if not mapping_matches:
        return None
    return max(
        mapping_matches,
        key=lambda match: (
            float(match.get("similarity_score") or 0.0),
            not bool(match.get("manual_review_required")),
        ),
    )


def _primary_contract_document_id(document_inventory: Mapping[str, Any]) -> Optional[str]:
    """Return the primary contract document ID from intake inventory."""
    value = document_inventory.get("primary_contract_id")
    if isinstance(value, str) and value.strip():
        return value
    documents = document_inventory.get("documents", [])
    if not isinstance(documents, list):
        return None
    for document in documents:
        if isinstance(document, Mapping) and document.get("is_primary"):
            document_id = document.get("document_id")
            if isinstance(document_id, str) and document_id.strip():
                return document_id
    return None


def _contract_id(context: Mapping[str, Any], document_id: Optional[str]) -> str:
    """Build a stable contract identifier for downstream payloads."""
    return ":".join(
        part
        for part in [
            str(context.get("bundle_name") or "unknown_bundle"),
            document_id or "",
            str(context.get("contract_file") or "unknown_contract"),
        ]
        if part
    )
