"""Final workflow artifact schemas."""

from datetime import datetime, timezone
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from schemas.common import Severity
from schemas.finding import Finding


class FinalFindingsResult(BaseModel):
    """Merged, deduplicated, severity-sorted finding output."""

    run_id: str = Field(..., description="Unique identifier for this pipeline run.")
    overall_severity: Severity = Field(
        ..., description="Highest severity across final findings."
    )
    findings: List[Finding] = Field(
        default_factory=list,
        description="Final merged findings sorted by severity.",
    )
    total_findings: int = Field(..., description="Number of final findings.")
    requires_human_review: bool = Field(
        ..., description="Whether the final result needs human review."
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when workflow orchestration finalized findings.",
    )


class ConfidenceDistribution(BaseModel):
    """Bucketed confidence-score summary for audit and metrics artifacts."""

    count: int = Field(0, ge=0, description="Number of confidence scores included.")
    low_count: int = Field(
        0,
        ge=0,
        description="Number of scores below the manual-review threshold.",
    )
    medium_count: int = Field(
        0,
        ge=0,
        description="Number of scores from the threshold up to 0.90.",
    )
    high_count: int = Field(
        0,
        ge=0,
        description="Number of scores at or above 0.90.",
    )
    min_score: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Lowest score observed, if any scores exist.",
    )
    max_score: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Highest score observed, if any scores exist.",
    )
    average_score: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Mean score, rounded for stable audit output.",
    )


class AgentAuditTrace(BaseModel):
    """Structured per-step audit trace rendered into audit_log.md."""

    step: str = Field(..., description="Pipeline step name.")
    agent: str = Field(..., description="Agent or component responsible for the step.")
    status: str = Field(..., description="Step status.")
    started_at: datetime = Field(..., description="Step start timestamp.")
    finished_at: datetime = Field(..., description="Step finish timestamp.")
    duration_seconds: float = Field(..., ge=0.0, description="Step duration.")
    input_paths: Dict[str, str] = Field(
        default_factory=dict,
        description="Files or folders consumed by this step.",
    )
    output_paths: Dict[str, str] = Field(
        default_factory=dict,
        description="Files written by this step.",
    )
    extracted_clause_count: int = Field(
        0,
        ge=0,
        description="Extracted clause count visible to this step.",
    )
    exception_count: int = Field(
        0,
        ge=0,
        description="Exception count visible to this step.",
    )
    exception_categories: Dict[str, int] = Field(
        default_factory=dict,
        description="Exception counts by category.",
    )
    fallback_used: bool = Field(
        False,
        description="Whether the step used fallback behavior.",
    )
    fallback_reason: Optional[str] = Field(
        None,
        description="Reason fallback behavior was used.",
    )
    low_confidence_count: int = Field(
        0,
        ge=0,
        description="Number of low-confidence cases visible to this step.",
    )
    confidence_distribution: ConfidenceDistribution = Field(
        default_factory=ConfidenceDistribution,
        description="Confidence distribution visible to this step.",
    )


