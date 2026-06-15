"""Tests for the Intake Agent (US-05)."""

import json
import shutil
from pathlib import Path

import pytest

from agents.intake_agent import IntakeAgentError, run_intake
from utils.bundle_loader import load_bundle
from utils.run_manager import create_run_folder


PROJECT_ROOT = Path(__file__).resolve().parent.parent
NDA_BUNDLE = PROJECT_ROOT / "data" / "bundles" / "clean_nda"


def _create_test_run(bundle_path: Path, tmp_path: Path) -> tuple[dict, Path]:
    """Create a run folder for intake tests."""
    run_info = create_run_folder(
        bundle_path=str(bundle_path),
        runs_dir=tmp_path / "runs",
    )
    return run_info, Path(run_info["run_dir"])


class TestRunIntake:
    """Test intake artifact creation and document classification."""

    def test_creates_context_packet_and_document_inventory(
        self, tmp_path: Path
    ) -> None:
        bundle_data = load_bundle(NDA_BUNDLE)
        run_info, run_dir = _create_test_run(NDA_BUNDLE, tmp_path)

        result = run_intake(
            bundle_data=bundle_data,
            run_id=run_info["run_id"],
            run_dir=run_dir,
        )

        context_path = run_dir / "context_packet.json"
        inventory_path = run_dir / "document_inventory.json"
        assert context_path.is_file()
        assert inventory_path.is_file()

        context = json.loads(context_path.read_text(encoding="utf-8"))
        assert context["run_id"] == run_info["run_id"]
        assert context["bundle_name"] == "clean_nda"
        assert context["contract_type"] == "Non-Disclosure Agreement"
        assert context["counterparty"] == "Acme Corporation"
        assert context["contract_file"] == "contract.pdf"
        assert "playbook" in context
        assert "approval_policy" in context
        assert "jurisdiction_rules" in context

        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        documents = {
            document["relative_path"]: document for document in inventory["documents"]
        }
        assert inventory["run_id"] == run_info["run_id"]
        assert "contract.pdf" in documents
        assert documents["contract.pdf"]["document_type"] == "contract"
        assert documents["contract.pdf"]["is_primary"] is True
        assert documents["contract.pdf"]["included"] is True
        assert (
            inventory["primary_contract_id"] == documents["contract.pdf"]["document_id"]
        )

        assert result["artifact_paths"]["context_packet"] == str(context_path)
        assert result["artifact_paths"]["document_inventory"] == str(inventory_path)

    def test_classifies_supporting_bundle_files(self, tmp_path: Path) -> None:
        bundle_data = load_bundle(NDA_BUNDLE)
        run_info, run_dir = _create_test_run(NDA_BUNDLE, tmp_path)

        run_intake(
            bundle_data=bundle_data,
            run_id=run_info["run_id"],
            run_dir=run_dir,
        )

        inventory = json.loads(
            (run_dir / "document_inventory.json").read_text(encoding="utf-8")
        )
        document_types = {
            document["relative_path"]: document["document_type"]
            for document in inventory["documents"]
        }

        assert document_types["manifest.yaml"] == "manifest"
        assert document_types["vendor_master.csv"] == "vendor_master"
        assert document_types["playbook.yaml"] == "playbook"
        assert document_types["approval_policy.yaml"] == "approval_policy"
        assert document_types["jurisdiction_rules.yaml"] == "jurisdiction_rules"

    def test_unsupported_files_are_recorded_but_excluded(self, tmp_path: Path) -> None:
        bundle_copy = tmp_path / "bundle_with_extra_file"
        shutil.copytree(NDA_BUNDLE, bundle_copy)
        (bundle_copy / "notes.txt").write_text(
            "This file is not part of the supported bundle format.",
            encoding="utf-8",
        )
        bundle_data = load_bundle(bundle_copy)
        run_info, run_dir = _create_test_run(bundle_copy, tmp_path)

        run_intake(
            bundle_data=bundle_data,
            run_id=run_info["run_id"],
            run_dir=run_dir,
        )

        inventory = json.loads(
            (run_dir / "document_inventory.json").read_text(encoding="utf-8")
        )
        documents = {
            document["relative_path"]: document for document in inventory["documents"]
        }
        assert documents["notes.txt"]["document_type"] == "unsupported"
        assert documents["notes.txt"]["included"] is False
        assert "Unsupported file type" in documents["notes.txt"]["reason"]

        audit_lines = (
            (run_dir / "audit_log.jsonl").read_text(encoding="utf-8").splitlines()
        )
        assert len(audit_lines) == 1
        audit_event = json.loads(audit_lines[0])
        assert audit_event["event"] == "intake_completed"
        assert audit_event["unsupported_document_count"] == 1

    def test_missing_run_directory_raises_clear_error(self) -> None:
        bundle_data = load_bundle(NDA_BUNDLE)

        with pytest.raises(IntakeAgentError, match="Create it with create_run_folder"):
            run_intake(
                bundle_data=bundle_data,
                run_id="test-run",
                run_dir=PROJECT_ROOT / "runs" / "missing-run-dir",
            )
