"""Tests for the CLM posting payload builder (US-29).

Covers:
    - Contract metadata mapping.
    - Counterparty details mapping (with and without a match).
    - Risk findings mapping.
    - Approval status mapping.
    - Obligation records mapping.
    - Full payload round-trip validation.
    - validate_posting_payload / validate_posting_payload_file helpers.
"""

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from schemas.approval_packet import ApprovalRoute, ApprovalStatus
from schemas.common import EvidencePointer, Severity
from schemas.finding import Finding
from schemas.posting_payload import PostingPayload
from utils.clm_validator import (
    CLMValidationResult,
    validate_posting_payload,
    validate_posting_payload_file,
)
from utils.posting_payload_builder import build_posting_payload


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _evidence_pointer() -> dict:
    return {
        "source_file": "contract.pdf",
        "page_number": 2,
        "clause_reference": "4.1",
        "excerpt": "The customer shall pay within 30 days.",
    }


def _finding(finding_id: str = "F-001", severity: str = "HIGH") -> Finding:
    return Finding(
        finding_id=finding_id,
        category="compliance",
        title="Missing liability cap",
        description="The contract does not include a liability cap.",
        severity=severity,
        confidence=0.9,
        evidence=[EvidencePointer(**_evidence_pointer())],
        recommendation="Add a liability cap clause.",
        field_name="liability_cap",
        issue_type="missing_field",
    )


def _approval_route(category: str = "LEGAL") -> ApprovalRoute:
    return ApprovalRoute(
        category=category,
        approvers=["legal_counsel"],
        reason="Missing liability cap requires legal review.",
        finding_ids=["F-001"],
    )


def _context(
    bundle_name: str = "clean_nda",
    contract_type: str = "Non-Disclosure Agreement",
    counterparty: str = "Acme Corporation",
    jurisdiction: str = "Delaware, USA",
    contract_file: str = "contract.pdf",
    effective_date: str | None = "2025-01-15",
) -> dict:
    return {
        "bundle_name": bundle_name,
        "contract_type": contract_type,
        "counterparty": counterparty,
        "jurisdiction": jurisdiction,
        "contract_file": contract_file,
        "effective_date": effective_date,
    }


def _document_inventory(primary_contract_id: str = "DOC-001") -> dict:
    return {
        "primary_contract_id": primary_contract_id,
        "documents": [
            {
                "document_id": primary_contract_id,
                "is_primary": True,
                "filename": "contract.pdf",
            }
        ],
    }


def _counterparty_resolution(
    *,
    matched: bool = True,
    similarity_score: float = 0.95,
    manual_review_required: bool = False,
) -> dict:
    if not matched:
        return {"matches": []}
    return {
        "matches": [
            {
                "original_party_name": "Acme Corporation",
                "normalized_party_name": "acme corporation",
                "match_status": "exact",
                "similarity_score": similarity_score,
                "manual_review_required": manual_review_required,
                "vendor_id": "V-001",
                "matched_vendor_name": "Acme Corporation",
                "evidence_pointer": _evidence_pointer(),
            }
        ]
    }


def _obligation_register(num_obligations: int = 1) -> dict:
    obligations = []
    for i in range(num_obligations):
        obligations.append(
            {
                "obligation_id": f"OBL-{i + 1:03d}",
                "obligation_type": "payment",
                "responsible_party": "Customer",
                "obligation_summary": f"Customer must pay invoice {i + 1}.",
                "due_date": None,
                "timing_trigger": "net 30",
                "is_recurring": True,
                "recurrence_frequency": "per invoice",
                "source_clause_text": "The customer shall pay within 30 days of invoice.",
                "source_file": "contract.pdf",
                "source_page": i + 1,
                "evidence_id": f"EV-{i + 1:03d}",
                "document_id": "DOC-001",
                "clause_reference": str(i + 5),
                "evidence_pointer": _evidence_pointer(),
            }
        )
    return {"obligations": obligations}