class PipelineMetrics(BaseModel):
    """Run-level metrics written by workflow orchestration."""

    run_id: str = Field(..., description="Unique identifier for this pipeline run.")
    status: str = Field(..., description="Final pipeline status.")
    duration_seconds: float = Field(
        ..., ge=0.0, description="End-to-end processing duration in seconds."
    )
    total_processing_time_seconds: float = Field(
        ...,
        ge=0.0,
        description="Alias for end-to-end processing duration used by auditors.",
    )
    extraction_clause_count: int = Field(
        ..., ge=0, description="Number of extracted clauses."
    )
    validation_finding_count: int = Field(
        ..., ge=0, description="Number of findings from validation."
    )
    risk_finding_count: int = Field(
        ..., ge=0, description="Number of findings from risk assessment."
    )
    compliance_finding_count: int = Field(
        ..., ge=0, description="Number of findings from compliance review."
    )
    anomaly_finding_count: int = Field(
        ..., ge=0, description="Number of findings from anomaly review."
    )
    counterparty_exception_count: int = Field(
        ..., ge=0, description="Number of counterparty matching exceptions."
    )
    final_finding_count: int = Field(
        ..., ge=0, description="Number of final merged findings."
    )
    exception_count: int = Field(
        ..., ge=0, description="Number of routed approval exceptions."
    )
    exception_categories: Dict[str, int] = Field(
        default_factory=dict,
        description="Routed approval exception counts by category.",
    )
    obligation_count: int = Field(
        ..., ge=0, description="Number of obligation records exported."
    )
    fallback_assisted: bool = Field(
        ..., description="Whether extraction used the synthetic fallback."
    )
    fallback_reason: Optional[str] = Field(
        None, description="Reason extraction fallback was used, if applicable."
    )
    ocr_used: bool = Field(
        ..., description="Whether OCR supplied text during extraction."
    )
    ocr_pages_processed: int = Field(
        ..., ge=0, description="Number of pages where OCR was attempted."
    )
    ocr_average_confidence: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Average OCR confidence across OCR-used pages.",
    )
    ocr_manual_review_required: bool = Field(
        ..., description="Whether OCR quality requires manual review."
    )
    low_confidence_count: int = Field(
        ...,
        ge=0,
        description="Number of low-confidence clauses and final findings.",
    )
    confidence_distributions: Dict[str, ConfidenceDistribution] = Field(
        default_factory=dict,
        description="Confidence-score distributions by artifact type.",
    )
    throughput_clauses_per_second: float = Field(
        ..., ge=0.0, description="Extracted clauses processed per second."
    )
    accuracy_percent: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Deterministic confidence-based accuracy proxy.",
    )
    exception_rate: float = Field(
        ..., ge=0.0, description="Routed exceptions divided by extracted clauses."
    )
    exception_rate_percent: float = Field(
        ..., ge=0.0, description="Routed exceptions divided by extracted clauses as a percentage."
    )
    determinism_check: str = Field(
        ...,
        description=(
            "PASS when the latest same-bundle comparison is stable, FAIL when "
            "differences are found, or REUSED when duplicate artifacts were reused."
        ),
    )
    determinism_baseline_run_id: Optional[str] = Field(
        None,
        description="Prior same-bundle run used as the comparison baseline.",
    )
    determinism_compared_sections: List[str] = Field(
        default_factory=list,
        description="Pipeline output sections compared for deterministic behavior.",
    )
    determinism_excluded_timestamp_fields: List[str] = Field(
        default_factory=list,
        description="Timestamp fields intentionally ignored during comparison.",
    )
    determinism_differences: List[str] = Field(
        default_factory=list,
        description="Non-timestamp differences found between compared runs.",
    )
    idempotency_status: str = Field(
        default="new",
        description="Whether this run processed new inputs or reused a duplicate run.",
    )
    idempotency_baseline_run_id: Optional[str] = Field(
        None,
        description="Completed run reused when this run was detected as a duplicate.",
    )
    external_posting_allowed: bool = Field(
        default=True,
        description="Whether downstream tracker/CLM posting is allowed for this run.",
    )
    posting_suppression_reason: Optional[str] = Field(
        None,
        description="Reason external posting was suppressed, if applicable.",
    )
    jira_posting_status: Optional[str] = Field(
        None,
        description="Safe status for Jira tracker posting, when attempted.",
    )
    jira_issue_key: Optional[str] = Field(
        None,
        description="Created Jira issue key, when available.",
    )
    jira_issue_url: Optional[str] = Field(
        None,
        description="Created Jira issue URL, when available.",
    )
    jira_posting_reason: Optional[str] = Field(
        None,
        description="Reason for the Jira posting status.",
    )
    overall_severity: Severity = Field(
        ..., description="Highest final finding severity."
    )
    artifact_paths: Dict[str, str] = Field(
        default_factory=dict,
        description="Run artifacts generated by the pipeline.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when metrics were generated.",
    )
