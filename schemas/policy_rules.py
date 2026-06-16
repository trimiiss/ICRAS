"""Policy rule schemas loaded from approval_policy.yaml."""

from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from schemas.common import ConfidenceScore, Severity
from schemas.exception_triage import ExceptionCategory


class ApprovalThreshold(BaseModel):
    """Approver requirements for a severity level."""

    auto_approve: bool = Field(
        ..., description="Whether findings at this level can be auto-approved."
    )
    required_approvers: List[str] = Field(
        default_factory=list,
        description="Approver roles required at this severity level.",
    )


class EscalationRule(BaseModel):
    """A conditional approver escalation rule."""

    condition: str = Field(..., description="Named condition that triggers escalation.")
    action: str = Field(..., description="Action to take when the condition is met.")
    approver: str = Field(..., description="Approver role added by this rule.")


class AutoApproveRouting(BaseModel):
    """Configured auto-approval route when no exceptions are detected."""

    category: ExceptionCategory = Field(
        ..., description="Routing category to use for auto-approved contracts."
    )
    reason: str = Field(..., description="Why auto-approval is allowed.")
    next_action: str = Field(..., description="Next action for auto-approved runs.")


class ExceptionRouteRule(BaseModel):
    """Configurable rule that maps findings to an approval category."""

    name: str = Field(..., description="Stable name for this routing rule.")
    category: ExceptionCategory = Field(
        ..., description="Approval-routing category selected by this rule."
    )
    approver: str = Field(..., description="Approver role selected by this rule.")
    reason: str = Field(..., description="Why this rule routes the finding.")
    next_action: str = Field(
        ..., description="Human-readable action for the approver."
    )
    match_issue_types: List[str] = Field(
        default_factory=list,
        description="Finding issue_type values that must match this rule.",
    )
    match_field_names: List[str] = Field(
        default_factory=list,
        description="Finding field_name values that must match this rule.",
    )
    match_categories: List[str] = Field(
        default_factory=list,
        description="Finding category values that must match this rule.",
    )
    match_text: List[str] = Field(
        default_factory=list,
        description="Text fragments to match across finding title/body fields.",
    )
    manual_review_required: Optional[bool] = Field(
        default=None,
        description="Require the finding manual-review flag to match this value.",
    )
    max_confidence: Optional[ConfidenceScore] = Field(
        default=None,
        description="Match findings at or below this confidence score.",
    )

    @model_validator(mode="after")
    def _require_match_criteria(self) -> "ExceptionRouteRule":
        """Require at least one match criterion for deterministic routing."""
        has_matcher = any(
            (
                self.match_issue_types,
                self.match_field_names,
                self.match_categories,
                self.match_text,
                self.manual_review_required is not None,
                self.max_confidence is not None,
            )
        )
        if not has_matcher:
            raise ValueError(
                f"Exception route rule '{self.name}' must define at least one "
                "match criterion."
            )
        return self

    @field_validator(
        "name",
        "approver",
        "reason",
        "next_action",
    )
    @classmethod
    def _text_fields_must_not_be_blank(cls, value: str) -> str:
        """Ensure required route text fields are not blank."""
        if not value.strip():
            raise ValueError("Exception route text fields must not be blank.")
        return value


class ExceptionRouting(BaseModel):
    """Configurable exception-routing table for Agent H."""

    auto_approve: Optional[AutoApproveRouting] = Field(
        default=None,
        description="Auto-approval route used when no exceptions exist.",
    )
    rules: List[ExceptionRouteRule] = Field(
        default_factory=list,
        description="Ordered finding-to-approver routing rules.",
    )


class ApprovedPaymentTerms(BaseModel):
    """Payment terms allowed by policy."""

    terms: List[str] = Field(
        default_factory=lambda: ["net-30"],
        min_length=1,
        description="Allowed payment terms, such as net-30 or net-60.",
    )
    severity_if_unapproved: Severity = Field(
        default=Severity.HIGH,
        description="Severity when detected payment terms are not approved.",
    )

    @field_validator("terms")
    @classmethod
    def _terms_must_not_be_blank(cls, terms: List[str]) -> List[str]:
        """Ensure every configured payment term contains text."""
        if any(not term.strip() for term in terms):
            raise ValueError("Payment terms must not contain blank values.")
        return terms