def _artifact_paths() -> dict:
    return {
        "approval_packet": "/tmp/run/approval_packet.json",
        "final_findings": "/tmp/run/final_findings.json",
        "posting_payload": "/tmp/run/posting_payload.json",
        "metrics": "/tmp/run/metrics.json",
    }


def _build(
    *,
    findings: list[Finding] | None = None,
    approval_routes: list[ApprovalRoute] | None = None,
    decision_status: ApprovalStatus = ApprovalStatus.ESCALATE,
    overall_severity: Severity = Severity.HIGH,
    matched: bool = True,
    num_obligations: int = 1,
    effective_date: str | None = "2025-01-15",
) -> PostingPayload:
    return build_posting_payload(
        run_id="20250101_120000_abc12345",
        context=_context(effective_date=effective_date),
        document_inventory=_document_inventory(),
        counterparty_resolution=_counterparty_resolution(matched=matched),
        decision_status=decision_status,
        decision_rationale="High-severity finding requires approval.",
        overall_severity=overall_severity,
        requires_human_review=decision_status != ApprovalStatus.AUTO_APPROVE,
        risk_summary="One high-severity finding.",
        final_findings=findings if findings is not None else [_finding()],
        approval_routes=approval_routes if approval_routes is not None else [_approval_route()],
        obligation_register=_obligation_register(num_obligations=num_obligations),
        artifact_paths=_artifact_paths(),
    )


# ---------------------------------------------------------------------------
# US-29: Contract metadata mapping
# ---------------------------------------------------------------------------

class TestContractMetadataMapping:
    def test_contract_id_combines_bundle_document_and_file(self) -> None:
        payload = _build()
        assert payload.contract.contract_id == "clean_nda:DOC-001:contract.pdf"

    def test_contract_bundle_name_is_mapped(self) -> None:
        payload = _build()
        assert payload.contract.bundle_name == "clean_nda"

    def test_contract_type_is_mapped(self) -> None:
        payload = _build()
        assert payload.contract.contract_type == "Non-Disclosure Agreement"

    def test_contract_source_file_is_mapped(self) -> None:
        payload = _build()
        assert payload.contract.source_file == "contract.pdf"

    def test_contract_jurisdiction_is_mapped(self) -> None:
        payload = _build()
        assert payload.contract.jurisdiction == "Delaware, USA"

    def test_contract_effective_date_is_mapped(self) -> None:
        payload = _build()
        assert payload.contract.effective_date == "2025-01-15"

    def test_contract_effective_date_none_when_absent(self) -> None:
        payload = _build(effective_date=None)
        assert payload.contract.effective_date is None

    def test_contract_document_id_from_inventory(self) -> None:
        payload = _build()
        assert payload.contract.document_id == "DOC-001"

    def test_payload_envelope_fields_are_set(self) -> None:
        payload = _build()
        assert payload.payload_version == "1.0"
        assert payload.payload_type == "CLM_POSTING_PAYLOAD"
        assert payload.source_system == "ICRAS"
        assert payload.run_id == "20250101_120000_abc12345"


# ---------------------------------------------------------------------------
# US-29: Counterparty details mapping
# ---------------------------------------------------------------------------

class TestCounterpartyMapping:
    def test_counterparty_name_is_mapped(self) -> None:
        payload = _build(matched=True)
        assert payload.counterparty.name == "Acme Corporation"

    def test_counterparty_vendor_id_is_mapped_when_matched(self) -> None:
        payload = _build(matched=True)
        assert payload.counterparty.vendor_id == "V-001"
        assert payload.counterparty.matched_vendor_name == "Acme Corporation"

    def test_counterparty_resolution_status_is_mapped(self) -> None:
        payload = _build(matched=True)
        assert payload.counterparty.resolution_status == "exact"

    def test_counterparty_confidence_is_mapped(self) -> None:
        payload = _build(matched=True)
        assert payload.counterparty.match_confidence == pytest.approx(0.95)

    def test_counterparty_manual_review_flag_is_mapped(self) -> None:
        payload = _build(matched=True)
        assert payload.counterparty.manual_review_required is False

    def test_counterparty_unmatched_falls_back_to_context_name(self) -> None:
        payload = _build(matched=False)
        assert payload.counterparty.name == "Acme Corporation"
        assert payload.counterparty.vendor_id is None
        assert payload.counterparty.matched_vendor_name is None
        assert payload.counterparty.match_confidence is None

    def test_counterparty_unmatched_has_default_resolution_status(self) -> None:
        payload = _build(matched=False)
        assert payload.counterparty.resolution_status == "unknown"


