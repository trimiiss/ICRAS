"""LangGraph assembly and audit-wrapped node execution."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from langgraph.graph import END, START, StateGraph

from agents.orchestrator.errors import OrchestratorAgentError
from agents.orchestrator.markdown import write_final_audit_markdown
from agents.orchestrator.metrics import (
    confidence_distribution,
    confidence_scores_from_clauses,
    exception_category_counts,
    safe_len,
)
from agents.orchestrator.nodes import (
    counterparty_node,
    create_run_node,
    evidence_index_node,
    extraction_node,
    finalize_node,
    intake_node,
    load_bundle_node,
    obligation_register_node,
    risk_node,
    validation_node,
)
from agents.orchestrator.state import PipelineState, STEP_INPUT_ARTIFACTS
from schemas.final_artifacts import AgentAuditTrace
from utils.mapping import as_mapping as _as_mapping
from utils.run_manager import append_audit_event, update_run_status


def build_pipeline_graph() -> Any:
    """Build the LangGraph workflow."""
    builder = StateGraph(PipelineState)
    builder.add_node("create_run", _pipeline_node("create_run", "orchestrator_agent", create_run_node))
    builder.add_node("load_bundle", _pipeline_node("load_bundle", "bundle_loader", load_bundle_node))
    builder.add_node("intake", _pipeline_node("intake", "intake_agent", intake_node))
    builder.add_node("evidence_index", _pipeline_node("evidence_index", "evidence_indexer", evidence_index_node))
    builder.add_node("extraction", _pipeline_node("extraction", "extraction_agent", extraction_node))
    builder.add_node("counterparty", _pipeline_node("counterparty", "counterparty_agent", counterparty_node))
    builder.add_node("validation", _pipeline_node("validation", "validation_agent", validation_node))
    builder.add_node("risk_scoring", _pipeline_node("risk_scoring", "risk_agent", risk_node))
    builder.add_node(
        "obligation_register",
        _pipeline_node("obligation_register", "orchestrator_agent", obligation_register_node),
    )
    builder.add_node("agent_h_finalize", _pipeline_node("agent_h_finalize", "orchestrator_agent", finalize_node))

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
            write_final_audit_markdown(
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
    exception_categories = exception_category_counts(exceptions)

    if not exception_categories and isinstance(metrics.get("exception_categories"), Mapping):
        exception_categories = {
            str(category): int(count)
            for category, count in metrics["exception_categories"].items()
            if isinstance(count, int)
        }

    clause_scores = confidence_scores_from_clauses(extracted_contract.get("clauses"))
    confidence_dist = confidence_distribution(clause_scores)
    fallback_reason = extracted_contract.get("fallback_reason") or metrics.get("fallback_reason")
    raw_low_confidence_count = metrics.get("low_confidence_count")
    low_confidence_count = (
        int(raw_low_confidence_count)
        if isinstance(raw_low_confidence_count, int)
        else confidence_dist.low_count
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
        extracted_clause_count=safe_len(extracted_contract.get("clauses")),
        exception_count=sum(exception_categories.values()),
        exception_categories=exception_categories,
        fallback_used=bool(extracted_contract.get("fallback_assisted") or metrics.get("fallback_assisted")),
        fallback_reason=str(fallback_reason) if fallback_reason else None,
        low_confidence_count=low_confidence_count,
        confidence_distribution=confidence_dist,
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

