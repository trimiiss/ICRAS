"""Tests for Pydantic v2 schemas (US-03).

Covers:
    - Valid schema data passes validation.
    - Invalid confidence values fail.
    - Findings without evidence fail.
    - All core schemas can be instantiated with valid data.
"""

import pytest
from pydantic import ValidationError

from schemas.common import EvidencePointer, Severity
from schemas.context_packet import ContextPacket
from schemas.extracted_clause import ExtractedClause
from schemas.finding import Finding
from schemas.risk_result import RiskResult
from schemas.approval_packet import ApprovalDecision, ApprovalPacket


# ---------------------------------------------------------------------------
# Helpers — reusable valid data factories
# ---------------------------------------------------------------------------

def _valid_evidence() -> dict:
    """Return a valid EvidencePointer dictionary."""
    return {
        "source_file": "contract.pdf",
        "page_number": 3,
        "clause_reference": "4.2",
        "excerpt": "The receiving party shall not disclose...",
    }


def _valid_finding(**overrides) -> dict:
    """Return a valid Finding dictionary, with optional overrides."""
    base = {
        "finding_id": "F-001",
        "category": "compliance",
        "title": "Missing whistleblower notice",
        "description": "The NDA does not include the required DTSA whistleblower notice.",
        "severity": "HIGH",
        "confidence": 0.85,
        "evidence": [_valid_evidence()],
        "recommendation": "Add DTSA whistleblower immunity notice in Section 7.",
    }
    base.update(overrides)
    return base


def _valid_risk_result() -> dict:
    """Return a valid RiskResult dictionary."""
    return {
        "overall_severity": "HIGH",
        "findings": [_valid_finding()],
        "requires_human_review": True,
        "summary": "One high-severity compliance finding requires legal review.",
    }


# ---------------------------------------------------------------------------
# EvidencePointer
# ---------------------------------------------------------------------------

class TestEvidencePointer:
    def test_valid_full(self):
        ep = EvidencePointer(**_valid_evidence())
        assert ep.source_file == "contract.pdf"
        assert ep.page_number == 3

    def test_valid_minimal(self):
        ep = EvidencePointer(source_file="contract.pdf")
        assert ep.page_number is None
        assert ep.excerpt is None


# ---------------------------------------------------------------------------
# Finding
# ---------------------------------------------------------------------------

class TestFinding:
    def test_valid_finding_passes(self):
        f = Finding(**_valid_finding())
        assert f.finding_id == "F-001"
        assert f.severity == Severity.HIGH
        assert f.confidence == 0.85
        assert len(f.evidence) == 1

    def test_finding_without_evidence_fails(self):
        """A Finding with an empty evidence list must fail validation."""
        with pytest.raises(ValidationError, match="evidence"):
            Finding(**_valid_finding(evidence=[]))

    def test_confidence_below_zero_fails(self):
        with pytest.raises(ValidationError, match="confidence"):
            Finding(**_valid_finding(confidence=-0.1))

    def test_confidence_above_one_fails(self):
        with pytest.raises(ValidationError, match="confidence"):
            Finding(**_valid_finding(confidence=1.5))

    def test_confidence_at_boundaries(self):
        f0 = Finding(**_valid_finding(confidence=0.0))
        assert f0.confidence == 0.0
        f1 = Finding(**_valid_finding(confidence=1.0))
        assert f1.confidence == 1.0

    def test_invalid_severity_fails(self):
        with pytest.raises(ValidationError):
            Finding(**_valid_finding(severity="EXTREME"))

    def test_recommendation_is_optional(self):
        f = Finding(**_valid_finding(recommendation=None))
        assert f.recommendation is None


# ---------------------------------------------------------------------------
# ExtractedClause
# ---------------------------------------------------------------------------

class TestExtractedClause:
    def test_valid_clause(self):
        clause = ExtractedClause(
            clause_id="C-001",
            clause_type="confidentiality_definition",
            title="Definition of Confidential Information",
            text="Confidential Information means any information...",
            page_number=2,
            section_reference="1.1",
            confidence=0.92,
        )
        assert clause.clause_id == "C-001"
        assert clause.confidence == 0.92

    def test_confidence_out_of_range_fails(self):
        with pytest.raises(ValidationError):
            ExtractedClause(
                clause_id="C-002",
                clause_type="term",
                title="Term",
                text="This agreement shall remain...",
                confidence=2.0,
            )


# ---------------------------------------------------------------------------
# ContextPacket
# ---------------------------------------------------------------------------

class TestContextPacket:
    def test_valid_context_packet(self):
        cp = ContextPacket(
            run_id="20250101_120000_abc12345",
            bundle_name="clean_nda",
            contract_type="Non-Disclosure Agreement",
            counterparty="Acme Corporation",
            jurisdiction="Delaware, USA",
            contract_file="contract.pdf",
        )
        assert cp.run_id == "20250101_120000_abc12345"
        assert cp.playbook == {}


# ---------------------------------------------------------------------------
# RiskResult
# ---------------------------------------------------------------------------

class TestRiskResult:
    def test_valid_risk_result(self):
        rr = RiskResult(**_valid_risk_result())
        assert rr.overall_severity == Severity.HIGH
        assert rr.requires_human_review is True
        assert len(rr.findings) == 1


# ---------------------------------------------------------------------------
# ApprovalPacket
# ---------------------------------------------------------------------------

class TestApprovalPacket:
    def test_valid_approval_packet(self):
        ap = ApprovalPacket(
            run_id="20250101_120000_abc12345",
            decision=ApprovalDecision(
                approved=False,
                rationale="High-severity compliance finding requires remediation.",
            ),
            risk_result=RiskResult(**_valid_risk_result()),
        )
        assert ap.run_id == "20250101_120000_abc12345"
        assert ap.decision.approved is False
        assert ap.reviewer is None

    def test_approval_packet_with_reviewer(self):
        ap = ApprovalPacket(
            run_id="20250101_120000_abc12345",
            decision=ApprovalDecision(approved=True, rationale="All clear."),
            risk_result=RiskResult(
                overall_severity="LOW",
                findings=[],
                requires_human_review=False,
                summary="No issues found.",
            ),
            reviewer="Jane Smith",
            notes="Reviewed and approved after manual check.",
        )
        assert ap.reviewer == "Jane Smith"
        assert ap.decision.approved is True
