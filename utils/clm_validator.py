"""Validate a persisted CLM posting payload against the ICRAS schema.

Usage (CLI):
    python -m utils.clm_validator <path/to/posting_payload.json>

Usage (library):
    from utils.clm_validator import validate_posting_payload
    result = validate_posting_payload(payload_dict)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from pydantic import ValidationError

from schemas.posting_payload import PostingPayload


class CLMValidationResult:
    """Result of a CLM posting payload validation pass."""

    def __init__(
        self,
        *,
        valid: bool,
        errors: List[Dict[str, Any]],
        payload: Dict[str, Any] | None = None,
    ) -> None:
        self.valid = valid
        self.errors = errors
        self.payload = payload

    def __repr__(self) -> str:  # pragma: no cover
        if self.valid:
            return "CLMValidationResult(valid=True)"
        return f"CLMValidationResult(valid=False, errors={len(self.errors)})"


def validate_posting_payload(data: Dict[str, Any]) -> CLMValidationResult:
    """Validate a posting-payload dictionary against the CLM schema.

    Args:
        data: The raw dictionary parsed from a ``posting_payload.json`` file.

    Returns:
        A :class:`CLMValidationResult` that is ``valid=True`` when all required
        fields pass Pydantic validation, or ``valid=False`` with a structured
        list of field-level errors when validation fails.
    """
    try:
        payload = PostingPayload.model_validate(data)
    except ValidationError as exc:
        errors = _format_validation_errors(exc)
        return CLMValidationResult(valid=False, errors=errors)

    return CLMValidationResult(
        valid=True,
        errors=[],
        payload=payload.model_dump(mode="json"),
    )


def validate_posting_payload_file(path: Path | str) -> CLMValidationResult:
    """Load and validate a ``posting_payload.json`` file from disk.

    Args:
        path: Filesystem path to the JSON file.

    Returns:
        A :class:`CLMValidationResult` as returned by
        :func:`validate_posting_payload`.

    Raises:
        FileNotFoundError: If *path* does not exist.
        ValueError: If the file cannot be parsed as JSON.
    """
    resolved = Path(path)
    if not resolved.is_file():
        raise FileNotFoundError(
            f"Posting payload file not found: {resolved}"
        )
    try:
        data = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Failed to parse posting payload JSON at {resolved}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise ValueError(
            f"Posting payload must be a JSON object, got {type(data).__name__}"
        )
    return validate_posting_payload(data)


def _format_validation_errors(exc: ValidationError) -> List[Dict[str, Any]]:
    """Convert Pydantic v2 errors into a presenter-friendly list."""
    errors: List[Dict[str, Any]] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ()))
        errors.append(
            {
                "field": location,
                "message": error.get("msg", ""),
                "type": error.get("type", ""),
                "input": error.get("input"),
            }
        )
    return errors


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _cli_main(argv: List[str]) -> int:
    """Validate a posting_payload.json file and print a human-readable report."""
    if len(argv) != 2:
        print(
            "Usage: python -m utils.clm_validator <path/to/posting_payload.json>",
            file=sys.stderr,
        )
        return 2

    target = Path(argv[1])
    try:
        result = validate_posting_payload_file(target)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if result.valid:
        print(f"PASS  {target.name}: CLM schema validation PASSED")
        _print_summary(result.payload or {})
        return 0

    print(f"FAIL  {target.name}: CLM schema validation FAILED ({len(result.errors)} error(s))")
    for idx, error in enumerate(result.errors, start=1):
        print(f"  {idx}. [{error['field']}] {error['message']}  (type={error['type']})")
    return 1


def _print_summary(payload: Dict[str, Any]) -> None:
    """Print key fields from a validated payload."""
    contract = payload.get("contract") or {}
    decision = payload.get("decision") or {}
    risk = payload.get("risk") or {}
    obligations = payload.get("obligations") or []
    findings = risk.get("findings") or []

    print(f"   run_id          : {payload.get('run_id', 'n/a')}")
    print(f"   payload_version : {payload.get('payload_version', 'n/a')}")
    print(f"   contract_id     : {contract.get('contract_id', 'n/a')}")
    print(f"   bundle_name     : {contract.get('bundle_name', 'n/a')}")
    print(f"   decision_status : {decision.get('status', 'n/a')}")
    print(f"   overall_severity: {risk.get('overall_severity', 'n/a')}")
    print(f"   findings        : {len(findings)}")
    print(f"   obligations     : {len(obligations)}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli_main(sys.argv))
