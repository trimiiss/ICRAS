"""Tests for the Compliance Agent."""

import json
from pathlib import Path

from agents.compliance import run_compliance_review
from agents.orchestrator import _triage_findings
from schemas.common import Severity
from schemas.finding import Finding
from utils.bundle_loader import load_bundle


PROJECT_ROOT = Path(__file__).resolve().parent.parent
NDA_BUNDLE = PROJECT_ROOT / "data" / "bundles" / "clean_nda"


def _run_dir(tmp_path: Path, run_id: str = "compliance-run") -> Path:
    """Create a run directory with an audit log."""
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "audit_log.jsonl").touch()
    return run_dir


def _context(**overrides: object) -> dict:
    """Return compliance context."""
    base = {
        "run_id": "compliance-run",
        "contract_file": "contract.pdf",
        "jurisdiction": "Delaware, USA",
        "approval_policy": {
            "high_risk_jurisdictions": ["Russia", "Iran", "North Korea", "Syria"],
            "gdpr_requirements": {
                "applies_when_personal_data": True,
                "required_clauses": [
                    "data_processing_terms",
                    "cross_border_transfer_controls",
                    "data_subject_rights",
                ],
                "severity_if_missing": "CRITICAL",
            },
        },
        "jurisdiction_rules": {
            "jurisdiction": "Delaware, USA",
            "regulatory_requirements": [],
        },
    }
    base.update(overrides)
    return base


def _extracted_contract(clauses: list[dict]) -> dict:
    """Return a minimal extracted contract payload."""
    return {
        "run_id": "compliance-run",
        "document_id": "DOC-001",
        "source_file": "contract.pdf",
        "clauses": clauses,
    }


def _clause(clause_type: str, text: str, page_number: int = 1) -> dict:
    """Return a minimal extracted clause."""
    return {
        "clause_type": clause_type,
        "title": clause_type.replace("_", " ").title(),
        "text": text,
        "page_number": page_number,
        "confidence": 0.95,
    }


def _evidence_index() -> dict:
    """Return a minimal evidence index."""
    return {
        "records": [
            {
                "evidence_id": "EV-001",
                "document_id": "DOC-001",
                "source_file": "contract.pdf",
                "page_number": 1,
                "excerpt": "Contract source excerpt.",
            }
        ]
    }


def test_missing_gdpr_clause_creates_compliance_finding(tmp_path: Path) -> None:
    """Personal-data language without GDPR should be a compliance finding."""
    run_dir = _run_dir(tmp_path)

    result = run_compliance_review(
        context=_context(),
        extracted_contract=_extracted_contract(
            [
                _clause(
                    "data_protection",
                    "Supplier may process personal data and privacy records.",
                )
            ]
        ),
        run_dir=run_dir,
        evidence_index=_evidence_index(),
    )

    findings = result["findings"]
    assert any(finding["issue_type"] == "missing_gdpr_clause" for finding in findings)
    gdpr_finding = next(
        finding for finding in findings if finding["issue_type"] == "missing_gdpr_clause"
    )
    assert gdpr_finding["severity"] == "CRITICAL"
    assert gdpr_finding["evidence"][0]["source_file"] == "contract.pdf"
    assert gdpr_finding["manual_review_required"] is True
    assert (run_dir / "compliance_findings.json").is_file()
    saved = json.loads((run_dir / "compliance_findings.json").read_text())
    assert saved["requires_compliance_review"] is True


def test_missing_gdpr_obligation_creates_compliance_finding() -> None:
    """GDPR language must include configured data-processing obligations."""
    result = run_compliance_review(
        context=_context(),
        extracted_contract=_extracted_contract(
            [
                _clause(
                    "data_protection",
                    "The parties comply with GDPR when processing personal data.",
                )
            ]
        ),
        evidence_index=_evidence_index(),
    )

    issue_types = {finding["issue_type"] for finding in result["findings"]}
    assert "missing_gdpr_obligation" in issue_types
    assert all(finding["evidence"] for finding in result["findings"])


def test_high_risk_jurisdiction_creates_critical_finding() -> None:
    """High-risk countries should route to compliance review."""
    result = run_compliance_review(
        context=_context(jurisdiction="Syria"),
        extracted_contract=_extracted_contract(
            [_clause("governing_law", "This Agreement is governed by Syria law.")]
        ),
        evidence_index=_evidence_index(),
    )

    finding = next(
        finding
        for finding in result["findings"]
        if finding["issue_type"] == "high_risk_jurisdiction"
    )
    assert finding["severity"] == "CRITICAL"
    assert finding["field_name"] == "governing_law"


def test_jurisdiction_required_clause_creates_compliance_finding() -> None:
    """jurisdiction_rules.yaml required clauses should be enforced."""
    result = run_compliance_review(
        context=_context(
            jurisdiction_rules={
                "jurisdiction": "Delaware, USA",
                "required_clauses": [
                    {
                        "clause_type": "data_residency",
                        "description": "Add data residency clause.",
                        "severity_if_missing": "HIGH",
                    }
                ],
            }
        ),
        extracted_contract=_extracted_contract(
            [_clause("governing_law", "This Agreement is governed by Delaware law.")]
        ),
        evidence_index=_evidence_index(),
    )

    finding = next(
        finding
        for finding in result["findings"]
        if finding["issue_type"] == "missing_compliance_clause"
    )
    assert finding["field_name"] == "data_residency"
    assert finding["severity"] == "HIGH"
    assert finding["recommendation"] == "Add data residency clause."


def test_compliance_findings_route_to_compliance_review() -> None:
    """New compliance issue types should match approval-policy routing."""
    context = {
        "approval_policy": load_bundle(NDA_BUNDLE)["approval_policy"],
        "contract_file": "contract.pdf",
    }
    raw_finding = {
        "finding_id": "CMP-001",
        "category": "compliance",
        "title": "Missing compliance clause",
        "description": "Missing jurisdiction-specific clause.",
        "severity": "HIGH",
        "confidence": 1.0,
        "evidence": [{"source_file": "contract.pdf"}],
        "recommendation": "Add required compliance language.",
        "field_name": "data_residency",
        "issue_type": "missing_compliance_clause",
        "manual_review_required": True,
        "risk_engine_ready": True,
    }

    status, exceptions = _triage_findings(
        context=context,
        findings=[Finding.model_validate(raw_finding)],
        overall_severity=Severity.HIGH,
    )

    assert status.value == "ESCALATE"
    assert exceptions[0].category.value == "COMPLIANCE"
    assert exceptions[0].approver == "compliance_officer"
