"""Idempotency decision artifact schema."""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class FingerprintedFile(BaseModel):
    """One input file included in an idempotency fingerprint."""

    path: str = Field(..., description="Bundle-relative file path.")
    sha256: str = Field(..., min_length=64, max_length=64, description="File SHA-256.")


class IdempotencyResult(BaseModel):
    """Run-level duplicate detection and reuse decision."""

    run_id: str = Field(..., description="Current run identifier.")
    status: Literal["new", "duplicate"] = Field(
        ..., description="Whether this run is a new input or duplicate input."
    )
    decision: str = Field(..., description="Human-readable idempotency decision.")
    contract_sha256: str = Field(
        ..., min_length=64, max_length=64, description="Primary contract SHA-256."
    )
    input_fingerprint_sha256: str = Field(
        ..., min_length=64, max_length=64, description="Full input fingerprint SHA-256."
    )
    fingerprint_algorithm: Literal["sha256"] = Field(
        default="sha256", description="Hash algorithm used for the fingerprint."
    )
    fingerprinted_files: List[FingerprintedFile] = Field(
        default_factory=list,
        description="Files included in the full input fingerprint.",
    )
    baseline_run_id: Optional[str] = Field(
        None, description="Completed equivalent run reused by this run."
    )
    baseline_run_dir: Optional[str] = Field(
        None, description="Filesystem path to the reused baseline run."
    )
    current_run_dir: Optional[str] = Field(
        None, description="Filesystem path to the current run."
    )
    external_posting_allowed: bool = Field(
        ..., description="Whether external posting is allowed for this run."
    )
    posting_suppression_reason: Optional[str] = Field(
        None, description="Reason external posting was suppressed."
    )
    copied_artifacts: Dict[str, str] = Field(
        default_factory=dict,
        description="Artifacts copied into the current run folder.",
    )
    artifact_paths: Dict[str, str] = Field(
        default_factory=dict,
        description="Artifact paths exposed for the current run.",
    )
