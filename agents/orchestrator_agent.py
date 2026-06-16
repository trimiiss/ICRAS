"""Agent H - lead orchestration and final contract triage."""

import csv
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Callable, Dict, Iterable, Mapping, Optional, Sequence, TypedDict

from langgraph.graph import END, START, StateGraph

from agents.counterparty_agent import run_counterparty_check
from agents.extraction_agent import run_extraction
from agents.intake_agent import run_intake
from agents.risk_agent import run_risk_assessment
from agents.validation_agent import run_validation
from schemas.approval_packet import (
    ApprovalDecision,
    ApprovalPacket,
    ApprovalRoute,
    ApprovalStatus,
)
from schemas.common import EvidencePointer, Severity
from schemas.extracted_clause import ExtractedClause
from schemas.final_artifacts import FinalFindingsResult, PipelineMetrics
from schemas.finding import Finding
from schemas.obligation_result import ObligationRecord, ObligationRegisterResult
from schemas.posting_payload import PostingPayload
from schemas.risk_result import RiskResult
from utils.bundle_loader import load_bundle
from utils.evidence_indexer import build_evidence_index
from utils.run_manager import append_audit_event, create_run_folder, update_run_status


class OrchestratorAgentError(Exception):
    """Raised when Agent H orchestration cannot complete the pipeline."""


class ObligationRegisterError(Exception):
    """Raised when Agent H cannot produce obligations.csv."""


def _merge_dicts(left: Optional[dict[str, str]], right: Optional[dict[str, str]]) -> dict[str, str]:
    """Merge graph state dictionaries from parallel branches."""
    merged: dict[str, str] = {}
    if left:
        merged.update(left)
    if right:
        merged.update(right)
    return merged


