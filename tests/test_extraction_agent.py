"""Tests for the Extraction Agent (US-07)."""

import json
import shutil
from pathlib import Path

import pymupdf
import pytest

from agents.extraction_agent import ExtractionAgentError, run_extraction
from agents.intake_agent import run_intake
from utils.bundle_loader import load_bundle
from utils.evidence_indexer import build_evidence_index
from utils.run_manager import create_run_folder


PROJECT_ROOT = Path(__file__).resolve().parent.parent
NDA_BUNDLE = PROJECT_ROOT / "data" / "bundles" / "clean_nda"


def _create_evidence_result(bundle_path: Path, tmp_path: Path) -> tuple[dict, Path, dict, dict]:
    """Load a bundle and run intake plus evidence indexing."""
    bundle_data = load_bundle(bundle_path)
    run_info = create_run_folder(
        bundle_path=str(bundle_path),
        runs_dir=tmp_path / "runs",
    )
    run_dir = Path(run_info["run_dir"])
    intake_result = run_intake(
        bundle_data=bundle_data,
        run_id=run_info["run_id"],
        run_dir=run_dir,
    )
    evidence_result = build_evidence_index(
        bundle_data=bundle_data,
        document_inventory=intake_result["document_inventory"],
        run_id=run_info["run_id"],
        run_dir=run_dir,
    )
    return bundle_data, run_dir, intake_result, evidence_result


class TestRunExtraction:
    """Test extracted_contract.json generation from clean PDFs."""

    def test_extracts_required_clause_categories_with_evidence(
        self, tmp_path: Path
    ) -> None:
        bundle_data, run_dir, intake_result, evidence_result = _create_evidence_result(
            NDA_BUNDLE,
            tmp_path,
        )

        result = run_extraction(
            bundle_data=bundle_data,
            document_inventory=intake_result["document_inventory"],
            evidence_index=evidence_result["evidence_index"],
            run_id=intake_result["document_inventory"]["run_id"],
            run_dir=run_dir,
        )

        extracted_path = run_dir / "extracted_contract.json"
        assert extracted_path.is_file()
        assert result["artifact_paths"]["extracted_contract"] == str(extracted_path)

        extracted_contract = json.loads(extracted_path.read_text(encoding="utf-8"))
        assert extracted_contract["source_file"] == "contract.pdf"
        assert extracted_contract["document_id"] == "DOC-002"
        assert extracted_contract["warnings"] == []

        clause_types = {
            clause["clause_type"] for clause in extracted_contract["clauses"]
        }
        assert clause_types == {
            "parties",
            "effective_date",
            "termination",
            "payment_terms",
            "liability_cap",
            "indemnity",
            "governing_law",
            "auto_renewal",
            "data_protection",
            "confidentiality",
        }

        for clause in extracted_contract["clauses"]:
            assert clause["text"]
            assert 0.0 <= clause["confidence"] <= 1.0
            assert clause["page_number"] == 1
            assert len(clause["bbox"]) == 4
            assert clause["bbox"][0] < clause["bbox"][2]
            assert clause["bbox"][1] < clause["bbox"][3]
            assert clause["evidence"]["evidence_id"] == "EV-001"
            assert clause["evidence"]["source_file"] == "contract.pdf"
            assert clause["evidence"]["excerpt"]

        audit_lines = (
            (run_dir / "audit_log.jsonl").read_text(encoding="utf-8").splitlines()
        )
        assert len(audit_lines) == 3
        extraction_event = json.loads(audit_lines[2])
        assert extraction_event["event"] == "extraction_completed"
        assert extraction_event["clause_count"] == 10

    def test_missing_primary_contract_raises_clear_error(self, tmp_path: Path) -> None:
        bundle_data, run_dir, intake_result, evidence_result = _create_evidence_result(
            NDA_BUNDLE,
            tmp_path,
        )
        intake_result["document_inventory"]["documents"] = []

        with pytest.raises(ExtractionAgentError, match="No primary contract document"):
            run_extraction(
                bundle_data=bundle_data,
                document_inventory=intake_result["document_inventory"],
                evidence_index=evidence_result["evidence_index"],
                run_id=intake_result["document_inventory"]["run_id"],
                run_dir=run_dir,
            )

    def test_blank_pdf_raises_clear_error(self, tmp_path: Path) -> None:
        bundle_copy = tmp_path / "blank_contract_bundle"
        shutil.copytree(NDA_BUNDLE, bundle_copy)

        blank_pdf = pymupdf.open()
        blank_pdf.new_page()
        blank_pdf.save(bundle_copy / "contract.pdf")
        blank_pdf.close()

        bundle_data, run_dir, intake_result, evidence_result = _create_evidence_result(
            bundle_copy,
            tmp_path,
        )

        with pytest.raises(ExtractionAgentError, match="No extractable text"):
            run_extraction(
                bundle_data=bundle_data,
                document_inventory=intake_result["document_inventory"],
                evidence_index=evidence_result["evidence_index"],
                run_id=intake_result["document_inventory"]["run_id"],
                run_dir=run_dir,
            )
