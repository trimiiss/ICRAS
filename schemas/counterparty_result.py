"""CounterpartyResult schema — counterparty resolution output for Agent C.

Each match result records the original extracted party name, the normalized
form, the closest vendor master match (if any), similarity score, and risk
flags.  The top-level ``CounterpartyResolution`` collects all results for a
single pipeline run and is serialized to ``counterparty_resolution.json``.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

from schemas.common import ConfidenceScore, EvidencePointer


class MatchStatus(str, Enum):
    """Outcome of matching a party name against the vendor master."""

    EXACT = "exact"
    FUZZY = "fuzzy"
    WEAK = "weak"
    NO_MATCH = "no_match"


class CounterpartyMatch(BaseModel):
    """Resolution result for one extracted party name."""

    original_party_name: str = Field(
        ..., description="Party name as extracted from the contract."
    )
    normalized_party_name: str = Field(
        ..., description="Party name after normalization (spacing, case, suffixes)."
    )
    matched_vendor_name: Optional[str] = Field(
        default=None,
        description="Closest vendor master name, or None when no match is found.",
    )
    vendor_id: Optional[str] = Field(
        default=None,
        description="Vendor ID from the vendor master, if a match was found.",
    )
    similarity_score: ConfidenceScore = Field(
        ...,
        description="Fuzzy similarity score between 0.0 and 1.0.",
    )
    match_status: MatchStatus = Field(
        ...,
        description="Match outcome: exact, fuzzy, weak, or no_match.",
    )
    manual_review_required: bool = Field(
        default=False,
        description="True when the match confidence is too low to auto-accept.",
    )
    risk_flag: Optional[str] = Field(
        default=None,
        description="Risk flag description when a high-risk counterparty change is detected.",
    )
    evidence_pointer: Optional[EvidencePointer] = Field(
        default=None,
        description="Evidence pointer linking the party name to its source location.",
    )


class CounterpartyResolution(BaseModel):
    """Structured output produced by Agent C — Counterparty Resolution Agent."""

    run_id: str = Field(..., description="Unique identifier for this pipeline run.")
    matches: List[CounterpartyMatch] = Field(
        default_factory=list,
        description="Resolution results for each extracted party name.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when counterparty resolution completed.",
    )
