"""Compliance review result schema."""

from datetime import datetime, timezone
from typing import List

from pydantic import BaseModel, Field

from schemas.finding import Finding


class ComplianceResult(BaseModel):
    """Structured output produced by the Compliance Agent."""

    run_id: str = Field(..., description="Unique identifier for this pipeline run.")
    findings: List[Finding] = Field(
        default_factory=list,
        description="Compliance findings raised by GDPR and jurisdiction checks.",
    )
    checked_rules: List[str] = Field(
        default_factory=list,
        description="Deterministic list of compliance rules evaluated.",
    )
    requires_compliance_review: bool = Field(
        ...,
        description="Whether any compliance finding requires human review.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when compliance review completed.",
    )
