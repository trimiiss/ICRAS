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
from schemas.exception_triage import ExceptionTriageItem
from schemas.extracted_clause import ExtractedClause
from schemas.final_artifacts import (
    AgentAuditTrace,
    ConfidenceDistribution,
    FinalFindingsResult,
    PipelineMetrics,
)
from schemas.finding import Finding
from schemas.obligation_result import ObligationRecord, ObligationRegisterResult
from schemas.posting_payload import (
    ApprovalPostingData,
    ArtifactReference,
    ContractPostingData,
    CounterpartyPostingData,
    DecisionPostingData,
    PostingPayload,
    RiskPostingData,
)
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

PIPELINE_STEP_ORDER: tuple[str, ...] = (
    "create_run",
    "load_bundle",
    "intake",
    "evidence_index",
    "extraction",
    "counterparty",
    "validation",
    "risk_scoring",
    "obligation_register",
    "agent_h_finalize",
)

STEP_INPUT_ARTIFACTS: dict[str, tuple[str, ...]] = {
    "evidence_index": ("document_inventory",),
    "extraction": ("document_inventory", "evidence_index"),
    "counterparty": ("context_packet", "extracted_contract", "evidence_index"),
    "validation": ("context_packet", "extracted_contract", "evidence_index"),
    "risk_scoring": ("context_packet", "extracted_contract", "validation_findings"),
    "obligation_register": ("context_packet", "extracted_contract"),
    "agent_h_finalize": (
        "context_packet",
        "document_inventory",
        "extracted_contract",
        "validation_findings",
        "counterparty_resolution",
        "clause_analysis",
        "risk_result",
        "obligations",
    ),
}

