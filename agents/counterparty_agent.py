"""Agent C — Counterparty Resolution Agent.

Matches contract party names against the vendor master using fuzzy matching,
flags unknown vendors, weak matches, and high-risk counterparty changes.

LLM logic will be added in a later user story.
"""

import csv
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from thefuzz import fuzz

from schemas.common import EvidencePointer
from schemas.counterparty_result import (
    CounterpartyMatch,
    CounterpartyResolution,
    MatchStatus,
)
from utils.run_manager import append_audit_event


class CounterpartyAgentError(Exception):
    """Raised when counterparty resolution cannot produce its required artifact."""


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

EXACT_THRESHOLD: float = 1.0
STRONG_MATCH_THRESHOLD: float = 0.85
WEAK_MATCH_THRESHOLD: float = 0.60

# Common company suffixes to strip during normalization
_COMPANY_SUFFIXES: tuple[str, ...] = (
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


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_counterparty_check(
    context: Dict[str, Any],
    extracted_contract: Dict[str, Any],
    vendor_master_path: str | Path,
    run_dir: str | Path | None = None,
    evidence_index: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Resolve contract party names against the vendor master.

    Args:
        context: Context packet from the Intake Agent.
        extracted_contract: The ``extracted_contract`` dict (with ``clauses``).
        vendor_master_path: Path to ``vendor_master.csv``.
        run_dir: Optional run directory for writing ``counterparty_resolution.json``.
        evidence_index: Optional page-level evidence index for source pointers.

    Returns:
        A dictionary containing the resolution result and artifact paths.

    Raises:
        CounterpartyAgentError: If inputs are malformed or the artifact cannot be saved.
    """
    run_path = _validate_run_dir(run_dir) if run_dir is not None else None
    vendor_records = _load_vendor_master(vendor_master_path)
    party_names = _extract_party_names(context, extracted_contract)
    evidence_records = _extract_evidence_records(evidence_index)

    run_id = str(context.get("run_id") or "unknown-run")

    matches: list[CounterpartyMatch] = []
    for party_name in party_names:
        match = _resolve_party(
            party_name=party_name,
            vendor_records=vendor_records,
            context=context,
            evidence_records=evidence_records,
        )
        matches.append(match)

    resolution = CounterpartyResolution(
        run_id=run_id,
        matches=matches,
    )

    artifact_paths: dict[str, str] = {}
    if run_path is not None:
        output_path = run_path / "counterparty_resolution.json"
        _write_model_json(output_path, resolution)
        append_audit_event(
            run_path,
            {
                "event": "counterparty_resolution_completed",
                "agent": "counterparty_agent",
                "message": "Agent C resolved contract party names against vendor master.",
                "artifacts": [output_path.name],
                "match_count": len(matches),
                "flagged_count": sum(
                    1 for m in matches if m.manual_review_required or m.risk_flag
                ),
            },
        )
        artifact_paths["counterparty_resolution"] = str(output_path)

    result = resolution.model_dump(mode="json")
    return {
        "counterparty_resolution": result,
        "matches": result["matches"],
        "artifact_paths": artifact_paths,
    }


# ---------------------------------------------------------------------------
# Vendor master loading
# ---------------------------------------------------------------------------


def _load_vendor_master(path: str | Path) -> list[dict[str, str]]:
    """Load vendor master CSV into a list of row dictionaries.

    Raises:
        CounterpartyAgentError: If the file is missing, empty, or malformed.
    """
    csv_path = Path(path).resolve()
    if not csv_path.exists():
        raise CounterpartyAgentError(
            f"Vendor master file not found: {csv_path}. "
            "Provide a valid vendor_master.csv path."
        )
    if not csv_path.is_file():
        raise CounterpartyAgentError(
            f"Vendor master path is not a file: {csv_path}."
        )

    try:
        with csv_path.open(encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            rows = [row for row in reader if any(v.strip() for v in row.values())]
    except Exception as exc:
        raise CounterpartyAgentError(
            f"Failed to read vendor master CSV: {exc}"
        ) from exc

    if not rows:
        raise CounterpartyAgentError(
            f"Vendor master CSV is empty or has no data rows: {csv_path}."
        )

    # Validate required columns
    first_row = rows[0]
    required_cols = {"vendor_id", "vendor_name"}
    missing_cols = required_cols - set(first_row.keys())
    if missing_cols:
        raise CounterpartyAgentError(
            f"Vendor master CSV is missing required columns: {sorted(missing_cols)}. "
            f"Found columns: {sorted(first_row.keys())}."
        )

    return rows


# ---------------------------------------------------------------------------
# Party name extraction
# ---------------------------------------------------------------------------


def _extract_party_names(
    context: Dict[str, Any],
    extracted_contract: Dict[str, Any],
) -> list[str]:
    """Collect unique party names from the context and extracted clauses.

    Sources (in priority order):
      1. ``context["counterparty"]``
      2. ``context["party_names"]`` / ``context["parties"]``
      3. Party-related clauses from ``extracted_contract["clauses"]``

    Returns:
        A deduplicated list of non-empty party name strings.

    Raises:
        CounterpartyAgentError: If no party names are found at all.
    """
    seen: set[str] = set()
    result: list[str] = []

    def _add(name: str) -> None:
        cleaned = name.strip()
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            result.append(cleaned)

    # Source 1: counterparty field
    counterparty = context.get("counterparty")
    if isinstance(counterparty, str) and counterparty.strip():
        _add(counterparty)

    # Source 2: party_names / parties list or string
    for key in ("party_names", "parties"):
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            for part in value.split(";"):
                _add(part)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    _add(item)

    # Source 3: clauses with party-related clause_type
    party_clause_types = {
        "party_names", "parties", "counterparty", "party",
        "contracting_parties", "party_identification",
    }
    clauses = extracted_contract.get("clauses", [])
    if isinstance(clauses, list):
        for clause in clauses:
            if not isinstance(clause, dict):
                continue
            clause_type = str(clause.get("clause_type", "")).lower().strip()
            if clause_type in party_clause_types:
                text = str(clause.get("text", "")).strip()
                if text:
                    _add(text)

    if not result:
        raise CounterpartyAgentError(
            "No party names found in context or extracted clauses. "
            "Ensure the contract bundle includes counterparty information "
            "or that extraction identified party-related clauses."
        )

    return result


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def normalize_name(name: str) -> str:
    """Normalize a party name for comparison.

    Steps:
      1. Unicode NFKD normalization
      2. Lowercase
      3. Strip common company suffixes
      4. Collapse whitespace and remove punctuation (except hyphens)
      5. Strip leading/trailing whitespace

    Args:
        name: The raw party name string.

    Returns:
        The normalized name.
    """
    # Unicode normalization
    text = unicodedata.normalize("NFKD", name)

    # Lowercase
    text = text.lower()

    # Remove punctuation except hyphens and spaces
    text = re.sub(r"[^\w\s\-]", " ", text)

    # Remove company suffixes
    words = text.split()
    stripped: list[str] = []
    for word in words:
        if word.replace(".", "").lower() not in {
            s.replace(".", "") for s in _COMPANY_SUFFIXES
        }:
            stripped.append(word)
    text = " ".join(stripped) if stripped else " ".join(words)

    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()

    return text


def _pre_suffix_normalize(name: str) -> str:
    """Normalize a party name *without* stripping company suffixes.

    This preserves the full name so that typos in suffixes (e.g.
    ``Corporaton`` vs ``Corporation``) still produce high fuzzy scores.

    Args:
        name: The raw party name string.

    Returns:
        The normalized name with suffixes preserved.
    """
    text = unicodedata.normalize("NFKD", name)
    text = text.lower()
    text = re.sub(r"[^\w\s\-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------------------------------------------------------------------------
# Fuzzy matching
# ---------------------------------------------------------------------------


def _resolve_party(
    party_name: str,
    vendor_records: list[dict[str, str]],
    context: Dict[str, Any],
    evidence_records: Sequence[Mapping[str, Any]],
) -> CounterpartyMatch:
    """Match a single party name against the vendor master.

    Uses a dual-comparison strategy:
      1. Compare suffix-stripped normalized names (handles suffix variation).
      2. Compare pre-suffix-stripped names (handles typos in suffixes).
    The higher score wins.

    Args:
        party_name: The raw extracted party name.
        vendor_records: Loaded vendor master rows.
        context: The context packet.
        evidence_records: Evidence records for building evidence pointers.

    Returns:
        A ``CounterpartyMatch`` with scores and flags.
    """
    normalized = normalize_name(party_name)
    pre_suffix = _pre_suffix_normalize(party_name)

    best_score: float = 0.0
    best_vendor: Optional[dict[str, str]] = None

    for vendor in vendor_records:
        vendor_name = vendor.get("vendor_name", "")
        normalized_vendor = normalize_name(vendor_name)
        pre_suffix_vendor = _pre_suffix_normalize(vendor_name)

        # Score both ways and keep the best
        score_stripped = fuzz.token_sort_ratio(normalized, normalized_vendor) / 100.0
        score_raw = fuzz.token_sort_ratio(pre_suffix, pre_suffix_vendor) / 100.0
        score = max(score_stripped, score_raw)

        if score > best_score:
            best_score = score
            best_vendor = vendor

    # Determine match status
    if best_score >= EXACT_THRESHOLD:
        match_status = MatchStatus.EXACT
    elif best_score >= STRONG_MATCH_THRESHOLD:
        match_status = MatchStatus.FUZZY
    elif best_score >= WEAK_MATCH_THRESHOLD:
        match_status = MatchStatus.WEAK
    else:
        match_status = MatchStatus.NO_MATCH

    # Build evidence pointer
    evidence_pointer = _build_evidence_pointer(context, evidence_records)

    # Determine flags
    manual_review = match_status in (MatchStatus.WEAK, MatchStatus.NO_MATCH)
    risk_flag = _assess_risk_flag(
        match_status=match_status,
        party_name=party_name,
        best_vendor=best_vendor,
        similarity_score=best_score,
    )

    matched_vendor_name: Optional[str] = None
    vendor_id: Optional[str] = None
    if best_vendor is not None and match_status != MatchStatus.NO_MATCH:
        matched_vendor_name = best_vendor.get("vendor_name")
        vendor_id = best_vendor.get("vendor_id")

    return CounterpartyMatch(
        original_party_name=party_name,
        normalized_party_name=normalized,
        matched_vendor_name=matched_vendor_name,
        vendor_id=vendor_id,
        similarity_score=round(best_score, 4),
        match_status=match_status,
        manual_review_required=manual_review,
        risk_flag=risk_flag,
        evidence_pointer=evidence_pointer,
    )


def _assess_risk_flag(
    match_status: MatchStatus,
    party_name: str,
    best_vendor: Optional[dict[str, str]],
    similarity_score: float,
) -> Optional[str]:
    """Determine if a risk flag should be raised for this match.

    Flags:
      - ``new_counterparty``: No reliable match in vendor master.
      - ``weak_match``: Similarity below 85% but above the no-match threshold.
      - ``high_risk_vendor``: Matched vendor has ``risk_tier == 'high'``.

    Args:
        match_status: The determined match status.
        party_name: The original party name.
        best_vendor: The best matching vendor record, if any.
        similarity_score: The similarity score.

    Returns:
        A risk flag string or ``None``.
    """
    if match_status == MatchStatus.NO_MATCH:
        return (
            f"new_counterparty: '{party_name}' has no reliable match in the vendor "
            f"master (best score: {similarity_score:.0%})."
        )

    if match_status == MatchStatus.WEAK:
        vendor_name = best_vendor.get("vendor_name", "unknown") if best_vendor else "unknown"
        return (
            f"weak_match: '{party_name}' matched '{vendor_name}' with only "
            f"{similarity_score:.0%} similarity (threshold: 85%)."
        )

    # Check high-risk vendor tier
    if best_vendor is not None:
        risk_tier = str(best_vendor.get("risk_tier", "")).lower().strip()
        if risk_tier == "high":
            vendor_name = best_vendor.get("vendor_name", "unknown")
            return (
                f"high_risk_vendor: '{vendor_name}' is classified as a "
                f"high-risk vendor in the vendor master."
            )

    return None


# ---------------------------------------------------------------------------
# Evidence helpers
# ---------------------------------------------------------------------------


def _extract_evidence_records(
    evidence_index: Optional[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Extract the evidence records list from the evidence index."""
    if evidence_index is None:
        return []
    records = evidence_index.get("records", [])
    if isinstance(records, list):
        return records
    return []


def _build_evidence_pointer(
    context: Dict[str, Any],
    evidence_records: Sequence[Mapping[str, Any]],
) -> Optional[EvidencePointer]:
    """Build an evidence pointer from context and evidence records.

    Uses the first evidence record if available, otherwise creates a
    pointer from the context packet's source file.
    """
    if evidence_records:
        first = evidence_records[0]
        return EvidencePointer(
            evidence_id=first.get("evidence_id"),
            document_id=first.get("document_id"),
            source_file=str(first.get("source_file", "unknown")),
            page_number=first.get("page_number"),
            excerpt=first.get("excerpt"),
        )

    source_file = str(context.get("contract_file", "unknown"))
    counterparty = context.get("counterparty", "")
    return EvidencePointer(
        source_file=source_file,
        excerpt=f"Counterparty: {counterparty}" if counterparty else None,
    )


# ---------------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------------


def _validate_run_dir(run_dir: str | Path) -> Path:
    """Return a valid run directory path or raise a clear error."""
    run_path = Path(run_dir).resolve()
    if not run_path.exists():
        raise CounterpartyAgentError(
            f"Run directory does not exist: {run_path}. "
            "Create it with create_run_folder before running counterparty resolution."
        )
    if not run_path.is_dir():
        raise CounterpartyAgentError(
            f"Run path is not a directory: {run_path}."
        )
    return run_path


def _write_model_json(path: Path, model: CounterpartyResolution) -> None:
    """Serialize a Pydantic model to a JSON file."""
    try:
        path.write_text(
            json.dumps(model.model_dump(mode="json"), indent=2, default=str),
            encoding="utf-8",
        )
    except Exception as exc:
        raise CounterpartyAgentError(
            f"Failed to write counterparty resolution artifact to {path}: {exc}"
        ) from exc
