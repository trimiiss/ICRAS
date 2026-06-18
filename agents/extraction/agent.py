"""Clause extraction public entry point."""

from pathlib import Path
from typing import Any, Mapping

from schemas.extracted_clause import ExtractedContract, ExtractionWarning
from agents.extraction.candidates import split_into_candidates
from agents.extraction.clauses import extract_required_clauses
from agents.extraction.errors import ExtractionAgentError
from agents.extraction.fallback import (
    fallback_reason as get_fallback_reason,
    find_fallback_fixture,
    load_fallback_clauses,
)
from agents.extraction.helpers import get_primary_document, require_str
from agents.extraction.pdf_text import extract_pdf_text
from utils.artifacts import validate_run_dir, write_model_json
from utils.run_manager import append_audit_event


def run_extraction(
    bundle_data: Mapping[str, Any],
    document_inventory: Mapping[str, Any],
    evidence_index: Mapping[str, Any],
    run_id: str,
    run_dir: str | Path,
) -> dict[str, Any]:
    """Extract structured clauses from the primary contract PDF.

    Args:
        bundle_data: Validated bundle data from the bundle loader.
        document_inventory: Document inventory produced by the intake agent.
        evidence_index: Evidence index produced for the primary contract.
        run_id: Unique run identifier.
        run_dir: Directory where run artifacts must be written.

    Returns:
        A dictionary containing the extracted contract and artifact path.

    Raises:
        ExtractionAgentError: If the primary contract cannot be extracted.
    """
    run_path = validate_run_dir(
        run_dir,
        error_type=ExtractionAgentError,
        before_action="running extraction",
    )
    primary_document = get_primary_document(document_inventory)
    contract_path = Path(require_str(bundle_data, "contract_path")).resolve()

    if not contract_path.is_file():
        raise ExtractionAgentError(
            f"Primary contract file does not exist: {contract_path}. "
            "Validate the bundle before running extraction."
        )

    pdf_text_result = extract_pdf_text(contract_path)
    page_texts = pdf_text_result.page_texts
    warnings: list[ExtractionWarning] = []
    if pdf_text_result.ocr_metadata is not None:
        for page in pdf_text_result.ocr_metadata.pages:
            if not page.warning:
                continue
            warnings.append(
                ExtractionWarning(
                    warning_id=f"WARN-{len(warnings) + 1:03d}",
                    message=str(page.warning),
                )
            )
        if pdf_text_result.ocr_metadata.used:
            append_audit_event(
                run_path,
                {
                    "event": "ocr_used",
                    "agent": "extraction_agent",
                    "message": "OCR was used for pages without extractable text.",
                    "engine": pdf_text_result.ocr_metadata.engine,
                    "pages_processed": pdf_text_result.ocr_metadata.pages_processed,
                    "average_confidence": (
                        pdf_text_result.ocr_metadata.average_confidence
                    ),
                    "manual_review_required": (
                        pdf_text_result.ocr_metadata.manual_review_required
                    ),
                },
            )
        else:
            append_audit_event(
                run_path,
                {
                    "event": "ocr_unavailable",
                    "agent": "extraction_agent",
                    "message": "OCR was attempted but no OCR text was available.",
                    "engine": pdf_text_result.ocr_metadata.engine,
                    "pages_processed": pdf_text_result.ocr_metadata.pages_processed,
                    "reason": pdf_text_result.ocr_metadata.reason,
                },
            )
    if page_texts:
        candidates = split_into_candidates(page_texts)
        clauses, extraction_warnings = extract_required_clauses(
            candidates=candidates,
            evidence_index=evidence_index,
            primary_document=primary_document,
        )
        warnings.extend(extraction_warnings)
    else:
        clauses = []
        warnings.append(
            ExtractionWarning(
                warning_id=f"WARN-{len(warnings) + 1:03d}",
                message=(
                    f"No extractable text found in primary contract PDF: "
                    f"{contract_path.name}."
                ),
            )
        )
    fallback_reason = get_fallback_reason(clauses)
    fallback_assisted = fallback_reason is not None
    fallback_fixture_path: Path | None = None

    if fallback_reason is not None:
        fallback_fixture_path = find_fallback_fixture(bundle_data)
        if fallback_fixture_path is not None:
            clauses = load_fallback_clauses(
                fixture_path=fallback_fixture_path,
                evidence_index=evidence_index,
                primary_document=primary_document,
            )
            warnings.append(
                ExtractionWarning(
                    warning_id=f"WARN-{len(warnings) + 1:03d}",
                    message=(
                        "Synthetic extraction fallback was used because "
                        f"{fallback_reason}"
                    ),
                )
            )
            append_audit_event(
                run_path,
                {
                    "event": "extraction_fallback_used",
                    "agent": "extraction_agent",
                    "message": "Synthetic extraction fallback was used.",
                    "reason": fallback_reason,
                    "fixture": str(fallback_fixture_path),
                },
            )
        else:
            warnings.append(
                ExtractionWarning(
                    warning_id=f"WARN-{len(warnings) + 1:03d}",
                    message=(
                        "Extraction quality was low, but no matching synthetic "
                        "fallback fixture was available."
                    ),
                )
            )
            fallback_assisted = False
            fallback_reason = None

    extracted_contract = ExtractedContract(
        run_id=run_id,
        document_id=str(primary_document["document_id"]),
        source_file=str(primary_document["relative_path"]),
        fallback_assisted=fallback_assisted,
        fallback_reason=fallback_reason,
        text_extraction_method=pdf_text_result.text_extraction_method,
        ocr_metadata=pdf_text_result.ocr_metadata,
        clauses=clauses,
        warnings=warnings,
    )

    output_path = run_path / "extracted_contract.json"
    write_model_json(output_path, extracted_contract)

    low_confidence_count = sum(1 for clause in clauses if clause.manual_review_required)
    append_audit_event(
        run_path,
        {
            "event": "extraction_completed",
            "agent": "extraction_agent",
            "message": "Extraction Agent created structured clause artifacts.",
            "artifacts": [output_path.name],
            "clause_count": len(extracted_contract.clauses),
            "warning_count": len(extracted_contract.warnings),
            "low_confidence_count": low_confidence_count,
            "fallback_assisted": fallback_assisted,
            "fallback_reason": fallback_reason,
            "text_extraction_method": pdf_text_result.text_extraction_method,
            "ocr_used": bool(
                pdf_text_result.ocr_metadata
                and pdf_text_result.ocr_metadata.used
            ),
            "ocr_average_confidence": (
                pdf_text_result.ocr_metadata.average_confidence
                if pdf_text_result.ocr_metadata
                else None
            ),
        },
    )

    return {
        "extracted_contract": extracted_contract.model_dump(mode="json"),
        "artifact_paths": {
            "extracted_contract": str(output_path),
        },
    }


__all__ = [
    "ExtractionAgentError",
    "run_extraction",
]
