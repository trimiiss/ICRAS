"""Node bodies for the ICRAS orchestrator graph."""

from pathlib import Path
from typing import Any, Mapping

from agents.counterparty import run_counterparty_check
from agents.extraction import run_extraction
from agents.intake import run_intake
from agents.obligation import run_obligation_register
from agents.risk import run_risk_assessment
from agents.validation import run_validation
from agents.orchestrator.errors import OrchestratorAgentError
from agents.orchestrator.finalizer import finalize_pipeline
from agents.orchestrator.state import PipelineState
from utils.bundle_loader import load_bundle
from utils.evidence_indexer import build_evidence_index
from utils.run_manager import create_run_folder


def create_run_node(state: PipelineState) -> PipelineState:
    """Create the deterministic run folder for this graph invocation."""
    bundle_path = require_state_str(state, "bundle_path")
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


def load_bundle_node(state: PipelineState) -> PipelineState:
    """Load and validate the source contract bundle."""
    bundle_data = load_bundle(require_state_str(state, "bundle_path"))
    return {"bundle_data": bundle_data}


def intake_node(state: PipelineState) -> PipelineState:
    """Run intake."""
    result = run_intake(
        bundle_data=require_state_mapping(state, "bundle_data"),
        run_id=require_state_str(state, "run_id"),
        run_dir=require_state_str(state, "run_dir"),
    )
    return {
        "context_packet": result["context_packet"],
        "document_inventory": result["document_inventory"],
        "artifact_paths": result["artifact_paths"],
    }


def evidence_index_node(state: PipelineState) -> PipelineState:
    """Build the source evidence index."""
    result = build_evidence_index(
        bundle_data=require_state_mapping(state, "bundle_data"),
        document_inventory=require_state_mapping(state, "document_inventory"),
        run_id=require_state_str(state, "run_id"),
        run_dir=require_state_str(state, "run_dir"),
    )
    return {
        "evidence_index": result["evidence_index"],
        "artifact_paths": result["artifact_paths"],
    }


def extraction_node(state: PipelineState) -> PipelineState:
    """Run clause extraction."""
    result = run_extraction(
        bundle_data=require_state_mapping(state, "bundle_data"),
        document_inventory=require_state_mapping(state, "document_inventory"),
        evidence_index=require_state_mapping(state, "evidence_index"),
        run_id=require_state_str(state, "run_id"),
        run_dir=require_state_str(state, "run_dir"),
    )
    return {
        "extracted_contract": result["extracted_contract"],
        "artifact_paths": result["artifact_paths"],
    }


def counterparty_node(state: PipelineState) -> PipelineState:
    """Run counterparty matching."""
    bundle_data = require_state_mapping(state, "bundle_data")
    result = run_counterparty_check(
        context=require_state_mapping(state, "context_packet"),
        extracted_contract=require_state_mapping(state, "extracted_contract"),
        vendor_master_path=Path(str(bundle_data["bundle_dir"])) / "vendor_master.csv",
        run_dir=require_state_str(state, "run_dir"),
        evidence_index=require_state_mapping(state, "evidence_index"),
    )
    return {
        "counterparty_resolution": result["counterparty_resolution"],
        "artifact_paths": result["artifact_paths"],
    }


def validation_node(state: PipelineState) -> PipelineState:
    """Run validation."""
    extracted_contract = require_state_mapping(state, "extracted_contract")
    result = run_validation(
        context=require_state_mapping(state, "context_packet"),
        clauses=list(extracted_contract.get("clauses", [])),
        run_dir=require_state_str(state, "run_dir"),
        evidence_index=require_state_mapping(state, "evidence_index"),
    )
    return {
        "validation_result": result["validation_result"],
        "artifact_paths": result["artifact_paths"],
    }


def risk_node(state: PipelineState) -> PipelineState:
    """Run risk assessment after matching and validation complete."""
    result = run_risk_assessment(
        context=require_state_mapping(state, "context_packet"),
        extracted_contract=require_state_mapping(state, "extracted_contract"),
        validation_result=require_state_mapping(state, "validation_result"),
        run_dir=require_state_str(state, "run_dir"),
    )
    return {
        "clause_analysis": result["clause_analysis"],
        "risk_result": result["risk_result"],
        "artifact_paths": result["artifact_paths"],
    }


def obligation_register_node(state: PipelineState) -> PipelineState:
    """Run obligation tracking."""
    result = run_obligation_register(
        context=require_state_mapping(state, "context_packet"),
        extracted_contract=require_state_mapping(state, "extracted_contract"),
        run_dir=require_state_str(state, "run_dir"),
    )
    return {
        "obligation_register": result["obligation_register"],
        "artifact_paths": result["artifact_paths"],
    }


def finalize_node(state: PipelineState) -> PipelineState:
    """Finalize findings, routing, and downstream artifacts."""
    return finalize_pipeline(state)


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
