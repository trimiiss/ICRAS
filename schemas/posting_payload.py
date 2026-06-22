"""CLM-ready posting payload schema produced by workflow orchestration."""

from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from schemas.approval_packet import ApprovalRoute, ApprovalStatus
from schemas.common import ConfidenceScore, EvidencePointer, Severity


class ContractPostingData(BaseModel):
    """Contract metadata a CLM system can use to create or update a record."""

    contract_id: str = Field(..., description="Stable contract identifier.")
    document_id: Optional[str] = Field(
        default=None,
        description="Primary contract document ID from the intake inventory.",
    )
    bundle_name: str = Field(..., description="Source contract bundle name.")
    contract_type: str = Field(..., description="Contract type from intake context.")
    source_file: str = Field(..., description="Primary contract file name.")
    jurisdiction: str = Field(..., description="Governing jurisdiction.")
    effective_date: Optional[str] = Field(
        default=None,
        description="Contract effective date from intake context, when known.",
    )


class CounterpartyPostingData(BaseModel):
    """Counterparty metadata and resolution summary for CLM posting."""

    name: str = Field(..., description="Primary counterparty name.")
    resolution_status: str = Field(
        default="unknown",
        description="Best available counterparty match status.",
    )
    vendor_id: Optional[str] = Field(
        default=None,
        description="Vendor master ID when a counterparty match exists.",
    )
    matched_vendor_name: Optional[str] = Field(
        default=None,
        description="Vendor master name when a counterparty match exists.",
    )
    match_confidence: Optional[ConfidenceScore] = Field(
        default=None,
        description="Best available match confidence score.",
    )
    manual_review_required: bool = Field(
        default=False,
        description="Whether counterparty resolution requires human review.",
    )


class DecisionPostingData(BaseModel):
    """Final decision data for CLM workflow status/routing."""

    status: ApprovalStatus = Field(..., description="Final approval status.")
    approved: bool = Field(..., description="Whether the contract is approved.")
    rationale: str = Field(..., description="Human-readable decision rationale.")
    requires_human_review: bool = Field(
        ..., description="Whether the contract requires human review."
    )


class RiskFindingPostingData(BaseModel):
    """One evidence-backed finding exposed to a downstream CLM record."""

    finding_id: str = Field(..., description="Stable finding identifier.")
    category: str = Field(..., description="Finding category.")
    title: str = Field(..., description="Short finding title.")
    description: str = Field(..., description="Finding detail for reviewers.")
    severity: Severity = Field(..., description="Finding severity.")
    confidence: ConfidenceScore = Field(
        ..., description="Confidence that this finding is accurate."
    )
    evidence: List[EvidencePointer] = Field(
        ..., min_length=1, description="Evidence supporting this finding."
    )
    recommendation: Optional[str] = Field(
        default=None, description="Recommended remediation or approval action."
    )
    field_name: Optional[str] = Field(
        default=None, description="Related contract field, when available."
    )
    issue_type: Optional[str] = Field(
        default=None, description="Machine-readable finding type."
    )


class RiskPostingData(BaseModel):
    """Risk summary data for downstream dashboards and workflow routing."""

    overall_severity: Severity = Field(..., description="Highest finding severity.")
    summary: str = Field(..., description="Human-readable risk summary.")
    final_finding_count: int = Field(
        ..., ge=0, description="Number of final merged findings."
    )
    critical_finding_count: int = Field(
        ..., ge=0, description="Number of CRITICAL final findings."
    )
    high_finding_count: int = Field(
        ..., ge=0, description="Number of HIGH final findings."
    )
    categories: List[str] = Field(
        default_factory=list,
        description="Sorted final finding categories.",
    )
    findings: List[RiskFindingPostingData] = Field(
        ...,
        description="Evidence-backed risk findings mapped for CLM display.",
    )


class ApprovalPostingData(BaseModel):
    """Approval routing data ready for a mock CLM workflow."""

    approval_required: bool = Field(
        ..., description="Whether approval workflow action is required."
    )
    routes: List[ApprovalRoute] = Field(
        default_factory=list,
        description="Grouped approval routes.",
    )
    next_approvers: List[str] = Field(
        default_factory=list,
        description="Flattened unique approver roles for the next CLM task.",
    )