LOW_CONFIDENCE_AUDIT_THRESHOLD = 0.75
DETERMINISM_COMPARED_SECTIONS: tuple[str, ...] = (
    "risk_result",
    "approval_decision",
)
DETERMINISM_TIMESTAMP_FIELDS: tuple[str, ...] = (
    "created_at",
    "updated_at",
    "reviewed_at",
    "timestamp",
    "started_at",
    "finished_at",
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
        started_at = datetime.now(timezone.utc)
        input_paths = _audit_input_paths(step_name, state)
        run_dir = state.get("run_dir")
        if run_dir:
            append_audit_event(
                run_dir,
                {
                    "event": f"{step_name}_started",
                    "agent": agent_name,
                    "message": f"{step_name} started.",
                    "timestamp": started_at.isoformat(),
                    "input_paths": input_paths,
                },
            )
        try:
            update = node_func(state)
        except Exception as exc:
            _record_pipeline_failure(state, step_name, agent_name, exc)
            raise OrchestratorAgentError(f"{step_name} failed: {exc}") from exc

        finished_at = datetime.now(timezone.utc)
        completed_run_dir = update.get("run_dir") or run_dir
        step_trace = _build_agent_audit_trace(
            step_name=step_name,
            agent_name=agent_name,
            state=state,
            update=update,
            started_at=started_at,
            finished_at=finished_at,
            input_paths=input_paths,
        )
        if completed_run_dir:
            if not run_dir:
                append_audit_event(
                    completed_run_dir,
                    {
                        "event": f"{step_name}_started",
                        "agent": agent_name,
                        "message": f"{step_name} started.",
                        "timestamp": started_at.isoformat(),
                        "input_paths": input_paths,
                    },
                )
            artifacts = sorted((update.get("artifact_paths") or {}).keys())
            append_audit_event(
                completed_run_dir,
                {
                    "event": f"{step_name}_completed",
                    "agent": agent_name,
                    "message": f"{step_name} completed.",
                    "timestamp": finished_at.isoformat(),
                    "artifacts": artifacts,
                    "duration_seconds": step_trace["duration_seconds"],
                    "input_paths": step_trace["input_paths"],
                    "output_paths": step_trace["output_paths"],
                    "extracted_clause_count": step_trace["extracted_clause_count"],
                    "exception_count": step_trace["exception_count"],
                    "exception_categories": step_trace["exception_categories"],
                    "fallback_used": step_trace["fallback_used"],
                    "fallback_reason": step_trace["fallback_reason"],
                    "low_confidence_count": step_trace["low_confidence_count"],
                    "confidence_distribution": step_trace["confidence_distribution"],
                },
            )
        node_update = dict(update)
        node_update["step_events"] = [step_trace]
        if step_name == "agent_h_finalize" and completed_run_dir:
            _write_final_audit_markdown(
                run_dir=Path(completed_run_dir),
                run_id=str(update.get("run_id") or state.get("run_id") or ""),
                step_events=[*(state.get("step_events") or []), step_trace],
                metrics=_as_mapping(update.get("metrics")),
                approval_packet=_as_mapping(update.get("approval_packet")),
                final_findings=_as_mapping(update.get("final_findings")),
                extracted_contract=_as_mapping(state.get("extracted_contract")),
                artifact_paths=_as_mapping(update.get("artifact_paths")),
            )
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


def _build_agent_audit_trace(
    step_name: str,
    agent_name: str,
    state: PipelineState,
    update: PipelineState,
    started_at: datetime,
    finished_at: datetime,
    input_paths: Mapping[str, str],
) -> dict[str, Any]:
    """Build one validated audit-trace entry for a pipeline node."""
    extracted_contract = _as_mapping(update.get("extracted_contract") or state.get("extracted_contract"))
    metrics = _as_mapping(update.get("metrics"))
    approval_packet = _as_mapping(update.get("approval_packet") or state.get("approval_packet"))
    exceptions = approval_packet.get("exceptions")
    exception_categories = _exception_category_counts(exceptions)

    if not exception_categories and isinstance(metrics.get("exception_categories"), Mapping):
        exception_categories = {
            str(category): int(count)
            for category, count in metrics["exception_categories"].items()
            if isinstance(count, int)
        }

    clause_scores = _confidence_scores_from_clauses(extracted_contract.get("clauses"))
    confidence_distribution = _confidence_distribution(clause_scores)
    fallback_reason = extracted_contract.get("fallback_reason") or metrics.get("fallback_reason")
    raw_low_confidence_count = metrics.get("low_confidence_count")
    low_confidence_count = (
        int(raw_low_confidence_count)
        if isinstance(raw_low_confidence_count, int)
        else confidence_distribution.low_count
    )

    trace = AgentAuditTrace(
        step=step_name,
        agent=agent_name,
        status="completed",
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=round((finished_at - started_at).total_seconds(), 6),
        input_paths=dict(input_paths),
        output_paths=_audit_output_paths(step_name, state, update),
        extracted_clause_count=_safe_len(extracted_contract.get("clauses")),
        exception_count=sum(exception_categories.values()),
        exception_categories=exception_categories,
        fallback_used=bool(extracted_contract.get("fallback_assisted") or metrics.get("fallback_assisted")),
        fallback_reason=str(fallback_reason) if fallback_reason else None,
        low_confidence_count=low_confidence_count,
        confidence_distribution=confidence_distribution,
    )
    return trace.model_dump(mode="json")


def _audit_input_paths(step_name: str, state: PipelineState) -> dict[str, str]:
    """Return deterministic input paths for one audit step."""
    paths: dict[str, str] = {}
    bundle_path = state.get("bundle_path")
    if isinstance(bundle_path, str) and bundle_path:
        paths["bundle"] = str(Path(bundle_path).resolve())

    bundle_data = _as_mapping(state.get("bundle_data"))
    bundle_dir = bundle_data.get("bundle_dir")
    if isinstance(bundle_dir, str) and bundle_dir:
        if step_name == "load_bundle":
            for filename in (
                "manifest.yaml",
                "contract.pdf",
                "vendor_master.csv",
                "playbook.yaml",
                "approval_policy.yaml",
                "jurisdiction_rules.yaml",
            ):
                paths[filename] = str(Path(bundle_dir) / filename)
        if step_name in {
            "evidence_index",
            "extraction",
            "counterparty",
            "validation",
            "risk_scoring",
            "obligation_register",
            "agent_h_finalize",
        }:
            contract_path = bundle_data.get("contract_path")
            if isinstance(contract_path, str) and contract_path:
                paths["contract"] = contract_path
        if step_name == "counterparty":
            paths["vendor_master"] = str(Path(bundle_dir) / "vendor_master.csv")

    artifact_paths = _as_mapping(state.get("artifact_paths"))
    for artifact_name in STEP_INPUT_ARTIFACTS.get(step_name, ()):
        artifact_path = artifact_paths.get(artifact_name)
        if isinstance(artifact_path, str) and artifact_path:
            paths[artifact_name] = artifact_path

    return {key: paths[key] for key in sorted(paths)}


def _audit_output_paths(
    step_name: str,
    state: PipelineState,
    update: PipelineState,
) -> dict[str, str]:
    """Return deterministic output paths newly written by one audit step."""
    previous_paths = _as_mapping(state.get("artifact_paths"))
    updated_paths = _as_mapping(update.get("artifact_paths"))
    outputs = {
        str(name): str(path)
        for name, path in updated_paths.items()
        if isinstance(path, str) and previous_paths.get(name) != path
    }
    if step_name == "create_run":
        outputs = {
            str(name): str(path)
            for name, path in updated_paths.items()
            if isinstance(path, str)
        }
    return {key: outputs[key] for key in sorted(outputs)}


def _exception_category_counts(raw_exceptions: Any) -> dict[str, int]:
    """Count routed exception categories from serialized or model exceptions."""
    if not isinstance(raw_exceptions, Sequence) or isinstance(raw_exceptions, (str, bytes)):
        return {}

    counts: dict[str, int] = {}
    for raw_exception in raw_exceptions:
        category: Any = None
        if isinstance(raw_exception, Mapping):
            category = raw_exception.get("category")
        elif hasattr(raw_exception, "category"):
            category = getattr(raw_exception, "category")
        if hasattr(category, "value"):
            category = category.value
        if not isinstance(category, str) or not category:
            category = "uncategorized"
        counts[category] = counts.get(category, 0) + 1
    return {key: counts[key] for key in sorted(counts)}


def _confidence_scores_from_clauses(raw_clauses: Any) -> list[float]:
    """Collect normalized confidence scores from extracted clauses."""
    if not isinstance(raw_clauses, Sequence) or isinstance(raw_clauses, (str, bytes)):
        return []

    scores: list[float] = []
    for raw_clause in raw_clauses:
        if not isinstance(raw_clause, Mapping):
            continue
        score = _confidence_value(raw_clause.get("confidence_score", raw_clause.get("confidence")))
        if score is not None:
            scores.append(score)
    return scores


def _confidence_scores_from_findings(raw_findings: Any) -> list[float]:
    """Collect normalized confidence scores from final findings."""
    if not isinstance(raw_findings, Sequence) or isinstance(raw_findings, (str, bytes)):
        return []

    scores: list[float] = []
    for raw_finding in raw_findings:
        if isinstance(raw_finding, Mapping):
            score = _confidence_value(raw_finding.get("confidence"))
        elif hasattr(raw_finding, "confidence"):
            score = _confidence_value(getattr(raw_finding, "confidence"))
        else:
            score = None
        if score is not None:
            scores.append(score)
    return scores


def _confidence_value(raw_value: Any) -> Optional[float]:
    """Return a valid confidence value or None."""
    try:
        score = float(raw_value)
    except (TypeError, ValueError):
        return None
    if score < 0.0 or score > 1.0:
        return None
    return score


def _confidence_distribution(scores: Sequence[float]) -> ConfidenceDistribution:
    """Bucket confidence scores for metrics and audit output."""
    if not scores:
        return ConfidenceDistribution()

    clean_scores = [min(max(float(score), 0.0), 1.0) for score in scores]
    return ConfidenceDistribution(
        count=len(clean_scores),
        low_count=sum(score < LOW_CONFIDENCE_AUDIT_THRESHOLD for score in clean_scores),
        medium_count=sum(
            LOW_CONFIDENCE_AUDIT_THRESHOLD <= score < 0.9
            for score in clean_scores
        ),
        high_count=sum(score >= 0.9 for score in clean_scores),
        min_score=round(min(clean_scores), 4),
        max_score=round(max(clean_scores), 4),
        average_score=round(sum(clean_scores) / len(clean_scores), 4),
    )


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
    run_info = _require_state_mapping(state, "run_info")
    context = _require_state_mapping(state, "context_packet")
    document_inventory = _require_state_mapping(state, "document_inventory")
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
    approval_status, exceptions = _triage_findings(
        context=context,
        findings=final_findings,
        overall_severity=overall_severity,
    )
    approval_routes = _build_approval_routes(
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
    artifact_paths = _merge_dicts(state.get("artifact_paths"), final_paths)
    decision_rationale = _approval_rationale(approval_status, overall_severity, final_findings)

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
    posting_payload = _build_posting_payload(
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
        obligation_count=len(obligation_register.get("obligations", [])),
        artifact_paths=artifact_paths,
    )
    determinism_result = _build_determinism_result(
        current_run_dir=run_dir,
        current_run_id=run_id,
        bundle_path=str(_as_mapping(run_info.get("metadata")).get("bundle_path") or ""),
        current_risk_result=final_risk_result.model_dump(mode="json"),
        current_approval_decision=approval_packet.decision.model_dump(mode="json"),
    )
    metrics = _build_metrics(
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
    _write_exceptions_markdown(
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


def _triage_findings(
    context: Mapping[str, Any],
    findings: Sequence[Finding],
    overall_severity: Severity,
) -> tuple[ApprovalStatus, list[ExceptionTriageItem]]:
    """Convert findings into configured per-exception triage items."""
    approval_policy = _as_mapping(context.get("approval_policy"))
    auto_approve = _policy_allows_auto_approval(
        approval_policy=approval_policy,
        overall_severity=overall_severity,
    )

    if not findings:
        if auto_approve:
            _require_auto_approve_routing(approval_policy)
            return ApprovalStatus.AUTO_APPROVE, []
        return ApprovalStatus.ESCALATE, []

    rules = _exception_route_rules(approval_policy)
    exceptions: list[ExceptionTriageItem] = []
    for finding in findings:
        matched_rule = _match_exception_route_rule(finding, rules)
        if matched_rule is None:
            raise OrchestratorAgentError(
                "No exception routing rule matched finding "
                f"'{finding.finding_id}' "
                f"(issue_type={finding.issue_type or 'unknown'}, "
                f"field_name={finding.field_name or 'unknown'}). "
                "Update approval_policy.yaml exception_routing.rules."
            )
        exceptions.append(_build_exception_triage_item(finding, matched_rule))

    return ApprovalStatus.ESCALATE, exceptions


def _build_approval_routes(
    context: Mapping[str, Any],
    exceptions: Sequence[ExceptionTriageItem],
    approval_status: ApprovalStatus,
    overall_severity: Severity,
) -> list[ApprovalRoute]:
    """Build grouped approval routes from per-exception triage items."""
    approval_policy = _as_mapping(context.get("approval_policy"))
    if approval_status == ApprovalStatus.AUTO_APPROVE:
        auto_route = _require_auto_approve_routing(approval_policy)
        return [
            ApprovalRoute(
                category=str(auto_route["category"]),
                approvers=[],
                reason=str(auto_route["reason"]),
                finding_ids=[],
            )
        ]

    route_data: dict[str, dict[str, Any]] = {}
    base_approvers = _severity_required_approvers(
        approval_policy=approval_policy,
        overall_severity=overall_severity,
    )
    for exception in exceptions:
        category = exception.category.value
        data = route_data.setdefault(
            category,
            {
                "approvers": [],
                "finding_ids": [],
                "reasons": [],
            },
        )
        data["finding_ids"].append(exception.finding_id)
        data["reasons"] = _ordered_unique([*data["reasons"], exception.reason])
        data["approvers"] = _ordered_unique(
            [
                *data["approvers"],
                exception.approver or "",
                *base_approvers,
            ]
        )

    return [
        ApprovalRoute(
            category=category,
            approvers=list(data["approvers"]),
            reason="; ".join(data["reasons"]),
            finding_ids=list(data["finding_ids"]),
        )
        for category, data in sorted(route_data.items())
    ]


def _policy_allows_auto_approval(
    approval_policy: Mapping[str, Any],
    overall_severity: Severity,
) -> bool:
    """Return whether the policy permits auto-approval for a severity level."""
    thresholds = _as_mapping(approval_policy.get("approval_thresholds"))
    severity_threshold = _as_mapping(thresholds.get(overall_severity.value))
    return bool(severity_threshold.get("auto_approve", False))


def _severity_required_approvers(
    approval_policy: Mapping[str, Any],
    overall_severity: Severity,
) -> list[str]:
    """Return policy approvers required for a severity level."""
    thresholds = _as_mapping(approval_policy.get("approval_thresholds"))
    severity_threshold = _as_mapping(thresholds.get(overall_severity.value))
    approvers = severity_threshold.get("required_approvers", [])
    if not isinstance(approvers, list):
        return []
    return _ordered_unique(str(approver) for approver in approvers)


def _exception_route_rules(approval_policy: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Return configured exception route rules or raise a clear error."""
    routing = _as_mapping(approval_policy.get("exception_routing"))
    rules = routing.get("rules")
    if not isinstance(rules, list) or not rules:
        raise OrchestratorAgentError(
            "approval_policy.yaml must define exception_routing.rules before "
            "Agent H can route exceptions."
        )
    return [dict(rule) for rule in rules if isinstance(rule, Mapping)]


def _require_auto_approve_routing(approval_policy: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return configured auto-approval routing details or raise a clear error."""
    routing = _as_mapping(approval_policy.get("exception_routing"))
    auto_approve = _as_mapping(routing.get("auto_approve"))
    required_fields = ("category", "reason", "next_action")
    missing = [
        field
        for field in required_fields
        if not str(auto_approve.get(field) or "").strip()
    ]
    if missing:
        raise OrchestratorAgentError(
            "approval_policy.yaml exception_routing.auto_approve is missing: "
            + ", ".join(missing)
        )
    return auto_approve


def _match_exception_route_rule(
    finding: Finding,
    rules: Sequence[Mapping[str, Any]],
) -> Optional[Mapping[str, Any]]:
    """Return the first configured route rule that matches a finding."""
    for rule in rules:
        if _exception_route_rule_matches(finding, rule):
            return rule
    return None


def _exception_route_rule_matches(
    finding: Finding,
    rule: Mapping[str, Any],
) -> bool:
    """Return whether a configured route rule matches a finding."""
    checks: list[bool] = []
    issue_types = _normalized_policy_values(rule.get("match_issue_types"))
    if issue_types:
        checks.append(_normalize_key(finding.issue_type or "") in issue_types)

    field_names = _normalized_policy_values(rule.get("match_field_names"))
    if field_names:
        checks.append(_normalize_key(finding.field_name or "") in field_names)

    categories = _normalized_policy_values(rule.get("match_categories"))
    if categories:
        checks.append(_normalize_key(finding.category) in categories)

    text_fragments = _normalized_policy_values(rule.get("match_text"))
    if text_fragments:
        haystack = _normalize_key(
            " ".join(
                [
                    finding.category,
                    finding.field_name or "",
                    finding.issue_type or "",
                    finding.title,
                    finding.description,
                    finding.message or "",
                    finding.source_clause_text or "",
                ]
            )
        )
        checks.append(any(fragment in haystack for fragment in text_fragments))

    manual_review_required = rule.get("manual_review_required")
    if isinstance(manual_review_required, bool):
        checks.append(finding.manual_review_required is manual_review_required)

    max_confidence = rule.get("max_confidence")
    if isinstance(max_confidence, (int, float)):
        checks.append(float(finding.confidence) <= float(max_confidence))

    return bool(checks) and all(checks)


def _build_exception_triage_item(
    finding: Finding,
    rule: Mapping[str, Any],
) -> ExceptionTriageItem:
    """Build one schema-valid exception triage item from a matched rule."""
    return ExceptionTriageItem(
        finding_id=finding.finding_id,
        category=str(rule["category"]),
        approver=str(rule.get("approver") or ""),
        reason=str(rule["reason"]),
        next_action=str(rule["next_action"]),
        severity=finding.severity,
        evidence=list(finding.evidence),
        source_title=finding.title,
        issue_type=finding.issue_type,
        field_name=finding.field_name,
    )


def _normalized_policy_values(raw_values: Any) -> set[str]:
    """Normalize a policy list into comparable string keys."""
    if not isinstance(raw_values, list):
        return set()
    return {
        _normalize_key(str(value))
        for value in raw_values
        if str(value).strip()
    }


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


def _build_posting_payload(
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
    obligation_count: int,
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
        name=str(context.get("counterparty") or match.get("original_party_name") or "unknown_counterparty"),
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
    )


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
    return _ordered_unique(
        approver
        for route in approval_routes
        for approver in route.approvers
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


def _build_determinism_result(
    current_run_dir: Path,
    current_run_id: str,
    bundle_path: str,
    current_risk_result: Mapping[str, Any],
    current_approval_decision: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare current risk and decision outputs to the latest same-bundle run."""
    baseline_run_dir = _find_previous_completed_run(
        current_run_dir=current_run_dir,
        current_run_id=current_run_id,
        bundle_path=bundle_path,
    )
    if baseline_run_dir is None:
        return _compare_determinism_payloads(
            baseline_payload={
                "risk_result": current_risk_result,
                "approval_decision": current_approval_decision,
            },
            current_payload={
                "risk_result": current_risk_result,
                "approval_decision": current_approval_decision,
            },
            baseline_run_id=None,
        )

    baseline_packet = _load_json_mapping(baseline_run_dir / "approval_packet.json")
    baseline_metadata = _load_json_mapping(baseline_run_dir / "metadata.json")
    return _compare_determinism_payloads(
        baseline_payload={
            "risk_result": _as_mapping(baseline_packet.get("risk_result")),
            "approval_decision": _as_mapping(baseline_packet.get("decision")),
        },
        current_payload={
            "risk_result": current_risk_result,
            "approval_decision": current_approval_decision,
        },
        baseline_run_id=str(
            baseline_metadata.get("run_id") or baseline_run_dir.name
        ),
    )


def _find_previous_completed_run(
    current_run_dir: Path,
    current_run_id: str,
    bundle_path: str,
) -> Optional[Path]:
    """Return the latest previous completed run for the same bundle."""
    runs_dir = current_run_dir.parent
    if not runs_dir.is_dir():
        return None

    normalized_bundle_path = _normalized_path_key(bundle_path)
    candidates: list[tuple[str, Path]] = []
    for run_dir in runs_dir.iterdir():
        if not run_dir.is_dir() or run_dir.resolve() == current_run_dir.resolve():
            continue
        metadata = _load_json_mapping(run_dir / "metadata.json")
        if metadata.get("run_id") == current_run_id:
            continue
        if metadata.get("status") != "completed":
            continue
        if _normalized_path_key(metadata.get("bundle_path")) != normalized_bundle_path:
            continue
        if not (run_dir / "approval_packet.json").is_file():
            continue
        candidates.append((str(metadata.get("created_at") or run_dir.name), run_dir))

    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate[0])[1]


def _load_json_mapping(path: Path) -> Mapping[str, Any]:
    """Read a JSON object from disk, returning an empty mapping on failure."""
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, Mapping) else {}


def _normalized_path_key(value: Any) -> str:
    """Return a stable comparable path string."""
    if not isinstance(value, str) or not value:
        return ""
    try:
        return str(Path(value).resolve()).casefold()
    except OSError:
        return value.casefold()


def _compare_determinism_payloads(
    baseline_payload: Mapping[str, Any],
    current_payload: Mapping[str, Any],
    baseline_run_id: Optional[str],
) -> dict[str, Any]:
    """Compare deterministic output sections while ignoring timestamp fields."""
    differences: list[str] = []
    for section in DETERMINISM_COMPARED_SECTIONS:
        baseline_section = _strip_determinism_ignored_fields(
            baseline_payload.get(section)
        )
        current_section = _strip_determinism_ignored_fields(
            current_payload.get(section)
        )
        _collect_determinism_differences(
            path=section,
            baseline=baseline_section,
            current=current_section,
            differences=differences,
        )

    return {
        "determinism_check": "PASS" if not differences else "FAIL",
        "determinism_baseline_run_id": baseline_run_id,
        "determinism_compared_sections": list(DETERMINISM_COMPARED_SECTIONS),
        "determinism_excluded_timestamp_fields": list(
            DETERMINISM_TIMESTAMP_FIELDS
        ),
        "determinism_differences": differences,
    }


def _strip_determinism_ignored_fields(value: Any) -> Any:
    """Remove timestamp fields recursively before determinism comparison."""
    if isinstance(value, Mapping):
        return {
            str(key): _strip_determinism_ignored_fields(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if not _is_determinism_timestamp_field(str(key))
        }
    if isinstance(value, list):
        return [_strip_determinism_ignored_fields(item) for item in value]
    return value


def _is_determinism_timestamp_field(key: str) -> bool:
    """Return whether a field is timestamp-like and ignored."""
    normalized_key = key.lower()
    return (
        normalized_key in DETERMINISM_TIMESTAMP_FIELDS
        or normalized_key.endswith("_at")
        or "timestamp" in normalized_key
    )


def _collect_determinism_differences(
    path: str,
    baseline: Any,
    current: Any,
    differences: list[str],
) -> None:
    """Append non-timestamp differences between two normalized values."""
    if type(baseline) is not type(current):
        differences.append(
            f"{path}: baseline={_format_determinism_value(baseline)}; "
            f"current={_format_determinism_value(current)}"
        )
        return

    if isinstance(baseline, Mapping) and isinstance(current, Mapping):
        keys = sorted(set(baseline) | set(current))
        for key in keys:
            child_path = f"{path}.{key}"
            if key not in baseline:
                differences.append(
                    f"{child_path}: baseline=<missing>; "
                    f"current={_format_determinism_value(current[key])}"
                )
                continue
            if key not in current:
                differences.append(
                    f"{child_path}: baseline="
                    f"{_format_determinism_value(baseline[key])}; "
                    "current=<missing>"
                )
                continue
            _collect_determinism_differences(
                path=child_path,
                baseline=baseline[key],
                current=current[key],
                differences=differences,
            )
        return

    if isinstance(baseline, list) and isinstance(current, list):
        if len(baseline) != len(current):
            differences.append(
                f"{path}: baseline_length={len(baseline)}; "
                f"current_length={len(current)}"
            )
        for index, (baseline_item, current_item) in enumerate(zip(baseline, current)):
            _collect_determinism_differences(
                path=f"{path}[{index}]",
                baseline=baseline_item,
                current=current_item,
                differences=differences,
            )
        return

    if baseline != current:
        differences.append(
            f"{path}: baseline={_format_determinism_value(baseline)}; "
            f"current={_format_determinism_value(current)}"
        )


def _format_determinism_value(value: Any) -> str:
    """Format a comparison value for compact metrics differences."""
    if isinstance(value, (Mapping, list)):
        formatted = json.dumps(value, sort_keys=True, ensure_ascii=False)
    else:
        formatted = repr(value)
    if len(formatted) <= 200:
        return formatted
    return formatted[:197].rstrip() + "..."


def _build_metrics(
    state: PipelineState,
    status: str,
    overall_severity: Severity,
    final_finding_count: int,
    exceptions: Sequence[ExceptionTriageItem],
    final_findings: Sequence[Finding],
    determinism_result: Mapping[str, Any],
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
    clause_confidence_distribution = _confidence_distribution(
        _confidence_scores_from_clauses(clauses)
    )
    finding_confidence_distribution = _confidence_distribution(
        _confidence_scores_from_findings(final_findings)
    )
    exception_categories = _exception_category_counts(exceptions)
    exception_count = len(exceptions)
    exception_rate = 0.0 if clause_count == 0 else round(exception_count / clause_count, 4)
    exception_rate_percent = round(exception_rate * 100, 2)
    throughput = (
        0.0 if duration_seconds <= 0.0 else round(clause_count / duration_seconds, 4)
    )
    accuracy_percent = (
        0.0
        if clause_confidence_distribution.average_score is None
        else round(clause_confidence_distribution.average_score * 100, 2)
    )
    return PipelineMetrics(
        run_id=_require_state_str(state, "run_id"),
        status=status,
        duration_seconds=duration_seconds,
        total_processing_time_seconds=duration_seconds,
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
        exception_count=exception_count,
        exception_categories=exception_categories,
        obligation_count=_safe_len(obligation_register.get("obligations")),
        fallback_assisted=bool(extracted_contract.get("fallback_assisted")),
        fallback_reason=(
            str(extracted_contract.get("fallback_reason"))
            if extracted_contract.get("fallback_reason")
            else None
        ),
        low_confidence_count=(
            clause_confidence_distribution.low_count
            + finding_confidence_distribution.low_count
        ),
        confidence_distributions={
            "clauses": clause_confidence_distribution,
            "final_findings": finding_confidence_distribution,
        },
        throughput_clauses_per_second=throughput,
        accuracy_percent=accuracy_percent,
        exception_rate=exception_rate,
        exception_rate_percent=exception_rate_percent,
        determinism_check=str(determinism_result.get("determinism_check") or "FAIL"),
        determinism_baseline_run_id=_optional_str(
            determinism_result.get("determinism_baseline_run_id")
        ),
        determinism_compared_sections=[
            str(section)
            for section in determinism_result.get("determinism_compared_sections", [])
        ],
        determinism_excluded_timestamp_fields=[
            str(field)
            for field in determinism_result.get(
                "determinism_excluded_timestamp_fields",
                [],
            )
        ],
        determinism_differences=[
            str(difference)
            for difference in determinism_result.get("determinism_differences", [])
        ],
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
    exceptions: Sequence[ExceptionTriageItem],
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
        f"- Routed Exception Count: {len(exceptions)}",
        "",
        "## Next Actions",
    ]
    if exceptions:
        for exception in exceptions:
            lines.extend(
                [
                    f"- {exception.category.value}: {exception.next_action} "
                    f"(Approver: {exception.approver})",
                ]
            )
        lines.append("")
    else:
        lines.extend(["- No human approval required.", ""])

    lines.append("## Approval Routes")
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

    lines.append("## Exceptions")
    if not exceptions:
        lines.extend(["No exceptions were detected.", ""])
    for exception in exceptions:
        evidence_text = _format_evidence_list(exception.evidence)
        lines.extend(
            [
                f"### {exception.category.value}: {exception.source_title}",
                f"- Finding ID: {exception.finding_id}",
                f"- Severity: {exception.severity.value}",
                f"- Approver: {exception.approver or 'None'}",
                f"- Reason: {exception.reason}",
                f"- Next Action: {exception.next_action}",
                f"- Evidence: {evidence_text}",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_final_audit_markdown(
    run_dir: Path,
    run_id: str,
    step_events: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, Any],
    approval_packet: Mapping[str, Any],
    final_findings: Mapping[str, Any],
    extracted_contract: Mapping[str, Any],
    artifact_paths: Mapping[str, Any],
) -> None:
    """Write the auditor-facing step-by-step Markdown trace."""
    audit_path = run_dir / "audit_log.md"
    decision = _as_mapping(approval_packet.get("decision"))
    exception_categories = _as_mapping(metrics.get("exception_categories"))
    confidence_distributions = _as_mapping(metrics.get("confidence_distributions"))
    ordered_events = _ordered_step_events(step_events)

    lines = [
        "# ICRAS Audit Log",
        "",
        "## Run Summary",
        f"- Run ID: {run_id}",
        f"- Status: {metrics.get('status', '')}",
        f"- Decision: {decision.get('status', '')}",
        f"- Processing Duration Seconds: {_format_metric(metrics.get('total_processing_time_seconds'))}",
        f"- Extraction Count: {metrics.get('extraction_clause_count', 0)}",
        f"- Exception Count: {metrics.get('exception_count', 0)}",
        f"- Exception Categories: {_format_count_map(exception_categories)}",
        f"- Exception Rate Percent: {_format_metric(metrics.get('exception_rate_percent'))}",
        f"- Accuracy Percent: {_format_metric(metrics.get('accuracy_percent'))}",
        f"- Throughput Clauses Per Second: {_format_metric(metrics.get('throughput_clauses_per_second'))}",
        f"- Fallback Used: {'yes' if metrics.get('fallback_assisted') else 'no'}",
        f"- Fallback Reason: {metrics.get('fallback_reason') or 'None'}",
        f"- Low-Confidence Count: {metrics.get('low_confidence_count', 0)}",
        "",
        "## Workflow Order",
    ]

    for index, event in enumerate(ordered_events, start=1):
        step = str(event.get("step") or "unknown_step")
        agent = str(event.get("agent") or "unknown_agent")
        lines.append(f"{index}. {step}_completed ({agent})")

    lines.extend(["", "## Step Trace"])
    for event in ordered_events:
        step = str(event.get("step") or "unknown_step")
        lines.extend(
            [
                f"### {step}_completed",
                f"- Agent: {event.get('agent', '')}",
                f"- Status: {event.get('status', '')}",
                f"- Started At: {event.get('started_at', '')}",
                f"- Finished At: {event.get('finished_at', '')}",
                f"- Duration Seconds: {_format_metric(event.get('duration_seconds'))}",
                f"- Extracted Clause Count: {event.get('extracted_clause_count', 0)}",
                f"- Exception Count: {event.get('exception_count', 0)}",
                f"- Exception Categories: {_format_count_map(_as_mapping(event.get('exception_categories')))}",
                f"- Fallback Used: {'yes' if event.get('fallback_used') else 'no'}",
                f"- Fallback Reason: {event.get('fallback_reason') or 'None'}",
                f"- Low-Confidence Count: {event.get('low_confidence_count', 0)}",
                "",
                "#### Inputs",
            ]
        )
        lines.extend(_format_path_map(_as_mapping(event.get("input_paths"))))
        lines.extend(["", "#### Outputs"])
        lines.extend(_format_path_map(_as_mapping(event.get("output_paths"))))
        lines.append("")

    lines.append("## Confidence Scores")
    if confidence_distributions:
        for name in sorted(confidence_distributions):
            lines.extend(
                _format_confidence_distribution(
                    name,
                    _as_mapping(confidence_distributions[name]),
                )
            )
    else:
        lines.append("- No confidence scores recorded.")

    lines.extend(["", "## Low-Confidence Cases"])
    low_confidence_cases = _low_confidence_cases(
        extracted_contract=extracted_contract,
        final_findings=final_findings,
    )
    if low_confidence_cases:
        lines.extend(f"- {case}" for case in low_confidence_cases)
    else:
        lines.append("- No low-confidence cases detected.")

    lines.extend(["", "## Generated Artifacts"])
    lines.extend(_format_path_map(artifact_paths))
    lines.append("")

    audit_path.write_text("\n".join(lines), encoding="utf-8")


def _ordered_step_events(
    step_events: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Return step events in stable workflow order."""
    by_step = {
        str(event.get("step")): event
        for event in step_events
        if isinstance(event, Mapping) and event.get("step")
    }
    return [by_step[step] for step in PIPELINE_STEP_ORDER if step in by_step]


def _format_metric(value: Any) -> str:
    """Format numeric metrics without unstable trailing precision."""
    if isinstance(value, (int, float)):
        return f"{float(value):.4f}".rstrip("0").rstrip(".")
    return str(value) if value is not None else "0"


def _format_count_map(values: Mapping[str, Any]) -> str:
    """Format a small count mapping for Markdown."""
    if not values:
        return "None"
    return ", ".join(f"{key}={values[key]}" for key in sorted(values))


def _format_path_map(paths: Mapping[str, Any]) -> list[str]:
    """Format path mappings as Markdown bullets."""
    if not paths:
        return ["- None recorded."]
    return [f"- {name}: {paths[name]}" for name in sorted(paths)]


def _format_confidence_distribution(
    name: str,
    distribution: Mapping[str, Any],
) -> list[str]:
    """Format one confidence distribution for Markdown."""
    return [
        f"### {name}",
        f"- Count: {distribution.get('count', 0)}",
        f"- Low: {distribution.get('low_count', 0)}",
        f"- Medium: {distribution.get('medium_count', 0)}",
        f"- High: {distribution.get('high_count', 0)}",
        f"- Min: {_format_metric(distribution.get('min_score'))}",
        f"- Max: {_format_metric(distribution.get('max_score'))}",
        f"- Average: {_format_metric(distribution.get('average_score'))}",
    ]


def _low_confidence_cases(
    extracted_contract: Mapping[str, Any],
    final_findings: Mapping[str, Any],
) -> list[str]:
    """Return human-readable low-confidence clause and finding summaries."""
    cases: list[str] = []
    clauses = extracted_contract.get("clauses")
    if isinstance(clauses, list):
        for clause in clauses:
            if not isinstance(clause, Mapping):
                continue
            score = _confidence_value(clause.get("confidence_score", clause.get("confidence")))
            if score is None or score >= LOW_CONFIDENCE_AUDIT_THRESHOLD:
                continue
            clause_id = str(clause.get("clause_id") or "unknown_clause")
            clause_type = str(clause.get("clause_type") or "unknown_type")
            title = str(clause.get("title") or clause_type)
            cases.append(
                f"Clause {clause_id} ({clause_type}) confidence={score:.2f}: {title}"
            )

    findings = final_findings.get("findings")
    if isinstance(findings, list):
        for finding in findings:
            if not isinstance(finding, Mapping):
                continue
            score = _confidence_value(finding.get("confidence"))
            if score is None or score >= LOW_CONFIDENCE_AUDIT_THRESHOLD:
                continue
            finding_id = str(finding.get("finding_id") or "unknown_finding")
            title = str(finding.get("title") or "Untitled finding")
            cases.append(f"Finding {finding_id} confidence={score:.2f}: {title}")

    return cases


def _format_evidence_list(evidence_items: Sequence[EvidencePointer]) -> str:
    """Format evidence pointers for human-readable markdown."""
    formatted: list[str] = []
    for evidence in evidence_items:
        evidence_bits = [
            evidence.source_file,
            f"page {evidence.page_number}" if evidence.page_number else None,
            evidence.clause_reference,
            evidence.evidence_id,
        ]
        formatted.append(" | ".join(bit for bit in evidence_bits if bit))
    return "; ".join(value for value in formatted if value) or "No evidence pointer"


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
