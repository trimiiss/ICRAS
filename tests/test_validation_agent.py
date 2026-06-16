"""Tests for required contract field validation."""

import json
from pathlib import Path

import pytest

from agents.validation_agent import ValidationAgentError, run_validation


def _run_dir(tmp_path: Path, run_id: str = "test-run") -> Path:
    """Create a run directory that follows the runtime artifact convention."""
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "audit_log.jsonl").touch()
    return run_dir


def _context(**overrides: object) -> dict:
    """Return valid context data for validation tests."""
    base = {
        "run_id": "test-run",
        "contract_type": "Master Services Agreement",
        "counterparty": "Acme Corporation",
        "jurisdiction": "New York, USA",
        "effective_date": "03/01/2025",
        "contract_file": "contract.pdf",
        "playbook": {
            "required_clauses": [
                {
                    "clause_type": "payment_terms",
                    "severity_if_missing": "HIGH",
                },
                {
                    "clause_type": "termination",
                    "severity_if_missing": "MEDIUM",
                },
            ]
        },
    }
    base.update(overrides)
    return base


def _evidence_index() -> dict:
    """Return a minimal page-level evidence index."""
    return {
        "records": [
            {
                "evidence_id": "EV-001",
                "document_id": "DOC-001",
                "source_file": "contract.pdf",
                "page_number": 1,
                "excerpt": "Master Services Agreement - Acme Corporation",
            }
        ]
    }


class TestRunValidation:
    """Validate required contract fields and artifact output."""

    def test_normalizes_effective_date_and_writes_artifact(
        self,
        tmp_path: Path,
    ) -> None:
        run_dir = _run_dir(tmp_path)
        clauses = [
            {
                "clause_type": "payment_terms",
                "title": "Payment Terms",
                "text": "Customer shall pay invoices within 30 days in USD.",
                "confidence": 0.95,
            },
            {
                "clause_type": "termination",
                "title": "Termination",
                "text": "Either party may terminate on 30 days' written notice.",
                "confidence": 0.95,
            },
        ]

        result = run_validation(
            context=_context(),
            clauses=clauses,
            run_dir=run_dir,
            evidence_index=_evidence_index(),
        )

        artifact_path = run_dir / "validation_findings.json"
        assert artifact_path.is_file()
        assert result["artifact_paths"]["validation_findings"] == str(artifact_path)
        assert result["findings"] == []
        assert result["validation_result"]["normalized_fields"]["effective_date"] == (
            "2025-03-01"
        )

        saved = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert saved["normalized_fields"]["effective_date"] == "2025-03-01"
        assert saved["findings"] == []

    def test_missing_fields_create_source_backed_findings(
        self,
        tmp_path: Path,
    ) -> None:
        run_dir = _run_dir(tmp_path)
        context = _context(
            jurisdiction="",
            effective_date="",
        )

        result = run_validation(
            context=context,
            clauses=[],
            run_dir=run_dir,
            evidence_index=_evidence_index(),
        )

        findings = result["findings"]
        titles = {finding["title"] for finding in findings}
        assert "Missing effective date" in titles
        assert "Missing governing law" in titles
        assert "Missing payment terms" in titles
        assert "Missing termination terms" in titles

        payment_finding = next(
            finding for finding in findings if finding["title"] == "Missing payment terms"
        )
        assert payment_finding["severity"] == "HIGH"
        assert payment_finding["evidence"][0]["evidence_id"] == "EV-001"
        assert payment_finding["evidence"][0]["excerpt"]

    def test_invalid_effective_date_creates_finding(self, tmp_path: Path) -> None:
        run_dir = _run_dir(tmp_path)

        result = run_validation(
            context=_context(effective_date="not a date"),
            clauses=[
                {
                    "clause_type": "payment_terms",
                    "text": "Fees are due within 30 days.",
                },
                {
                    "clause_type": "termination",
                    "text": "Either party may terminate for uncured breach.",
                },
            ],
            run_dir=run_dir,
            evidence_index=_evidence_index(),
        )

        assert "effective_date" not in result["validation_result"]["normalized_fields"]
        invalid_finding = next(
            finding for finding in result["findings"] if finding["title"] == "Invalid effective date"
        )
        assert invalid_finding["severity"] == "HIGH"
        assert invalid_finding["evidence"][0]["excerpt"] == (
            "effective_date: not a date"
        )

    def test_payment_terms_are_skipped_when_not_applicable(
        self,
        tmp_path: Path,
    ) -> None:
        run_dir = _run_dir(tmp_path)

        result = run_validation(
            context=_context(
                contract_type="Non-Disclosure Agreement",
                playbook={"required_clauses": []},
            ),
            clauses=[
                {
                    "clause_type": "termination",
                    "text": "Either party may terminate with written notice.",
                }
            ],
            run_dir=run_dir,
            evidence_index=_evidence_index(),
        )

        assert all(
            finding["title"] != "Missing payment terms"
            for finding in result["findings"]
        )
        payment_field = next(
            field
            for field in result["validation_result"]["validated_fields"]
            if field["field_name"] == "payment_terms"
        )
        assert payment_field["normalized_value"] == "not_applicable"

    def test_missing_run_directory_raises_clear_error(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationAgentError, match="Create it with create_run_folder"):
            run_validation(
                context=_context(),
                clauses=[],
                run_dir=tmp_path / "runs" / "missing",
                evidence_index=_evidence_index(),
            )
