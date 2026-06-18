"""Tests for the Anomaly Agent."""

import json
from pathlib import Path

import pytest

from agents.anomaly_agent import AnomalyAgentError, run_anomaly_review


def _run_dir(tmp_path: Path, run_id: str = "anomaly-run") -> Path:
    """Create a run directory that follows the runtime artifact convention."""
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "audit_log.jsonl").touch()
    return run_dir


def _context(**overrides: object) -> dict:
    """Return anomaly review context."""
    base = {
        "run_id": "anomaly-run",
        "contract_type": "Master Services Agreement",
        "counterparty": "Acme Corporation",
        "jurisdiction": "Delaware, USA",
        "effective_date": "2025-03-01",
        "contract_file": "contract.pdf",
    }
    base.update(overrides)
    return base


def _evidence(page_number: int, excerpt: str) -> dict:
    """Return a valid evidence pointer."""
    return {
        "evidence_id": f"EV-{page_number:03d}",
        "document_id": "DOC-001",
        "source_file": "contract.pdf",
        "page_number": page_number,
        "excerpt": excerpt,
    }


def _clause(
    clause_type: str,
    text: str,
    page_number: int,
    **overrides: object,
) -> dict:
    """Return an extracted clause dictionary."""
    base = {
        "clause_type": clause_type,
        "title": clause_type.replace("_", " ").title(),
        "text": text,
        "page_number": page_number,
        "section_reference": str(page_number),
        "confidence": 0.95,
        "evidence": _evidence(page_number, text),
        "evidence_pointer": _evidence(page_number, text),
    }
    base.update(overrides)
    return base


def _evidence_index() -> dict:
    """Return a minimal evidence index."""
    return {
        "records": [
            {
                "evidence_id": "EV-001",
                "document_id": "DOC-001",
                "source_file": "contract.pdf",
                "page_number": 1,
                "excerpt": "Master Services Agreement",
            }
        ]
    }


def test_anomaly_agent_detects_conflicts_and_unusual_terms(tmp_path: Path) -> None:
    """Conflicting and unusual values should produce source-backed findings."""
    run_dir = _run_dir(tmp_path)
    result = run_anomaly_review(
        context=_context(),
        extracted_contract={
            "run_id": "anomaly-run",
            "clauses": [
                _clause(
                    "governing_law",
                    "This Agreement is governed by the laws of Delaware, USA.",
                    1,
                ),
                _clause(
                    "governing_law",
                    "Any dispute is governed by the laws of New York, USA.",
                    2,
                ),
                _clause(
                    "payment_terms",
                    "Customer shall pay invoices net 30.",
                    3,
                ),
                _clause(
                    "payment_terms",
                    "Supplier invoices are payable net 60.",
                    4,
                ),
                _clause(
                    "liability_cap",
                    "Supplier liability is capped at 100000 USD.",
                    5,
                ),
                _clause(
                    "liability_cap",
                    "Supplier liability is capped at 250000 USD.",
                    6,
                ),
                _clause(
                    "effective_date",
                    "This Agreement is effective March 1, 2025.",
                    7,
                ),
                _clause(
                    "expiry_date",
                    "This Agreement expires on February 1, 2025.",
                    8,
                ),
                _clause(
                    "auto_renewal",
                    "The Agreement automatically renews indefinitely.",
                    9,
                ),
            ],
        },
        run_dir=run_dir,
        evidence_index=_evidence_index(),
    )

    assert (run_dir / "anomaly_findings.json").is_file()
    assert result["artifact_paths"]["anomaly_findings"] == str(
        run_dir / "anomaly_findings.json"
    )
    issue_types = {finding["issue_type"] for finding in result["findings"]}
    assert "conflicting_governing_law" in issue_types
    assert "contradictory_payment_terms" in issue_types
    assert "duplicate_clause_value_conflict" in issue_types
    assert "suspicious_date_ordering" in issue_types
    assert "unusual_contract_pattern" in issue_types
    assert all(finding["evidence"] for finding in result["findings"])
    assert all(finding["evidence"][0]["excerpt"] for finding in result["findings"])
    assert all(finding["manual_review_required"] for finding in result["findings"])

    saved = json.loads((run_dir / "anomaly_findings.json").read_text(encoding="utf-8"))
    assert saved["requires_legal_review"] is True
    assert saved["checked_rules"] == [
        "conflicting_governing_law",
        "contradictory_payment_terms",
        "duplicate_clause_value_conflict",
        "suspicious_date_ordering",
        "unusual_contract_pattern",
    ]


def test_standard_terms_create_no_anomaly_findings(tmp_path: Path) -> None:
    """Standard, internally consistent terms should not create anomaly findings."""
    result = run_anomaly_review(
        context=_context(effective_date="2025-01-15"),
        extracted_contract={
            "run_id": "anomaly-run",
            "clauses": [
                _clause(
                    "payment_terms",
                    "Invoices are payable net 30.",
                    1,
                ),
                _clause(
                    "liability_cap",
                    "Each party's liability is capped at fees paid in 12 months.",
                    2,
                ),
                _clause(
                    "governing_law",
                    "This Agreement is governed by Delaware law.",
                    3,
                ),
                _clause(
                    "auto_renewal",
                    "This Agreement does not auto-renew.",
                    4,
                ),
                _clause(
                    "expiry_date",
                    "This Agreement expires on January 14, 2026.",
                    5,
                ),
            ],
        },
        run_dir=_run_dir(tmp_path),
        evidence_index=_evidence_index(),
    )

    assert result["findings"] == []
    assert result["anomaly_result"]["requires_legal_review"] is False


def test_missing_run_directory_raises_clear_error(tmp_path: Path) -> None:
    """Anomaly review should fail clearly when run_dir has not been created."""
    with pytest.raises(AnomalyAgentError, match="Create it with create_run_folder"):
        run_anomaly_review(
            context=_context(),
            extracted_contract={"clauses": []},
            run_dir=tmp_path / "runs" / "missing",
            evidence_index=_evidence_index(),
        )
