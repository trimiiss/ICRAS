"""Node bodies for the ICRAS orchestrator graph."""

import json
import shutil
from pathlib import Path

from agents.anomaly import run_anomaly_review
from agents.compliance import run_compliance_review
from agents.counterparty import run_counterparty_check
from agents.extraction import run_extraction
from agents.intake import run_intake
from agents.obligation import run_obligation_register
from agents.risk import run_risk_assessment
from agents.validation import run_validation
from agents.orchestrator.finalizer import finalize_pipeline
from agents.orchestrator.state import (
    PipelineState,
    require_state_mapping,
    require_state_str,
)
from utils.bundle_loader import load_bundle
from utils.evidence_indexer import build_evidence_index
from utils.idempotency import (
    build_bundle_fingerprint,
    find_completed_run_by_fingerprint,
)
from utils.jira_posting import run_jira_posting
from utils.run_manager import (
    append_audit_event,
    create_run_folder,
    update_run_metadata,
    update_run_status,
)
from schemas.final_artifacts import PipelineMetrics
from schemas.idempotency_result import IdempotencyResult
from schemas.posting_payload import PostingPayload


IDEMPOTENCY_ARTIFACTS: dict[str, str] = {
    "approval_packet": "approval_packet.json",
    "final_findings": "final_findings.json",
    "exceptions": "exceptions.md",
    "posting_payload": "posting_payload.json",
    "metrics": "metrics.json",
}


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


def idempotency_check_node(state: PipelineState) -> PipelineState:
    """Fingerprint loaded inputs and decide whether a completed run can be reused."""
    run_id = require_state_str(state, "run_id")
    run_dir = Path(require_state_str(state, "run_dir"))
    bundle_data = require_state_mapping(state, "bundle_data")
    fingerprint = build_bundle_fingerprint(bundle_data)
    run_info = require_state_mapping(state, "run_info")
    run_info["metadata"] = update_run_metadata(
        run_dir,
        {
            **fingerprint,
            "idempotency_status": "checking",
        },
    )
    baseline_run_dir = find_completed_run_by_fingerprint(
        runs_dir=Path(run_dir).parent,
        fingerprint=str(fingerprint["input_fingerprint_sha256"]),
        current_run_id=run_id,
    )
    idempotency_result = _build_idempotency_result(
        run_id=run_id,
        run_dir=run_dir,
        fingerprint=fingerprint,
        baseline_run_dir=baseline_run_dir,
    )
    _write_json(run_dir / "idempotency_result.json", idempotency_result)
    run_info["metadata"] = update_run_metadata(
        run_dir,
        {
            "idempotency_status": idempotency_result["status"],
            "idempotency_baseline_run_id": idempotency_result.get("baseline_run_id"),
            "idempotency_baseline_run_dir": idempotency_result.get("baseline_run_dir"),
            "external_posting_allowed": idempotency_result["external_posting_allowed"],
            "posting_suppression_reason": idempotency_result.get("posting_suppression_reason"),
        },
    )
    append_audit_event(
        run_dir,
        {
            "event": (
                "idempotency_duplicate_detected"
                if idempotency_result["status"] == "duplicate"
                else "idempotency_new_input_detected"
            ),
            "agent": "orchestrator_agent",
            "message": idempotency_result["decision"],
            "input_fingerprint_sha256": fingerprint["input_fingerprint_sha256"],
            "baseline_run_id": idempotency_result.get("baseline_run_id"),
            "external_posting_allowed": idempotency_result["external_posting_allowed"],
        },
    )
    return {
        "run_info": run_info,
        "idempotency_result": idempotency_result,
        "artifact_paths": {
            "idempotency_result": str(Path(run_dir) / "idempotency_result.json"),
        },
    }


