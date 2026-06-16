"""Finding schema — a single issue or observation raised during contract review."""

from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from schemas.common import ConfidenceScore, EvidencePointer, Severity


class Finding(BaseModel):
    """A single issue, risk, or observation identified during contract review.

    Every finding must be backed by at least one piece of evidence.
    """

    finding_id: str = Field(..., description="Unique identifier for this finding.")
    category: str = Field(
        ...,
        description="Category of the finding (e.g. 'compliance', 'financial').",
    )
    title: str = Field(..., description="Short title summarising the finding.")
    description: str = Field(
        ..., description="Detailed description of the finding."
    )
    severity: Severity = Field(..., description="Severity level of this finding.")
    confidence: ConfidenceScore = Field(
        ...,
        description="Confidence that this finding is accurate (0.0–1.0).",
    )
    evidence: List[EvidencePointer] = Field(
        ...,
        description="Supporting evidence pointers (at least one required).",
    )
    recommendation: Optional[str] = Field(
        default=None,
        description="Suggested action to address this finding.",
    )
    field_name: Optional[str] = Field(
        default=None,
        description="Contract field related to this finding, when applicable.",
    )
    issue_type: Optional[str] = Field(
        default=None,
        description="Machine-readable validation issue type.",
    )
    message: Optional[str] = Field(
        default=None,
        description="Agent-facing finding message for downstream consumers.",
    )
    source_clause_text: Optional[str] = Field(
        default=None,
        description="Source clause text or excerpt supporting this finding.",
    )
    source_page: Optional[int] = Field(
        default=None,
        description="Source page for the finding evidence, when available.",
    )
    evidence_pointer: Optional[EvidencePointer] = Field(
        default=None,
        description="Primary evidence pointer for Agent E risk scoring.",
    )
    manual_review_required: bool = Field(
        default=False,
        description="Whether this finding must be reviewed by a human.",
    )
    risk_engine_ready: bool = Field(
        default=False,
        description="Whether this finding is ready for risk scoring.",
    )

    @model_validator(mode="after")
    def _require_at_least_one_evidence(self) -> "Finding":
        """Ensure every finding has at least one evidence pointer."""
        if not self.evidence:
            raise ValueError(
                "A Finding must include at least one evidence pointer. "
                "Provide a non-empty 'evidence' list."
            )
        return self
