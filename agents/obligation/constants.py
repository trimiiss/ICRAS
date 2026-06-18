"""Constants used by obligation register generation."""

OBLIGATION_CSV_COLUMNS: tuple[str, ...] = (
    "obligation_id",
    "obligation_type",
    "responsible_party",
    "obligation_summary",
    "due_date",
    "timing_trigger",
    "is_recurring",
    "recurrence_frequency",
    "source_clause_text",
    "source_file",
    "source_page",
    "evidence_id",
    "document_id",
    "clause_reference",
    "evidence_pointer",
)

OBLIGATION_TYPE_BY_CLAUSE: dict[str, str] = {
    "payment_terms": "payment",
    "payment": "payment",
    "fees": "payment",
    "termination": "termination_notice",
    "term_and_duration": "termination_notice",
    "data_protection": "compliance",
    "privacy": "compliance",
    "confidentiality": "confidentiality",
    "confidentiality_definition": "confidentiality",
    "indemnity": "indemnity",
    "indemnification": "indemnity",
    "auto_renewal": "renewal",
    "automatic_renewal": "renewal",
}

OBLIGATION_CUES: tuple[str, ...] = (
    "shall",
    "must",
    "will",
    "payable",
    "due",
    "comply",
    "protect",
    "indemnify",
    "return",
    "notice",
    "renew",
)

RESPONSIBLE_PARTIES: tuple[str, ...] = (
    "Customer",
    "Supplier",
    "Vendor",
    "Provider",
    "Each party",
    "Either party",
    "Receiving party",
    "Disclosing party",
    "Client",
    "Contractor",
)

DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d",
    "%m/%d/%Y",
    "%m-%d-%Y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d %B %Y",
    "%d %b %Y",
)

TIMING_PATTERNS: tuple[str, ...] = (
    r"\bwithin\s+\d+\s+(?:business\s+)?days?\b",
    r"\b\d+\s+days?\s+(?:written\s+)?notice\b",
    r"\bnet[\s-]?\d+\b",
    r"\bafter\s+\d+\s+(?:business\s+)?days?\b",
    r"\bprior\s+to\s+expiration\b",
    r"\bupon\s+[a-z ]{3,60}\b",
)
