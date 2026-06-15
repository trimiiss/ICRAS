"""Tests for the command-line pipeline entry point."""

import json
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
NDA_BUNDLE = PROJECT_ROOT / "data" / "bundles" / "clean_nda"


def test_valid_bundle_creates_intake_evidence_and_extraction_artifacts(
    tmp_path: Path,
) -> None:
    """A valid CLI run should create intake, evidence, and extraction artifacts."""
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "main.py"),
            "--bundle",
            str(NDA_BUNDLE),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "evidence_index.json" in result.stdout
    assert "extracted_contract.json" in result.stdout

    run_dirs = list((tmp_path / "runs").iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]

    assert (run_dir / "context_packet.json").is_file()
    assert (run_dir / "document_inventory.json").is_file()
    assert (run_dir / "evidence_index.json").is_file()
    assert (run_dir / "extracted_contract.json").is_file()

    evidence_index = json.loads(
        (run_dir / "evidence_index.json").read_text(encoding="utf-8")
    )
    assert evidence_index["records"][0]["evidence_id"] == "EV-001"

    extracted_contract = json.loads(
        (run_dir / "extracted_contract.json").read_text(encoding="utf-8")
    )
    assert len(extracted_contract["clauses"]) == 10
    assert extracted_contract["clauses"][0]["evidence"]["evidence_id"] == "EV-001"


def test_invalid_bundle_creates_failed_run_with_audit_log(tmp_path: Path) -> None:
    """Bundle validation failures should be traceable in a run audit log."""
    invalid_bundle = tmp_path / "invalid_bundle"
    shutil.copytree(NDA_BUNDLE, invalid_bundle)
    (invalid_bundle / "playbook.yaml").unlink()

    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "main.py"),
            "--bundle",
            str(invalid_bundle),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "Bundle validation failed" in result.stderr

    run_dirs = list((tmp_path / "runs").iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]

    metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "failed"
    assert "playbook.yaml" in metadata["error_message"]

    audit_lines = (run_dir / "audit_log.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(audit_lines) == 1
    audit_event = json.loads(audit_lines[0])
    assert audit_event["event"] == "bundle_validation_failed"
    assert audit_event["agent"] == "intake_agent"
    assert "playbook.yaml" in audit_event["error"]
