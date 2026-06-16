"""ContextPacket schema — the initial context passed into the agent pipeline."""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class ContextPacket(BaseModel):
    """Bundles all information an agent needs to begin processing a contract.

    Created by the intake agent and passed down through the pipeline.
    """

    run_id: str = Field(..., description="Unique identifier for this pipeline run.")
    bundle_name: str = Field(..., description="Name of the loaded contract bundle.")
    contract_type: str = Field(
        ..., description="Type of contract (e.g. NDA, Services Agreement)."
    )
    counterparty: str = Field(..., description="Name of the counterparty.")
    jurisdiction: str = Field(
        ..., description="Governing jurisdiction (e.g. 'New York, USA')."
    )
    effective_date: Optional[str] = Field(
        default=None,
        description="Raw effective date from the contract bundle, if provided.",
    )
    contract_file: str = Field(..., description="Path to the contract PDF file.")
    playbook: Dict[str, Any] = Field(
        default_factory=dict,
        description="Parsed playbook rules for this contract type.",
    )
    approval_policy: Dict[str, Any] = Field(
        default_factory=dict,
        description="Parsed approval policy for this contract type.",
    )
    jurisdiction_rules: Dict[str, Any] = Field(
        default_factory=dict,
        description="Parsed jurisdiction-specific rules.",
    )
    vendor_info: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Vendor master record for the counterparty, if found.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when this context packet was created.",
    )
