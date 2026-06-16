"""Agent H - orchestration helpers and obligation register generation.

The full lead-orchestrator flow remains a future story. US-15 adds the
obligation register owned by Agent H.
"""

import csv
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from schemas.common import EvidencePointer
from schemas.extracted_clause import ExtractedClause
from schemas.obligation_result import ObligationRecord, ObligationRegisterResult
from utils.run_manager import append_audit_event


class ObligationRegisterError(Exception):
    """Raised when Agent H cannot produce obligations.csv."""


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

DATE_CANDIDATE_PATTERNS: tuple[str, ...] = (
    r"\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b",
    r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b",
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*"
    r"\s+\d{1,2},\s+\d{4}\b",
    r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*"
    r"\s+\d{4}\b",
)

TIMING_PATTERNS: tuple[str, ...] = (
    r"\bwithin\s+\d+\s+(?:business\s+)?days?\b",
    r"\b\d+\s+days?\s+(?:written\s+)?notice\b",
    r"\bnet[\s-]?\d+\b",
    r"\bafter\s+\d+\s+(?:business\s+)?days?\b",
    r"\bprior\s+to\s+expiration\b",
    r"\bupon\s+[a-z ]{3,60}\b",
)


def run_obligation_register(
    context: Dict[str, Any],
    extracted_contract: Dict[str, Any],
    run_dir: str | Path,
) -> Dict[str, Any]:
    """Generate ``obligations.csv`` from extracted contract clauses.

    Args:
        context: Context packet data from Agent A.
        extracted_contract: Extracted contract data from Agent B.
        run_dir: Run directory where ``obligations.csv`` must be written.

    Returns:
        A dictionary containing the obligation register and artifact path.

    Raises:
        ObligationRegisterError: If inputs are malformed or CSV output fails.
    """
    run_path = _validate_run_dir(run_dir)
    clauses = _coerce_clauses(extracted_contract.get("clauses", []))
    run_id = str(context.get("run_id") or extracted_contract.get("run_id") or "unknown-run")

    obligations = _extract_obligations(
        context=context,
        clauses=clauses,
    )
    register = ObligationRegisterResult(run_id=run_id, obligations=obligations)

    output_path = run_path / "obligations.csv"
    _write_obligations_csv(output_path, register)
    append_audit_event(
        run_path,
        {
            "event": "obligation_register_completed",
            "agent": "orchestrator_agent",
            "message": "Agent H generated the obligation register.",
            "artifacts": [output_path.name],
            "obligation_count": len(register.obligations),
        },
    )

    result = register.model_dump(mode="json")
    return {
        "obligation_register": result,
        "obligations": result["obligations"],
        "artifact_paths": {"obligations": str(output_path)},
    }


def run_pipeline(bundle_path: str) -> Dict[str, Any]:
    """Execute the full contract review pipeline.

    Args:
        bundle_path: Path to the contract bundle folder.

    Returns:
        A dictionary representing the final approval packet.

    .. note:: Placeholder - full orchestrator logic will be implemented later.
    """
    raise NotImplementedError(
        "Orchestrator agent logic will be implemented in a later story."
    )


def _extract_obligations(
    context: Mapping[str, Any],
    clauses: Sequence[ExtractedClause],
) -> list[ObligationRecord]:
    """Extract obligation records from clauses."""
    obligations: list[ObligationRecord] = []
    for clause in clauses:
        obligation_type = _obligation_type_for_clause(clause)
        if obligation_type is None:
            continue
        if not _has_obligation_signal(clause.text):
            continue
        if _is_negative_renewal_clause(clause):
            continue

        evidence = _clause_evidence(context, clause)
        obligation = ObligationRecord(
            obligation_id=f"OBL-{len(obligations) + 1:03d}",
            obligation_type=obligation_type,
            responsible_party=_extract_responsible_party(clause.text),
            obligation_summary=_summarize_obligation(clause.text),
            due_date=_extract_due_date(clause.text),
            timing_trigger=_extract_timing_trigger(clause.text),
            is_recurring=_is_recurring(clause.text),
            recurrence_frequency=_recurrence_frequency(clause.text, obligation_type),
            source_clause_text=_truncate(clause.text),
            source_file=evidence.source_file,
            source_page=evidence.page_number,
            evidence_id=evidence.evidence_id,
            document_id=evidence.document_id,
            clause_reference=evidence.clause_reference,
            evidence_pointer=evidence,
        )
        obligations.append(obligation)

    return obligations


