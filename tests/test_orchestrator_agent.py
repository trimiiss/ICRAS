"""Tests for workflow orchestration."""

import json
from pathlib import Path

from agents.orchestrator import (
    _build_approval_routes,
    _compare_determinism_payloads,
    _merge_deduplicate_sort_findings,
    _triage_findings,
    run_pipeline,
)
from schemas.common import Severity
from schemas.exception_triage import ExceptionCategory
from schemas.finding import Finding
from schemas.posting_payload import PostingPayload
from utils.bundle_loader import load_bundle


PROJECT_ROOT = Path(__file__).resolve().parent.parent
NDA_BUNDLE = PROJECT_ROOT / "data" / "bundles" / "clean_nda"
LOW_CONFIDENCE_BUNDLE = (
    PROJECT_ROOT / "data" / "bundles" / "scenario_07_low_signature_confidence"
)


def _clear_jira_env(monkeypatch) -> None:
    """Keep pipeline tests deterministic even on machines with Jira env vars."""
    for name in (
        "JIRA_BASE_URL",
        "JIRA_EMAIL",
        "JIRA_API_TOKEN",
        "JIRA_PROJECT_KEY",
        "JIRA_ISSUE_TYPE",
    ):
        monkeypatch.delenv(name, raising=False)


def _evidence(evidence_id: str = "EV-001") -> dict:
    """Return a shared evidence pointer."""
    return {
        "evidence_id": evidence_id,
        "source_file": "contract.pdf",
        "page_number": 2,
        "clause_reference": "4",
        "excerpt": "Customer shall pay invoices net 90.",
    }


def _finding(
    finding_id: str,
    severity: str,
    title: str = "Payment term exceeds policy",
    field_name: str = "payment_terms",
    issue_type: str = "payment_terms_policy_violation",
    confidence: float = 0.9,
    manual_review_required: bool = True,
) -> dict:
    """Return a shared finding payload."""
    evidence = _evidence()
    return {
        "finding_id": finding_id,
        "category": "finance",
        "title": title,
        "description": title,
        "severity": severity,
        "confidence": confidence,
        "evidence": [evidence],
        "recommendation": "Request finance approval.",
        "field_name": field_name,
        "issue_type": issue_type,
        "message": title,
        "source_clause_text": evidence["excerpt"],
        "source_page": 2,
        "evidence_pointer": evidence,
        "manual_review_required": manual_review_required,
        "risk_engine_ready": True,
    }


def test_agent_h_deduplicates_and_sorts_findings_by_severity() -> None:
    """Duplicate findings should merge and CRITICAL findings should sort first."""
    findings = _merge_deduplicate_sort_findings(
        run_id="run-1",
        context={"contract_file": "contract.pdf"},
        validation_result={"findings": [_finding("VAL-001", "HIGH")]},
        risk_result={"findings": [_finding("RISK-001", "CRITICAL")]},
        counterparty_resolution={
            "matches": [
                {
                    "original_party_name": "Unknown Vendor LLC",
                    "normalized_party_name": "unknown vendor",
                    "similarity_score": 0.2,
                    "match_status": "no_match",
                    "manual_review_required": True,
                    "risk_flag": "No vendor master match.",
                    "evidence_pointer": _evidence("EV-002"),
                }
            ]
        },
    )

    assert len(findings) == 2
    assert findings[0].severity.value == "CRITICAL"
    assert findings[0].finding_id == "VAL-001"
    assert findings[1].finding_id == "CPY-001"


def test_agent_h_merges_anomaly_findings() -> None:
    """Anomaly results should be included in Agent H final findings."""
    findings = _merge_deduplicate_sort_findings(
        run_id="run-1",
        context={"contract_file": "contract.pdf"},
        validation_result={"findings": []},
        risk_result={"findings": []},
        counterparty_resolution={"matches": []},
        anomaly_result={
            "findings": [
                _finding(
                    "ANM-001",
                    "HIGH",
                    "Contradictory payment terms",
                    field_name="payment_terms",
                    issue_type="contradictory_payment_terms",
                )
            ]
        },
    )

    assert len(findings) == 1
    assert findings[0].finding_id == "ANM-001"
    assert findings[0].issue_type == "contradictory_payment_terms"


