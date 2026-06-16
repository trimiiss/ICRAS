"""Risk result schemas produced by Agent E."""

from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field

from schemas.common import EvidencePointer, Severity
from schemas.finding import Finding


class ClauseRisk(BaseModel):
    """Clause-level risk scored by Agent E."""

    risk_id: str = Field(..., description="Unique identifier for this clause risk.")
    clause_id: Optional[str] = Field(
        default=None,
        description="Extracted clause ID related to this risk, when available.",
    )
    field_name: str = Field(..., description="Canonical field or clause name.")
    issue_type: str = Field(..., description="Machine-readable risk issue type.")
    severity: Severity = Field(..., description="Risk severity.")
    risk_explanation: str = Field(..., description="Why this clause is risky.")
    recommended_action: str = Field(
        ..., description="Recommended remediation or next action."
    )
    clause_text: str = Field(
        ..., description="Specific clause text or source excerpt for this risk."
    )
    source_page: Optional[int] = Field(
        default=None,
        description="1-indexed source page for this risk, when available.",
    )
    evidence_pointer: EvidencePointer = Field(
        ..., description="Primary source pointer for this risk."
    )
    legal_review_required: bool = Field(
        ..., description="Whether legal review is required for this risk."
    )
    tolerance_threshold: Optional[str] = Field(
        default=None,
        description="Tolerance threshold used to classify this risk.",
    )
    risk_engine_ready: bool = Field(
        default=True,
        description="Whether this risk is ready for downstream routing.",
    )


class ClauseAnalysisResult(BaseModel):
    """Agent E clause-analysis artifact."""

    run_id: str = Field(..., description="Unique identifier for this pipeline run.")
    overall_severity: Severity = Field(
        ..., description="Highest severity across clause risks."
    )
    clause_risks: List[ClauseRisk] = Field(
        default_factory=list,
        description="Clause-level risk findings.",
    )
    findings: List[Finding] = Field(
        default_factory=list,
        description="Risk findings in the shared finding format.",
    )
    requires_legal_review: bool = Field(
        ..., description="Whether any clause risk requires legal review."
    )
    summary: str = Field(..., description="Human-readable risk scoring summary.")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when clause analysis completed.",
    )


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