def _obligation_type_for_clause(clause: ExtractedClause) -> Optional[str]:
    """Return the obligation type for a clause, when supported."""
    candidates = (
        _normalize_key(clause.clause_type),
        _normalize_key(clause.title),
    )
    for candidate in candidates:
        for alias, obligation_type in OBLIGATION_TYPE_BY_CLAUSE.items():
            normalized_alias = _normalize_key(alias)
            if candidate == normalized_alias or normalized_alias in candidate:
                return obligation_type
    return None


def _has_obligation_signal(text: str) -> bool:
    """Return whether text contains deterministic obligation language."""
    normalized_text = text.lower()
    return any(re.search(rf"\b{re.escape(cue)}\b", normalized_text) for cue in OBLIGATION_CUES)


def _is_negative_renewal_clause(clause: ExtractedClause) -> bool:
    """Skip renewal clauses that explicitly say there is no renewal."""
    if _obligation_type_for_clause(clause) != "renewal":
        return False
    return bool(
        re.search(
            r"\b(?:does not|will not|shall not|must not)\s+auto[- ]?renew\b",
            clause.text,
            re.IGNORECASE,
        )
    )


def _extract_responsible_party(text: str) -> str:
    """Extract the responsible party from obligation text."""
    for party in RESPONSIBLE_PARTIES:
        if re.search(rf"\b{re.escape(party)}\b", text, re.IGNORECASE):
            return party
    return "Unspecified"


def _summarize_obligation(text: str) -> str:
    """Return a compact sentence-level obligation summary."""
    normalized = " ".join(text.split())
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", normalized)
        if sentence.strip()
    ]
    for sentence in sentences:
        if _has_obligation_signal(sentence):
            return _truncate(sentence, max_chars=240)
    return _truncate(normalized, max_chars=240)


def _extract_due_date(text: str) -> Optional[str]:
    """Extract an absolute due date as ISO 8601 when present."""
    for pattern in DATE_CANDIDATE_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match is None:
            continue
        normalized = _normalize_date(match.group(0))
        if normalized is not None:
            return normalized
    return None


def _extract_timing_trigger(text: str) -> Optional[str]:
    """Extract relative timing language for obligation tracking."""
    normalized_text = " ".join(text.split())
    for pattern in TIMING_PATTERNS:
        match = re.search(pattern, normalized_text, re.IGNORECASE)
        if match is not None:
            return match.group(0)
    return None


def _is_recurring(text: str) -> bool:
    """Return whether obligation text indicates recurrence."""
    return _recurrence_frequency(text, obligation_type=None) is not None


def _recurrence_frequency(
    text: str,
    obligation_type: Optional[str],
) -> Optional[str]:
    """Return a recurring cadence when detectable."""
    normalized = text.lower()
    if re.search(r"\bmonthly\b|\beach month\b", normalized):
        return "monthly"
    if re.search(r"\bannually\b|\beach year\b|\byearly\b", normalized):
        return "annually"
    if "automatic renewal" in normalized or "auto-renew" in normalized:
        return "annually"
    if obligation_type == "payment" and re.search(r"\binvoice|invoices\b", normalized):
        return "per invoice"
    return None