def test_agent_h_routes_ticket_exception_categories_from_policy() -> None:
    """Each routing-table case should map through approval_policy.yaml."""
    context = {
        "approval_policy": load_bundle(NDA_BUNDLE)["approval_policy"],
        "contract_file": "contract.pdf",
    }
    cases = [
        (
            _finding(
                "F-001",
                "HIGH",
                "Missing liability cap",
                field_name="liability_cap",
                issue_type="missing_field",
            ),
            ExceptionCategory.LEGAL,
            "legal_counsel",
        ),
        (
            _finding(
                "F-002",
                "HIGH",
                "Net-90 payment terms exceed policy",
                field_name="payment_terms",
                issue_type="payment_terms_policy_violation",
            ),
            ExceptionCategory.FINANCE,
            "finance_manager",
        ),
        (
            _finding(
                "F-003",
                "HIGH",
                "High-risk jurisdiction detected",
                field_name="governing_law",
                issue_type="high_risk_jurisdiction",
            ),
            ExceptionCategory.COMPLIANCE,
            "compliance_officer",
        ),
        (
            _finding(
                "F-004",
                "MEDIUM",
                "Low-confidence signature section",
                field_name="signature",
                issue_type="low_confidence_signature",
                confidence=0.5,
                manual_review_required=True,
            ),
            ExceptionCategory.MANUAL_REVIEW,
            "contract_reviewer",
        ),
        (
            _finding(
                "F-005",
                "HIGH",
                "Conflicting governing law clauses",
                field_name="governing_law",
                issue_type="conflicting_governing_law",
            ),
            ExceptionCategory.LEGAL,
            "legal_counsel",
        ),
        (
            _finding(
                "F-006",
                "HIGH",
                "Missing GDPR clause",
                field_name="data_protection",
                issue_type="missing_gdpr_clause",
            ),
            ExceptionCategory.COMPLIANCE,
            "compliance_officer",
        ),
        (
            _finding(
                "F-007",
                "HIGH",
                "Contradictory payment terms",
                field_name="payment_terms",
                issue_type="contradictory_payment_terms",
            ),
            ExceptionCategory.FINANCE,
            "finance_manager",
        ),
        (
            _finding(
                "F-008",
                "HIGH",
                "Duplicate clauses contain different values",
                field_name="liability_cap",
                issue_type="duplicate_clause_value_conflict",
            ),
            ExceptionCategory.LEGAL,
            "legal_counsel",
        ),
        (
            _finding(
                "F-009",
                "HIGH",
                "Unusual liability exposure",
                field_name="liability_cap",
                issue_type="unusual_contract_pattern",
            ),
            ExceptionCategory.LEGAL,
            "legal_counsel",
        ),
    ]

    for raw_finding, expected_category, expected_approver in cases:
        status, exceptions = _triage_findings(
            context=context,
            findings=[Finding.model_validate(raw_finding)],
            overall_severity=Severity.HIGH,
        )

        assert status.value == "ESCALATE"
        assert exceptions[0].category == expected_category
        assert exceptions[0].approver == expected_approver
        assert exceptions[0].reason
        assert exceptions[0].next_action
        assert exceptions[0].evidence


def test_agent_h_auto_approves_standard_terms_from_policy() -> None:
    """No findings at an auto-approved severity should produce auto-approval."""
    context = {
        "approval_policy": load_bundle(NDA_BUNDLE)["approval_policy"],
        "contract_file": "contract.pdf",
    }

    status, exceptions = _triage_findings(
        context=context,
        findings=[],
        overall_severity=Severity.LOW,
    )
    routes = _build_approval_routes(
        context=context,
        exceptions=exceptions,
        approval_status=status,
        overall_severity=Severity.LOW,
    )

    assert status.value == "AUTO_APPROVE"
    assert exceptions == []
    assert routes[0].category == "AUTO_APPROVE"


