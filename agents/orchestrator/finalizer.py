"""Final workflow artifact assembly and routing."""

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from agents.orchestrator.approval_routing import (
    approval_rationale,
    build_approval_routes,
    triage_findings,
)
from agents.orchestrator.finding_merger import (
    merge_deduplicate_sort_findings,
    overall_severity as highest_overall_severity,
)
from agents.orchestrator.markdown import write_exceptions_markdown
from agents.orchestrator.metrics import build_metrics
from agents.orchestrator.state import require_state_mapping, require_state_str
from schemas.approval_packet import ApprovalDecision, ApprovalPacket, ApprovalStatus
from schemas.common import Severity
from schemas.final_artifacts import FinalFindingsResult
from schemas.finding import Finding
from schemas.risk_result import RiskResult
from utils.determinism import build_determinism_result
from utils.mapping import as_mapping as _as_mapping
from utils.posting_payload_builder import build_posting_payload
from utils.run_manager import append_audit_event, update_run_status


def finalize_pipeline(state: Mapping[str, Any]) -> dict[str, Any]:
    """Finalize findings, routing, and downstream artifacts."""
    run_id = require_state_str(state, "run_id")
    run_dir = Path(require_state_str(state, "run_dir"))
    run_info = require_state_mapping(state, "run_info")
    context = require_state_mapping(state, "context_packet")
    document_inventory = require_state_mapping(state, "document_inventory")
    validation_result = require_state_mapping(state, "validation_result")
    risk_result = require_state_mapping(state, "risk_result")
    compliance_result = require_state_mapping(state, "compliance_result")
    anomaly_result = require_state_mapping(state, "anomaly_result")
    counterparty_resolution = require_state_mapping(state, "counterparty_resolution")
    obligation_register = require_state_mapping(state, "obligation_register")
    final_findings = merge_deduplicate_sort_findings(
        run_id=run_id,
        context=context,
        validation_result=validation_result,
        risk_result=risk_result,
        counterparty_resolution=counterparty_resolution,
        compliance_result=compliance_result,
        anomaly_result=anomaly_result,
    )
    overall_severity = highest_overall_severity(
        [finding.severity for finding in final_findings]
    )
    approval_status, exceptions = triage_findings(
        context=context,
        findings=final_findings,
        overall_severity=overall_severity,
    )
    approval_routes = build_approval_routes(
        context=context,
        exceptions=exceptions,
        approval_status=approval_status,
        overall_severity=overall_severity,
    )
    requires_human_review = approval_status != ApprovalStatus.AUTO_APPROVE
    summary = _final_risk_summary(overall_severity, final_findings)

    final_risk_result = RiskResult(
        overall_severity=overall_severity,
        findings=final_findings,
        requires_human_review=requires_human_review,
        summary=summary,
        total_findings=len(final_findings),
    )
    final_findings_result = FinalFindingsResult(
        run_id=run_id,
        overall_severity=overall_severity,
        findings=final_findings,
        total_findings=len(final_findings),
        requires_human_review=requires_human_review,
    )

    final_paths = {
        "final_findings": str(run_dir / "final_findings.json"),
        "exceptions": str(run_dir / "exceptions.md"),
        "approval_packet": str(run_dir / "approval_packet.json"),
        "posting_payload": str(run_dir / "posting_payload.json"),
        "metrics": str(run_dir / "metrics.json"),
    }
    artifact_paths = _merge_dicts(_as_mapping(state.get("artifact_paths")), final_paths)
    decision_rationale = approval_rationale(
        approval_status,
        overall_severity,
        final_findings,
    )

    approval_packet = ApprovalPacket(
        run_id=run_id,
        decision=ApprovalDecision(
            approved=approval_status == ApprovalStatus.AUTO_APPROVE,
            status=approval_status,
            rationale=decision_rationale,
        ),
        risk_result=final_risk_result,
        approval_route=approval_routes,
        exceptions=exceptions,
        final_findings=final_findings,
        artifact_paths=artifact_paths,
    )
    posting_payload = build_posting_payload(
        run_id=run_id,
        context=context,
        document_inventory=document_inventory,
        counterparty_resolution=counterparty_resolution,
        decision_status=approval_status,
        decision_rationale=decision_rationale,
        overall_severity=overall_severity,
        requires_human_review=requires_human_review,
        risk_summary=summary,
        final_findings=final_findings,
        approval_routes=approval_routes,
        obligation_register=obligation_register,
        artifact_paths=artifact_paths,
    )
    determinism_result = build_determinism_result(
        current_run_dir=run_dir,
        current_run_id=run_id,
        bundle_path=str(_as_mapping(run_info.get("metadata")).get("bundle_path") or ""),
        current_risk_result=final_risk_result.model_dump(mode="json"),
        current_approval_decision=approval_packet.decision.model_dump(mode="json"),
    )
    metrics = build_metrics(
        state=state,
        status="completed",
        overall_severity=overall_severity,
        final_finding_count=len(final_findings),
        exceptions=exceptions,
        final_findings=final_findings,
        determinism_result=determinism_result,
        artifact_paths=artifact_paths,
    )

    _write_json_file(Path(final_paths["final_findings"]), final_findings_result.model_dump(mode="json"))
    write_exceptions_markdown(
        Path(final_paths["exceptions"]),
        run_id=run_id,
        approval_status=approval_status,
        overall_severity=overall_severity,
        approval_routes=approval_routes,
        exceptions=exceptions,
        findings=final_findings,
    )
    _write_json_file(Path(final_paths["approval_packet"]), approval_packet.model_dump(mode="json"))
    _write_json_file(Path(final_paths["posting_payload"]), posting_payload.model_dump(mode="json"))
    _write_json_file(Path(final_paths["metrics"]), metrics.model_dump(mode="json"))

    append_audit_event(
        run_dir,
        {
            "event": "agent_h_finalized",
            "agent": "orchestrator_agent",
            "message": "Workflow orchestration merged findings, routed approval, and wrote final artifacts.",
            "artifacts": [Path(path).name for path in final_paths.values()],
            "final_finding_count": len(final_findings),
            "overall_severity": overall_severity.value,
            "approval_status": approval_status.value,
        },
    )
    update_run_status(run_dir, "completed")
    return {
        "final_findings": final_findings_result.model_dump(mode="json"),
        "risk_result": final_risk_result.model_dump(mode="json"),
        "approval_packet": approval_packet.model_dump(mode="json"),
        "posting_payload": posting_payload.model_dump(mode="json"),
        "metrics": metrics.model_dump(mode="json"),
        "idempotency_result": _as_mapping(state.get("idempotency_result")),
        "artifact_paths": artifact_paths,
    }

def _final_risk_summary(overall_severity: Severity, findings: Sequence[Finding]) -> str:
    """Create a deterministic final risk summary."""
    if not findings:
        return "No findings were detected by the contract review pipeline."
    categories = ", ".join(sorted({finding.category for finding in findings}))
    return (
        f"{len(findings)} final finding(s) detected across {categories}. "
        f"Highest severity: {overall_severity.value}."
    )


def _write_json_file(path: Path, data: Mapping[str, Any]) -> None:
    """Write deterministic JSON to a run artifact path."""
    with path.open("w", encoding="utf-8") as file:
        json.dump(dict(data), file, indent=2, ensure_ascii=False, sort_keys=True)
        file.write("\n")


def _merge_dicts(left: Mapping[str, str], right: Mapping[str, str]) -> dict[str, str]:
    """Merge graph state dictionaries from finalization inputs."""
    merged = dict(left)
    merged.update(right)
    return merged
