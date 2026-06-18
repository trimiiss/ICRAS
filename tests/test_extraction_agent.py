"""Tests for clause extraction."""

import json
import shutil
from pathlib import Path

import pymupdf
import pytest

import agents.extraction.pdf_text as pdf_text
from agents.extraction import ExtractionAgentError, run_extraction
from agents.extraction.models import TextLine
from agents.intake import run_intake
from schemas.extracted_clause import OcrPageResult
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


def _write_text_pdf(pdf_path: Path, pages: list[list[str]]) -> None:
    """Create a born-digital PDF fixture with one or more text pages."""
    pdf = pymupdf.open()
    try:
        for lines in pages:
            page = pdf.new_page()
            y = 72
            for line in lines:
                page.insert_text((72, y), line, fontsize=10)
                y += 18
        pdf.save(pdf_path)
    finally:
        pdf.close()


def _mock_ocr_unavailable(
    page: pymupdf.Page,
    page_number: int,
) -> tuple[list[TextLine], OcrPageResult]:
    """Return deterministic unavailable OCR output for blank-PDF tests."""
    return [], OcrPageResult(
        page_number=page_number,
        used=False,
        confidence=None,
        text_length=0,
        warning=f"OCR unavailable for page {page_number}.",
    )


def _mock_ocr_lines(
    page: pymupdf.Page,
    page_number: int,
) -> tuple[list[TextLine], OcrPageResult]:
    """Return deterministic OCR text for scanned-PDF tests."""
    lines = [
        TextLine(
            page_number=page_number,
            text=text,
            bbox=[72.0, float(72 + index * 18), 500.0, float(84 + index * 18)],
            char_start=index * 100,
            char_end=index * 100 + len(text),
        )
        for index, text in enumerate(
            [
                "1 Parties",
                "This Agreement is between Genpact LLC and Acme Corporation.",
                "2 Data Protection",
                (
                    "Each party shall comply with GDPR data processing terms, "
                    "cross-border transfer controls, and data subject rights."
                ),
            ]
        )
    ]
    return lines, OcrPageResult(
        page_number=page_number,
        used=True,
        confidence=0.91,
        text_length=sum(len(line.text) for line in lines),
        warning=None,
    )


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
        assert extracted_contract["fallback_assisted"] is False
        assert extracted_contract["fallback_reason"] is None
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
            assert clause["clause_text"] == clause["text"]
            assert 0.0 <= clause["confidence"] <= 1.0
            assert clause["confidence_score"] == clause["confidence"]
            assert clause["page_number"] == 1
            assert clause["page_numbers"] == [1]
            assert len(clause["bbox"]) == 4
            assert clause["bounding_box_coordinates"] == clause["bbox"]
            assert clause["bbox"][0] < clause["bbox"][2]
            assert clause["bbox"][1] < clause["bbox"][3]
            assert clause["manual_review_required"] is False
            assert clause["evidence"]["evidence_id"] == "EV-001"
            assert clause["evidence_pointer"] == clause["evidence"]
            assert clause["evidence"]["source_file"] == "contract.pdf"
            assert clause["evidence"]["excerpt"]
            assert len(clause["evidence_spans"]) == 1

        audit_lines = (
            (run_dir / "audit_log.jsonl").read_text(encoding="utf-8").splitlines()
        )
        assert len(audit_lines) == 3
        extraction_event = json.loads(audit_lines[2])
        assert extraction_event["event"] == "extraction_completed"
        assert extraction_event["clause_count"] == 10

    def test_born_digital_pdf_does_not_attempt_ocr(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def fail_ocr(
            page: pymupdf.Page,
            page_number: int,
        ) -> tuple[list[TextLine], OcrPageResult]:
            raise AssertionError("OCR should not run for born-digital PDFs.")

        monkeypatch.setattr(pdf_text, "_extract_ocr_text_lines", fail_ocr)
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

        extracted_contract = result["extracted_contract"]
        assert extracted_contract["text_extraction_method"] == "digital"
        assert extracted_contract["ocr_metadata"] is None

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

    def test_blank_pdf_uses_synthetic_fallback(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            pdf_text,
            "_extract_ocr_text_lines",
            _mock_ocr_unavailable,
        )
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

        result = run_extraction(
            bundle_data=bundle_data,
            document_inventory=intake_result["document_inventory"],
            evidence_index=evidence_result["evidence_index"],
            run_id=intake_result["document_inventory"]["run_id"],
            run_dir=run_dir,
        )

        extracted_contract = result["extracted_contract"]
        assert extracted_contract["text_extraction_method"] == "none"
        assert extracted_contract["ocr_metadata"]["used"] is False
        assert extracted_contract["ocr_metadata"]["pages_processed"] == 1
        assert extracted_contract["fallback_assisted"] is True
        assert "no required clauses" in extracted_contract["fallback_reason"]
        assert len(extracted_contract["clauses"]) == 10
        assert all(
            clause["evidence"]["source_file"] == "contract.pdf"
            for clause in extracted_contract["clauses"]
        )

        audit_lines = (
            (run_dir / "audit_log.jsonl").read_text(encoding="utf-8").splitlines()
        )
        ocr_event = json.loads(audit_lines[2])
        assert ocr_event["event"] == "ocr_unavailable"
        fallback_event = json.loads(audit_lines[3])
        assert fallback_event["event"] == "extraction_fallback_used"
        assert "no required clauses" in fallback_event["reason"]
        extraction_event = json.loads(audit_lines[4])
        assert extraction_event["event"] == "extraction_completed"
        assert extraction_event["fallback_assisted"] is True

    def test_scanned_pdf_uses_ocr_metadata_and_audit(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(pdf_text, "_extract_ocr_text_lines", _mock_ocr_lines)
        bundle_copy = tmp_path / "ocr_contract_bundle"
        shutil.copytree(NDA_BUNDLE, bundle_copy)

        blank_pdf = pymupdf.open()
        blank_pdf.new_page()
        blank_pdf.save(bundle_copy / "contract.pdf")
        blank_pdf.close()

        bundle_data, run_dir, intake_result, evidence_result = _create_evidence_result(
            bundle_copy,
            tmp_path,
        )

        result = run_extraction(
            bundle_data=bundle_data,
            document_inventory=intake_result["document_inventory"],
            evidence_index=evidence_result["evidence_index"],
            run_id=intake_result["document_inventory"]["run_id"],
            run_dir=run_dir,
        )

        extracted_contract = result["extracted_contract"]
        assert extracted_contract["text_extraction_method"] == "ocr"
        assert extracted_contract["ocr_metadata"]["used"] is True
        assert extracted_contract["ocr_metadata"]["average_confidence"] == 0.91
        assert extracted_contract["ocr_metadata"]["manual_review_required"] is False
        assert extracted_contract["warnings"]
        assert any(
            clause["clause_type"] == "data_protection"
            for clause in extracted_contract["clauses"]
        )

        audit_events = [
            json.loads(line)
            for line in (run_dir / "audit_log.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert any(event["event"] == "ocr_used" for event in audit_events)

    def test_aggregates_clause_text_across_pages_and_filters_repeated_artifacts(
        self, tmp_path: Path
    ) -> None:
        bundle_copy = tmp_path / "multi_page_bundle"
        shutil.copytree(NDA_BUNDLE, bundle_copy)
        manifest_path = bundle_copy / "manifest.yaml"
        manifest_path.write_text(
            manifest_path.read_text(encoding="utf-8").replace(
                "bundle_name: clean_nda",
                "bundle_name: multi_page_bundle",
            ),
            encoding="utf-8",
        )
        _write_text_pdf(
            bundle_copy / "contract.pdf",
            [
                [
                    "ACME NDA HEADER",
                    "1 Confidentiality",
                    "Each party shall protect Confidential Information received",
                    "from the other party and use it only for Project Alpha.",
                    "Page 1",
                ],
                [
                    "ACME NDA HEADER",
                    "The confidentiality obligations continue for five years",
                    "after termination of this Agreement.",
                    "2 Termination",
                    "Either party may terminate this Agreement with 30 days notice.",
                    "Page 2",
                ],
            ],
        )
        bundle_data, run_dir, intake_result, evidence_result = _create_evidence_result(
            bundle_copy,
            tmp_path,
        )

        result = run_extraction(
            bundle_data=bundle_data,
            document_inventory=intake_result["document_inventory"],
            evidence_index=evidence_result["evidence_index"],
            run_id=intake_result["document_inventory"]["run_id"],
            run_dir=run_dir,
        )

        clauses = result["extracted_contract"]["clauses"]
        confidentiality = next(
            clause for clause in clauses if clause["clause_type"] == "confidentiality"
        )
        assert confidentiality["page_numbers"] == [1, 2]
        assert confidentiality["page_number"] == 1
        assert len(confidentiality["evidence_spans"]) == 2
        assert {span["page_number"] for span in confidentiality["evidence_spans"]} == {
            1,
            2,
        }
        assert "continue for five years" in confidentiality["text"]
        assert "ACME NDA HEADER" not in confidentiality["text"]
        assert "Page 1" not in confidentiality["text"]
        assert "Page 2" not in confidentiality["text"]

        termination = next(
            clause for clause in clauses if clause["clause_type"] == "termination"
        )
        assert termination["page_numbers"] == [2]

    def test_flags_low_confidence_clause_for_manual_review(
        self, tmp_path: Path
    ) -> None:
        bundle_copy = tmp_path / "low_confidence_bundle"
        shutil.copytree(NDA_BUNDLE, bundle_copy)
        _write_text_pdf(
            bundle_copy / "contract.pdf",
            [
                [
                    "1 Auto",
                    "This agreement may continue.",
                ],
            ],
        )
        bundle_data, run_dir, intake_result, evidence_result = _create_evidence_result(
            bundle_copy,
            tmp_path,
        )

        result = run_extraction(
            bundle_data=bundle_data,
            document_inventory=intake_result["document_inventory"],
            evidence_index=evidence_result["evidence_index"],
            run_id=intake_result["document_inventory"]["run_id"],
            run_dir=run_dir,
        )

        extracted_contract = result["extracted_contract"]
        assert extracted_contract["fallback_assisted"] is True
        assert "required clause coverage" in extracted_contract["fallback_reason"]
        assert len(extracted_contract["clauses"]) == 10

        auto_renewal = next(
            clause
            for clause in extracted_contract["clauses"]
            if clause["clause_type"] == "auto_renewal"
        )
        assert auto_renewal["confidence"] >= 0.75
        assert auto_renewal["manual_review_required"] is False

        warnings = extracted_contract["warnings"]
        assert any(
            warning["clause_type"] == "auto_renewal"
            and "Low confidence" in warning["message"]
            for warning in warnings
        )
        assert any(
            warning["clause_type"] is None
            and "Synthetic extraction fallback" in warning["message"]
            for warning in warnings
        )
