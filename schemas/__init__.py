"""ICRAS core Pydantic v2 schemas for inter-agent communication."""

from schemas.approval_packet import ApprovalPacket
from schemas.common import ConfidenceScore, EvidencePointer, Severity
from schemas.context_packet import ContextPacket
from schemas.counterparty_result import (
    CounterpartyMatch,
    CounterpartyResolution,
    MatchStatus,
)
from schemas.document_inventory import (
    DocumentInventory,
    DocumentInventoryItem,
    DocumentType,
)
from schemas.evidence_index import EvidenceIndex, EvidenceRecord, EvidenceWarning
from schemas.extracted_clause import (
    ClauseEvidenceSpan,
    ExtractedClause,
    ExtractedContract,
    ExtractionWarning,
)
from schemas.finding import Finding
from schemas.policy_rules import (
    ApprovalThreshold,
    ApprovedPaymentTerms,
    AutoRenewalRules,
    EscalationRule,
    GDPRRequirements,
    LiabilityCapRequirements,
    PolicyRules,
    SigningAuthorityThresholds,
)
from schemas.risk_result import ClauseAnalysisResult, ClauseRisk, RiskResult
from schemas.validation_result import ValidatedContractField, ValidationResult

__all__ = [
    "Severity",
    "EvidencePointer",
    "ConfidenceScore",
    "ContextPacket",
    "CounterpartyMatch",
    "CounterpartyResolution",
    "MatchStatus",
    "DocumentInventory",
    "DocumentInventoryItem",
    "DocumentType",
    "EvidenceIndex",
    "EvidenceRecord",
    "EvidenceWarning",
    "ClauseEvidenceSpan",
    "ExtractedClause",
    "ExtractedContract",
    "ExtractionWarning",
    "Finding",
    "ApprovalThreshold",
    "ApprovedPaymentTerms",
    "AutoRenewalRules",
    "EscalationRule",
    "GDPRRequirements",
    "LiabilityCapRequirements",
    "PolicyRules",
    "SigningAuthorityThresholds",
    "ClauseAnalysisResult",
    "ClauseRisk",
    "RiskResult",
    "ApprovalPacket",
    "ValidatedContractField",
    "ValidationResult",
]
