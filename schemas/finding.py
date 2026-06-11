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

    @model_validator(mode="after")
    def _require_at_least_one_evidence(self) -> "Finding":
        """Ensure every finding has at least one evidence pointer."""
        if not self.evidence:
            raise ValueError(
                "A Finding must include at least one evidence pointer. "
                "Provide a non-empty 'evidence' list."
            )
        return self
