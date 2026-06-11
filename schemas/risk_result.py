"""RiskResult schema — overall risk assessment produced by the risk agent."""

from typing import List, Optional

from pydantic import BaseModel, Field

from schemas.common import Severity
from schemas.finding import Finding


class RiskResult(BaseModel):
    """Aggregated risk assessment for a contract review run."""

    overall_severity: Severity = Field(
        ..., description="Highest severity across all findings."
    )
    findings: List[Finding] = Field(
        default_factory=list,
        description="List of all findings from the review.",
    )
    requires_human_review: bool = Field(
        ...,
        description="Whether a human reviewer must sign off on this contract.",
    )
    summary: str = Field(
        ..., description="Human-readable summary of the risk assessment."
    )
    total_findings: Optional[int] = Field(
        default=None,
        description="Total number of findings (auto-computed if omitted).",
    )