# ---------------------------------------------------------------------------
# US-29: Risk findings mapping
# ---------------------------------------------------------------------------

class TestRiskFindingsMapping:
    def test_risk_overall_severity_is_mapped(self) -> None:
        payload = _build(overall_severity=Severity.HIGH)
        assert payload.risk.overall_severity == Severity.HIGH

    def test_risk_summary_is_mapped(self) -> None:
        payload = _build()
        assert payload.risk.summary == "One high-severity finding."

    def test_risk_finding_count_is_correct(self) -> None:
        payload = _build(findings=[_finding("F-001"), _finding("F-002")])
        assert payload.risk.final_finding_count == 2

    def test_risk_critical_finding_count_is_correct(self) -> None:
        payload = _build(
            findings=[
                _finding("F-001", "CRITICAL"),
                _finding("F-002", "HIGH"),
            ]
        )
        assert payload.risk.critical_finding_count == 1
        assert payload.risk.high_finding_count == 1

    def test_risk_findings_are_mapped(self) -> None:
        payload = _build()
        assert len(payload.risk.findings) == 1
        finding = payload.risk.findings[0]
        assert finding.finding_id == "F-001"
        assert finding.category == "compliance"
        assert finding.title == "Missing liability cap"
        assert finding.severity == Severity.HIGH
        assert finding.confidence == pytest.approx(0.9)
        assert finding.field_name == "liability_cap"
        assert finding.issue_type == "missing_field"

    def test_risk_finding_evidence_is_mapped(self) -> None:
        payload = _build()
        evidence = payload.risk.findings[0].evidence
        assert len(evidence) == 1
        assert evidence[0].source_file == "contract.pdf"
        assert evidence[0].page_number == 2

    def test_risk_finding_categories_are_sorted(self) -> None:
        payload = _build(
            findings=[
                _finding("F-001"),
                Finding(
                    finding_id="F-002",
                    category="anomaly",
                    title="Anomaly detected",
                    description="Anomaly in clause.",
                    severity="LOW",
                    confidence=0.7,
                    evidence=[EvidencePointer(**_evidence_pointer())],
                ),
            ]
        )
        assert payload.risk.categories == sorted(payload.risk.categories)

    def test_risk_is_empty_when_no_findings(self) -> None:
        payload = _build(
            findings=[],
            overall_severity=Severity.LOW,
            decision_status=ApprovalStatus.AUTO_APPROVE,
            approval_routes=[],
        )
        assert payload.risk.final_finding_count == 0
        assert payload.risk.findings == []


# ---------------------------------------------------------------------------
# US-29: Approval status mapping
# ---------------------------------------------------------------------------

class TestApprovalStatusMapping:
    def test_approval_required_is_true_for_escalate(self) -> None:
        payload = _build(decision_status=ApprovalStatus.ESCALATE)
        assert payload.approval.approval_required is True

    def test_approval_required_is_false_for_auto_approve(self) -> None:
        payload = _build(
            decision_status=ApprovalStatus.AUTO_APPROVE,
            findings=[],
            overall_severity=Severity.LOW,
        )
        assert payload.approval.approval_required is False

    def test_approval_routes_are_mapped(self) -> None:
        route = _approval_route(category="FINANCE")
        payload = _build(approval_routes=[route])
        assert len(payload.approval.routes) == 1
        assert payload.approval.routes[0].category == "FINANCE"

    def test_next_approvers_are_flattened_unique(self) -> None:
        routes = [
            _approval_route("LEGAL"),
            ApprovalRoute(
                category="FINANCE",
                approvers=["finance_manager", "legal_counsel"],
                reason="Finance review required.",
                finding_ids=["F-002"],
            ),
        ]
        payload = _build(approval_routes=routes)
        # "legal_counsel" appears in both routes — must be deduplicated
        approvers = payload.approval.next_approvers
        assert approvers.count("legal_counsel") == 1
        assert "finance_manager" in approvers

    def test_decision_status_is_mapped(self) -> None:
        payload = _build(decision_status=ApprovalStatus.ESCALATE)
        assert payload.decision.status == ApprovalStatus.ESCALATE
        assert payload.decision.approved is False
        assert payload.decision.requires_human_review is True

    def test_decision_auto_approve_sets_approved_true(self) -> None:
        payload = _build(
            decision_status=ApprovalStatus.AUTO_APPROVE,
            findings=[],
            overall_severity=Severity.LOW,
        )
        assert payload.decision.approved is True
        assert payload.decision.requires_human_review is False