def idempotent_reuse_node(state: PipelineState) -> PipelineState:
    """Reuse final artifacts from a previous completed duplicate run."""
    run_id = require_state_str(state, "run_id")
    run_dir = Path(require_state_str(state, "run_dir"))
    idempotency_result = require_state_mapping(state, "idempotency_result")
    baseline_run_dir = Path(str(idempotency_result.get("baseline_run_dir") or ""))
    if not baseline_run_dir.is_dir():
        raise FileNotFoundError(
            f"Idempotent baseline run directory does not exist: {baseline_run_dir}"
        )

    artifact_paths = {
        **state.get("artifact_paths", {}),
        **{
            artifact_name: str(run_dir / filename)
            for artifact_name, filename in IDEMPOTENCY_ARTIFACTS.items()
        },
    }
    copied_artifacts = _copy_reusable_artifacts(
        baseline_run_dir=baseline_run_dir,
        run_dir=run_dir,
        run_id=run_id,
        idempotency_result=idempotency_result,
        artifact_paths=artifact_paths,
    )
    completed_idempotency_result = IdempotencyResult.model_validate(
        {
            **idempotency_result,
            "copied_artifacts": copied_artifacts,
            "artifact_paths": artifact_paths,
        }
    ).model_dump(mode="json")
    _write_json(run_dir / "idempotency_result.json", completed_idempotency_result)
    update_run_metadata(
        run_dir,
        {
            "idempotency_status": "duplicate",
            "idempotency_baseline_run_id": idempotency_result.get("baseline_run_id"),
            "external_posting_allowed": False,
            "posting_suppression_reason": idempotency_result.get("posting_suppression_reason"),
        },
    )
    update_run_status(run_dir, "completed")
    append_audit_event(
        run_dir,
        {
            "event": "idempotency_results_reused",
            "agent": "orchestrator_agent",
            "message": "Duplicate input reused final artifacts from a previous completed run.",
            "baseline_run_id": idempotency_result.get("baseline_run_id"),
            "baseline_run_dir": str(baseline_run_dir),
            "external_posting_allowed": False,
            "posting_suppression_reason": idempotency_result.get("posting_suppression_reason"),
        },
    )

    return {
        "idempotency_result": completed_idempotency_result,
        "final_findings": _read_json(run_dir / "final_findings.json"),
        "approval_packet": _read_json(run_dir / "approval_packet.json"),
        "posting_payload": _read_json(run_dir / "posting_payload.json"),
        "metrics": _read_json(run_dir / "metrics.json"),
        "artifact_paths": artifact_paths,
    }


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
        extracted_contract=extracted_contract,
    )
    return {
        "validation_result": result["validation_result"],
        "artifact_paths": result["artifact_paths"],
    }


def compliance_node(state: PipelineState) -> PipelineState:
    """Run compliance review."""
    result = run_compliance_review(
        context=require_state_mapping(state, "context_packet"),
        extracted_contract=require_state_mapping(state, "extracted_contract"),
        run_dir=require_state_str(state, "run_dir"),
        evidence_index=require_state_mapping(state, "evidence_index"),
    )
    return {
        "compliance_result": result["compliance_result"],
        "artifact_paths": result["artifact_paths"],
    }


