"""Document inventory schema for files discovered during intake."""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class DocumentType(str, Enum):
    """Known document classifications for contract bundles."""

    MANIFEST = "manifest"
    CONTRACT = "contract"
    VENDOR_MASTER = "vendor_master"
    PLAYBOOK = "playbook"
    APPROVAL_POLICY = "approval_policy"
    JURISDICTION_RULES = "jurisdiction_rules"
    SUPPORTING_DOCUMENT = "supporting_document"
    SUPPORTING_DATA = "supporting_data"
    SUPPORTING_POLICY = "supporting_policy"
    UNSUPPORTED = "unsupported"


class DocumentInventoryItem(BaseModel):
    """One file discovered in the submitted contract bundle."""

    document_id: str = Field(..., description="Stable document ID for this run.")
    file_name: str = Field(..., description="File name without parent directories.")
    relative_path: str = Field(..., description="Path relative to the bundle root.")
    file_extension: str = Field(..., description="Lowercase file extension.")
    document_type: DocumentType = Field(..., description="Classified document type.")
    file_size_bytes: int = Field(..., ge=0, description="File size in bytes.")
    is_primary: bool = Field(
        default=False,
        description="Whether this is the primary contract document.",
    )
    included: bool = Field(
        default=True,
        description="Whether downstream agents should process this file.",
    )
    reason: Optional[str] = Field(
        default=None,
        description="Classification or exclusion reason, when useful.",
    )


class DocumentInventory(BaseModel):
    """All files found in the bundle during intake."""

    run_id: str = Field(..., description="Unique identifier for this pipeline run.")
    bundle_name: str = Field(..., description="Name of the loaded contract bundle.")
    primary_contract_id: Optional[str] = Field(
        default=None,
        description="Document ID of the primary contract, if identified.",
    )
    documents: list[DocumentInventoryItem] = Field(
        default_factory=list,
        description="Classified files found in the bundle.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when this inventory was created.",
    )
