"""Tests for Agent H obligation register generation."""

import csv
import json
from pathlib import Path

from agents.orchestrator_agent import (
    OBLIGATION_CSV_COLUMNS,
    run_obligation_register,
)


def _run_dir(tmp_path: Path, run_id: str = "obligation-run") -> Path:
    """Create a run directory with audit files."""
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "audit_log.jsonl").touch()
    return run_dir


def _context(**overrides: object) -> dict:
    """Return a minimal context packet."""
    base = {
        "run_id": "obligation-run",
        "contract_file": "contract.pdf",
    }
    base.update(overrides)
    return base


def _evidence(page_number: int, text: str) -> dict:
    """Return a source evidence pointer."""
    return {
        "evidence_id": f"EV-{page_number:03d}",
        "document_id": "DOC-001",
        "source_file": "contract.pdf",
        "page_number": page_number,
        "clause_reference": str(page_number),
        "excerpt": text,
    }


def _clause(
    clause_type: str,
    text: str,
    page_number: int,
    **overrides: object,
) -> dict:
    """Return an extracted clause dictionary."""
    base = {
        "clause_type": clause_type,
        "title": clause_type.replace("_", " ").title(),
        "text": text,
        "page_number": page_number,
        "section_reference": str(page_number),
        "confidence": 0.95,
        "evidence": _evidence(page_number, text),
        "evidence_pointer": _evidence(page_number, text),
    }
    base.update(overrides)
    return base


def _read_rows(csv_path: Path) -> list[dict[str, str]]:
    """Read obligation CSV rows."""
    with csv_path.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def test_obligations_csv_uses_consistent_columns(tmp_path: Path) -> None:
    """CSV output should use the exact Agent H column contract."""
    run_dir = _run_dir(tmp_path)

    result = run_obligation_register(
        context=_context(),
        extracted_contract={
            "run_id": "obligation-run",
            "clauses": [
                _clause(
                    "payment_terms",
                    "Customer shall pay all valid invoices net 30.",
                    2,
                )
            ],
        },
        run_dir=run_dir,
    )

    csv_path = Path(result["artifact_paths"]["obligations"])
    assert csv_path == run_dir / "obligations.csv"
    with csv_path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        assert reader.fieldnames == list(OBLIGATION_CSV_COLUMNS)


def test_payment_obligation_includes_party_timing_and_evidence(
    tmp_path: Path,
) -> None:
    """Payment obligations should include owner, timing, and source pointer."""
    run_dir = _run_dir(tmp_path)

    run_obligation_register(
        context=_context(),
        extracted_contract={
            "run_id": "obligation-run",
            "clauses": [
                _clause(
                    "payment_terms",
                    "Customer shall pay all valid invoices net 30.",
                    2,
                )
            ],
        },
        run_dir=run_dir,
    )

    rows = _read_rows(run_dir / "obligations.csv")
    assert len(rows) == 1
    row = rows[0]
    assert row["obligation_type"] == "payment"
    assert row["responsible_party"] == "Customer"
    assert row["timing_trigger"].lower() == "net 30"
    assert row["source_page"] == "2"
    assert row["evidence_id"] == "EV-002"
    assert json.loads(row["evidence_pointer"])["page_number"] == 2


def test_core_clause_obligations_are_extracted(tmp_path: Path) -> None:
    """Confidentiality, compliance, and indemnity clauses become obligations."""
    run_dir = _run_dir(tmp_path)

    run_obligation_register(
        context=_context(),
        extracted_contract={
            "run_id": "obligation-run",
            "clauses": [
                _clause(
                    "confidentiality",
                    "Receiving party shall protect Confidential Information.",
                    3,
                ),
                _clause(
                    "data_protection",
                    "Supplier must comply with applicable privacy laws.",
                    4,
                ),
                _clause(
                    "indemnity",
                    "Vendor shall indemnify Customer for third-party claims.",
                    5,
                ),
            ],
        },
        run_dir=run_dir,
    )

    rows = _read_rows(run_dir / "obligations.csv")
    obligation_types = {row["obligation_type"] for row in rows}
    assert obligation_types == {"confidentiality", "compliance", "indemnity"}
    assert {row["responsible_party"] for row in rows} >= {
        "Receiving party",
        "Supplier",
        "Customer",
    }


def test_recurring_obligations_are_marked(tmp_path: Path) -> None:
    """Recurring renewal and invoice obligations should be marked."""
    run_dir = _run_dir(tmp_path)

    run_obligation_register(
        context=_context(),
        extracted_contract={
            "run_id": "obligation-run",
            "clauses": [
                _clause(
                    "auto_renewal",
                    "This Agreement will automatically renew each year.",
                    6,
                ),
                _clause(
                    "payment_terms",
                    "Customer shall pay invoices monthly within 30 days.",
                    7,
                ),
            ],
        },
        run_dir=run_dir,
    )

    rows = _read_rows(run_dir / "obligations.csv")
    assert {row["is_recurring"] for row in rows} == {"true"}
    frequencies = {row["recurrence_frequency"] for row in rows}
    assert "annually" in frequencies
    assert "monthly" in frequencies


def test_header_only_csv_is_generated_when_no_obligations(tmp_path: Path) -> None:
    """Agent H should still write a header-only CSV with no obligation rows."""
    run_dir = _run_dir(tmp_path)

    result = run_obligation_register(
        context=_context(),
        extracted_contract={
            "run_id": "obligation-run",
            "clauses": [
                _clause(
                    "governing_law",
                    "This Agreement is governed by Delaware law.",
                    8,
                )
            ],
        },
        run_dir=run_dir,
    )

    csv_path = Path(result["artifact_paths"]["obligations"])
    with csv_path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        assert reader.fieldnames == list(OBLIGATION_CSV_COLUMNS)
        assert list(reader) == []
    assert result["obligations"] == []