def anomaly_node(state: PipelineState) -> PipelineState:
    """Run anomaly review."""
    result = run_anomaly_review(
        context=require_state_mapping(state, "context_packet"),
        extracted_contract=require_state_mapping(state, "extracted_contract"),
        run_dir=require_state_str(state, "run_dir"),
        evidence_index=require_state_mapping(state, "evidence_index"),
    )
    return {
        "anomaly_result": result["anomaly_result"],
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


def jira_posting_node(state: PipelineState) -> PipelineState:
    """Post final review results to Jira when configured and allowed."""
    run_id = require_state_str(state, "run_id")
    run_dir = Path(require_state_str(state, "run_dir"))
    artifact_paths = dict(state.get("artifact_paths", {}))
    result = run_jira_posting(
        run_id=run_id,
        approval_packet_data=require_state_mapping(state, "approval_packet"),
        posting_payload_data=require_state_mapping(state, "posting_payload"),
        idempotency_result=require_state_mapping(state, "idempotency_result"),
        artifact_paths=artifact_paths,
    ).model_dump(mode="json")
    result_path = run_dir / "jira_posting_result.json"
    _write_json(result_path, result)

    updated_artifact_paths = {
        **artifact_paths,
        "jira_posting_result": str(result_path),
    }
    updated_metrics = _metrics_with_jira_posting(
        metrics=require_state_mapping(state, "metrics"),
        jira_posting_result=result,
        artifact_paths=updated_artifact_paths,
    )
    metrics_path = updated_artifact_paths.get("metrics")
    if metrics_path:
        _write_json(Path(metrics_path), updated_metrics)

    update_run_metadata(
        run_dir,
        {
            "jira_posting_status": result["status"],
            "jira_issue_key": result.get("jira_issue_key"),
            "jira_posting_reason": result.get("reason"),
        },
    )
    append_audit_event(
        run_dir,
        {
            "event": _jira_posting_event_name(str(result["status"])),
            "agent": "jira_posting",
            "message": str(result.get("reason") or ""),
            "jira_posting_status": result["status"],
            "jira_issue_key": result.get("jira_issue_key"),
            "jira_issue_url": result.get("jira_issue_url"),
            "error": result.get("error_message"),
        },
    )
    return {
        "jira_posting_result": result,
        "metrics": updated_metrics,
        "artifact_paths": updated_artifact_paths,
    }


def _build_idempotency_result(
    run_id: str,
    run_dir: Path,
    fingerprint: dict[str, object],
    baseline_run_dir: Path | None,
) -> dict[str, object]:
    """Build the idempotency decision payload for a run."""
    if baseline_run_dir is None:
        return IdempotencyResult.model_validate(
            {
                "run_id": run_id,
                "status": "new",
                "decision": "No completed run with the same input fingerprint was found.",
                "contract_sha256": fingerprint["contract_sha256"],
                "input_fingerprint_sha256": fingerprint["input_fingerprint_sha256"],
                "fingerprint_algorithm": fingerprint["fingerprint_algorithm"],
                "fingerprinted_files": fingerprint["fingerprinted_files"],
                "baseline_run_id": None,
                "baseline_run_dir": None,
                "external_posting_allowed": True,
                "posting_suppression_reason": None,
            }
        ).model_dump(mode="json")

    baseline_metadata = _read_json(baseline_run_dir / "metadata.json")
    baseline_run_id = str(baseline_metadata.get("run_id") or baseline_run_dir.name)
    return IdempotencyResult.model_validate(
        {
            "run_id": run_id,
            "status": "duplicate",
            "decision": (
                "Duplicate input fingerprint matched completed run "
                f"{baseline_run_id}; final results will be reused."
            ),
            "contract_sha256": fingerprint["contract_sha256"],
            "input_fingerprint_sha256": fingerprint["input_fingerprint_sha256"],
            "fingerprint_algorithm": fingerprint["fingerprint_algorithm"],
            "fingerprinted_files": fingerprint["fingerprinted_files"],
            "baseline_run_id": baseline_run_id,
            "baseline_run_dir": str(baseline_run_dir.resolve()),
            "external_posting_allowed": False,
            "posting_suppression_reason": (
                "Duplicate input fingerprint matched completed run "
                f"{baseline_run_id}."
            ),
            "current_run_dir": str(run_dir.resolve()),
        }
    ).model_dump(mode="json")


def _copy_reusable_artifacts(
    baseline_run_dir: Path,
    run_dir: Path,
    run_id: str,
    idempotency_result: dict[str, object],
    artifact_paths: dict[str, str],
) -> dict[str, str]:
    """Copy final artifacts from the reusable baseline run."""
    copied: dict[str, str] = {}
    for artifact_name, filename in IDEMPOTENCY_ARTIFACTS.items():
        source = baseline_run_dir / filename
        target = run_dir / filename
        if not source.is_file():
            raise FileNotFoundError(f"Reusable artifact is missing: {source}")
        if filename.endswith(".json"):
            payload = _read_json(source)
            if artifact_name == "posting_payload":
                payload.update(
                    {
                        "run_id": run_id,
                        "external_posting_allowed": False,
                        "duplicate_of_run_id": idempotency_result.get("baseline_run_id"),
                        "posting_suppression_reason": idempotency_result.get(
                            "posting_suppression_reason"
                        ),
                        "artifact_references": artifact_paths,
                    }
                )
                payload["artifacts"] = _updated_artifact_references(
                    payload.get("artifacts"),
                    artifact_paths,
                )
                payload = PostingPayload.model_validate(payload).model_dump(mode="json")
            elif artifact_name == "metrics":
                payload.update(
                    {
                        "run_id": run_id,
                        "status": "completed",
                        "idempotency_status": "duplicate",
                        "idempotency_baseline_run_id": idempotency_result.get(
                            "baseline_run_id"
                        ),
                        "external_posting_allowed": False,
                        "posting_suppression_reason": idempotency_result.get(
                            "posting_suppression_reason"
                        ),
                        "determinism_baseline_run_id": idempotency_result.get(
                            "baseline_run_id"
                        ),
                        "determinism_check": "REUSED",
                        "artifact_paths": artifact_paths,
                    }
                )
                payload = PipelineMetrics.model_validate(payload).model_dump(mode="json")
            _write_json(target, payload)
        else:
            shutil.copyfile(source, target)
        copied[artifact_name] = str(target)
    return copied


def _read_json(path: Path) -> dict[str, object]:
    """Read a JSON object from disk."""
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    return data if isinstance(data, dict) else {}


def _updated_artifact_references(
    artifacts: object,
    artifact_paths: dict[str, str],
) -> list[dict[str, object]]:
    """Return posting artifact references with current-run paths."""
    if not isinstance(artifacts, list):
        return []

    updated: list[dict[str, object]] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        name = artifact.get("name")
        current_path = artifact_paths.get(str(name)) if name is not None else None
        updated_artifact = dict(artifact)
        if current_path:
            updated_artifact["path"] = current_path
        updated.append(updated_artifact)
    return updated


def _write_json(path: Path, payload: dict[str, object]) -> None:
    """Write a deterministic JSON object."""
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False, sort_keys=True)
        file.write("\n")


def _metrics_with_jira_posting(
    metrics: dict[str, object],
    jira_posting_result: dict[str, object],
    artifact_paths: dict[str, str],
) -> dict[str, object]:
    """Return metrics updated with safe Jira posting fields."""
    updated = {
        **metrics,
        "jira_posting_status": jira_posting_result.get("status"),
        "jira_issue_key": jira_posting_result.get("jira_issue_key"),
        "jira_issue_url": jira_posting_result.get("jira_issue_url"),
        "jira_posting_reason": jira_posting_result.get("reason"),
        "artifact_paths": artifact_paths,
    }
    return PipelineMetrics.model_validate(updated).model_dump(mode="json")


def _jira_posting_event_name(status: str) -> str:
    """Return the audit event name for a Jira posting status."""
    if status == "CREATED":
        return "jira_posting_created"
    if status == "FAILED":
        return "jira_posting_failed"
    return "jira_posting_skipped"
