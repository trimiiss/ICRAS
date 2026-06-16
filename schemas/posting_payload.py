"""CLM-ready posting payload schema produced by Agent H."""

from datetime import datetime, timezone
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from schemas.approval_packet import ApprovalRoute, ApprovalStatus
from schemas.common import Severity


class PostingPayload(BaseModel):
    """Structured payload suitable for a future CLM integration."""

    run_id: str = Field(..., description="Unique identifier for this pipeline run.")
    contract_id: str = Field(..., description="Stable contract or bundle identifier.")
    bundle_name: str = Field(..., description="Source contract bundle name.")
    contract_type: str = Field(..., description="Contract type from intake context.")
    counterparty: str = Field(..., description="Primary counterparty name.")
    jurisdiction: str = Field(..., description="Governing jurisdiction.")
    final_decision: ApprovalStatus = Field(
        ..., description="Final approval decision status."
    )
    approved: bool = Field(..., description="Whether the contract is approved.")
    overall_severity: Severity = Field(
        ..., description="Highest final finding severity."
    )
    requires_human_review: bool = Field(
        ..., description="Whether the contract needs human review."
    )
    risk_summary: str = Field(..., description="Human-readable risk summary.")
    approval_route: List[ApprovalRoute] = Field(
        default_factory=list,
        description="Approver routing for escalated contracts.",
    )
    final_finding_count: int = Field(
        ..., ge=0, description="Number of final merged findings."
    )
    obligation_count: int = Field(
        ..., ge=0, description="Number of exported obligations."
    )
    artifact_references: Dict[str, str] = Field(
        default_factory=dict,
        description="Generated artifact paths for downstream systems.",
    )
    source_contract_file: Optional[str] = Field(
        default=None, description="Primary contract file path from the bundle."
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when the payload was generated.",
    )