class ArtifactReference(BaseModel):
    """One generated run artifact referenced by the CLM payload."""

    name: str = Field(..., description="Stable artifact key.")
    path: str = Field(..., description="Filesystem path to the generated artifact.")
    artifact_type: str = Field(
        ..., description="Artifact file type, such as json, markdown, or csv."
    )
    required: bool = Field(
        default=True,
        description="Whether the artifact is required for CLM review.",
    )


class ObligationPostingData(BaseModel):
    """One contract obligation mapped for a mock CLM obligation register."""

    obligation_id: str = Field(..., description="Stable obligation identifier.")
    obligation_type: str = Field(..., description="Canonical obligation category.")
    responsible_party: str = Field(
        ..., description="Party responsible for satisfying the obligation."
    )
    obligation_summary: str = Field(..., description="Short obligation summary.")
    due_date: Optional[str] = Field(
        default=None, description="Absolute due date, when known."
    )
    timing_trigger: Optional[str] = Field(
        default=None, description="Relative timing trigger, when known."
    )
    is_recurring: bool = Field(
        ..., description="Whether the obligation repeats over time."
    )
    recurrence_frequency: Optional[str] = Field(
        default=None, description="Recurring cadence, when known."
    )
    source_file: str = Field(..., description="Source contract filename.")
    source_page: Optional[int] = Field(
        default=None, description="1-indexed source page number, when available."
    )
    evidence_id: Optional[str] = Field(
        default=None, description="Evidence index identifier, when available."
    )
    document_id: Optional[str] = Field(
        default=None, description="Document inventory identifier, when available."
    )
    clause_reference: Optional[str] = Field(
        default=None, description="Clause or section reference, when available."
    )
    evidence_pointer: EvidencePointer = Field(
        ..., description="Primary source pointer for traceability."
    )


class PostingPayload(BaseModel):
    """Vendor-neutral payload suitable for a future CLM integration adapter."""

    payload_version: Literal["1.0"] = Field(
        default="1.0",
        description="Version of the ICRAS CLM posting payload contract.",
    )
    payload_type: Literal["CLM_POSTING_PAYLOAD"] = Field(
        default="CLM_POSTING_PAYLOAD",
        description="Machine-readable payload type for downstream consumers.",
    )
    source_system: Literal["ICRAS"] = Field(
        default="ICRAS",
        description="System that produced this payload.",
    )
    run_id: str = Field(..., description="Unique identifier for this pipeline run.")
    contract: ContractPostingData = Field(
        ..., description="Contract metadata for CLM record creation."
    )
    counterparty: CounterpartyPostingData = Field(
        ..., description="Counterparty data for the CLM record."
    )
    decision: DecisionPostingData = Field(
        ..., description="Final decision and approval status."
    )
    risk: RiskPostingData = Field(
        ..., description="Risk summary for CLM routing and display."
    )
    approval: ApprovalPostingData = Field(
        ..., description="Approval workflow routing data."
    )
    obligations: List[ObligationPostingData] = Field(
        ...,
        description="Mapped obligation records for downstream CLM tracking.",
    )
    artifacts: List[ArtifactReference] = Field(
        default_factory=list,
        description="Structured artifact references for CLM consumers.",
    )
    artifact_references: Dict[str, str] = Field(
        default_factory=dict,
        description="Compatibility map of artifact names to generated paths.",
    )
    source_contract_file: Optional[str] = Field(
        default=None,
        description="Primary contract file path from the bundle.",
    )
    external_posting_allowed: bool = Field(
        default=True,
        description="Whether downstream external posting is allowed.",
    )
    duplicate_of_run_id: Optional[str] = Field(
        default=None,
        description="Baseline run ID when this payload belongs to a duplicate rerun.",
    )
    posting_suppression_reason: Optional[str] = Field(
        default=None,
        description="Reason external posting is suppressed, if applicable.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when the payload was generated.",
    )
