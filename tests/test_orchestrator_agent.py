"""Tests for Agent H LangGraph orchestration."""

from pathlib import Path

from agents.orchestrator_agent import (
    _build_approval_routes,
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


def test_agent_h_routes_ticket_exception_categories_from_policy() -> None:
    """Each US-17 routing-table case should map through approval_policy.yaml."""
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
    """The LangGraph pipeline should run all required Agent H steps."""
    monkeypatch.chdir(tmp_path)

    result = run_pipeline(str(NDA_BUNDLE))

    assert result["metrics"]["status"] == "completed"
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
    posting_payload = PostingPayload.model_validate(result["posting_payload"])
    assert posting_payload.payload_type == "CLM_POSTING_PAYLOAD"
    assert posting_payload.contract.contract_id
    assert posting_payload.counterparty.name
    assert posting_payload.decision.status.value in {"AUTO_APPROVE", "ESCALATE"}
    assert posting_payload.risk.summary
    assert posting_payload.approval.routes
    assert posting_payload.approval.next_approvers
    assert posting_payload.artifacts
    assert set(posting_payload.artifact_references) == set(result["artifact_paths"])

    steps = [event["step"] for event in result["step_events"]]
    assert steps.index("counterparty") < steps.index("risk_scoring")
    assert steps.index("validation") < steps.index("risk_scoring")
    assert steps[-1] == "agent_h_finalize"
