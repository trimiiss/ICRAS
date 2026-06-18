"""Tests for page-level evidence indexing."""

import json
import shutil
from pathlib import Path

import pymupdf
import pytest

from agents.intake import run_intake
from utils.bundle_loader import load_bundle
from utils.evidence_indexer import EvidenceIndexError, build_evidence_index
from utils.run_manager import create_run_folder


PROJECT_ROOT = Path(__file__).resolve().parent.parent
NDA_BUNDLE = PROJECT_ROOT / "data" / "bundles" / "clean_nda"


def _create_intake_result(bundle_path: Path, tmp_path: Path) -> tuple[dict, Path, dict]:
    """Load a bundle, create a run folder, and run intake."""
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
    return bundle_data, run_dir, intake_result


class TestBuildEvidenceIndex:
    """Test evidence_index.json generation from clean PDFs."""

    def test_creates_evidence_index_with_page_reference_and_excerpt(
        self, tmp_path: Path
    ) -> None:
        bundle_data, run_dir, intake_result = _create_intake_result(
            NDA_BUNDLE,
            tmp_path,
        )

        result = build_evidence_index(
            bundle_data=bundle_data,
            document_inventory=intake_result["document_inventory"],
            run_id=intake_result["document_inventory"]["run_id"],
            run_dir=run_dir,
        )

        evidence_path = run_dir / "evidence_index.json"
        assert evidence_path.is_file()
        assert result["artifact_paths"]["evidence_index"] == str(evidence_path)

        evidence_index = json.loads(evidence_path.read_text(encoding="utf-8"))
        assert evidence_index["source_file"] == "contract.pdf"
        assert evidence_index["document_id"] == "DOC-002"
        assert len(evidence_index["records"]) == 1
        assert evidence_index["warnings"] == []

        record = evidence_index["records"][0]
        assert record["evidence_id"] == "EV-001"
        assert record["document_id"] == "DOC-002"
        assert record["source_file"] == "contract.pdf"
        assert record["page_number"] == 1
        assert record["clause_id"] is None
        assert record["section_reference"] is None
        assert record["related_finding_ids"] == []
        assert record["char_start"] == 0
        assert record["char_end"] >= len(record["excerpt"])
        assert "Acme Corporation" in record["excerpt"]

    def test_evidence_ids_are_unique_for_all_records(self, tmp_path: Path) -> None:
        bundle_data, run_dir, intake_result = _create_intake_result(
            NDA_BUNDLE,
            tmp_path,
        )

        result = build_evidence_index(
            bundle_data=bundle_data,
            document_inventory=intake_result["document_inventory"],
            run_id=intake_result["document_inventory"]["run_id"],
            run_dir=run_dir,
        )

        records = result["evidence_index"]["records"]
        evidence_ids = [record["evidence_id"] for record in records]
        assert evidence_ids
        assert len(evidence_ids) == len(set(evidence_ids))

    def test_empty_page_text_creates_warning(self, tmp_path: Path) -> None:
        bundle_copy = tmp_path / "blank_contract_bundle"
        shutil.copytree(NDA_BUNDLE, bundle_copy)

        blank_pdf = pymupdf.open()
        blank_pdf.new_page()
        blank_pdf.save(bundle_copy / "contract.pdf")
        blank_pdf.close()

        bundle_data, run_dir, intake_result = _create_intake_result(
            bundle_copy,
            tmp_path,
        )

        result = build_evidence_index(
            bundle_data=bundle_data,
            document_inventory=intake_result["document_inventory"],
            run_id=intake_result["document_inventory"]["run_id"],
            run_dir=run_dir,
        )

        evidence_index = result["evidence_index"]
        assert evidence_index["records"] == []
        assert len(evidence_index["warnings"]) == 1
        warning = evidence_index["warnings"][0]
        assert warning["warning_id"] == "WARN-001"
        assert warning["page_number"] == 1
        assert "No extractable text" in warning["message"]

        audit_lines = (
            (run_dir / "audit_log.jsonl").read_text(encoding="utf-8").splitlines()
        )
        assert len(audit_lines) == 2
        evidence_event = json.loads(audit_lines[1])
        assert evidence_event["event"] == "evidence_index_completed"
        assert evidence_event["warning_count"] == 1

    def test_missing_primary_contract_raises_clear_error(self, tmp_path: Path) -> None:
        bundle_data, run_dir, intake_result = _create_intake_result(
            NDA_BUNDLE,
            tmp_path,
        )
        intake_result["document_inventory"]["documents"] = []

        with pytest.raises(EvidenceIndexError, match="No primary contract document"):
            build_evidence_index(
                bundle_data=bundle_data,
                document_inventory=intake_result["document_inventory"],
                run_id=intake_result["document_inventory"]["run_id"],
                run_dir=run_dir,
            )
