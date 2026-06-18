"""Schemas for extracted contract clauses and extraction outputs."""

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

from schemas.common import ConfidenceScore, EvidencePointer


class ClauseEvidenceSpan(BaseModel):
    """Page-local source span for an extracted clause."""

    page_number: int = Field(..., ge=1, description="1-indexed source page number.")
    evidence_id: Optional[str] = Field(
        default=None,
        description="Evidence record ID from evidence_index.json, if available.",
    )
    document_id: Optional[str] = Field(
        default=None,
        description="Document inventory ID from document_inventory.json, if available.",
    )
    source_file: str = Field(..., description="Name or path of the source file.")
    char_start: Optional[int] = Field(
        default=None,
        ge=0,
        description="Start character offset within the extracted page text, if known.",
    )
    char_end: Optional[int] = Field(
        default=None,
        ge=0,
        description="End character offset within the extracted page text, if known.",
    )
    bbox: Optional[list[float]] = Field(
        default=None,
        description="Bounding box coordinates for this page-local span.",
    )
    excerpt: Optional[str] = Field(
        default=None,
        description="Short excerpt from this page-local span.",
    )


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
    clause_text: str = Field(
        ..., description="Ticket-facing alias for the full clause text."
    )
    page_number: Optional[int] = Field(
        default=None, description="Page where the clause appears (1-indexed)."
    )
    page_numbers: list[int] = Field(
        default_factory=list,
        description="All source pages that contribute text to this clause.",
    )
    section_reference: Optional[str] = Field(
        default=None, description="Section or article reference (e.g. '3.1')."
    )
    confidence: ConfidenceScore = Field(
        ..., description="Confidence score for extraction accuracy (0.0–1.0)."
    )
    confidence_score: ConfidenceScore = Field(
        ..., description="Ticket-facing alias for extraction confidence."
    )
    evidence: EvidencePointer = Field(
        ..., description="Evidence pointer linking the clause to source text."
    )
    evidence_pointer: EvidencePointer = Field(
        ..., description="Ticket-facing alias for the primary evidence pointer."
    )
    evidence_spans: list[ClauseEvidenceSpan] = Field(
        default_factory=list,
        description="Page-local evidence spans for single-page or multi-page clauses.",
    )
    manual_review_required: bool = Field(
        default=False,
        description="Whether extraction confidence is low enough for manual review.",
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
    bounding_box_coordinates: Optional[list[float]] = Field(
        default=None,
        description="Ticket-facing alias for the primary bounding box coordinates.",
    )


class ExtractionWarning(BaseModel):
    """A non-blocking warning raised during clause extraction."""

    warning_id: str = Field(..., description="Stable warning ID for this run.")
    clause_type: Optional[str] = Field(
        default=None,
        description="Clause category related to the warning, if applicable.",
    )
    message: str = Field(..., description="Human-readable warning message.")


class OcrPageResult(BaseModel):
    """OCR status and quality metadata for one source page."""

    page_number: int = Field(..., ge=1, description="1-indexed source page number.")
    used: bool = Field(..., description="Whether OCR text was used for this page.")
    confidence: Optional[ConfidenceScore] = Field(
        default=None,
        description="Deterministic OCR quality score for this page, when available.",
    )
    text_length: int = Field(
        default=0,
        ge=0,
        description="Number of normalized OCR characters extracted from the page.",
    )
    warning: Optional[str] = Field(
        default=None,
        description="Non-blocking OCR warning for this page, if OCR failed or was low quality.",
    )


class OcrMetadata(BaseModel):
    """Run-level OCR usage and confidence metadata for clause extraction."""

    used: bool = Field(..., description="Whether OCR supplied text for any page.")
    engine: str = Field(..., description="OCR engine used by the extraction agent.")
    pages_processed: int = Field(
        default=0,
        ge=0,
        description="Number of pages where OCR was attempted.",
    )
    average_confidence: Optional[ConfidenceScore] = Field(
        default=None,
        description="Average deterministic OCR confidence across OCR-used pages.",
    )
    low_confidence: bool = Field(
        default=False,
        description="Whether any OCR-used page is below the confidence threshold.",
    )
    manual_review_required: bool = Field(
        default=False,
        description="Whether OCR quality requires manual source review.",
    )
    reason: Optional[str] = Field(
        default=None,
        description="Why OCR was attempted or why OCR metadata was recorded.",
    )
    pages: list[OcrPageResult] = Field(
        default_factory=list,
        description="Page-level OCR status and confidence details.",
    )


class ExtractedContract(BaseModel):
    """Structured clause extraction output for one contract run."""

    run_id: str = Field(..., description="Unique identifier for this pipeline run.")
    document_id: str = Field(..., description="Primary contract document ID.")
    source_file: str = Field(..., description="Primary contract source file.")
    fallback_assisted: bool = Field(
        default=False,
        description="Whether synthetic fallback data assisted this extraction.",
    )
    fallback_reason: Optional[str] = Field(
        default=None,
        description="Reason synthetic fallback data was used, if applicable.",
    )
    text_extraction_method: Literal["digital", "ocr", "none"] = Field(
        default="digital",
        description="Primary text extraction method used for this contract.",
    )
    ocr_metadata: Optional[OcrMetadata] = Field(
        default=None,
        description="OCR usage and confidence metadata, when OCR was attempted.",
    )
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
