"""Evidence indexing utilities for source-backed contract review."""

import json
from pathlib import Path
from typing import Any, Dict, Mapping

import pymupdf

from schemas.evidence_index import EvidenceIndex, EvidenceRecord, EvidenceWarning
from utils.run_manager import append_audit_event


class EvidenceIndexError(Exception):
    """Raised when evidence indexing cannot produce the required artifact."""


def build_evidence_index(
    bundle_data: Dict[str, Any],
    document_inventory: Mapping[str, Any],
    run_id: str,
    run_dir: str | Path,
) -> Dict[str, Any]:
    """Build page-level evidence records for the primary contract PDF.

    Args:
        bundle_data: Validated bundle data from the bundle loader.
        document_inventory: Document inventory produced by the intake agent.
        run_id: Unique run identifier.
        run_dir: Directory where run artifacts must be written.

    Returns:
        A dictionary containing the evidence index and artifact path.

    Raises:
        EvidenceIndexError: If the primary contract cannot be indexed.
    """
    run_path = _validate_run_dir(run_dir)
    primary_document = _get_primary_document(document_inventory)
    contract_path = Path(_require_str(bundle_data, "contract_path")).resolve()

    if not contract_path.is_file():
        raise EvidenceIndexError(
            f"Primary contract file does not exist: {contract_path}. "
            "Validate the bundle before building the evidence index."
        )

    records, warnings = _extract_page_records(
        contract_path=contract_path,
        document_id=str(primary_document["document_id"]),
        source_file=str(primary_document["relative_path"]),
    )

    evidence_index = EvidenceIndex(
        run_id=run_id,
        document_id=str(primary_document["document_id"]),
        source_file=str(primary_document["relative_path"]),
        records=records,
        warnings=warnings,
    )

    output_path = run_path / "evidence_index.json"
    _write_model_json(output_path, evidence_index)

    append_audit_event(
        run_path,
        {
            "event": "evidence_index_completed",
            "agent": "evidence_indexer",
            "message": "Evidence indexer created page-level source evidence.",
            "artifacts": [output_path.name],
            "record_count": len(evidence_index.records),
            "warning_count": len(evidence_index.warnings),
        },
    )

    return {
        "evidence_index": evidence_index.model_dump(mode="json"),
        "artifact_paths": {
            "evidence_index": str(output_path),
        },
    }


def _validate_run_dir(run_dir: str | Path) -> Path:
    """Return a valid run directory path or raise a clear evidence error."""
    run_path = Path(run_dir).resolve()
    if not run_path.exists():
        raise EvidenceIndexError(
            f"Run directory does not exist: {run_path}. "
            "Create it with create_run_folder before indexing evidence."
        )
    if not run_path.is_dir():
        raise EvidenceIndexError(f"Run path is not a directory: {run_path}")
    return run_path


def _require_str(data: Mapping[str, Any], key: str) -> str:
    """Read a string value with a developer-friendly evidence error."""
    value = data.get(key)
    if not isinstance(value, str):
        raise EvidenceIndexError(
            f"Expected bundle_data['{key}'] to be a string. "
            "Load the bundle with utils.bundle_loader.load_bundle first."
        )
    return value


def _get_primary_document(document_inventory: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the primary contract entry from document_inventory.json data."""
    documents = document_inventory.get("documents")
    if not isinstance(documents, list):
        raise EvidenceIndexError(
            "Expected document_inventory['documents'] to be a list. "
            "Run the Intake Agent before indexing evidence."
        )

    for document in documents:
        if isinstance(document, Mapping) and document.get("is_primary") is True:
            if document.get("document_type") != "contract":
                raise EvidenceIndexError(
                    "Primary document is not classified as a contract. "
                    "Check document_inventory.json before extraction."
                )
            return document

    raise EvidenceIndexError(
        "No primary contract document found in document_inventory.json. "
        "Run the Intake Agent and confirm contract.pdf is identified."
    )


def _extract_page_records(
    contract_path: Path,
    document_id: str,
    source_file: str,
) -> tuple[list[EvidenceRecord], list[EvidenceWarning]]:
    """Extract page text and convert it into evidence records and warnings."""
    records: list[EvidenceRecord] = []
    warnings: list[EvidenceWarning] = []

    try:
        pdf = pymupdf.open(contract_path)
    except Exception as exc:
        raise EvidenceIndexError(
            f"Failed to open primary contract PDF '{contract_path.name}': {exc}"
        ) from exc

    try:
        if pdf.page_count == 0:
            warnings.append(
                EvidenceWarning(
                    warning_id="WARN-001",
                    document_id=document_id,
                    source_file=source_file,
                    message="Primary contract PDF has no pages.",
                )
            )
            return records, warnings

        for index, page in enumerate(pdf, start=1):
            text = _normalize_text(page.get_text("text"))
            if not text:
                warnings.append(
                    EvidenceWarning(
                        warning_id=f"WARN-{len(warnings) + 1:03d}",
                        document_id=document_id,
                        source_file=source_file,
                        page_number=index,
                        message=(
                            "No extractable text found on page "
                            f"{index} of {source_file}."
                        ),
                    )
                )
                continue

            excerpt = _make_excerpt(text)
            records.append(
                EvidenceRecord(
                    evidence_id=f"EV-{len(records) + 1:03d}",
                    document_id=document_id,
                    source_file=source_file,
                    page_number=index,
                    char_start=0,
                    char_end=len(text),
                    excerpt=excerpt,
                )
            )
    finally:
        pdf.close()

    return records, warnings


def _normalize_text(text: str) -> str:
    """Normalize whitespace while preserving readable text order."""
    return " ".join(text.split())


def _make_excerpt(text: str, max_chars: int = 500) -> str:
    """Return a compact evidence snippet from page text."""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _write_model_json(path: Path, model: EvidenceIndex) -> None:
    """Write an EvidenceIndex model as deterministic, formatted JSON."""
    with open(path, "w", encoding="utf-8") as file:
        json.dump(model.model_dump(mode="json"), file, indent=2, ensure_ascii=False)
        file.write("\n")
