"""Workflow orchestrator public entry points."""

from typing import Any, Dict

from agents.obligation import (
    OBLIGATION_CSV_COLUMNS,
    ObligationRegisterError,
    run_obligation_register,
)
from agents.orchestrator import approval_routing as _approval_routing
from agents.orchestrator import finding_merger as _finding_merger
from agents.orchestrator.errors import OrchestratorAgentError
from agents.orchestrator.graph import build_pipeline_graph
from agents.orchestrator.state import PipelineState
from utils import determinism as _determinism


build_determinism_result = _determinism.build_determinism_result
_compare_determinism_payloads = _determinism.compare_determinism_payloads
_build_approval_routes = _approval_routing.build_approval_routes
_triage_findings = _approval_routing.triage_findings
_merge_deduplicate_sort_findings = _finding_merger.merge_deduplicate_sort_findings

__all__ = [
    "OBLIGATION_CSV_COLUMNS",
    "ObligationRegisterError",
    "OrchestratorAgentError",
    "PipelineState",
    "_build_approval_routes",
    "_compare_determinism_payloads",
    "_merge_deduplicate_sort_findings",
    "_triage_findings",
    "build_determinism_result",
    "build_pipeline_graph",
    "run_obligation_register",
    "run_pipeline",
]


def run_pipeline(bundle_path: str) -> Dict[str, Any]:
    """Execute the full contract review pipeline.

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
