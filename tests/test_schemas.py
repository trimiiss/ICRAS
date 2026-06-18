"""Tests for Pydantic v2 schemas.

Covers:
    - Valid schema data passes validation.
    - Invalid confidence values fail.
    - Findings without evidence fail.
    - All core schemas can be instantiated with valid data.
"""

import pytest
from pydantic import ValidationError

from schemas.common import EvidencePointer, Severity
from schemas.anomaly_result import AnomalyResult
from schemas.context_packet import ContextPacket
from schemas.exception_triage import ExceptionCategory, ExceptionTriageItem
from schemas.extracted_clause import (
    ExtractedClause,
    ExtractedContract,
    OcrMetadata,
    OcrPageResult,
)
from schemas.finding import Finding
from schemas.policy_rules import PolicyRules
from schemas.posting_payload import (
    ApprovalPostingData,
    ArtifactReference,
    ContractPostingData,
    CounterpartyPostingData,
    DecisionPostingData,
    PostingPayload,
    RiskPostingData,
)
from schemas.risk_result import RiskResult
from schemas.approval_packet import ApprovalDecision, ApprovalPacket
from schemas.validation_result import ValidationResult, ValidatedContractField


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
            clause_text="Confidential Information means any information...",
            page_number=2,
            page_numbers=[2],
            section_reference="1.1",
            confidence=0.92,
            confidence_score=0.92,
            evidence=_valid_evidence(),
            evidence_pointer=_valid_evidence(),
            manual_review_required=False,
        )
        assert clause.clause_id == "C-001"
        assert clause.confidence == 0.92
        assert clause.confidence_score == 0.92
        assert clause.evidence.source_file == "contract.pdf"
        assert clause.evidence_pointer.source_file == "contract.pdf"

    def test_confidence_out_of_range_fails(self):
        with pytest.raises(ValidationError):
            ExtractedClause(
                clause_id="C-002",
                clause_type="term",
                title="Term",
                text="This agreement shall remain...",
                clause_text="This agreement shall remain...",
                confidence=2.0,
                confidence_score=2.0,
                evidence=_valid_evidence(),
                evidence_pointer=_valid_evidence(),
            )

    def test_valid_extracted_contract(self):
        extracted = ExtractedContract(
            run_id="20250101_120000_abc12345",
            document_id="DOC-001",
            source_file="contract.pdf",
            clauses=[
                ExtractedClause(
                    clause_id="C-001",
                    clause_type="confidentiality",
                    title="Confidentiality",
                    text="The receiving party shall protect confidential information.",
                    clause_text="The receiving party shall protect confidential information.",
                    page_number=1,
                    page_numbers=[1],
                    section_reference="3",
                    confidence=0.9,
                    confidence_score=0.9,
                    evidence=_valid_evidence(),
                    evidence_pointer=_valid_evidence(),
                    manual_review_required=False,
                )
            ],
        )
        assert extracted.source_file == "contract.pdf"
        assert len(extracted.clauses) == 1
        assert extracted.warnings == []
        assert extracted.text_extraction_method == "digital"

    def test_valid_ocr_metadata(self):
        metadata = OcrMetadata(
            used=True,
            engine="pymupdf_tesseract",
            pages_processed=1,
            average_confidence=0.82,
            low_confidence=False,
            manual_review_required=False,
            pages=[
                OcrPageResult(
                    page_number=1,
                    used=True,
                    confidence=0.82,
                    text_length=240,
                )
            ],
        )
        assert metadata.used is True
        assert metadata.pages[0].confidence == 0.82


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
# PolicyRules
# ---------------------------------------------------------------------------

class TestPolicyRules:
    def test_policy_defaults(self):
        policy = PolicyRules()
        assert policy.approved_payment_terms.terms == ["net-30"]
        assert policy.manual_review_confidence_threshold == 0.75
        assert policy.liability_cap_requirements.required is True
        assert policy.auto_renewal_rules.allowed is False
        assert policy.gdpr_requirements.applies_when_personal_data is True
        assert policy.exception_routing.rules == []


# ---------------------------------------------------------------------------
# ExceptionTriageItem
# ---------------------------------------------------------------------------

