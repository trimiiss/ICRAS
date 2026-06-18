"""Compliance agent constants."""


CHECKED_RULES: list[str] = [
    "high_risk_jurisdiction",
    "gdpr_requirements",
    "jurisdiction_required_clauses",
]

GDPR_PRIVACY_TOKENS = (
    "personal data",
    "privacy",
    "data processing",
    "data protection",
)

GDPR_OBLIGATION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "data_processing_terms": (
        "data processing terms",
        "process personal data",
        "processing personal data",
        "processor",
        "controller",
    ),
    "cross_border_transfer_controls": (
        "cross-border transfer",
        "cross border transfer",
        "international transfer",
        "standard contractual clauses",
        "transfer controls",
    ),
    "data_subject_rights": (
        "data subject rights",
        "data subject",
        "access",
        "erasure",
        "rectification",
    ),
}
