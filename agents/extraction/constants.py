"""Extraction constants and regular expressions."""

import re
from pathlib import Path


CLAUSE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "parties": ("parties", "between", "counterparty"),
    "effective_date": ("effective date", "commencement date"),
    "termination": ("termination", "terminate", "expiration"),
    "payment_terms": ("payment", "invoice", "net 30", "fees"),
    "liability_cap": ("liability", "cap", "limitation of liability"),
    "indemnity": ("indemnity", "indemnification", "indemnify"),
    "governing_law": ("governing law", "jurisdiction", "delaware"),
    "auto_renewal": ("auto-renewal", "automatic renewal", "renewal"),
    "data_protection": ("data protection", "personal data", "gdpr", "privacy"),
    "confidentiality": ("confidential", "confidentiality", "non-disclosure"),
}

SECTION_PATTERN = re.compile(
    r"^\s*(?P<section>\d+(?:\.\d+)*)[.)]?\s+(?P<title>[A-Z][^\n]{2,})\s*$"
)
SCHEDULE_PATTERN = re.compile(
    r"^\s*(?P<section>(?:schedule|exhibit)\s+[A-Z0-9]+)[\s:.-]*(?P<title>.*)$",
    re.IGNORECASE,
)
PAGE_NUMBER_PATTERN = re.compile(r"^\s*(?:page\s+)?\d+(?:\s+of\s+\d+)?\s*$", re.I)
LOW_CONFIDENCE_THRESHOLD = 0.75
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FALLBACK_FIXTURE_DIR = PROJECT_ROOT / "data" / "extraction_fallbacks"
MIN_REQUIRED_CLAUSE_COVERAGE = 0.8
