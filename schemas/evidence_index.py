"""Evidence index schema for page-level source references."""

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


class EvidenceRecord(BaseModel):
    """A source-backed evidence snippet extracted from a contract document."""

    evidence_id: str = Field(..., description="Stable evidence ID for this run.")
    document_id: str = Field(..., description="Document inventory ID for the source.")
    source_file: str = Field(..., description="Source file name or relative path.")
    page_number: int = Field(..., ge=1, description="1-indexed source page number.")
    clause_id: Optional[str] = Field(
        default=None,
        description="Related clause ID, populated by clause extraction when available.",
    )
    section_reference: Optional[str] = Field(
        default=None,
        description="Related section heading or reference, when available.",
    )
    related_finding_ids: list[str] = Field(
        default_factory=list,
        description="Finding IDs supported by this evidence, populated later.",
    )
    char_start: Optional[int] = Field(
        default=None,
        ge=0,
        description="Start character offset within the extracted page text.",
    )
    char_end: Optional[int] = Field(
        default=None,
        ge=0,
        description="End character offset within the extracted page text.",
    )
    excerpt: str = Field(..., description="Short source snippet for review.")


class EvidenceWarning(BaseModel):
    """A non-blocking warning raised while building the evidence index."""

    warning_id: str = Field(..., description="Stable warning ID for this run.")
    document_id: Optional[str] = Field(
        default=None,
        description="Document inventory ID related to the warning, if any.",
    )
    source_file: Optional[str] = Field(
        default=None,
        description="Source file related to the warning, if any.",
    )
    page_number: Optional[int] = Field(
        default=None,
        ge=1,
        description="Source page number related to the warning, if any.",
    )
    message: str = Field(..., description="Human-readable warning message.")


class EvidenceIndex(BaseModel):
    """Page-level evidence records created for one pipeline run."""

    run_id: str = Field(..., description="Unique identifier for this pipeline run.")
    document_id: str = Field(..., description="Primary contract document ID.")
    source_file: str = Field(..., description="Primary contract source file.")
    records: list[EvidenceRecord] = Field(
        default_factory=list,
        description="Evidence records extracted from the source document.",
    )
    warnings: list[EvidenceWarning] = Field(
        default_factory=list,
        description="Non-blocking warnings from evidence indexing.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when this evidence index was created.",
    )