# ---------------------------------------------------------------------------
# US-29: Obligation records mapping
# ---------------------------------------------------------------------------

class TestObligationRecordsMapping:
    def test_obligations_count_is_correct(self) -> None:
        payload = _build(num_obligations=3)
        assert len(payload.obligations) == 3

    def test_obligation_id_is_mapped(self) -> None:
        payload = _build(num_obligations=1)
        assert payload.obligations[0].obligation_id == "OBL-001"

    def test_obligation_type_is_mapped(self) -> None:
        payload = _build(num_obligations=1)
        assert payload.obligations[0].obligation_type == "payment"

    def test_obligation_responsible_party_is_mapped(self) -> None:
        payload = _build(num_obligations=1)
        assert payload.obligations[0].responsible_party == "Customer"

    def test_obligation_summary_is_mapped(self) -> None:
        payload = _build(num_obligations=1)
        assert "Customer must pay invoice 1" in payload.obligations[0].obligation_summary

    def test_obligation_timing_trigger_is_mapped(self) -> None:
        payload = _build(num_obligations=1)
        assert payload.obligations[0].timing_trigger == "net 30"

    def test_obligation_is_recurring_is_mapped(self) -> None:
        payload = _build(num_obligations=1)
        assert payload.obligations[0].is_recurring is True

    def test_obligation_source_file_is_mapped(self) -> None:
        payload = _build(num_obligations=1)
        assert payload.obligations[0].source_file == "contract.pdf"

    def test_obligation_evidence_pointer_source_file_is_mapped(self) -> None:
        payload = _build(num_obligations=1)
        assert payload.obligations[0].evidence_pointer.source_file == "contract.pdf"

    def test_obligation_empty_when_register_is_empty(self) -> None:
        payload = _build(num_obligations=0)
        assert payload.obligations == []


# ---------------------------------------------------------------------------
# US-29: Artifact references mapping
# ---------------------------------------------------------------------------

class TestArtifactReferencesMapping:
    def test_artifact_references_are_mapped(self) -> None:
        payload = _build()
        assert "approval_packet" in payload.artifact_references
        assert "metrics" in payload.artifact_references

    def test_structured_artifact_list_is_built(self) -> None:
        payload = _build()
        artifact_names = {a.name for a in payload.artifacts}
        assert "approval_packet" in artifact_names
        assert "metrics" in artifact_names

    def test_artifact_type_json_is_detected(self) -> None:
        payload = _build()
        approval_artifact = next(a for a in payload.artifacts if a.name == "approval_packet")
        assert approval_artifact.artifact_type == "json"

    def test_source_contract_file_is_set(self) -> None:
        payload = _build()
        assert payload.source_contract_file == "contract.pdf"


# ---------------------------------------------------------------------------
# US-29: Full payload schema validation round-trip
# ---------------------------------------------------------------------------

