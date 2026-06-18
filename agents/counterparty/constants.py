"""Counterparty matching constants."""


EXACT_THRESHOLD: float = 1.0
STRONG_MATCH_THRESHOLD: float = 0.85
WEAK_MATCH_THRESHOLD: float = 0.60

COMPANY_SUFFIXES: tuple[str, ...] = (
    "inc",
    "inc.",
    "incorporated",
    "llc",
    "l.l.c.",
    "ltd",
    "ltd.",
    "limited",
    "corp",
    "corp.",
    "corporation",
    "co",
    "co.",
    "company",
    "plc",
    "p.l.c.",
    "gmbh",
    "ag",
    "sa",
    "s.a.",
    "pty",
    "pty.",
    "lp",
    "l.p.",
    "llp",
    "l.l.p.",
)
