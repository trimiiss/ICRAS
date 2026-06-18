"""Anomaly agent constants."""

from schemas.common import Severity

from .models import UnusualPattern


CHECKED_RULES: list[str] = [
    "conflicting_governing_law",
    "contradictory_payment_terms",
    "duplicate_clause_value_conflict",
    "suspicious_date_ordering",
    "unusual_contract_pattern",
]

KNOWN_GOVERNING_LAW_JURISDICTIONS: tuple[str, ...] = (
    "New York",
    "Delaware",
    "California",
    "Texas",
    "Florida",
    "England and Wales",
    "United Kingdom",
    "Germany",
    "France",
    "India",
    "Singapore",
    "Netherlands",
    "Ireland",
)

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "governing_law": (
        "governing_law",
        "governing law",
        "jurisdiction",
        "choice_of_law",
        "choice of law",
    ),
    "payment_terms": (
        "payment_terms",
        "payment terms",
        "payment",
        "fees",
        "compensation",
        "invoice",
        "invoicing",
        "billing",
    ),
    "effective_date": (
        "effective_date",
        "effective date",
        "commencement_date",
        "start_date",
        "agreement_date",
    ),
    "expiry_date": (
        "expiry_date",
        "expiry date",
        "expiration_date",
        "end_date",
        "contract_end_date",
        "term_expiry",
    ),
    "signature_date": (
        "signature",
        "signatures",
        "execution",
        "signed",
        "signature_date",
    ),
    "liability_cap": (
        "liability_cap",
        "limitation_of_liability",
        "limited_liability",
        "liability_limit",
    ),
    "auto_renewal": (
        "auto_renewal",
        "automatic renewal",
        "auto renewal",
        "renewal",
    ),
    "termination_terms": (
        "termination_terms",
        "termination",
        "term_and_termination",
        "term_and_duration",
    ),
    "confidentiality": ("confidentiality", "confidential information"),
    "data_protection": ("data_protection", "privacy", "personal data"),
    "indemnity": ("indemnity", "indemnification"),
}

DEDICATED_CONFLICT_FIELDS: set[str] = {"governing_law", "payment_terms"}

UNUSUAL_PATTERNS: tuple[UnusualPattern, ...] = (
    UnusualPattern(
        pattern_id="unlimited_liability",
        field_name="liability_cap",
        title="Unusual liability exposure",
        description="The contract appears to include unlimited or uncapped liability.",
        regex=(
            r"\b(?:unlimited|uncapped)\s+liability\b|"
            r"\bliability\s+(?:is\s+)?(?:unlimited|uncapped)\b"
        ),
        severity=Severity.HIGH,
        recommendation="Legal must confirm whether unlimited liability is intended.",
    ),
    UnusualPattern(
        pattern_id="indefinite_auto_renewal",
        field_name="auto_renewal",
        title="Unusual indefinite auto-renewal",
        description="The contract appears to auto-renew indefinitely or perpetually.",
        regex=(
            r"\bauto(?:matically)?[-\s]?renew\w*\b.{0,100}\b"
            r"(?:indefinitely|perpetual|forever)\b|"
            r"\b(?:indefinitely|perpetual|forever)\b.{0,100}\b"
            r"auto(?:matically)?[-\s]?renew\w*\b"
        ),
        severity=Severity.HIGH,
        recommendation="Legal must confirm the renewal term and opt-out mechanics.",
    ),
    UnusualPattern(
        pattern_id="unilateral_amendment",
        field_name="amendment",
        title="Unusual unilateral amendment right",
        description="One party may be able to amend the agreement unilaterally.",
        regex=(
            r"\bmay\s+amend\b.{0,80}\b"
            r"(?:without\s+notice|sole\s+discretion|unilaterally)\b|"
            r"\bunilateral(?:ly)?\s+amend"
        ),
        severity=Severity.HIGH,
        recommendation="Legal must confirm amendment rights and notice requirements.",
    ),
    UnusualPattern(
        pattern_id="no_termination_right",
        field_name="termination_terms",
        title="Unusual termination restriction",
        description="The contract appears to restrict ordinary termination rights.",
        regex=r"\bmay\s+not\s+terminate\b|\bno\s+(?:right\s+to\s+)?terminate\b",
        severity=Severity.MEDIUM,
        recommendation=(
            "Legal must confirm whether the termination restriction is acceptable."
        ),
    ),
    UnusualPattern(
        pattern_id="non_compete",
        field_name="non_compete",
        title="Unusual non-compete language",
        description=(
            "The contract contains non-compete language that may be unusual for the "
            "agreement type."
        ),
        regex=r"\bnon[-\s]?compete\b|\bshall\s+not\s+compete\b",
        severity=Severity.HIGH,
        recommendation="Legal must review enforceability and business impact.",
    ),
)
