"""Anomaly review result schema."""

from datetime import datetime, timezone
from typing import List

from pydantic import BaseModel, Field

from schemas.finding import Finding


class AnomalyResult(BaseModel):
    """Structured output produced by the Anomaly Agent."""

    run_id: str = Field(..., description="Unique identifier for this pipeline run.")
    findings: List[Finding] = Field(
        default_factory=list,
        description="Anomaly findings raised by conflict and unusual-pattern checks.",
    )
    checked_rules: List[str] = Field(
        default_factory=list,
        description="Deterministic list of anomaly rules evaluated.",
    )
    requires_legal_review: bool = Field(
        ...,
        description="Whether any anomaly finding requires legal review.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when anomaly review completed.",
    )
