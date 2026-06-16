"""ApprovalPacket schema - the final output of the contract review pipeline."""

from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from schemas.exception_triage import ExceptionTriageItem
from schemas.finding import Finding
from schemas.risk_result import RiskResult


class ApprovalStatus(str, Enum):
    """Final machine-readable approval status."""

    AUTO_APPROVE = "AUTO_APPROVE"
    ESCALATE = "ESCALATE"
    REJECT = "REJECT"


class ApprovalRoute(BaseModel):
    """One approval route selected by Agent H."""

    category: str = Field(
        ..., description="Routing category, such as LEGAL, FINANCE, or COMPLIANCE."
    )
    approvers: List[str] = Field(
        default_factory=list,
        description="Approver roles required for this route.",
    )
    reason: str = Field(..., description="Why this route is required.")
    finding_ids: List[str] = Field(
        default_factory=list,
        description="Findings that triggered this route.",
    )


class ApprovalDecision(BaseModel):
    """The approval decision and its rationale."""

    approved: bool = Field(
        ..., description="Whether the contract is approved."
    )
    status: ApprovalStatus = Field(
        default=ApprovalStatus.ESCALATE,
        description="Machine-readable final approval status.",
    )
    rationale: str = Field(
        ..., description="Reason for the approval or rejection."
    )


class ApprovalPacket(BaseModel):
    """Final deliverable of the ICRAS pipeline — bundles the decision with risk data."""

    run_id: str = Field(..., description="Run identifier that produced this packet.")
    decision: ApprovalDecision = Field(
        ..., description="The approval decision and rationale."
    )
    risk_result: RiskResult = Field(
        ..., description="Full risk assessment backing the decision."
    )
    approval_route: List[ApprovalRoute] = Field(
        default_factory=list,
        description="Approver routing selected by Agent H.",
    )
    exceptions: List[ExceptionTriageItem] = Field(
        default_factory=list,
        description="Per-exception triage decisions with approvers and evidence.",
    )
    final_findings: List[Finding] = Field(
        default_factory=list,
        description="Merged and severity-sorted findings used for the decision.",
    )
    artifact_paths: Dict[str, str] = Field(
        default_factory=dict,
        description="Run artifact paths backing this approval packet.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when Agent H created the approval packet.",
    )
    reviewed_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp when a human reviewed the packet (if applicable).",
    )
    reviewer: Optional[str] = Field(
        default=None, description="Name or ID of the human reviewer."
    )
    notes: Optional[str] = Field(
        default=None, description="Additional reviewer notes."
    )
