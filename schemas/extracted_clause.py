"""ExtractedClause schema — a single clause extracted from a contract."""

from typing import Optional

from pydantic import BaseModel, Field

from schemas.common import ConfidenceScore


class ExtractedClause(BaseModel):
    """Represents a single clause or provision extracted from a contract document."""

    clause_id: str = Field(
        ..., description="Unique identifier for this extracted clause."
    )
    clause_type: str = Field(
        ...,
        description="Category of clause (e.g. 'non-compete', 'indemnification').",
    )
    title: str = Field(..., description="Short human-readable title for the clause.")
    text: str = Field(..., description="Full text content of the clause.")
    page_number: Optional[int] = Field(
        default=None, description="Page where the clause appears (1-indexed)."
    )
    section_reference: Optional[str] = Field(
        default=None, description="Section or article reference (e.g. '3.1')."
    )
    confidence: ConfidenceScore = Field(
        ..., description="Confidence score for extraction accuracy (0.0–1.0)."
    )
