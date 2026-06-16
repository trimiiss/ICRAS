"""Smoke tests for Sprint 1 intake, extraction, and validation flow."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pymupdf

from agents.extraction_agent import run_extraction
from agents.intake_agent import run_intake
from agents.risk_agent import run_risk_assessment
from agents.validation_agent import run_validation
from utils.bundle_loader import load_bundle
from utils.evidence_indexer import build_evidence_index
from utils.run_manager import create_run_folder


PROJECT_ROOT = Path(__file__).resolve().parent.parent
NDA_BUNDLE = PROJECT_ROOT / "data" / "bundles" / "clean_nda"


EXPECTED_SMOKE_FILES = {
    "context_packet.json",
    "extracted_contract.json",
    "validation_findings.json",
    "clause_analysis.json",
    "audit_log.md",
}


def _run_main(bundle_path: Path, tmp_path: Path) -> tuple[subprocess.CompletedProcess, Path]:
    """Run the CLI against a bundle and return the result and run directory."""
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "main.py"),
            "--bundle",
            str(bundle_path),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    run_dirs = list((tmp_path / "runs").iterdir())
    assert len(run_dirs) == 1
    return result, run_dirs[0]


def _assert_expected_files(run_dir: Path) -> None:
    """Assert the Sprint 1 smoke output files exist."""
    for filename in EXPECTED_SMOKE_FILES:
        assert (run_dir / filename).is_file(), f"Missing smoke artifact: {filename}"


def _write_text_pdf(pdf_path: Path, lines: list[str]) -> None:
    """Create a one-page born-digital PDF fixture."""
    pdf = pymupdf.open()
    try:
        page = pdf.new_page()
        y = 72
        for line in lines:
            page.insert_text((72, y), line, fontsize=10)
            y += 18
        pdf.save(pdf_path)
    finally:
        pdf.close()


def test_clean_nda_full_flow_creates_smoke_artifacts(tmp_path: Path) -> None:
    """Clean NDA should run Agent A -> B -> D without errors."""
    result, run_dir = _run_main(NDA_BUNDLE, tmp_path)

    assert result.returncode == 0, result.stderr
    _assert_expected_files(run_dir)

    extracted_contract = json.loads(
        (run_dir / "extracted_contract.json").read_text(encoding="utf-8")
    )
    assert extracted_contract["run_id"]
    assert extracted_contract["clauses"]

    audit_log = (run_dir / "audit_log.md").read_text(encoding="utf-8")
    assert "intake_completed" in audit_log
    assert "extraction_completed" in audit_log
    assert "validation_completed" in audit_log
    assert "risk_scoring_completed" in audit_log


def test_missing_required_field_is_flagged_by_validation(tmp_path: Path) -> None:
    """A missing effective date should become a validation finding."""
    bundle_copy = tmp_path / "missing_effective_date_bundle"
    shutil.copytree(NDA_BUNDLE, bundle_copy)
    manifest_path = bundle_copy / "manifest.yaml"
    manifest_lines = manifest_path.read_text(encoding="utf-8").splitlines()
    manifest_path.write_text(
        "\n".join(
            line.replace("bundle_name: clean_nda", "bundle_name: missing_effective_date_bundle")
            for line in manifest_lines
            if not line.startswith("effective_date:")
        )
        + "\n",
        encoding="utf-8",
    )
    _write_text_pdf(
        bundle_copy / "contract.pdf",
        [
            "1 Parties",
            "This Agreement is between Genpact LLC and Acme Corporation.",
            "2 Confidentiality",
            "Each party shall protect Confidential Information for Project Alpha.",
            "3 Termination",
            "Either party may terminate this Agreement with 30 days written notice.",
            "4 Payment Terms",
            "No fees are due under this NDA, and approved invoices are payable net 30.",
            "5 Limitation of Liability",
            "Each party's liability is capped at 100000 USD.",
            "6 Indemnity",
            "Each party shall indemnify the other for third-party claims.",
            "7 Governing Law",
            "This Agreement is governed by the laws of Delaware, USA.",
            "8 Auto-Renewal",
            "This Agreement does not auto-renew after expiration.",
            "9 Data Protection",
            "Each party shall comply with applicable data protection and privacy laws.",
        ],
    )

    result, run_dir = _run_main(bundle_copy, tmp_path)

    assert result.returncode == 0, result.stderr
    _assert_expected_files(run_dir)

    validation = json.loads(
        (run_dir / "validation_findings.json").read_text(encoding="utf-8")
    )
    assert any(
        finding["title"] == "Missing effective date"
        for finding in validation["findings"]
    )


def test_low_confidence_extraction_fallback_continues_pipeline(
    tmp_path: Path,
) -> None:
    """Fallback extraction should still allow validation to produce output."""
    bundle_copy = tmp_path / "blank_contract_bundle"
    shutil.copytree(NDA_BUNDLE, bundle_copy)

    blank_pdf = pymupdf.open()
    try:
        blank_pdf.new_page()
        blank_pdf.save(bundle_copy / "contract.pdf")
    finally:
        blank_pdf.close()

    bundle_data = load_bundle(bundle_copy)
    run_info = create_run_folder(str(bundle_copy), runs_dir=tmp_path / "runs")
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

    extraction_result = run_extraction(
        bundle_data=bundle_data,
        document_inventory=intake_result["document_inventory"],
        evidence_index=evidence_result["evidence_index"],
        run_id=run_info["run_id"],
        run_dir=run_dir,
    )
    validation_result = run_validation(
        context=intake_result["context_packet"],
        clauses=extraction_result["extracted_contract"]["clauses"],
        run_dir=run_dir,
        evidence_index=evidence_result["evidence_index"],
    )
    risk_result = run_risk_assessment(
        context=intake_result["context_packet"],
        extracted_contract=extraction_result["extracted_contract"],
        validation_result=validation_result["validation_result"],
        run_dir=run_dir,
    )

    _assert_expected_files(run_dir)
    assert extraction_result["extracted_contract"]["fallback_assisted"] is True
    assert validation_result["validation_result"]["run_id"] == run_info["run_id"]
    assert risk_result["clause_analysis"]["run_id"] == run_info["run_id"]
