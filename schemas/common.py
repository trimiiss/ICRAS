"""Shared types used across all ICRAS schemas."""

from enum import Enum
from typing import Annotated, Optional

from pydantic import BaseModel, Field


class Severity(str, Enum):
    """Risk severity level for findings and assessments."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


# A float between 0.0 and 1.0 (inclusive), used for confidence scores.
ConfidenceScore = Annotated[float, Field(ge=0.0, le=1.0)]


class EvidencePointer(BaseModel):
    """Points to a specific location in a source document that supports a finding."""

    source_file: str = Field(
        ..., description="Name or path of the source file."
    )
    page_number: Optional[int] = Field(
        default=None, description="Page number in the document (1-indexed)."
    )
    clause_reference: Optional[str] = Field(
        default=None, description="Clause or section identifier (e.g. '4.2')."
    )
    excerpt: Optional[str] = Field(
        default=None,
        description="Short excerpt from the source that supports the finding.",
    )