class SigningAuthorityThresholds(BaseModel):
    """Contract value thresholds for signing authority."""

    thresholds_usd: Dict[str, float] = Field(
        default_factory=lambda: {
            "department_head": 50000.0,
            "cfo": 100000.0,
        },
        description="Minimum contract value in USD requiring each signer role.",
    )

    @field_validator("thresholds_usd")
    @classmethod
    def _thresholds_must_be_non_negative(
        cls,
        thresholds_usd: Dict[str, float],
    ) -> Dict[str, float]:
        """Ensure signing thresholds are valid non-negative amounts."""
        for role, amount in thresholds_usd.items():
            if amount < 0:
                raise ValueError(
                    f"Signing threshold for '{role}' must be non-negative."
                )
        return thresholds_usd


class LiabilityCapRequirements(BaseModel):
    """Policy requirements for limitation of liability clauses."""

    required: bool = Field(
        default=True,
        description="Whether a liability cap is required.",
    )
    minimum_cap: str = Field(
        default="fees_paid_12_months",
        description="Minimum acceptable liability cap standard.",
    )
    severity_if_missing: Severity = Field(
        default=Severity.HIGH,
        description="Severity when a required liability cap is missing.",
    )


class AutoRenewalRules(BaseModel):
    """Policy rules for auto-renewal provisions."""

    allowed: bool = Field(
        default=False,
        description="Whether auto-renewal is allowed.",
    )
    minimum_notice_days: int = Field(
        default=30,
        ge=0,
        description="Minimum opt-out notice period required when allowed.",
    )
    severity_if_unapproved: Severity = Field(
        default=Severity.MEDIUM,
        description="Severity when auto-renewal violates policy.",
    )


class GDPRRequirements(BaseModel):
    """Policy requirements for GDPR-related contract terms."""

    applies_when_personal_data: bool = Field(
        default=True,
        description="Whether GDPR checks apply when personal data is involved.",
    )
    required_clauses: List[str] = Field(
        default_factory=lambda: [
            "data_processing_terms",
            "cross_border_transfer_controls",
            "data_subject_rights",
        ],
        description="Clause types required when GDPR applies.",
    )
    severity_if_missing: Severity = Field(
        default=Severity.CRITICAL,
        description="Severity when a required GDPR clause is missing.",
    )


class PolicyRules(BaseModel):
    """All configurable policy rules loaded from approval_policy.yaml."""

    approval_thresholds: Dict[str, ApprovalThreshold] = Field(
        default_factory=dict,
        description="Approval requirements by severity level.",
    )
    escalation_rules: List[EscalationRule] = Field(
        default_factory=list,
        description="Conditional approver escalation rules.",
    )
    exception_routing: ExceptionRouting = Field(
        default_factory=ExceptionRouting,
        description="Agent H exception triage and approval routing table.",
    )
    approved_payment_terms: ApprovedPaymentTerms = Field(
        default_factory=ApprovedPaymentTerms,
        description="Allowed payment terms.",
    )
    signing_authority_thresholds: SigningAuthorityThresholds = Field(
        default_factory=SigningAuthorityThresholds,
        description="Signing authority thresholds.",
    )
    liability_cap_requirements: LiabilityCapRequirements = Field(
        default_factory=LiabilityCapRequirements,
        description="Liability cap requirements.",
    )
    auto_renewal_rules: AutoRenewalRules = Field(
        default_factory=AutoRenewalRules,
        description="Auto-renewal policy.",
    )
    high_risk_jurisdictions: List[str] = Field(
        default_factory=lambda: ["Russia", "Iran", "North Korea", "Syria"],
        description="Jurisdictions that require high-risk handling.",
    )
    gdpr_requirements: GDPRRequirements = Field(
        default_factory=GDPRRequirements,
        description="GDPR-related policy requirements.",
    )
    manual_review_confidence_threshold: ConfidenceScore = Field(
        default=0.75,
        description="Minimum confidence before manual review is required.",
    )
