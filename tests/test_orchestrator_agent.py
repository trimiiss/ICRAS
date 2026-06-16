"""Tests for Agent H LangGraph orchestration."""

from pathlib import Path

from agents.orchestrator_agent import (
    _merge_deduplicate_sort_findings,
    run_pipeline,
)


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
) -> dict:
    """Return a shared finding payload."""
    evidence = _evidence()
    return {
        "finding_id": finding_id,
        "category": "finance",
        "title": title,
        "description": title,
        "severity": severity,
        "confidence": 0.9,
        "evidence": [evidence],
        "recommendation": "Request finance approval.",
        "field_name": "payment_terms",
        "issue_type": "payment_terms_policy_violation",
        "message": title,
        "source_clause_text": evidence["excerpt"],
        "source_page": 2,
        "evidence_pointer": evidence,
        "manual_review_required": True,
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


def test_run_pipeline_executes_agent_h_graph(tmp_path: Path, monkeypatch) -> None:
    """The LangGraph pipeline should run all required Agent H steps."""
    monkeypatch.chdir(tmp_path)

    result = run_pipeline(str(NDA_BUNDLE))

    assert result["metrics"]["status"] == "completed"
    assert result["approval_packet"]["decision"]["status"] in {
        "AUTO_APPROVE",
        "ESCALATE",
    }
    assert (Path(result["artifact_paths"]["final_findings"])).is_file()
    assert (Path(result["artifact_paths"]["approval_packet"])).is_file()
    assert (Path(result["artifact_paths"]["posting_payload"])).is_file()

    steps = [event["step"] for event in result["step_events"]]
    assert steps.index("counterparty") < steps.index("risk_scoring")
    assert steps.index("validation") < steps.index("risk_scoring")
    assert steps[-1] == "agent_h_finalize"
