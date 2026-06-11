"""ApprovalPacket schema — the final output of the contract review pipeline."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from schemas.risk_result import RiskResult


class ApprovalDecision(BaseModel):
    """The approval decision and its rationale."""

    approved: bool = Field(
        ..., description="Whether the contract is approved."
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