def test_run_pipeline_executes_agent_h_graph(tmp_path: Path, monkeypatch) -> None:
    """The LangGraph pipeline should run all required workflow steps."""
    monkeypatch.chdir(tmp_path)
    _clear_jira_env(monkeypatch)

    result = run_pipeline(str(LOW_CONFIDENCE_BUNDLE))
    metrics = result["metrics"]

    assert metrics["status"] == "completed"
    assert metrics["total_processing_time_seconds"] == metrics["duration_seconds"]
    assert metrics["extraction_clause_count"] == 10
    assert "compliance_finding_count" in metrics
    assert "anomaly_finding_count" in metrics
    assert "ocr_used" in metrics
    assert metrics["exception_count"] == len(result["approval_packet"]["exceptions"])
    assert metrics["exception_rate_percent"] >= 0.0
    assert metrics["throughput_clauses_per_second"] > 0.0
    assert 0.0 <= metrics["accuracy_percent"] <= 100.0
    assert metrics["confidence_distributions"]["clauses"]["count"] == 10
    assert metrics["determinism_check"] == "PASS"
    assert metrics["determinism_compared_sections"] == [
        "risk_result",
        "approval_decision",
    ]
    assert "created_at" in metrics["determinism_excluded_timestamp_fields"]
    assert metrics["jira_posting_status"] == "DISABLED"
    assert metrics["jira_posting_reason"]
    assert result["approval_packet"]["decision"]["status"] in {
        "AUTO_APPROVE",
        "ESCALATE",
    }
    assert result["approval_packet"]["exceptions"]
    assert all(
        exception["category"]
        and exception["approver"]
        and exception["reason"]
        and exception["evidence"]
        for exception in result["approval_packet"]["exceptions"]
    )
    assert (Path(result["artifact_paths"]["final_findings"])).is_file()
    assert (Path(result["artifact_paths"]["approval_packet"])).is_file()
    assert (Path(result["artifact_paths"]["posting_payload"])).is_file()
    assert (Path(result["artifact_paths"]["jira_posting_result"])).is_file()
    assert result["jira_posting_result"]["status"] == "DISABLED"
    metrics_path = Path(result["artifact_paths"]["metrics"])
    assert metrics_path.is_file()
    saved_metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert saved_metrics["exception_count"] == metrics["exception_count"]
    assert saved_metrics["jira_posting_status"] == "DISABLED"

    audit_log_path = Path(result["artifact_paths"]["audit_log"])
    audit_log = audit_log_path.read_text(encoding="utf-8")
    assert "## Workflow Order" in audit_log
    assert "create_run_completed" in audit_log
    assert "extraction_completed" in audit_log
    assert "compliance_completed" in audit_log
    assert "anomaly_completed" in audit_log
    assert "OCR Used" in audit_log
    assert "agent_h_finalize_completed" in audit_log
    assert "jira_posting_completed" in audit_log
    assert "Jira Posting Status: DISABLED" in audit_log
    assert "Started At" in audit_log
    assert "Finished At" in audit_log
    assert "#### Inputs" in audit_log
    assert "#### Outputs" in audit_log
    assert "Exception Categories" in audit_log
    assert "## Confidence Scores" in audit_log
    assert "## Low-Confidence Cases" in audit_log
    low_confidence_findings = [
        finding
        for finding in result["final_findings"]["findings"]
        if str(finding.get("issue_type", "")).startswith("low_confidence")
    ]
    assert low_confidence_findings
    assert all(
        finding["finding_id"] in audit_log
        for finding in low_confidence_findings
    )

    posting_payload = PostingPayload.model_validate(result["posting_payload"])
    assert posting_payload.payload_type == "CLM_POSTING_PAYLOAD"
    assert posting_payload.contract.contract_id
    assert posting_payload.counterparty.name
    assert posting_payload.decision.status.value in {"AUTO_APPROVE", "ESCALATE"}
    assert posting_payload.risk.summary
    assert posting_payload.risk.findings
    assert posting_payload.obligations
    assert posting_payload.approval.routes
    assert posting_payload.approval.next_approvers
    assert posting_payload.artifacts
    assert "jira_posting_result" not in posting_payload.artifact_references
    assert set(posting_payload.artifact_references).issubset(set(result["artifact_paths"]))

    steps = [event["step"] for event in result["step_events"]]
    assert steps.index("counterparty") < steps.index("risk_scoring")
    assert steps.index("validation") < steps.index("risk_scoring")
    assert steps.index("risk_scoring") < steps.index("compliance")
    assert steps.index("compliance") < steps.index("anomaly")
    assert steps.index("anomaly") < steps.index("obligation_register")
    assert steps.index("agent_h_finalize") < steps.index("jira_posting")
    assert steps[-1] == "jira_posting"


