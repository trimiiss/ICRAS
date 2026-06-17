"""Exception triage schemas produced by Agent H."""

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from schemas.common import EvidencePointer, Severity


class ExceptionCategory(str, Enum):
    """Supported approval-routing categories for triaged exceptions."""

    LEGAL = "LEGAL"
    FINANCE = "FINANCE"
    COMPLIANCE = "COMPLIANCE"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    AUTO_APPROVE = "AUTO_APPROVE"


class ExceptionTriageItem(BaseModel):
    """One finding routed to an approver with evidence and next action."""

    finding_id: str = Field(..., description="Finding that triggered this exception.")
    category: ExceptionCategory = Field(
        ..., description="Approval-routing category selected by Agent H."
    )
    approver: Optional[str] = Field(
        default=None,
        description="Approver role responsible for this exception.",
    )
    reason: str = Field(..., description="Why this exception requires the route.")
    next_action: str = Field(
        ..., description="Human-readable action the approver should take."
    )
    severity: Severity = Field(..., description="Severity inherited from the finding.")
    evidence: List[EvidencePointer] = Field(
        ...,
        min_length=1,
        description="Evidence supporting this routed exception.",
    )
    source_title: str = Field(..., description="Finding title shown to reviewers.")
    issue_type: Optional[str] = Field(
        default=None,
        description="Machine-readable finding issue type, when available.",
    )
    field_name: Optional[str] = Field(
        default=None,
        description="Contract field that triggered this route, when available.",
    )

    @model_validator(mode="after")
    def _approver_required_for_exceptions(self) -> "ExceptionTriageItem":
        """Require an approver for every non-auto-approval exception."""
        if self.category != ExceptionCategory.AUTO_APPROVE and not self.approver:
            raise ValueError(
                "Exception approver is required unless category is AUTO_APPROVE."
            )
        return self
