"""Schemas for the contract review API."""

from datetime import datetime, timezone
from typing import Dict, Optional

from pydantic import BaseModel, Field


class UploadedFilePayload(BaseModel):
    """Validated uploaded file content used to create a bundle."""

    filename: str = Field(..., description="Original uploaded filename.")
    content_type: Optional[str] = Field(
        default=None,
        description="Client-supplied MIME type, when available.",
    )
    content: bytes = Field(..., description="Uploaded file bytes.")


class ContractReviewMetadata(BaseModel):
    """Metadata used to generate the bundle manifest."""

    bundle_name: Optional[str] = Field(
        default=None,
        description="Optional bundle name. A stable upload name is generated if omitted.",
    )
    contract_type: str = Field(
        default="Uploaded Contract",
        description="Contract type used by downstream policy checks.",
    )
    counterparty: str = Field(
        default="Unknown Counterparty",
        description="Primary counterparty name.",
    )
    jurisdiction: str = Field(
        default="Unspecified",
        description="Governing jurisdiction or review jurisdiction.",
    )
    effective_date: Optional[str] = Field(
        default=None,
        description="Optional effective date copied into manifest.yaml.",
    )


class ContractReviewResponse(BaseModel):
    """Response returned after the pipeline completes."""

    run_id: str = Field(..., description="Final pipeline run identifier.")
    status: str = Field(..., description="Final pipeline status.")
    approval_status: Optional[str] = Field(
        default=None,
        description="Final approval decision status, when available.",
    )
    idempotency_status: Optional[str] = Field(
        default=None,
        description="Whether the run processed new inputs or reused a duplicate run.",
    )
    duplicate_of_run_id: Optional[str] = Field(
        default=None,
        description="Baseline run ID reused for duplicate input, when applicable.",
    )
    external_posting_allowed: Optional[bool] = Field(
        default=None,
        description="Whether downstream external posting is allowed for this run.",
    )
    jira_posting_status: Optional[str] = Field(
        default=None,
        description="Safe Jira posting status, when available.",
    )
    jira_issue_key: Optional[str] = Field(
        default=None,
        description="Created Jira issue key, when available.",
    )
    jira_issue_url: Optional[str] = Field(
        default=None,
        description="Created Jira issue URL, when available.",
    )
    bundle_path: str = Field(
        ...,
        description="Generated bundle folder consumed by the pipeline.",
    )
    artifact_paths: Dict[str, str] = Field(
        default_factory=dict,
        description="Pipeline artifact paths keyed by artifact name.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when the API response was created.",
    )
