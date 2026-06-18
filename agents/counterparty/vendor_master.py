"""Vendor master CSV loading."""

import csv
from pathlib import Path

from agents.counterparty.errors import CounterpartyAgentError


def load_vendor_master(path: str | Path) -> list[dict[str, str]]:
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

    first_row = rows[0]
    required_cols = {"vendor_id", "vendor_name"}
    missing_cols = required_cols - set(first_row.keys())
    if missing_cols:
        raise CounterpartyAgentError(
            f"Vendor master CSV is missing required columns: {sorted(missing_cols)}. "
            f"Found columns: {sorted(first_row.keys())}."
        )

    return rows