class TestExceptionTriageItem:
    def test_valid_exception_triage_item(self):
        item = ExceptionTriageItem(
            finding_id="F-001",
            category=ExceptionCategory.LEGAL,
            approver="legal_counsel",
            reason="Missing liability cap requires legal review.",
            next_action="Legal must approve the liability exposure.",
            severity="HIGH",
            evidence=[_valid_evidence()],
            source_title="Missing liability cap",
        )
        assert item.category == ExceptionCategory.LEGAL
        assert item.approver == "legal_counsel"

    def test_non_auto_exception_requires_approver(self):
        with pytest.raises(ValidationError, match="approver"):
            ExceptionTriageItem(
                finding_id="F-001",
                category=ExceptionCategory.LEGAL,
                reason="Missing liability cap requires legal review.",
                next_action="Legal must approve the liability exposure.",
                severity="HIGH",
                evidence=[_valid_evidence()],
                source_title="Missing liability cap",
            )


# ---------------------------------------------------------------------------
# ValidationResult
# ---------------------------------------------------------------------------

class TestValidationResult:
    def test_valid_validation_result(self):
        vr = ValidationResult(
            run_id="20250101_120000_abc12345",
            normalized_fields={"effective_date": "2025-01-15"},
            validated_fields=[
                ValidatedContractField(
                    field_name="effective_date",
                    is_present=True,
                    normalized_value="2025-01-15",
                    source="context",
                    evidence=[EvidencePointer(source_file="contract.pdf")],
                )
            ],
            findings=[],
        )
        assert vr.normalized_fields["effective_date"] == "2025-01-15"
        assert vr.validated_fields[0].field_name == "effective_date"


# ---------------------------------------------------------------------------
# RiskResult
# ---------------------------------------------------------------------------

class TestRiskResult:
    def test_valid_risk_result(self):
        rr = RiskResult(**_valid_risk_result())
        assert rr.overall_severity == Severity.HIGH
        assert rr.requires_human_review is True
        assert len(rr.findings) == 1


class TestAnomalyResult:
    def test_valid_anomaly_result(self):
        result = AnomalyResult(
            run_id="20250101_120000_abc12345",
            findings=[Finding(**_valid_finding(category="contract_anomaly"))],
            checked_rules=["conflicting_governing_law"],
            requires_legal_review=True,
        )
        assert result.run_id == "20250101_120000_abc12345"
        assert result.findings[0].category == "contract_anomaly"


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


# ---------------------------------------------------------------------------
# PostingPayload
# ---------------------------------------------------------------------------

class TestPostingPayload:
    def test_valid_clm_posting_payload(self):
        payload = PostingPayload(
            run_id="20250101_120000_abc12345",
            contract=ContractPostingData(
                contract_id="clean_nda:DOC-002:contract.pdf",
                document_id="DOC-002",
                bundle_name="clean_nda",
                contract_type="Non-Disclosure Agreement",
                source_file="contract.pdf",
                jurisdiction="Delaware, USA",
                effective_date="2025-01-15",
            ),
            counterparty=CounterpartyPostingData(
                name="Acme Corporation",
                resolution_status="exact",
                vendor_id="V-001",
                matched_vendor_name="Acme Corporation",
                match_confidence=1.0,
            ),
            decision=DecisionPostingData(
                status="ESCALATE",
                approved=False,
                rationale="High-severity finding requires approval.",
                requires_human_review=True,
            ),
            risk=RiskPostingData(
                overall_severity="HIGH",
                summary="One high-severity finding.",
                final_finding_count=1,
                critical_finding_count=0,
                high_finding_count=1,
                categories=["clause_risk"],
            ),
            approval=ApprovalPostingData(
                approval_required=True,
                routes=[],
                next_approvers=["legal_counsel"],
            ),
            artifacts=[
                ArtifactReference(
                    name="approval_packet",
                    path="/tmp/run/approval_packet.json",
                    artifact_type="json",
                )
            ],
            artifact_references={
                "approval_packet": "/tmp/run/approval_packet.json",
            },
        )

        assert payload.payload_version == "1.0"
        assert payload.payload_type == "CLM_POSTING_PAYLOAD"
        assert payload.contract.contract_id == "clean_nda:DOC-002:contract.pdf"
        assert payload.decision.status.value == "ESCALATE"

    def test_posting_payload_rejects_missing_contract_id(self):
        with pytest.raises(ValidationError, match="contract_id"):
            ContractPostingData(
                document_id="DOC-002",
                bundle_name="clean_nda",
                contract_type="Non-Disclosure Agreement",
                source_file="contract.pdf",
                jurisdiction="Delaware, USA",
            )