def _coerce_clauses(raw_clauses: Any) -> list[ExtractedClause]:
    """Convert extracted clause dictionaries to Pydantic models."""
    if not isinstance(raw_clauses, list):
        raise ObligationRegisterError(
            "Expected extracted_contract['clauses'] to be a list."
        )

    clauses: list[ExtractedClause] = []
    for index, raw_clause in enumerate(raw_clauses, start=1):
        if not isinstance(raw_clause, Mapping):
            raise ObligationRegisterError(
                f"Expected extracted_contract['clauses'][{index - 1}] to be a mapping."
            )

        clause_data = dict(raw_clause)
        clause_type = str(clause_data.get("clause_type") or f"clause_{index}")
        text = str(clause_data.get("text") or clause_data.get("clause_text") or "")
        if not text.strip():
            raise ObligationRegisterError(
                f"extracted_contract['clauses'][{index - 1}] is missing clause text."
            )

        clause_data.setdefault("clause_id", f"CLAUSE-{index:03d}")
        clause_data.setdefault("clause_type", clause_type)
        clause_data.setdefault("title", clause_type.replace("_", " ").title())
        clause_data.setdefault("text", text)
        clause_data.setdefault("clause_text", text)
        clause_data.setdefault("confidence", 1.0)
        clause_data.setdefault("confidence_score", clause_data["confidence"])
        if "page_numbers" not in clause_data and clause_data.get("page_number") is not None:
            clause_data["page_numbers"] = [clause_data["page_number"]]
        clause_data.setdefault(
            "evidence",
            EvidencePointer(
                source_file="unknown",
                page_number=_optional_int(clause_data.get("page_number")),
                clause_reference=_optional_str(clause_data.get("section_reference")),
                excerpt=_truncate(text),
            ).model_dump(mode="json"),
        )
        clause_data.setdefault("evidence_pointer", clause_data["evidence"])
        clause_data.setdefault(
            "manual_review_required",
            float(clause_data["confidence"]) < 0.75,
        )

        try:
            clauses.append(ExtractedClause.model_validate(clause_data))
        except Exception as exc:
            raise ObligationRegisterError(
                f"extracted_contract['clauses'][{index - 1}] is invalid: {exc}"
            ) from exc

    return clauses


def _validate_run_dir(run_dir: str | Path) -> Path:
    """Return a valid run directory path or raise a clear error."""
    run_path = Path(run_dir).resolve()
    if not run_path.exists():
        raise ObligationRegisterError(
            f"Run directory does not exist: {run_path}. "
            "Create it with create_run_folder before obligation extraction."
        )
    if not run_path.is_dir():
        raise ObligationRegisterError(f"Run path is not a directory: {run_path}")
    return run_path


def _write_obligations_csv(path: Path, register: ObligationRegisterResult) -> None:
    """Write the obligation register with deterministic columns."""
    try:
        with open(path, "w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=OBLIGATION_CSV_COLUMNS)
            writer.writeheader()
            for obligation in register.obligations:
                writer.writerow(_obligation_to_csv_row(obligation))
    except OSError as exc:
        raise ObligationRegisterError(
            f"Failed to write obligations artifact '{path}': {exc}"
        ) from exc


def _obligation_to_csv_row(obligation: ObligationRecord) -> dict[str, str]:
    """Convert an obligation model into a CSV row."""
    row = obligation.model_dump(mode="json")
    row["is_recurring"] = "true" if obligation.is_recurring else "false"
    row["source_page"] = "" if obligation.source_page is None else str(obligation.source_page)
    row["evidence_pointer"] = json.dumps(
        row["evidence_pointer"],
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        column: "" if row.get(column) is None else str(row.get(column, ""))
        for column in OBLIGATION_CSV_COLUMNS
    }


def _clause_evidence(
    context: Mapping[str, Any],
    clause: ExtractedClause,
) -> EvidencePointer:
    """Build a source pointer from an extracted clause."""
    return EvidencePointer(
        evidence_id=clause.evidence.evidence_id,
        document_id=clause.evidence.document_id,
        source_file=str(context.get("contract_file") or clause.evidence.source_file),
        page_number=clause.page_number or clause.evidence.page_number,
        clause_reference=clause.section_reference or clause.evidence.clause_reference,
        excerpt=_truncate(clause.text),
    )


def _normalize_date(raw_value: Any) -> Optional[str]:
    """Normalize a date-like value to an ISO 8601 date string."""
    if isinstance(raw_value, datetime):
        return raw_value.date().isoformat()
    if isinstance(raw_value, date):
        return raw_value.isoformat()
    if not isinstance(raw_value, str):
        return None

    value = raw_value.strip()
    if not value:
        return None

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass

    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(value, date_format).date().isoformat()
        except ValueError:
            continue
    return None


def _normalize_key(value: str) -> str:
    """Normalize free-form text for alias matching."""
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _optional_str(value: Any) -> Optional[str]:
    """Return value as a string when non-empty, else None."""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return str(value)


def _optional_int(value: Any) -> Optional[int]:
    """Return value as an int when possible, else None."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _truncate(value: str, max_chars: int = 500) -> str:
    """Return a compact source snippet."""
    normalized = " ".join(value.split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."
