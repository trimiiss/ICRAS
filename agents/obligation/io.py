"""Artifact writing for the obligation register."""

import csv
import json
from pathlib import Path

from schemas.obligation_result import ObligationRecord, ObligationRegisterResult

from .constants import OBLIGATION_CSV_COLUMNS
from .errors import ObligationRegisterError


def write_obligations_csv(path: Path, register: ObligationRegisterResult) -> None:
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
