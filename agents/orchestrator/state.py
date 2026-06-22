"""Pipeline state contracts and workflow constants."""

from typing import Annotated, Any, Mapping, Optional, TypedDict

from agents.orchestrator.errors import OrchestratorAgentError


def merge_dicts(left: Optional[dict[str, str]], right: Optional[dict[str, str]]) -> dict[str, str]:
    """Merge graph state dictionaries from parallel branches."""
    merged: dict[str, str] = {}
    if left:
        merged.update(left)
    if right:
        merged.update(right)
    return merged


def append_lists(left: Optional[list[dict[str, Any]]], right: Optional[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Append graph state event lists from parallel branches."""
    return [*(left or []), *(right or [])]


def require_state_str(state: Mapping[str, Any], key: str) -> str:
    """Return a required string from graph state."""
    value = state.get(key)
    if not isinstance(value, str) or not value:
        raise OrchestratorAgentError(
            f"Pipeline state is missing required string '{key}'."
        )
    return value


def require_state_mapping(state: Mapping[str, Any], key: str) -> dict[str, Any]:
    """Return a required mapping from graph state."""
    value = state.get(key)
    if not isinstance(value, Mapping):
        raise OrchestratorAgentError(
            f"Pipeline state is missing required mapping '{key}'."
        )
    return dict(value)


class PipelineState(TypedDict, total=False):
    """Shared state for the workflow orchestrator graph."""

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
    compliance_result: dict[str, Any]
    anomaly_result: dict[str, Any]
    obligation_register: dict[str, Any]
    final_findings: dict[str, Any]
    approval_packet: dict[str, Any]
    posting_payload: dict[str, Any]
    jira_posting_result: dict[str, Any]
    metrics: dict[str, Any]
    idempotency_result: dict[str, Any]
    artifact_paths: Annotated[dict[str, str], merge_dicts]
    step_events: Annotated[list[dict[str, Any]], append_lists]


PIPELINE_STEP_ORDER: tuple[str, ...] = (
    "create_run",
    "load_bundle",
    "idempotency_check",
    "intake",
    "evidence_index",
    "extraction",
    "counterparty",
    "validation",
    "risk_scoring",
    "compliance",
    "anomaly",
    "obligation_register",
    "agent_h_finalize",
    "jira_posting",
)

STEP_INPUT_ARTIFACTS: dict[str, tuple[str, ...]] = {
    "idempotency_check": ("metadata",),
    "evidence_index": ("document_inventory",),
    "extraction": ("document_inventory", "evidence_index"),
    "counterparty": ("context_packet", "extracted_contract", "evidence_index"),
    "validation": ("context_packet", "extracted_contract", "evidence_index"),
    "risk_scoring": ("context_packet", "extracted_contract", "validation_findings"),
    "compliance": ("context_packet", "extracted_contract", "evidence_index"),
    "anomaly": ("context_packet", "extracted_contract", "evidence_index"),
    "obligation_register": ("context_packet", "extracted_contract"),
    "agent_h_finalize": (
        "context_packet",
        "document_inventory",
        "extracted_contract",
        "validation_findings",
        "counterparty_resolution",
        "clause_analysis",
        "risk_result",
        "compliance_findings",
        "anomaly_findings",
        "obligations",
    ),
    "idempotent_reuse": ("idempotency_result",),
    "jira_posting": (
        "approval_packet",
        "posting_payload",
        "idempotency_result",
        "metrics",
    ),
}
