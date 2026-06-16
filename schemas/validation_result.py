"""ValidationResult schema for deterministic contract field validation."""

from datetime import datetime, timezone
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from schemas.common import EvidencePointer
from schemas.finding import Finding


class ValidatedContractField(BaseModel):
    """Validation status for one required contract field."""

    field_name: str = Field(..., description="Canonical contract field name.")
    is_present: bool = Field(..., description="Whether the field was found.")
    normalized_value: Optional[str] = Field(
        default=None,
        description="Normalized field value, when applicable.",
    )
    source: Optional[str] = Field(
        default=None,
        description="Where the validator found the field value.",
    )
    evidence: List[EvidencePointer] = Field(
        default_factory=list,
        description="Evidence pointers related to this field, when available.",
    )


class ValidationResult(BaseModel):
    """Structured output produced by the Validation Agent."""

    run_id: str = Field(..., description="Unique identifier for this pipeline run.")
    normalized_fields: Dict[str, str] = Field(
        default_factory=dict,
        description="Canonical field values normalized by validation.",
    )
    validated_fields: List[ValidatedContractField] = Field(
        default_factory=list,
        description="Per-field validation outcomes.",
    )
    findings: List[Finding] = Field(
        default_factory=list,
        description="Findings raised by required field validation.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when validation completed.",
    )
