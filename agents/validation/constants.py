"""Validation rule constants."""

from schemas.common import Severity


FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "party_names": (
        "party_names",
        "parties",
        "counterparty",
        "party",
    ),
    "effective_date": (
        "effective_date",
        "commencement_date",
        "start_date",
        "agreement_date",
    ),
    "governing_law": (
        "governing_law",
        "jurisdiction",
        "choice_of_law",
    ),
    "payment_terms": (
        "payment_terms",
        "fees",
        "compensation",
        "invoicing",
        "payment",
    ),
    "termination_terms": (
        "termination_terms",
        "termination",
        "term_and_termination",
        "term_and_duration",
    ),
    "expiry_date": (
        "expiry_date",
        "expiration_date",
        "end_date",
        "contract_end_date",
        "term_expiry",
    ),
    "liability_cap": (
        "liability_cap",
        "limitation_of_liability",
        "limited_liability",
        "liability_limit",
    ),
    "signature": (
        "signature",
        "signatures",
        "execution",
        "signatory",
        "signed",
    ),
}

CONTRACT_TYPE_PAYMENT_HINTS: tuple[str, ...] = (
    "service",
    "statement of work",
    "sow",
    "vendor",
    "purchase",
    "procurement",
    "subscription",
    "license",
    "consulting",
)

DEFAULT_FIELD_SEVERITIES: dict[str, Severity] = {
    "party_names": Severity.HIGH,
    "effective_date": Severity.HIGH,
    "governing_law": Severity.HIGH,
    "payment_terms": Severity.HIGH,
    "termination_terms": Severity.MEDIUM,
    "expiry_date": Severity.HIGH,
    "liability_cap": Severity.HIGH,
    "signature": Severity.MEDIUM,
}

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