class TestPostingPayloadSchemaValidation:
    def test_valid_payload_passes_schema_validation(self) -> None:
        payload = _build()
        round_tripped = PostingPayload.model_validate(payload.model_dump(mode="json"))
        assert round_tripped.run_id == payload.run_id
        assert round_tripped.payload_type == "CLM_POSTING_PAYLOAD"

    def test_payload_serialises_to_json(self) -> None:
        payload = _build()
        serialized = payload.model_dump_json()
        parsed = json.loads(serialized)
        assert parsed["payload_type"] == "CLM_POSTING_PAYLOAD"
        assert parsed["contract"]["contract_id"] == "clean_nda:DOC-001:contract.pdf"


# ---------------------------------------------------------------------------
# US-29: validate_posting_payload — required-field validation
# ---------------------------------------------------------------------------

class TestValidatePostingPayload:
    def test_valid_payload_passes(self) -> None:
        payload = _build()
        data = payload.model_dump(mode="json")
        result = validate_posting_payload(data)
        assert result.valid is True
        assert result.errors == []
        assert result.payload is not None

    def test_missing_contract_id_returns_error(self) -> None:
        payload = _build()
        data = payload.model_dump(mode="json")
        del data["contract"]["contract_id"]
        result = validate_posting_payload(data)
        assert result.valid is False
        fields = [e["field"] for e in result.errors]
        assert any("contract_id" in f for f in fields)

    def test_missing_obligations_returns_error(self) -> None:
        payload = _build()
        data = payload.model_dump(mode="json")
        del data["obligations"]
        result = validate_posting_payload(data)
        assert result.valid is False
        fields = [e["field"] for e in result.errors]
        assert any("obligations" in f for f in fields)

    def test_missing_run_id_returns_error(self) -> None:
        payload = _build()
        data = payload.model_dump(mode="json")
        del data["run_id"]
        result = validate_posting_payload(data)
        assert result.valid is False
        fields = [e["field"] for e in result.errors]
        assert any("run_id" in f for f in fields)

    def test_missing_decision_rationale_returns_error(self) -> None:
        payload = _build()
        data = payload.model_dump(mode="json")
        del data["decision"]["rationale"]
        result = validate_posting_payload(data)
        assert result.valid is False

    def test_invalid_risk_severity_returns_error(self) -> None:
        payload = _build()
        data = payload.model_dump(mode="json")
        data["risk"]["overall_severity"] = "EXTREME"
        result = validate_posting_payload(data)
        assert result.valid is False

    def test_error_format_has_required_keys(self) -> None:
        payload = _build()
        data = payload.model_dump(mode="json")
        del data["run_id"]
        result = validate_posting_payload(data)
        assert result.valid is False
        for error in result.errors:
            assert "field" in error
            assert "message" in error
            assert "type" in error


# ---------------------------------------------------------------------------
# US-29: validate_posting_payload_file — file-level validation
# ---------------------------------------------------------------------------

class TestValidatePostingPayloadFile:
    def test_valid_json_file_passes(self, tmp_path: Path) -> None:
        payload = _build()
        target = tmp_path / "posting_payload.json"
        target.write_text(payload.model_dump_json(), encoding="utf-8")
        result = validate_posting_payload_file(target)
        assert result.valid is True

    def test_missing_file_raises_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="not found"):
            validate_posting_payload_file(tmp_path / "nonexistent.json")

    def test_invalid_json_raises_value_error(self, tmp_path: Path) -> None:
        target = tmp_path / "bad.json"
        target.write_text("{not valid json}", encoding="utf-8")
        with pytest.raises(ValueError, match="Failed to parse"):
            validate_posting_payload_file(target)

    def test_non_object_json_raises_value_error(self, tmp_path: Path) -> None:
        target = tmp_path / "array.json"
        target.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(ValueError, match="JSON object"):
            validate_posting_payload_file(target)

    def test_file_with_missing_required_field_returns_errors(self, tmp_path: Path) -> None:
        payload = _build()
        data = payload.model_dump(mode="json")
        del data["contract"]["contract_id"]
        target = tmp_path / "posting_payload.json"
        target.write_text(json.dumps(data), encoding="utf-8")
        result = validate_posting_payload_file(target)
        assert result.valid is False
        assert any("contract_id" in e["field"] for e in result.errors)
