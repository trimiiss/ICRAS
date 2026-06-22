"""Jira posting result schemas for tracker integration."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class JiraPostingStatus(str, Enum):
    """Machine-readable Jira posting outcome."""

    DISABLED = "DISABLED"
    SKIPPED = "SKIPPED"
    CREATED = "CREATED"
    FAILED = "FAILED"


class JiraPostingConfig(BaseModel):
    """Runtime Jira configuration loaded from environment variables."""

    base_url: str = Field(..., description="Jira site base URL.")
    email: str = Field(..., description="Jira account email.")
    api_token: str = Field(..., repr=False, description="Jira API token.")
    project_key: str = Field(..., description="Jira project key for created issues.")
    issue_type: str = Field(default="Task", description="Jira issue type name.")


class JiraIssueRequest(BaseModel):
    """Validated Jira issue creation request."""

    project_key: str = Field(..., description="Jira project key.")
    issue_type: str = Field(default="Task", description="Jira issue type name.")
    summary: str = Field(..., description="Jira issue summary.")
    description: Dict[str, Any] = Field(
        ..., description="Jira Cloud Atlassian Document Format description."
    )
    labels: List[str] = Field(
        default_factory=list,
        description="Jira issue labels used for traceability.",
    )
    idempotency_key: Optional[str] = Field(
        default=None,
        description="Input fingerprint marker used to identify duplicate postings.",
    )


class JiraIssueResult(BaseModel):
    """Result returned by Jira after issue creation."""

    issue_id: Optional[str] = Field(default=None, description="Jira issue ID.")
    issue_key: str = Field(..., description="Jira issue key, such as GEN-123.")
    issue_url: str = Field(..., description="Browser URL for the created Jira issue.")


class JiraPostingResult(BaseModel):
    """Run artifact describing the tracker posting decision."""

    run_id: str = Field(..., description="Pipeline run ID.")
    status: JiraPostingStatus = Field(..., description="Posting outcome.")
    should_post: bool = Field(..., description="Whether the run attempted Jira posting.")
    reason: str = Field(..., description="Human-readable posting decision reason.")
    jira_issue_key: Optional[str] = Field(
        default=None, description="Created Jira issue key, when available."
    )
    jira_issue_url: Optional[str] = Field(
        default=None, description="Created Jira issue URL, when available."
    )
    duplicate_guard: Optional[str] = Field(
        default=None,
        description="Duplicate-prevention fingerprint or suppression reason.",
    )
    request_summary: Dict[str, Any] = Field(
        default_factory=dict,
        description="Safe summary of the Jira request. Secrets are never included.",
    )
    error_message: Optional[str] = Field(
        default=None,
        description="Safe error text for failed posting attempts.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when the posting result was generated.",
    )
