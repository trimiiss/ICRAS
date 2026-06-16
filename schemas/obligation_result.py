"""Obligation register schemas produced by Agent H."""

from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field

from schemas.common import EvidencePointer


class ObligationRecord(BaseModel):
    """One obligation extracted from a contract clause."""

    obligation_id: str = Field(..., description="Stable obligation identifier.")
    obligation_type: str = Field(..., description="Canonical obligation category.")
    responsible_party: str = Field(
        ..., description="Party responsible for satisfying the obligation."
    )
    obligation_summary: str = Field(..., description="Short obligation summary.")
    due_date: Optional[str] = Field(
        default=None,
        description="ISO 8601 due date when an absolute date is available.",
    )
    timing_trigger: Optional[str] = Field(
        default=None,
        description="Relative timing trigger, such as net 30 or 30 days notice.",
    )
    is_recurring: bool = Field(
        ..., description="Whether the obligation repeats over time."
    )
    recurrence_frequency: Optional[str] = Field(
        default=None,
        description="Recurring cadence, such as monthly, annually, or per invoice.",
    )
    source_clause_text: str = Field(
        ..., description="Source clause text supporting this obligation."
    )
    source_file: str = Field(..., description="Source contract filename.")
    source_page: Optional[int] = Field(
        default=None,
        description="1-indexed source page number when available.",
    )
    evidence_id: Optional[str] = Field(
        default=None,
        description="Evidence index identifier when available.",
    )
    document_id: Optional[str] = Field(
        default=None,
        description="Document inventory identifier when available.",
    )
    clause_reference: Optional[str] = Field(
        default=None,
        description="Clause or section reference when available.",
    )
    evidence_pointer: EvidencePointer = Field(
        ..., description="Primary source pointer for traceability."
    )


class ObligationRegisterResult(BaseModel):
    """Agent H obligation-register artifact."""

    run_id: str = Field(..., description="Unique identifier for this pipeline run.")
    obligations: List[ObligationRecord] = Field(
        default_factory=list,
        description="Extracted obligation records.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when the obligation register was created.",
    )
