"""Tests for required contract field validation."""

import json
from pathlib import Path

import pytest

from agents.validation import ValidationAgentError, run_validation


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


def _approval_policy(**overrides: object) -> dict:
    """Return approval policy data for deeper validation tests."""
    base = {
        "approved_payment_terms": {
            "terms": ["net-30"],
            "severity_if_unapproved": "HIGH",
        },
        "liability_cap_requirements": {
            "required": True,
            "minimum_cap": "fees_paid_12_months",
            "severity_if_missing": "HIGH",
        },
        "manual_review_confidence_threshold": 0.75,
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

    def test_deeper_validation_findings_are_risk_engine_ready(
        self,
        tmp_path: Path,
    ) -> None:
        run_dir = _run_dir(tmp_path)
        clauses = [
            {
                "clause_type": "parties",
                "title": "Parties",
                "text": (
                    "This Agreement is among Alpha LLC, Beta Inc, and Gamma GmbH."
                ),
                "page_number": 1,
                "confidence": 0.95,
            },
            {
                "clause_type": "payment_terms",
                "title": "Payment Terms",
                "text": (
                    "Customer shall pay invoices on net 90 terms. The monthly fee "
                    "of 100 for 12 months totals 1000."
                ),
                "page_number": 2,
                "confidence": 0.95,
            },
            {
                "clause_type": "termination",
                "title": "Termination",
                "text": "Either party may terminate with 30 days' notice.",
                "page_number": 3,
                "confidence": 0.95,
            },
            {
                "clause_type": "governing_law",
                "title": "Governing Law",
                "text": "This Agreement is governed by the laws of Delaware, USA.",
                "page_number": 4,
                "confidence": 0.95,
            },
            {
                "clause_type": "governing_law",
                "title": "Governing Law",
                "text": "Any dispute is governed by the laws of New York, USA.",
                "page_number": 5,
                "confidence": 0.95,
            },
            {
                "clause_type": "expiry_date",
                "title": "Expiry Date",
                "text": "This Agreement expires on February 1, 2025.",
                "page_number": 6,
                "confidence": 0.95,
            },
            {
                "clause_type": "signature",
                "title": "Signature",
                "text": "Signed by Alpha LLC and Beta Inc.",
                "page_number": 7,
                "confidence": 0.5,
            },
        ]

        result = run_validation(
            context=_context(
                effective_date="2025-03-01",
                approval_policy=_approval_policy(),
            ),
            clauses=clauses,
            run_dir=run_dir,
            evidence_index=_evidence_index(),
        )

        findings = result["findings"]
        issue_types = {finding["issue_type"] for finding in findings}
        assert "conflicting_governing_law" in issue_types
        assert "missing_field" in issue_types
        assert "suspicious_date_ordering" in issue_types
        assert "payment_terms_policy_violation" in issue_types
        assert "calculation_error" in issue_types
        assert "low_confidence_signature" in issue_types
        assert "multi_party_signature_incomplete" in issue_types

        net_90_finding = next(
            finding
            for finding in findings
            if finding["issue_type"] == "payment_terms_policy_violation"
        )
        assert net_90_finding["field_name"] == "payment_terms"
        assert net_90_finding["severity"] == "HIGH"
        assert net_90_finding["risk_engine_ready"] is True
        assert net_90_finding["manual_review_required"] is True
        assert net_90_finding["source_clause_text"]
        assert net_90_finding["source_page"] == 2
        assert net_90_finding["evidence_pointer"]["page_number"] == 2

        liability_finding = next(
            finding
            for finding in findings
            if finding["title"] == "Missing liability cap"
        )
        assert liability_finding["field_name"] == "liability_cap"
        assert liability_finding["severity"] == "HIGH"

        saved = json.loads((run_dir / "validation_findings.json").read_text())
        assert saved["findings"]

    def test_low_ocr_confidence_creates_manual_review_finding(
        self,
        tmp_path: Path,
    ) -> None:
        run_dir = _run_dir(tmp_path)
        extracted_contract = {
            "run_id": "test-run",
            "document_id": "DOC-001",
            "source_file": "contract.pdf",
            "text_extraction_method": "ocr",
            "ocr_metadata": {
                "used": True,
                "engine": "pymupdf_tesseract",
                "pages_processed": 1,
                "average_confidence": 0.62,
                "low_confidence": True,
                "manual_review_required": True,
                "reason": "OCR was used because normal extraction failed.",
                "pages": [
                    {
                        "page_number": 1,
                        "used": True,
                        "confidence": 0.62,
                        "text_length": 120,
                        "warning": "OCR confidence is low on page 1.",
                    }
                ],
            },
            "clauses": [
                {
                    "clause_type": "payment_terms",
                    "title": "Payment Terms",
                    "text": "Invoices are payable net 30.",
                    "page_number": 1,
                    "confidence": 0.95,
                },
                {
                    "clause_type": "termination",
                    "title": "Termination",
                    "text": "Either party may terminate on notice.",
                    "page_number": 1,
                    "confidence": 0.95,
                },
            ],
        }

        result = run_validation(
            context=_context(approval_policy=_approval_policy()),
            clauses=None,
            run_dir=run_dir,
            evidence_index=_evidence_index(),
            extracted_contract=extracted_contract,
        )

        finding = next(
            finding
            for finding in result["findings"]
            if finding["issue_type"] == "low_ocr_confidence"
        )
        assert finding["field_name"] == "ocr_confidence"
        assert finding["confidence"] == 0.62
        assert finding["manual_review_required"] is True
        assert finding["evidence"][0]["page_number"] == 1

    def test_reads_extracted_contract_and_existing_validation_results(
        self,
        tmp_path: Path,
    ) -> None:
        run_dir = _run_dir(tmp_path)
        (run_dir / "extracted_contract.json").write_text(
            json.dumps(
                {
                    "run_id": "test-run",
                    "document_id": "DOC-001",
                    "source_file": "contract.pdf",
                    "clauses": [
                        {
                            "clause_type": "payment_terms",
                            "title": "Payment Terms",
                            "text": "Invoices are payable net 90.",
                            "page_number": 2,
                            "confidence": 0.95,
                        },
                        {
                            "clause_type": "termination",
                            "title": "Termination",
                            "text": "Either party may terminate on notice.",
                            "page_number": 3,
                            "confidence": 0.95,
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        (run_dir / "validation_findings.json").write_text(
            json.dumps(
                {
                    "run_id": "test-run",
                    "normalized_fields": {},
                    "validated_fields": [],
                    "findings": [
                        {
                            "finding_id": "VAL-001",
                            "category": "contract_validation",
                            "title": "Existing manual note",
                            "description": "Existing validation note.",
                            "severity": "LOW",
                            "confidence": 1.0,
                            "evidence": [{"source_file": "contract.pdf"}],
                            "field_name": "manual_note",
                            "issue_type": "manual_note",
                            "message": "Existing validation note.",
                            "source_clause_text": "Existing validation note.",
                            "evidence_pointer": {"source_file": "contract.pdf"},
                            "manual_review_required": False,
                            "risk_engine_ready": True,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        result = run_validation(
            context=_context(approval_policy=_approval_policy()),
            clauses=None,
            run_dir=run_dir,
            evidence_index=_evidence_index(),
        )

        titles = {finding["title"] for finding in result["findings"]}
        assert "Existing manual note" in titles
        assert "Unapproved payment terms" in titles
