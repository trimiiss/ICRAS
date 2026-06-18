"""Risk scoring constants."""

CLAUSE_ALIASES: dict[str, tuple[str, ...]] = {
    "payment_terms": ("payment_terms", "payment", "fees", "invoice", "billing"),
    "liability_cap": (
        "liability_cap",
        "limitation_of_liability",
        "limited_liability",
        "liability_limit",
    ),
    "governing_law": ("governing_law", "jurisdiction", "choice_of_law"),
    "auto_renewal": ("auto_renewal", "renewal", "automatic_renewal"),
    "data_protection": ("data_protection", "gdpr", "privacy", "personal_data"),
    "party_names": ("party_names", "parties", "party", "counterparty"),
    "signature": ("signature", "signatures", "execution", "signed"),
    "termination": ("termination", "term_and_duration", "term", "expiration"),
    "confidentiality_definition": (
        "confidentiality_definition",
        "confidentiality",
        "confidential_information",
    ),
    "term_and_duration": ("term_and_duration", "termination", "duration", "term"),
    "limitation_of_liability": (
        "limitation_of_liability",
        "liability_cap",
        "liability",
    ),
}

KNOWN_JURISDICTIONS: tuple[str, ...] = (
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
    "Russia",
    "Iran",
    "North Korea",
    "Syria",
)

STANDARD_PAYMENT_DAYS = 30
