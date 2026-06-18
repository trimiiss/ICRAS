"""Artifact readers for validation."""

import json
from pathlib import Path
from typing import Any, Mapping, Optional

from schemas.finding import Finding
from agents.validation.errors import ValidationAgentError


def _read_extracted_contract(run_path: Optional[Path]) -> dict[str, Any] | None:
    """Read run-local extracted_contract.json if present."""
    if run_path is None:
        return None

    extracted_path = run_path / "extracted_contract.json"
    if not extracted_path.exists():
        return None

    try:
        with open(extracted_path, "r", encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationAgentError(
            f"Failed to read extracted contract artifact '{extracted_path}': {exc}"
        ) from exc

    if not isinstance(payload, Mapping):
        raise ValidationAgentError(
            f"Expected '{extracted_path}' to contain a JSON object."
        )
    return dict(payload)


def _read_extracted_contract_clauses(run_path: Optional[Path]) -> list[dict[str, Any]]:
    """Read extracted clauses from run-local extracted_contract.json if present."""
    payload = _read_extracted_contract(run_path)
    if payload is None:
        return []
    raw_clauses = payload.get("clauses", [])
    if not isinstance(raw_clauses, list):
        raise ValidationAgentError(
            "Expected 'extracted_contract.json' field 'clauses' to be a list."
        )
    return [dict(clause) for clause in raw_clauses if isinstance(clause, Mapping)]


def _read_existing_findings(run_path: Optional[Path]) -> list[Finding]:
    """Read existing validation findings so reruns update instead of discard."""
    if run_path is None:
        return []

    validation_path = run_path / "validation_findings.json"
    if not validation_path.exists():
        return []

    try:
        with open(validation_path, "r", encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationAgentError(
            f"Failed to read existing validation artifact '{validation_path}': {exc}"
        ) from exc

    if not isinstance(payload, Mapping):
        return []
    raw_findings = payload.get("findings", [])
    if not isinstance(raw_findings, list):
        return []

    findings: list[Finding] = []
    for index, raw_finding in enumerate(raw_findings):
        if not isinstance(raw_finding, Mapping):
            continue
        try:
            findings.append(Finding.model_validate(raw_finding))
        except Exception as exc:
            raise ValidationAgentError(
                "Existing validation_findings.json contains an invalid finding at "
                f"index {index}: {exc}"
            ) from exc
    return findings


