"""Tests for the command-line pipeline entry point."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from main import _require_metrics_status
from schemas.posting_payload import PostingPayload


PROJECT_ROOT = Path(__file__).resolve().parent.parent
NDA_BUNDLE = PROJECT_ROOT / "data" / "bundles" / "clean_nda"
NET90_SERVICES_BUNDLE = PROJECT_ROOT / "data" / "bundles" / "net90_services_agreement"


def test_valid_bundle_creates_intake_evidence_extraction_and_validation_artifacts(
    tmp_path: Path,
) -> None:
    """A valid CLI run should create intake, evidence, extraction, and validation artifacts."""
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "main.py"),
            "--bundle",
            str(NDA_BUNDLE),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0
    assert "Selected bundle:" in result.stdout
    assert "✅ create_run" in result.stdout
    assert "✅ agent_h_finalize" in result.stdout
    assert "Final Decision: AUTO-APPROVE" in result.stdout
    assert "Auto-approve (AUTO_APPROVE)" in result.stdout
    assert "evidence_index.json" in result.stdout
    assert "extracted_contract.json" in result.stdout
    assert "validation_findings.json" in result.stdout
    assert "clause_analysis.json" in result.stdout
    assert "audit_log.md" in result.stdout
    assert "counterparty_resolution.json" in result.stdout
    assert "obligations.csv" in result.stdout
    assert "final_findings.json" in result.stdout
    assert "exceptions.md" in result.stdout
    assert "approval_packet.json" in result.stdout
    assert "posting_payload.json" in result.stdout
    assert "metrics.json" in result.stdout

    run_dirs = list((tmp_path / "runs").iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]

    assert (run_dir / "context_packet.json").is_file()
    assert (run_dir / "document_inventory.json").is_file()
    assert (run_dir / "evidence_index.json").is_file()
    assert (run_dir / "extracted_contract.json").is_file()
    assert (run_dir / "validation_findings.json").is_file()
    assert (run_dir / "clause_analysis.json").is_file()
    assert (run_dir / "counterparty_resolution.json").is_file()
    assert (run_dir / "obligations.csv").is_file()
    assert (run_dir / "final_findings.json").is_file()
    assert (run_dir / "exceptions.md").is_file()
    assert (run_dir / "approval_packet.json").is_file()
    assert (run_dir / "posting_payload.json").is_file()
    assert (run_dir / "metrics.json").is_file()
    assert (run_dir / "audit_log.md").is_file()

    evidence_index = json.loads(
        (run_dir / "evidence_index.json").read_text(encoding="utf-8")
    )
    assert evidence_index["records"][0]["evidence_id"] == "EV-001"

    extracted_contract = json.loads(
        (run_dir / "extracted_contract.json").read_text(encoding="utf-8")
    )
    assert len(extracted_contract["clauses"]) == 10
    assert extracted_contract["clauses"][0]["evidence"]["evidence_id"] == "EV-001"

    validation_findings = json.loads(
        (run_dir / "validation_findings.json").read_text(encoding="utf-8")
    )
    assert "validated_fields" in validation_findings

    clause_analysis = json.loads(
        (run_dir / "clause_analysis.json").read_text(encoding="utf-8")
    )
    assert "clause_risks" in clause_analysis

    final_findings = json.loads(
        (run_dir / "final_findings.json").read_text(encoding="utf-8")
    )
    assert "findings" in final_findings

    approval_packet = json.loads(
        (run_dir / "approval_packet.json").read_text(encoding="utf-8")
    )
    assert approval_packet["decision"]["status"] == "AUTO_APPROVE"
    assert approval_packet["exceptions"] == []
    assert approval_packet["approval_route"][0]["category"] == "AUTO_APPROVE"

    exceptions_markdown = (run_dir / "exceptions.md").read_text(encoding="utf-8")
    assert "## Next Actions" in exceptions_markdown
    assert "## Exceptions" in exceptions_markdown

    posting_payload = PostingPayload.model_validate_json(
        (run_dir / "posting_payload.json").read_text(encoding="utf-8")
    )
    assert posting_payload.payload_version == "1.0"
    assert posting_payload.payload_type == "CLM_POSTING_PAYLOAD"
    assert posting_payload.source_system == "ICRAS"
    assert posting_payload.contract.contract_id
    assert posting_payload.contract.bundle_name == "clean_nda"
    assert posting_payload.counterparty.name
    assert posting_payload.decision.status.value == "AUTO_APPROVE"
    assert posting_payload.risk.summary
    assert posting_payload.approval.routes
    assert posting_payload.approval.next_approvers == []
    assert posting_payload.artifacts
    assert set(posting_payload.artifact_references) == {
        artifact.name for artifact in posting_payload.artifacts
    }


def test_invalid_bundle_creates_failed_run_with_audit_log(tmp_path: Path) -> None:
    """Bundle validation failures should be traceable in a run audit log."""
    invalid_bundle = tmp_path / "invalid_bundle"
    shutil.copytree(NDA_BUNDLE, invalid_bundle)
    (invalid_bundle / "playbook.yaml").unlink()

    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "main.py"),
            "--bundle",
            str(invalid_bundle),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 1
    assert "Bundle validation failed" in result.stderr

    run_dirs = list((tmp_path / "runs").iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]

    metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "failed"
    assert "playbook.yaml" in metadata["error_message"]

    audit_lines = (run_dir / "audit_log.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(audit_lines) >= 3
    audit_events = [json.loads(line) for line in audit_lines]
    event_names = {event["event"] for event in audit_events}
    assert "create_run_completed" in event_names
    assert "load_bundle_started" in event_names
    assert "load_bundle_failed" in event_names
    failed_event = next(event for event in audit_events if event["event"] == "load_bundle_failed")
    assert failed_event["agent"] == "bundle_loader"
    assert "playbook.yaml" in failed_event["error"]

    audit_markdown = (run_dir / "audit_log.md").read_text(encoding="utf-8")
    assert "load_bundle_failed" in audit_markdown
    assert "playbook.yaml" in audit_markdown


def test_require_metrics_status_rejects_missing_status() -> None:
    """CLI summary should not pretend a missing metrics.status is completed."""
    with pytest.raises(RuntimeError, match="metrics.status is missing"):
        _require_metrics_status({})


def test_require_metrics_status_rejects_non_mapping_metrics() -> None:
    """CLI summary should fail clearly when metrics has the wrong shape."""
    with pytest.raises(RuntimeError, match="metrics is missing or not a mapping"):
        _require_metrics_status(None)


def test_net90_services_demo_prints_finance_route(tmp_path: Path) -> None:
    """The presenter demo command should show the finance approval path."""
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "main.py"),
            "--bundle",
            str(NET90_SERVICES_BUNDLE),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0
    assert "Selected bundle:" in result.stdout
    assert "net90_services_agreement" in result.stdout
    assert "✅ validation" in result.stdout
    assert "✅ risk_scoring" in result.stdout
    assert "Final Decision: ESCALATE" in result.stdout
    assert "Finance approval (FINANCE)" in result.stdout
    assert "finance_manager" in result.stdout
    assert "Generated artifact paths:" in result.stdout

    run_dirs = list((tmp_path / "runs").iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    artifact_files = [
        "audit_log.md",
        "context_packet.json",
        "document_inventory.json",
        "evidence_index.json",
        "extracted_contract.json",
        "validation_findings.json",
        "clause_analysis.json",
        "approval_packet.json",
        "posting_payload.json",
        "metrics.json",
    ]
    assert all((run_dir / artifact_file).is_file() for artifact_file in artifact_files)