def test_audit_log_structure_is_stable_for_same_bundle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Re-running the same bundle should log the idempotent reuse decision."""
    monkeypatch.chdir(tmp_path)
    _clear_jira_env(monkeypatch)

    first_result = run_pipeline(str(NDA_BUNDLE))
    second_result = run_pipeline(str(NDA_BUNDLE))

    first_log = Path(first_result["artifact_paths"]["audit_log"]).read_text(
        encoding="utf-8"
    )
    second_log = Path(second_result["artifact_paths"]["audit_log"]).read_text(
        encoding="utf-8"
    )

    assert "Idempotency Status: new" in first_log
    assert "Idempotency Status: duplicate" in second_log
    assert "Jira Posting Status: SKIPPED" in second_log
    second_audit_events = [
        json.loads(line)
        for line in Path(second_result["artifact_paths"]["audit_log_jsonl"])
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    event_names = {event["event"] for event in second_audit_events}
    assert "idempotency_duplicate_detected" in event_names
    assert "idempotency_results_reused" in event_names
    assert second_result["idempotency_result"]["status"] == "duplicate"
    assert (
        second_result["idempotency_result"]["baseline_run_id"]
        == first_result["run_id"]
    )
    assert set(first_result["metrics"]) == set(second_result["metrics"])


def test_determinism_check_passes_for_identical_bundle_rerun(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The second same-bundle run should be detected as idempotent duplicate."""
    monkeypatch.chdir(tmp_path)
    _clear_jira_env(monkeypatch)

    first_result = run_pipeline(str(NDA_BUNDLE))
    second_result = run_pipeline(str(NDA_BUNDLE))
    second_metrics = second_result["metrics"]

    assert second_result["idempotency_result"]["status"] == "duplicate"
    assert second_result["idempotency_result"]["external_posting_allowed"] is False
    assert second_metrics["determinism_check"] == "REUSED"
    assert second_metrics["determinism_baseline_run_id"] == first_result["run_id"]
    assert second_metrics["idempotency_status"] == "duplicate"
    assert second_metrics["idempotency_baseline_run_id"] == first_result["run_id"]
    assert second_metrics["external_posting_allowed"] is False
    assert second_metrics["jira_posting_status"] == "SKIPPED"
    assert second_metrics["determinism_differences"] == []
    assert second_metrics["determinism_compared_sections"] == [
        "risk_result",
        "approval_decision",
    ]
    assert "created_at" in second_metrics["determinism_excluded_timestamp_fields"]

    saved_metrics = json.loads(
        Path(second_result["artifact_paths"]["metrics"]).read_text(encoding="utf-8")
    )
    assert saved_metrics["determinism_check"] == "REUSED"
    assert saved_metrics["determinism_baseline_run_id"] == first_result["run_id"]
    assert saved_metrics["idempotency_status"] == "duplicate"
    assert saved_metrics["jira_posting_status"] == "SKIPPED"

    idempotency_path = Path(second_result["artifact_paths"]["idempotency_result"])
    assert idempotency_path.is_file()
    saved_idempotency = json.loads(idempotency_path.read_text(encoding="utf-8"))
    assert saved_idempotency["baseline_run_id"] == first_result["run_id"]
    assert saved_idempotency["external_posting_allowed"] is False

    posting_payload = json.loads(
        Path(second_result["artifact_paths"]["posting_payload"]).read_text(
            encoding="utf-8"
        )
    )
    assert posting_payload["run_id"] == second_result["run_id"]
    assert posting_payload["external_posting_allowed"] is False
    assert posting_payload["duplicate_of_run_id"] == first_result["run_id"]

    jira_result = json.loads(
        Path(second_result["artifact_paths"]["jira_posting_result"]).read_text(
            encoding="utf-8"
        )
    )
    assert jira_result["status"] == "SKIPPED"
    assert "Duplicate input fingerprint" in jira_result["reason"]


def test_determinism_comparison_ignores_timestamps_and_reports_differences() -> None:
    """Timestamp changes should be ignored while decision/risk changes fail."""
    result = _compare_determinism_payloads(
        baseline_payload={
            "risk_result": {
                "overall_severity": "LOW",
                "summary": "No issues.",
                "created_at": "2026-01-01T00:00:00Z",
            },
            "approval_decision": {
                "status": "AUTO_APPROVE",
                "approved": True,
                "rationale": "Standard terms.",
                "reviewed_at": "2026-01-01T00:00:00Z",
            },
        },
        current_payload={
            "risk_result": {
                "overall_severity": "HIGH",
                "summary": "High risk.",
                "created_at": "2026-01-02T00:00:00Z",
            },
            "approval_decision": {
                "status": "ESCALATE",
                "approved": False,
                "rationale": "Review required.",
                "reviewed_at": "2026-01-02T00:00:00Z",
            },
        },
        baseline_run_id="baseline-run",
    )

    assert result["determinism_check"] == "FAIL"
    assert result["determinism_baseline_run_id"] == "baseline-run"
    differences = "\n".join(result["determinism_differences"])
    assert "risk_result.overall_severity" in differences
    assert "approval_decision.status" in differences
    assert "created_at" not in differences
    assert "reviewed_at" not in differences
