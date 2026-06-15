"""Schemas for extracted contract clauses and extraction outputs."""

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from schemas.common import ConfidenceScore, EvidencePointer


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
    evidence: EvidencePointer = Field(
        ..., description="Evidence pointer linking the clause to source text."
    )
    char_start: Optional[int] = Field(
        default=None,
        ge=0,
        description="Start character offset within the source page text, if known.",
    )
    char_end: Optional[int] = Field(
        default=None,
        ge=0,
        description="End character offset within the source page text, if known.",
    )
    bbox: Optional[list[float]] = Field(
        default=None,
        description="Bounding box coordinates for future OCR/layout extraction.",
    )


class ExtractionWarning(BaseModel):
    """A non-blocking warning raised during clause extraction."""

    warning_id: str = Field(..., description="Stable warning ID for this run.")
    clause_type: Optional[str] = Field(
        default=None,
        description="Clause category related to the warning, if applicable.",
    )
    message: str = Field(..., description="Human-readable warning message.")


class ExtractedContract(BaseModel):
    """Structured clause extraction output for one contract run."""

    run_id: str = Field(..., description="Unique identifier for this pipeline run.")
    document_id: str = Field(..., description="Primary contract document ID.")
    source_file: str = Field(..., description="Primary contract source file.")
    clauses: list[ExtractedClause] = Field(
        default_factory=list,
        description="Extracted clauses and key terms from the source contract.",
    )
    warnings: list[ExtractionWarning] = Field(
        default_factory=list,
        description="Non-blocking extraction warnings and low-confidence notes.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when clause extraction was completed.",
    )