def _append_lists(left: Optional[list[dict[str, Any]]], right: Optional[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Append graph state event lists from parallel branches."""
    return [*(left or []), *(right or [])]


class PipelineState(TypedDict, total=False):
    """Shared state for the Agent H LangGraph pipeline."""

    bundle_path: str
    bundle_data: dict[str, Any]
    run_info: dict[str, Any]
    run_id: str
    run_dir: str
    context_packet: dict[str, Any]
    document_inventory: dict[str, Any]
    evidence_index: dict[str, Any]
    extracted_contract: dict[str, Any]
    validation_result: dict[str, Any]
    counterparty_resolution: dict[str, Any]
    clause_analysis: dict[str, Any]
    risk_result: dict[str, Any]
    obligation_register: dict[str, Any]
    final_findings: dict[str, Any]
    approval_packet: dict[str, Any]
    posting_payload: dict[str, Any]
    metrics: dict[str, Any]
    artifact_paths: Annotated[dict[str, str], _merge_dicts]
    step_events: Annotated[list[dict[str, Any]], _append_lists]


OBLIGATION_CSV_COLUMNS: tuple[str, ...] = (
    "obligation_id",
    "obligation_type",
    "responsible_party",
    "obligation_summary",
    "due_date",
    "timing_trigger",
    "is_recurring",
    "recurrence_frequency",
    "source_clause_text",
    "source_file",
    "source_page",
    "evidence_id",
    "document_id",
    "clause_reference",
    "evidence_pointer",
)

OBLIGATION_TYPE_BY_CLAUSE: dict[str, str] = {
    "payment_terms": "payment",
    "payment": "payment",
    "fees": "payment",
    "termination": "termination_notice",
    "term_and_duration": "termination_notice",
    "data_protection": "compliance",
    "privacy": "compliance",
    "confidentiality": "confidentiality",
    "confidentiality_definition": "confidentiality",
    "indemnity": "indemnity",
    "indemnification": "indemnity",
    "auto_renewal": "renewal",
    "automatic_renewal": "renewal",
}

OBLIGATION_CUES: tuple[str, ...] = (
    "shall",
    "must",
    "will",
    "payable",
    "due",
    "comply",
    "protect",
    "indemnify",
    "return",
    "notice",
    "renew",
)

RESPONSIBLE_PARTIES: tuple[str, ...] = (
    "Customer",
    "Supplier",
    "Vendor",
    "Provider",
    "Each party",
    "Either party",
    "Receiving party",
    "Disclosing party",
    "Client",
    "Contractor",
)

DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d",
    "%m/%d/%Y",
    "%m-%d-%Y",
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

TIMING_PATTERNS: tuple[str, ...] = (
    r"\bwithin\s+\d+\s+(?:business\s+)?days?\b",
    r"\b\d+\s+days?\s+(?:written\s+)?notice\b",
    r"\bnet[\s-]?\d+\b",
    r"\bafter\s+\d+\s+(?:business\s+)?days?\b",
    r"\bprior\s+to\s+expiration\b",
    r"\bupon\s+[a-z ]{3,60}\b",
)

SEVERITY_RANK: dict[Severity, int] = {
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}

ROUTE_APPROVERS: dict[str, tuple[str, ...]] = {
    "LEGAL": ("legal_counsel",),
    "FINANCE": ("finance_manager",),
    "COMPLIANCE": ("compliance_officer",),
    "COUNTERPARTY": ("procurement_manager",),
    "MANUAL_REVIEW": ("contract_reviewer",),
    "GENERAL": ("department_head",),
}


def build_pipeline_graph() -> Any:
    """Build the Agent H LangGraph workflow.

    Returns:
        A compiled LangGraph application that runs Agent A through Agent H.
    """
    builder = StateGraph(PipelineState)
    builder.add_node("create_run", _pipeline_node("create_run", "orchestrator_agent", _create_run_node))
    builder.add_node("load_bundle", _pipeline_node("load_bundle", "bundle_loader", _load_bundle_node))
    builder.add_node("intake", _pipeline_node("intake", "intake_agent", _intake_node))
    builder.add_node("evidence_index", _pipeline_node("evidence_index", "evidence_indexer", _evidence_index_node))
    builder.add_node("extraction", _pipeline_node("extraction", "extraction_agent", _extraction_node))
    builder.add_node("counterparty", _pipeline_node("counterparty", "counterparty_agent", _counterparty_node))
    builder.add_node("validation", _pipeline_node("validation", "validation_agent", _validation_node))
    builder.add_node("risk_scoring", _pipeline_node("risk_scoring", "risk_agent", _risk_node))
    builder.add_node(
        "obligation_register",
        _pipeline_node("obligation_register", "orchestrator_agent", _obligation_register_node),
    )
    builder.add_node("agent_h_finalize", _pipeline_node("agent_h_finalize", "orchestrator_agent", _finalize_node))

    builder.add_edge(START, "create_run")
    builder.add_edge("create_run", "load_bundle")
    builder.add_edge("load_bundle", "intake")
    builder.add_edge("intake", "evidence_index")
    builder.add_edge("evidence_index", "extraction")
    builder.add_edge("extraction", "counterparty")
    builder.add_edge("extraction", "validation")
    builder.add_edge("counterparty", "risk_scoring")
    builder.add_edge("validation", "risk_scoring")
    builder.add_edge("risk_scoring", "obligation_register")
    builder.add_edge("obligation_register", "agent_h_finalize")
    builder.add_edge("agent_h_finalize", END)
    return builder.compile()


def run_pipeline(bundle_path: str) -> Dict[str, Any]:
    """Execute the full contract review pipeline through Agent H.

    Args:
        bundle_path: Path to the contract bundle folder.

    Returns:
        The final LangGraph state with run metadata and artifact paths.

    Raises:
        OrchestratorAgentError: If any pipeline node fails.
    """
    _load_optional_dotenv()
    graph = build_pipeline_graph()
    initial_state: PipelineState = {
        "bundle_path": bundle_path,
        "artifact_paths": {},
        "step_events": [],
    }
    return graph.invoke(
        initial_state,
        config={
            "run_name": "icras-agent-h-pipeline",
            "tags": ["icras", "agent-h"],
        },
    )


def _load_optional_dotenv() -> None:
    """Load .env when python-dotenv is installed."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


def _pipeline_node(
    step_name: str,
    agent_name: str,
    node_func: Callable[[PipelineState], PipelineState],
) -> Callable[[PipelineState], PipelineState]:
    """Wrap a graph node with audit logging and clear failure handling."""

    def wrapped(state: PipelineState) -> PipelineState:
        run_dir = state.get("run_dir")
        if run_dir:
            append_audit_event(
                run_dir,
                {
                    "event": f"{step_name}_started",
                    "agent": agent_name,
                    "message": f"{step_name} started.",
                },
            )
        try:
            update = node_func(state)
        except Exception as exc:
            _record_pipeline_failure(state, step_name, agent_name, exc)
            raise OrchestratorAgentError(f"{step_name} failed: {exc}") from exc

        completed_run_dir = update.get("run_dir") or run_dir
        if completed_run_dir:
            artifacts = sorted((update.get("artifact_paths") or {}).keys())
            append_audit_event(
                completed_run_dir,
                {
                    "event": f"{step_name}_completed",
                    "agent": agent_name,
                    "message": f"{step_name} completed.",
                    "artifacts": artifacts,
                },
            )
        node_update = dict(update)
        node_update["step_events"] = [{"step": step_name, "status": "completed"}]
        return node_update

    return wrapped


def _record_pipeline_failure(
    state: PipelineState,
    step_name: str,
    agent_name: str,
    exc: Exception,
) -> None:
    """Persist a failed node event when a run folder already exists."""
    run_dir = state.get("run_dir")
    if not run_dir:
        return
    error_message = str(exc)
    append_audit_event(
        run_dir,
        {
            "event": f"{step_name}_failed",
            "agent": agent_name,
            "message": f"{step_name} failed.",
            "error": error_message,
        },
    )
    update_run_status(run_dir, "failed", error_message)


def _create_run_node(state: PipelineState) -> PipelineState:
    """Create the deterministic run folder for this graph invocation."""
    bundle_path = _require_state_str(state, "bundle_path")
    run_info = create_run_folder(bundle_path)
    run_dir = str(run_info["run_dir"])
    return {
        "run_info": run_info,
        "run_id": str(run_info["run_id"]),
        "run_dir": run_dir,
        "artifact_paths": {
            "metadata": str(Path(run_dir) / "metadata.json"),
            "config": str(Path(run_dir) / "config.json"),
            "audit_log_jsonl": str(Path(run_dir) / "audit_log.jsonl"),
            "audit_log": str(Path(run_dir) / "audit_log.md"),
        },
    }


def _load_bundle_node(state: PipelineState) -> PipelineState:
    """Load and validate the source contract bundle."""
    bundle_data = load_bundle(_require_state_str(state, "bundle_path"))
    return {"bundle_data": bundle_data}


def _intake_node(state: PipelineState) -> PipelineState:
    """Run Agent A."""
    result = run_intake(
        bundle_data=_require_state_mapping(state, "bundle_data"),
        run_id=_require_state_str(state, "run_id"),
        run_dir=_require_state_str(state, "run_dir"),
    )
    return {
        "context_packet": result["context_packet"],
        "document_inventory": result["document_inventory"],
        "artifact_paths": result["artifact_paths"],
    }


def _evidence_index_node(state: PipelineState) -> PipelineState:
    """Build the source evidence index."""
    result = build_evidence_index(
        bundle_data=_require_state_mapping(state, "bundle_data"),
        document_inventory=_require_state_mapping(state, "document_inventory"),
        run_id=_require_state_str(state, "run_id"),
        run_dir=_require_state_str(state, "run_dir"),
    )
    return {
        "evidence_index": result["evidence_index"],
        "artifact_paths": result["artifact_paths"],
    }


def _extraction_node(state: PipelineState) -> PipelineState:
    """Run Agent B."""
    result = run_extraction(
        bundle_data=_require_state_mapping(state, "bundle_data"),
        document_inventory=_require_state_mapping(state, "document_inventory"),
        evidence_index=_require_state_mapping(state, "evidence_index"),
        run_id=_require_state_str(state, "run_id"),
        run_dir=_require_state_str(state, "run_dir"),
    )
    return {
        "extracted_contract": result["extracted_contract"],
        "artifact_paths": result["artifact_paths"],
    }


def _counterparty_node(state: PipelineState) -> PipelineState:
    """Run Agent C."""
    bundle_data = _require_state_mapping(state, "bundle_data")
    result = run_counterparty_check(
        context=_require_state_mapping(state, "context_packet"),
        extracted_contract=_require_state_mapping(state, "extracted_contract"),
        vendor_master_path=Path(str(bundle_data["bundle_dir"])) / "vendor_master.csv",
        run_dir=_require_state_str(state, "run_dir"),
        evidence_index=_require_state_mapping(state, "evidence_index"),
    )
    return {
        "counterparty_resolution": result["counterparty_resolution"],
        "artifact_paths": result["artifact_paths"],
    }


def _validation_node(state: PipelineState) -> PipelineState:
    """Run Agent D."""
    extracted_contract = _require_state_mapping(state, "extracted_contract")
    result = run_validation(
        context=_require_state_mapping(state, "context_packet"),
        clauses=list(extracted_contract.get("clauses", [])),
        run_dir=_require_state_str(state, "run_dir"),
        evidence_index=_require_state_mapping(state, "evidence_index"),
    )
    return {
        "validation_result": result["validation_result"],
        "artifact_paths": result["artifact_paths"],
    }


def _risk_node(state: PipelineState) -> PipelineState:
    """Run Agent E after Agents C and D complete."""
    result = run_risk_assessment(
        context=_require_state_mapping(state, "context_packet"),
        extracted_contract=_require_state_mapping(state, "extracted_contract"),
        validation_result=_require_state_mapping(state, "validation_result"),
        run_dir=_require_state_str(state, "run_dir"),
    )
    return {
        "clause_analysis": result["clause_analysis"],
        "risk_result": result["risk_result"],
        "artifact_paths": result["artifact_paths"],
    }


def _obligation_register_node(state: PipelineState) -> PipelineState:
    """Run Agent H obligation extraction from US-15."""
    result = run_obligation_register(
        context=_require_state_mapping(state, "context_packet"),
        extracted_contract=_require_state_mapping(state, "extracted_contract"),
        run_dir=_require_state_str(state, "run_dir"),
    )
    return {
        "obligation_register": result["obligation_register"],
        "artifact_paths": result["artifact_paths"],
    }


def _finalize_node(state: PipelineState) -> PipelineState:
    """Finalize Agent H findings, routing, and downstream artifacts."""
    run_id = _require_state_str(state, "run_id")
    run_dir = Path(_require_state_str(state, "run_dir"))
    context = _require_state_mapping(state, "context_packet")
    validation_result = _require_state_mapping(state, "validation_result")
    risk_result = _require_state_mapping(state, "risk_result")
    counterparty_resolution = _require_state_mapping(state, "counterparty_resolution")
    obligation_register = _require_state_mapping(state, "obligation_register")

    final_findings = _merge_deduplicate_sort_findings(
        run_id=run_id,
        context=context,
        validation_result=validation_result,
        risk_result=risk_result,
        counterparty_resolution=counterparty_resolution,
    )
    overall_severity = _overall_severity([finding.severity for finding in final_findings])
    approval_status, approval_routes = _build_approval_routes(
        context=context,
        findings=final_findings,
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
    artifact_paths = _merge_dicts(state.get("artifact_paths"), final_paths)

    approval_packet = ApprovalPacket(
        run_id=run_id,
        decision=ApprovalDecision(
            approved=approval_status == ApprovalStatus.AUTO_APPROVE,
            status=approval_status,
            rationale=_approval_rationale(approval_status, overall_severity, final_findings),
        ),
        risk_result=final_risk_result,
        approval_route=approval_routes,
        final_findings=final_findings,
        artifact_paths=artifact_paths,
    )
    posting_payload = PostingPayload(
        run_id=run_id,
        contract_id=_contract_id(context),
        bundle_name=str(context.get("bundle_name") or "unknown_bundle"),
        contract_type=str(context.get("contract_type") or "unknown_contract_type"),
        counterparty=str(context.get("counterparty") or "unknown_counterparty"),
        jurisdiction=str(context.get("jurisdiction") or "unknown_jurisdiction"),
        final_decision=approval_status,
        approved=approval_status == ApprovalStatus.AUTO_APPROVE,
        overall_severity=overall_severity,
        requires_human_review=requires_human_review,
        risk_summary=summary,
        approval_route=approval_routes,
        final_finding_count=len(final_findings),
        obligation_count=len(obligation_register.get("obligations", [])),
        artifact_references=artifact_paths,
        source_contract_file=str(context.get("contract_file") or ""),
    )
    metrics = _build_metrics(
        state=state,
        status="completed",
        overall_severity=overall_severity,
        final_finding_count=len(final_findings),
        artifact_paths=artifact_paths,
    )

    _write_json_file(Path(final_paths["final_findings"]), final_findings_result.model_dump(mode="json"))
    _write_exceptions_markdown(
        Path(final_paths["exceptions"]),
        run_id=run_id,
        approval_status=approval_status,
        overall_severity=overall_severity,
        approval_routes=approval_routes,
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
            "message": "Agent H merged findings, routed approval, and wrote final artifacts.",
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
        "artifact_paths": artifact_paths,
    }


def _require_state_str(state: Mapping[str, Any], key: str) -> str:
    """Return a required string from graph state."""
    value = state.get(key)
    if not isinstance(value, str) or not value:
        raise OrchestratorAgentError(
            f"Pipeline state is missing required string '{key}'."
        )
    return value


def _require_state_mapping(state: Mapping[str, Any], key: str) -> dict[str, Any]:
    """Return a required mapping from graph state."""
    value = state.get(key)
    if not isinstance(value, Mapping):
        raise OrchestratorAgentError(
            f"Pipeline state is missing required mapping '{key}'."
        )
    return dict(value)


def _merge_deduplicate_sort_findings(
    run_id: str,
    context: Mapping[str, Any],
    validation_result: Mapping[str, Any],
    risk_result: Mapping[str, Any],
    counterparty_resolution: Mapping[str, Any],
) -> list[Finding]:
    """Merge all source findings into a deterministic final list."""
    source_findings: list[Finding] = []
    source_findings.extend(
        _coerce_finding_list(
            validation_result.get("findings", []),
            fallback_prefix="VAL",
            context=context,
        )
    )
    source_findings.extend(
        _coerce_finding_list(
            risk_result.get("findings", []),
            fallback_prefix="RISK",
            context=context,
        )
    )
    source_findings.extend(
        _counterparty_findings(
            run_id=run_id,
            context=context,
            counterparty_resolution=counterparty_resolution,
        )
    )

    by_key: dict[tuple[str, ...], Finding] = {}
    for finding in source_findings:
        key = _finding_key(finding)
        if key in by_key:
            by_key[key] = _merge_findings(by_key[key], finding)
        else:
            by_key[key] = finding

    return sorted(
        by_key.values(),
        key=lambda finding: (
            -SEVERITY_RANK[finding.severity],
            finding.category.lower(),
            finding.finding_id,
        ),
    )


def _coerce_finding_list(
    raw_findings: Any,
    fallback_prefix: str,
    context: Mapping[str, Any],
) -> list[Finding]:
    """Validate finding dictionaries with clear Agent H errors."""
    if not isinstance(raw_findings, list):
        raise OrchestratorAgentError(
            f"Expected {fallback_prefix} findings to be a list."
        )

    findings: list[Finding] = []
    for index, raw_finding in enumerate(raw_findings, start=1):
        if not isinstance(raw_finding, Mapping):
            raise OrchestratorAgentError(
                f"Expected {fallback_prefix} finding {index} to be a mapping."
            )
        finding_data = dict(raw_finding)
        finding_data.setdefault("finding_id", f"{fallback_prefix}-{index:03d}")
        if not finding_data.get("evidence"):
            evidence_pointer = finding_data.get("evidence_pointer")
            if isinstance(evidence_pointer, Mapping):
                finding_data["evidence"] = [dict(evidence_pointer)]
            else:
                finding_data["evidence"] = [_fallback_evidence(context).model_dump(mode="json")]
        try:
            findings.append(Finding.model_validate(finding_data))
        except Exception as exc:
            raise OrchestratorAgentError(
                f"Invalid {fallback_prefix} finding {index}: {exc}"
            ) from exc
    return findings


def _counterparty_findings(
    run_id: str,
    context: Mapping[str, Any],
    counterparty_resolution: Mapping[str, Any],
) -> list[Finding]:
    """Convert flagged Agent C matches into shared findings."""
    raw_matches = counterparty_resolution.get("matches", [])
    if not isinstance(raw_matches, list):
        raise OrchestratorAgentError(
            "Expected counterparty_resolution['matches'] to be a list."
        )

    findings: list[Finding] = []
    for index, raw_match in enumerate(raw_matches, start=1):
        if not isinstance(raw_match, Mapping):
            continue
        match_status = str(raw_match.get("match_status") or "")
        risk_flag = str(raw_match.get("risk_flag") or "").strip()
        manual_review_required = bool(raw_match.get("manual_review_required"))
        if not manual_review_required and not risk_flag and match_status not in {"weak", "no_match"}:
            continue

        original_name = str(raw_match.get("original_party_name") or "Unknown party")
        evidence_data = raw_match.get("evidence_pointer")
        evidence = (
            EvidencePointer.model_validate(evidence_data)
            if isinstance(evidence_data, Mapping)
            else _fallback_evidence(context, excerpt=original_name)
        )
        severity = Severity.HIGH if match_status == "no_match" or risk_flag else Severity.MEDIUM
        findings.append(
            Finding(
                finding_id=f"CPY-{index:03d}",
                category="counterparty",
                title="Counterparty requires review",
                description=(
                    f"Counterparty '{original_name}' resolved with status "
                    f"'{match_status or 'unknown'}'."
                ),
                severity=severity,
                confidence=float(raw_match.get("similarity_score") or 0.0),
                evidence=[evidence],
                recommendation="Review counterparty identity before approval.",
                field_name="counterparty",
                issue_type="counterparty_resolution_review",
                message=risk_flag or "Counterparty match requires manual review.",
                source_clause_text=original_name,
                source_page=evidence.page_number,
                evidence_pointer=evidence,
                manual_review_required=True,
                risk_engine_ready=True,
            )
        )
    return findings


def _fallback_evidence(
    context: Mapping[str, Any],
    excerpt: Optional[str] = None,
) -> EvidencePointer:
    """Build a fallback evidence pointer for non-clause findings."""
    return EvidencePointer(
        source_file=str(context.get("contract_file") or "unknown"),
        excerpt=excerpt or "Finding created from structured pipeline output.",
    )


def _finding_key(finding: Finding) -> tuple[str, ...]:
    """Return a stable key used to deduplicate equivalent findings."""
    evidence = finding.evidence_pointer or finding.evidence[0]
    evidence_key = (
        evidence.evidence_id
        or f"{evidence.source_file}:{evidence.page_number}:{evidence.clause_reference}:{_normalize_key(evidence.excerpt or '')[:80]}"
    )
    return (
        _normalize_key(finding.issue_type or finding.title),
        _normalize_key(finding.field_name or finding.category),
        _normalize_key(evidence_key),
    )


def _merge_findings(existing: Finding, incoming: Finding) -> Finding:
    """Merge duplicate findings while preserving the strongest signal."""
    severity = (
        incoming.severity
        if SEVERITY_RANK[incoming.severity] > SEVERITY_RANK[existing.severity]
        else existing.severity
    )
    evidence_by_key: dict[tuple[str, ...], EvidencePointer] = {}
    for evidence in [*existing.evidence, *incoming.evidence]:
        key = (
            evidence.evidence_id or "",
            evidence.source_file,
            str(evidence.page_number or ""),
            evidence.clause_reference or "",
            evidence.excerpt or "",
        )
        evidence_by_key[key] = evidence
    evidence = list(evidence_by_key.values())
    primary_evidence = existing.evidence_pointer or evidence[0]
    return Finding(
        finding_id=existing.finding_id,
        category=existing.category,
        title=existing.title,
        description=existing.description,
        severity=severity,
        confidence=max(existing.confidence, incoming.confidence),
        evidence=evidence,
        recommendation=existing.recommendation or incoming.recommendation,
        field_name=existing.field_name or incoming.field_name,
        issue_type=existing.issue_type or incoming.issue_type,
        message=existing.message or incoming.message,
        source_clause_text=existing.source_clause_text or incoming.source_clause_text,
        source_page=existing.source_page or incoming.source_page,
        evidence_pointer=primary_evidence,
        manual_review_required=(
            existing.manual_review_required or incoming.manual_review_required
        ),
        risk_engine_ready=existing.risk_engine_ready or incoming.risk_engine_ready,
    )


def _overall_severity(severities: Sequence[Severity]) -> Severity:
    """Return the highest severity, defaulting to LOW."""
    if not severities:
        return Severity.LOW
    return max(severities, key=lambda severity: SEVERITY_RANK[severity])


def _build_approval_routes(
    context: Mapping[str, Any],
    findings: Sequence[Finding],
    overall_severity: Severity,
) -> tuple[ApprovalStatus, list[ApprovalRoute]]:
    """Route final findings to approver categories."""
    approval_policy = _as_mapping(context.get("approval_policy"))
    thresholds = _as_mapping(approval_policy.get("approval_thresholds"))
    severity_threshold = _as_mapping(thresholds.get(overall_severity.value))
    base_approvers = [
        str(approver)
        for approver in severity_threshold.get("required_approvers", [])
        if str(approver).strip()
    ]
    auto_approve = bool(severity_threshold.get("auto_approve", overall_severity == Severity.LOW))

    if not findings and auto_approve:
        return (
            ApprovalStatus.AUTO_APPROVE,
            [
                ApprovalRoute(
                    category="AUTO_APPROVE",
                    approvers=[],
                    reason="No routed exceptions were detected.",
                    finding_ids=[],
                )
            ],
        )

    route_data: dict[str, dict[str, Any]] = {}
    for finding in findings:
        category = _route_category(finding)
        data = route_data.setdefault(
            category,
            {
                "approvers": [],
                "finding_ids": [],
                "reason": _route_reason(category),
            },
        )
        data["finding_ids"].append(finding.finding_id)
        data["approvers"] = _ordered_unique(
            [
                *data["approvers"],
                *ROUTE_APPROVERS.get(category, ROUTE_APPROVERS["GENERAL"]),
                *base_approvers,
            ]
        )

    routes = [
        ApprovalRoute(
            category=category,
            approvers=list(data["approvers"]),
            reason=str(data["reason"]),
            finding_ids=list(data["finding_ids"]),
        )
        for category, data in sorted(route_data.items())
    ]
    if not routes and auto_approve:
        return (
            ApprovalStatus.AUTO_APPROVE,
            [
                ApprovalRoute(
                    category="AUTO_APPROVE",
                    approvers=[],
                    reason="Policy permits automatic approval.",
                    finding_ids=[],
                )
            ],
        )
    return ApprovalStatus.ESCALATE, routes


def _route_category(finding: Finding) -> str:
    """Map a finding to an approval route category."""
    text = " ".join(
        [
            finding.category,
            finding.field_name or "",
            finding.issue_type or "",
            finding.title,
            finding.description,
        ]
    ).lower()
    if "payment" in text or "invoice" in text or "fee" in text:
        return "FINANCE"
    if "gdpr" in text or "data" in text or "privacy" in text or "jurisdiction" in text:
        return "COMPLIANCE"
    if "counterparty" in text or "vendor" in text:
        return "COUNTERPARTY"
    if "confidence" in text or finding.manual_review_required:
        return "MANUAL_REVIEW"
    if "law" in text or "liability" in text or "indemn" in text or "termination" in text:
        return "LEGAL"
    return "LEGAL" if finding.severity in {Severity.HIGH, Severity.CRITICAL} else "GENERAL"


def _route_reason(category: str) -> str:
    """Return a human-readable reason for an approval route."""
    reasons = {
        "LEGAL": "Legal review is required for contract term risk.",
        "FINANCE": "Finance approval is required for commercial term risk.",
        "COMPLIANCE": "Compliance review is required for regulatory or jurisdiction risk.",
        "COUNTERPARTY": "Procurement review is required for counterparty identity risk.",
        "MANUAL_REVIEW": "Manual review is required because confidence or data quality is low.",
        "GENERAL": "Business approval is required for a routed exception.",
    }
    return reasons.get(category, "Review is required for a routed exception.")


def _ordered_unique(values: Iterable[str]) -> list[str]:
    """Return values in first-seen order with blanks removed."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = str(value).strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result


def _approval_rationale(
    approval_status: ApprovalStatus,
    overall_severity: Severity,
    findings: Sequence[Finding],
) -> str:
    """Create a deterministic approval rationale."""
    if approval_status == ApprovalStatus.AUTO_APPROVE:
        return "No routed exceptions were detected, so the contract can be auto-approved."
    return (
        f"{len(findings)} finding(s) require review. "
        f"The highest severity is {overall_severity.value}."
    )


def _final_risk_summary(overall_severity: Severity, findings: Sequence[Finding]) -> str:
    """Create a deterministic final risk summary."""
    if not findings:
        return "No findings were detected by the contract review pipeline."
    categories = ", ".join(sorted({finding.category for finding in findings}))
    return (
        f"{len(findings)} final finding(s) detected across {categories}. "
        f"Highest severity: {overall_severity.value}."
    )


def _contract_id(context: Mapping[str, Any]) -> str:
    """Build a stable contract identifier for downstream payloads."""
    return ":".join(
        [
            str(context.get("bundle_name") or "unknown_bundle"),
            str(context.get("contract_file") or "unknown_contract"),
        ]
    )


def _build_metrics(
    state: PipelineState,
    status: str,
    overall_severity: Severity,
    final_finding_count: int,
    artifact_paths: Mapping[str, str],
) -> PipelineMetrics:
    """Build final pipeline metrics."""
    run_info = _require_state_mapping(state, "run_info")
    metadata = _as_mapping(run_info.get("metadata"))
    extracted_contract = _require_state_mapping(state, "extracted_contract")
    validation_result = _require_state_mapping(state, "validation_result")
    risk_result = _require_state_mapping(state, "risk_result")
    counterparty_resolution = _require_state_mapping(state, "counterparty_resolution")
    obligation_register = _require_state_mapping(state, "obligation_register")
    clauses = extracted_contract.get("clauses", [])
    clause_count = len(clauses) if isinstance(clauses, list) else 0
    duration_seconds = _duration_since(metadata.get("created_at"))
    return PipelineMetrics(
        run_id=_require_state_str(state, "run_id"),
        status=status,
        duration_seconds=duration_seconds,
        extraction_clause_count=clause_count,
        validation_finding_count=_safe_len(validation_result.get("findings")),
        risk_finding_count=_safe_len(risk_result.get("findings")),
        counterparty_exception_count=len(
            _counterparty_findings(
                run_id=_require_state_str(state, "run_id"),
                context=_require_state_mapping(state, "context_packet"),
                counterparty_resolution=counterparty_resolution,
            )
        ),
        final_finding_count=final_finding_count,
        obligation_count=_safe_len(obligation_register.get("obligations")),
        fallback_assisted=bool(extracted_contract.get("fallback_assisted")),
        exception_rate=(
            0.0 if clause_count == 0 else round(final_finding_count / clause_count, 4)
        ),
        overall_severity=overall_severity,
        artifact_paths=dict(artifact_paths),
    )


def _duration_since(created_at: Any) -> float:
    """Return seconds since an ISO timestamp, or zero when missing."""
    if not isinstance(created_at, str) or not created_at:
        return 0.0
    try:
        start = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return max(0.0, round((datetime.now(timezone.utc) - start).total_seconds(), 6))


def _safe_len(value: Any) -> int:
    """Return len(value) when value is a list, else zero."""
    return len(value) if isinstance(value, list) else 0


def _write_json_file(path: Path, data: Mapping[str, Any]) -> None:
    """Write deterministic JSON to a run artifact path."""
    with path.open("w", encoding="utf-8") as file:
        json.dump(dict(data), file, indent=2, ensure_ascii=False, sort_keys=True)
        file.write("\n")


def _write_exceptions_markdown(
    path: Path,
    run_id: str,
    approval_status: ApprovalStatus,
    overall_severity: Severity,
    approval_routes: Sequence[ApprovalRoute],
    findings: Sequence[Finding],
) -> None:
    """Write the human-readable exception summary."""
    lines = [
        "# ICRAS Exceptions Summary",
        "",
        f"- Run ID: {run_id}",
        f"- Decision: {approval_status.value}",
        f"- Overall Severity: {overall_severity.value}",
        f"- Final Finding Count: {len(findings)}",
        "",
        "## Approval Routes",
    ]
    if approval_routes:
        for route in approval_routes:
            approvers = ", ".join(route.approvers) if route.approvers else "None"
            finding_ids = ", ".join(route.finding_ids) if route.finding_ids else "None"
            lines.extend(
                [
                    f"### {route.category}",
                    f"- Approvers: {approvers}",
                    f"- Reason: {route.reason}",
                    f"- Findings: {finding_ids}",
                    "",
                ]
            )
    else:
        lines.extend(["No approval routes were required.", ""])

    lines.append("## Findings")
    if not findings:
        lines.extend(["No exceptions were detected.", ""])
    for finding in findings:
        evidence = finding.evidence_pointer or finding.evidence[0]
        evidence_bits = [
            evidence.source_file,
            f"page {evidence.page_number}" if evidence.page_number else None,
            evidence.clause_reference,
            evidence.evidence_id,
        ]
        evidence_text = " | ".join(bit for bit in evidence_bits if bit)
        lines.extend(
            [
                f"### {finding.finding_id}: {finding.title}",
                f"- Severity: {finding.severity.value}",
                f"- Category: {finding.category}",
                f"- Reason: {finding.description}",
                f"- Recommendation: {finding.recommendation or 'Review before approval.'}",
                f"- Evidence: {evidence_text or evidence.source_file}",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def _as_mapping(value: Any) -> Mapping[str, Any]:
    """Return value when it is a mapping, else an empty mapping."""
    return value if isinstance(value, Mapping) else {}


def run_obligation_register(
    context: Dict[str, Any],
    extracted_contract: Dict[str, Any],
    run_dir: str | Path,
) -> Dict[str, Any]:
    """Generate ``obligations.csv`` from extracted contract clauses.

    Args:
        context: Context packet data from Agent A.
        extracted_contract: Extracted contract data from Agent B.
        run_dir: Run directory where ``obligations.csv`` must be written.

    Returns:
        A dictionary containing the obligation register and artifact path.

    Raises:
        ObligationRegisterError: If inputs are malformed or CSV output fails.
    """
    run_path = _validate_run_dir(run_dir)
    clauses = _coerce_clauses(extracted_contract.get("clauses", []))
    run_id = str(context.get("run_id") or extracted_contract.get("run_id") or "unknown-run")

    obligations = _extract_obligations(
        context=context,
        clauses=clauses,
    )
    register = ObligationRegisterResult(run_id=run_id, obligations=obligations)

    output_path = run_path / "obligations.csv"
    _write_obligations_csv(output_path, register)
    append_audit_event(
        run_path,
        {
            "event": "obligation_register_completed",
            "agent": "orchestrator_agent",
            "message": "Agent H generated the obligation register.",
            "artifacts": [output_path.name],
            "obligation_count": len(register.obligations),
        },
    )

    result = register.model_dump(mode="json")
    return {
        "obligation_register": result,
        "obligations": result["obligations"],
        "artifact_paths": {"obligations": str(output_path)},
    }


def _extract_obligations(
    context: Mapping[str, Any],
    clauses: Sequence[ExtractedClause],
) -> list[ObligationRecord]:
    """Extract obligation records from clauses."""
    obligations: list[ObligationRecord] = []
    for clause in clauses:
        obligation_type = _obligation_type_for_clause(clause)
        if obligation_type is None:
            continue
        if not _has_obligation_signal(clause.text):
            continue
        if _is_negative_renewal_clause(clause):
            continue

        evidence = _clause_evidence(context, clause)
        obligation = ObligationRecord(
            obligation_id=f"OBL-{len(obligations) + 1:03d}",
            obligation_type=obligation_type,
            responsible_party=_extract_responsible_party(clause.text),
            obligation_summary=_summarize_obligation(clause.text),
            due_date=_extract_due_date(clause.text),
            timing_trigger=_extract_timing_trigger(clause.text),
            is_recurring=_is_recurring(clause.text),
            recurrence_frequency=_recurrence_frequency(clause.text, obligation_type),
            source_clause_text=_truncate(clause.text),
            source_file=evidence.source_file,
            source_page=evidence.page_number,
            evidence_id=evidence.evidence_id,
            document_id=evidence.document_id,
            clause_reference=evidence.clause_reference,
            evidence_pointer=evidence,
        )
        obligations.append(obligation)

    return obligations


def _obligation_type_for_clause(clause: ExtractedClause) -> Optional[str]:
    """Return the obligation type for a clause, when supported."""
    candidates = (
        _normalize_key(clause.clause_type),
        _normalize_key(clause.title),
    )
    for candidate in candidates:
        for alias, obligation_type in OBLIGATION_TYPE_BY_CLAUSE.items():
            normalized_alias = _normalize_key(alias)
            if candidate == normalized_alias or normalized_alias in candidate:
                return obligation_type
    return None


def _has_obligation_signal(text: str) -> bool:
    """Return whether text contains deterministic obligation language."""
    normalized_text = text.lower()
    return any(re.search(rf"\b{re.escape(cue)}\b", normalized_text) for cue in OBLIGATION_CUES)


def _is_negative_renewal_clause(clause: ExtractedClause) -> bool:
    """Skip renewal clauses that explicitly say there is no renewal."""
    if _obligation_type_for_clause(clause) != "renewal":
        return False
    return bool(
        re.search(
            r"\b(?:does not|will not|shall not|must not)\s+auto[- ]?renew\b",
            clause.text,
            re.IGNORECASE,
        )
    )


def _extract_responsible_party(text: str) -> str:
    """Extract the responsible party from obligation text."""
    for party in RESPONSIBLE_PARTIES:
        if re.search(rf"\b{re.escape(party)}\b", text, re.IGNORECASE):
            return party
    return "Unspecified"


def _summarize_obligation(text: str) -> str:
    """Return a compact sentence-level obligation summary."""
    normalized = " ".join(text.split())
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", normalized)
        if sentence.strip()
    ]
    for sentence in sentences:
        if _has_obligation_signal(sentence):
            return _truncate(sentence, max_chars=240)
    return _truncate(normalized, max_chars=240)


def _extract_due_date(text: str) -> Optional[str]:
    """Extract an absolute due date as ISO 8601 when present."""
    for pattern in DATE_CANDIDATE_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match is None:
            continue
        normalized = _normalize_date(match.group(0))
        if normalized is not None:
            return normalized
    return None


def _extract_timing_trigger(text: str) -> Optional[str]:
    """Extract relative timing language for obligation tracking."""
    normalized_text = " ".join(text.split())
    for pattern in TIMING_PATTERNS:
        match = re.search(pattern, normalized_text, re.IGNORECASE)
        if match is not None:
            return match.group(0)
    return None


def _is_recurring(text: str) -> bool:
    """Return whether obligation text indicates recurrence."""
    return _recurrence_frequency(text, obligation_type=None) is not None


def _recurrence_frequency(
    text: str,
    obligation_type: Optional[str],
) -> Optional[str]:
    """Return a recurring cadence when detectable."""
    normalized = text.lower()
    if re.search(r"\bmonthly\b|\beach month\b", normalized):
        return "monthly"
    if re.search(r"\bannually\b|\beach year\b|\byearly\b", normalized):
        return "annually"
    if "automatic renewal" in normalized or "auto-renew" in normalized:
        return "annually"
    if obligation_type == "payment" and re.search(r"\binvoice|invoices\b", normalized):
        return "per invoice"
    return None


def _coerce_clauses(raw_clauses: Any) -> list[ExtractedClause]:
    """Convert extracted clause dictionaries to Pydantic models."""
    if not isinstance(raw_clauses, list):
        raise ObligationRegisterError(
            "Expected extracted_contract['clauses'] to be a list."
        )

    clauses: list[ExtractedClause] = []
    for index, raw_clause in enumerate(raw_clauses, start=1):
        if not isinstance(raw_clause, Mapping):
            raise ObligationRegisterError(
                f"Expected extracted_contract['clauses'][{index - 1}] to be a mapping."
            )

        clause_data = dict(raw_clause)
        clause_type = str(clause_data.get("clause_type") or f"clause_{index}")
        text = str(clause_data.get("text") or clause_data.get("clause_text") or "")
        if not text.strip():
            raise ObligationRegisterError(
                f"extracted_contract['clauses'][{index - 1}] is missing clause text."
            )

        clause_data.setdefault("clause_id", f"CLAUSE-{index:03d}")
        clause_data.setdefault("clause_type", clause_type)
        clause_data.setdefault("title", clause_type.replace("_", " ").title())
        clause_data.setdefault("text", text)
        clause_data.setdefault("clause_text", text)
        clause_data.setdefault("confidence", 1.0)
        clause_data.setdefault("confidence_score", clause_data["confidence"])
        if "page_numbers" not in clause_data and clause_data.get("page_number") is not None:
            clause_data["page_numbers"] = [clause_data["page_number"]]
        clause_data.setdefault(
            "evidence",
            EvidencePointer(
                source_file="unknown",
                page_number=_optional_int(clause_data.get("page_number")),
                clause_reference=_optional_str(clause_data.get("section_reference")),
                excerpt=_truncate(text),
            ).model_dump(mode="json"),
        )
        clause_data.setdefault("evidence_pointer", clause_data["evidence"])
        clause_data.setdefault(
            "manual_review_required",
            float(clause_data["confidence"]) < 0.75,
        )

        try:
            clauses.append(ExtractedClause.model_validate(clause_data))
        except Exception as exc:
            raise ObligationRegisterError(
                f"extracted_contract['clauses'][{index - 1}] is invalid: {exc}"
            ) from exc

    return clauses


def _validate_run_dir(run_dir: str | Path) -> Path:
    """Return a valid run directory path or raise a clear error."""
    run_path = Path(run_dir).resolve()
    if not run_path.exists():
        raise ObligationRegisterError(
            f"Run directory does not exist: {run_path}. "
            "Create it with create_run_folder before obligation extraction."
        )
    if not run_path.is_dir():
        raise ObligationRegisterError(f"Run path is not a directory: {run_path}")
    return run_path


def _write_obligations_csv(path: Path, register: ObligationRegisterResult) -> None:
    """Write the obligation register with deterministic columns."""
    try:
        with open(path, "w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=OBLIGATION_CSV_COLUMNS)
            writer.writeheader()
            for obligation in register.obligations:
                writer.writerow(_obligation_to_csv_row(obligation))
    except OSError as exc:
        raise ObligationRegisterError(
            f"Failed to write obligations artifact '{path}': {exc}"
        ) from exc


def _obligation_to_csv_row(obligation: ObligationRecord) -> dict[str, str]:
    """Convert an obligation model into a CSV row."""
    row = obligation.model_dump(mode="json")
    row["is_recurring"] = "true" if obligation.is_recurring else "false"
    row["source_page"] = "" if obligation.source_page is None else str(obligation.source_page)
    row["evidence_pointer"] = json.dumps(
        row["evidence_pointer"],
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        column: "" if row.get(column) is None else str(row.get(column, ""))
        for column in OBLIGATION_CSV_COLUMNS
    }


def _clause_evidence(
    context: Mapping[str, Any],
    clause: ExtractedClause,
) -> EvidencePointer:
    """Build a source pointer from an extracted clause."""
    return EvidencePointer(
        evidence_id=clause.evidence.evidence_id,
        document_id=clause.evidence.document_id,
        source_file=str(context.get("contract_file") or clause.evidence.source_file),
        page_number=clause.page_number or clause.evidence.page_number,
        clause_reference=clause.section_reference or clause.evidence.clause_reference,
        excerpt=_truncate(clause.text),
    )


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

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass

    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(value, date_format).date().isoformat()
        except ValueError:
            continue
    return None


def _normalize_key(value: str) -> str:
    """Normalize free-form text for alias matching."""
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _optional_str(value: Any) -> Optional[str]:
    """Return value as a string when non-empty, else None."""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
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
    """Return a compact source snippet."""
    normalized = " ".join(value.split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."
